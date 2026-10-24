"""Worker telemetry follows the existing runtime and resource owners."""

import asyncio
from contextlib import contextmanager
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

import app.worker.processing as processing
import app.worker.resources as resources
import app.worker.runtime as worker_runtime
from app.core.config import Settings
from app.core.transactions import CommitOutcomeUnknown
from app.observability.runtime import TelemetryRuntime
from app.worker.models import FailureReason, Outcome
from app.worker.runtime import Shutdown

VALID_SETTINGS = {
    "DATABASE_URL": "mysql+asyncmy://user:password@localhost:3306/prl",
    "APPLICATION_ORIGIN": "http://localhost:3000",
    "COOKIE_SECRET": "cookie-secret",
    "CSRF_SECRET": "csrf-secret",
    "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
}


def _settings() -> Settings:
    return Settings.model_validate(VALID_SETTINGS)


async def test_worker_runtime_owns_otlp_only_observer_and_closes_it(monkeypatch):
    settings = _settings()
    shutdown = Shutdown(settings.worker_grace_seconds)
    shutdown.request_stop()
    calls = []

    class Telemetry:
        async def close(self, deadline):
            calls.append(("telemetry.close", deadline))

    telemetry = Telemetry()

    class Observer:
        async def close(self, deadline):
            calls.append(("observer.close", deadline))

    observer = Observer()

    def create_telemetry(actual_settings, *, service, stdout_enabled):
        calls.append(("create", actual_settings, service, stdout_enabled))
        return telemetry

    class Reporter:
        def __init__(self, *, observer):
            calls.append(("reporter.create", observer))

        def emit(self, event, state, **kwargs):
            calls.append(("reporter.emit", event))
            return True

        async def close(self, deadline):
            calls.append(("reporter.close", deadline))

    monkeypatch.setattr(worker_runtime, "create_telemetry", create_telemetry, raising=False)
    monkeypatch.setattr(worker_runtime, "WorkerObserver", lambda *_: observer, raising=False)
    monkeypatch.setattr(worker_runtime, "Reporter", Reporter)

    result = await worker_runtime.run_worker(settings, shutdown=shutdown)

    assert result == 0
    assert calls[:2] == [
        ("create", settings, "worker", False),
        ("reporter.create", observer),
    ]
    assert [call[0] for call in calls[-3:]] == [
        "reporter.close",
        "observer.close",
        "telemetry.close",
    ]
    assert all(call[-1] == shutdown.deadline for call in calls[-3:])


async def test_open_resources_detaches_database_observer_before_disposal(monkeypatch):
    calls = []

    class Engine:
        sync_engine = object()

        async def dispose(self):
            calls.append("dispose")

    engine = Engine()
    telemetry = object()

    def attach_database(actual_engine, actual_telemetry):
        assert actual_engine is engine
        assert actual_telemetry is telemetry
        calls.append("attach")

        def detach():
            calls.append("detach")

        return detach

    monkeypatch.setattr(resources, "attach_database", attach_database, raising=False)
    monkeypatch.setattr(resources, "async_sessionmaker", lambda _, **kwargs: object())

    async with resources.open_resources(
        _settings(),
        engine_factory=lambda *args, **kwargs: cast(AsyncEngine, engine),
        telemetry=cast(TelemetryRuntime, telemetry),
    ) as opened:
        assert opened.engine is engine

    assert calls == ["attach", "detach", "dispose"]


class _Span:
    def __init__(self, name, outcomes):
        self.name = name
        self.outcomes = outcomes

    def set_attribute(self, name, value):
        if name == "business.outcome":
            self.outcomes[self.name] = value


def _recording_span_factory(outcomes):
    @contextmanager
    def factory(name):
        yield _Span(name, outcomes)

    return factory


class _Session:
    async def begin(self):
        return object()

    async def flush(self):
        return None


async def _prepare_candidate(monkeypatch, transition, commit):
    async def validate_schema(*args):
        return None

    async def expire(db, **kwargs):
        factory = kwargs["span_factory"]
        with factory("proposal.expire") as span:
            span.set_attribute("business.outcome", "staged")
        return transition

    monkeypatch.setattr(processing, "validate_schema", validate_schema)
    monkeypatch.setattr(processing, "expire_change_proposal_in_transaction", expire)
    monkeypatch.setattr(processing, "commit_safely", commit)
    monkeypatch.setattr(processing, "_finish_session", lambda *args, **kwargs: asyncio.sleep(0))


async def test_candidate_expiry_owns_one_span_and_marks_commit_only_after_success(monkeypatch):
    outcomes = {}
    calls = []

    def callback(error, *, kind="statement"):
        calls.append((error, kind))

    async def commit(db, transaction, **kwargs):
        calls.append(kwargs)

    await _prepare_candidate(monkeypatch, "EXPIRED", commit)

    result = await processing.process_candidate(
        cast(processing.SessionFactory, lambda: _Session()),
        uuid4(),
        _settings(),
        "worker.test",
        span_factory=_recording_span_factory(outcomes),
        failure_callback=callback,
    )

    assert result.outcome == Outcome.EXPIRED
    assert outcomes == {
        "proposal.expire": "staged",
        "worker.candidate": "committed",
    }
    assert calls and calls[0]["failure_callback"] is callback
    assert calls[0]["span_factory"] is not None


async def test_candidate_commit_cancellation_keeps_unknown_span_and_original_exception(monkeypatch):
    outcomes = {}
    cancellation = asyncio.CancelledError("commit wait cancelled")

    async def commit(db, transaction, *, commit_unknown_callback=None, **kwargs):
        assert commit_unknown_callback is not None
        commit_unknown_callback()
        raise cancellation

    await _prepare_candidate(monkeypatch, "EXPIRED", commit)

    with pytest.raises(asyncio.CancelledError) as raised:
        await processing.process_candidate(
            cast(processing.SessionFactory, lambda: _Session()),
            uuid4(),
            _settings(),
            "worker.test",
            span_factory=_recording_span_factory(outcomes),
        )

    assert raised.value is cancellation
    assert outcomes["worker.candidate"] == "commit_unknown"


async def test_candidate_commit_unknown_result_is_a_failure_observation(monkeypatch):
    outcomes = {}

    async def commit(db, transaction, **kwargs):
        raise CommitOutcomeUnknown()

    await _prepare_candidate(monkeypatch, "EXPIRED", commit)

    result = await processing.process_candidate(
        cast(processing.SessionFactory, lambda: _Session()),
        uuid4(),
        _settings(),
        "worker.test",
        span_factory=_recording_span_factory(outcomes),
    )

    assert result.outcome == Outcome.FAILED
    assert result.reason == FailureReason.COMMIT_UNKNOWN
    assert outcomes["worker.candidate"] == "commit_unknown"
