"""Real-MySQL coverage for safe database traces, failures, pool state, and ownership."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.errors import AppError
from app.domain.auth import AuthContext
from app.factory import create_app
from app.observability.database import record_database_failure
from app.observability.runtime import TelemetryRuntime
from app.schemas.appointments import AppointmentCreateRequest
from app.services.booking import create_appointment

pytestmark = pytest.mark.mysql


def _sample(runtime, name: str, labels: dict[str, str] | None = None) -> float:
    expected = labels or {}
    for family in runtime.metrics.registry.collect():
        for sample in family.samples:
            if sample.name == name and sample.labels == expected:
                return float(sample.value)
    return 0.0


def test_failure_deduplication_is_scoped_to_the_telemetry_owner():
    error = OperationalError("COMMIT", {}, Exception(1213, "driver detail"))
    first_record = Mock()
    second_record = Mock()
    first = cast(
        TelemetryRuntime,
        SimpleNamespace(metrics=SimpleNamespace(record_db_failure=first_record)),
    )
    second = cast(
        TelemetryRuntime,
        SimpleNamespace(metrics=SimpleNamespace(record_db_failure=second_record)),
    )

    record_database_failure(first, error)
    record_database_failure(first, error)
    record_database_failure(second, error)
    record_database_failure(first, error)

    first_record.assert_called_once_with("deadlock")
    second_record.assert_called_once_with("deadlock")


async def test_real_query_is_timed_and_pool_returns_to_zero_without_sql_or_parameter_attributes(
    availability_env,
):
    env = availability_env
    runtime = env.app.state.telemetry
    exporter = InMemorySpanExporter()
    runtime.trace_provider.add_span_processor(SimpleSpanProcessor(exporter))

    async with env.factory() as db:
        value = await db.scalar(
            text("SELECT :private_parameter AS result"),
            {"private_parameter": "PRIVATE_PARAMETER_CANARY"},
        )

    assert value == "PRIVATE_PARAMETER_CANARY"
    assert _sample(runtime, "prl_db_statement_duration_seconds_count", {"operation": "SELECT"}) >= 1
    assert _sample(runtime, "prl_db_pool_checked_out") == 0

    spans = exporter.get_finished_spans()
    query_spans = [span for span in spans if span.name == "db.query"]
    assert query_spans
    forbidden = {"db.statement", "db.query.text", "db.parameters", "exception.message"}
    for span in query_spans:
        attributes = span.attributes or {}
        assert not forbidden.intersection(attributes)
        assert all("PRIVATE_PARAMETER_CANARY" not in str(value) for value in attributes.values())


async def test_telemetry_callback_failure_does_not_change_a_real_mysql_query(
    availability_env, monkeypatch
):
    env = availability_env
    runtime = env.app.state.telemetry
    before = _sample(runtime, "prl_db_statement_duration_seconds_count", {"operation": "SELECT"})
    record = runtime.metrics.record_db_statement

    def record_then_raise(operation: str, duration_seconds: float) -> None:
        record(operation, duration_seconds)
        raise RuntimeError("private telemetry listener failure")

    monkeypatch.setattr(runtime.metrics, "record_db_statement", record_then_raise)
    async with env.factory() as db:
        assert await db.scalar(text("SELECT 29")) == 29

    assert (
        _sample(runtime, "prl_db_statement_duration_seconds_count", {"operation": "SELECT"})
        == before + 1
    )


async def test_mysql_lock_timeout_has_one_finite_failure_signal(availability_env):
    env = availability_env
    runtime = env.app.state.telemetry
    engine = env.factory.kw["bind"]

    for expected_failures in (1, 2):
        async with engine.connect() as holder:
            transaction = await holder.begin()
            await holder.execute(
                text("UPDATE service_settings SET timezone = timezone WHERE id = 1")
            )
            try:
                async with engine.connect() as contender:
                    await contender.execute(text("SET SESSION innodb_lock_wait_timeout = 1"))
                    with pytest.raises(OperationalError) as caught:
                        await contender.execute(
                            text("UPDATE service_settings SET timezone = timezone WHERE id = 1")
                        )
                    # The SQLAlchemy listener and service helper see the same error object.
                    record_database_failure(runtime, caught.value)
                    await contender.rollback()
            finally:
                await transaction.rollback()
        assert (
            _sample(runtime, "prl_db_failures_total", {"reason": "lock_timeout"})
            == expected_failures
        )

    assert _sample(runtime, "prl_db_pool_checked_out") == 0


async def test_pool_acquisition_timeout_is_observed_before_any_cursor_event(
    booking_env, auth_settings
):
    from app.core.database import create_database_engine

    env, _, starts_at = booking_env
    engine = create_database_engine(
        env.factory.kw["bind"].url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.05,
        connect_args={"connect_timeout": 5},
    )
    runtime = None

    def telemetry_factory(settings, *, service):
        nonlocal runtime
        runtime = TelemetryRuntime(settings, service, stdout_sink=lambda _line: None)
        return runtime

    app = create_app(
        auth_settings,
        engine_factory=lambda *args, **kwargs: engine,
        telemetry_factory=telemetry_factory,
    )
    try:
        async with app.router.lifespan_context(app):
            assert runtime is not None
            factory = async_sessionmaker(engine, expire_on_commit=False)
            request = AppointmentCreateRequest(
                purpose_id=env.purpose_id,
                staff_id=env.staff_id,
                starts_at=starts_at,
            )
            context = AuthContext(
                session_id=uuid4(),
                user_id=env.student_id,
                email="student@example.invalid",
                name="Test Student",
                role="STUDENT",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            async with engine.connect() as blocker:
                await blocker.execute(text("SELECT 1"))
                async with factory() as db:
                    with pytest.raises(AppError) as caught:
                        await create_appointment(
                            db,
                            request,
                            context=context,
                            idempotency_key="database-observation-pool-timeout",
                            request_id="database-observation-pool-timeout",
                            settings=auth_settings,
                            deadline=asyncio.get_running_loop().time() + 5,
                            span_factory=runtime.span,
                            failure_callback=partial(record_database_failure, runtime),
                        )
                    assert getattr(caught.value, "code", None) == "SERVICE_UNAVAILABLE"
            assert _sample(runtime, "prl_db_pool_checked_out") == 0
            assert _sample(runtime, "prl_db_pool_idle") == 1
            assert _sample(runtime, "prl_db_pool_overflow") == 0
        assert runtime is not None
        assert (
            _sample(runtime, "prl_db_failures_total", {"reason": "pool_timeout"}) == 1
        ), runtime.metrics.render()
        assert _sample(runtime, "prl_db_pool_checked_out") == 0
    finally:
        await engine.dispose()


async def test_api_lifespan_detaches_database_listeners_before_disposing_engine(
    auth_settings, mysql_engine
):
    runtime = None

    def telemetry_factory(settings, *, service):
        nonlocal runtime
        runtime = TelemetryRuntime(settings, service, stdout_sink=lambda _line: None)
        return runtime

    app = create_app(
        auth_settings,
        engine_factory=lambda *args, **kwargs: mysql_engine,
        telemetry_factory=telemetry_factory,
    )
    async with app.router.lifespan_context(app):
        async with mysql_engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
        assert runtime is not None
        before_cleanup = _sample(
            runtime, "prl_db_statement_duration_seconds_count", {"operation": "SELECT"}
        )
        assert before_cleanup >= 1

    async with mysql_engine.connect() as connection:
        assert await connection.scalar(text("SELECT 2")) == 2
    after_cleanup = _sample(
        runtime, "prl_db_statement_duration_seconds_count", {"operation": "SELECT"}
    )
    assert after_cleanup == before_cleanup
