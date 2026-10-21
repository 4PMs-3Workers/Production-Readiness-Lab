import json
from datetime import UTC, datetime
from uuid import uuid4

from app.core.request_context import REQUEST_ID_PATTERN
from app.worker.models import CandidateResult, FailureReason, Outcome, WorkerIdentity, WorkerState
from app.worker.reporting import Reporter


def test_worker_run_and_candidate_ids_fit_the_shared_audit_contract():
    identity = WorkerIdentity()
    run, proposal = uuid4(), uuid4()
    request_id = identity.request_id(run, proposal)
    assert REQUEST_ID_PATTERN.fullmatch(request_id.encode("ascii"))
    assert len(request_id) <= 128
    assert request_id == identity.request_id(run, proposal)
    assert request_id != identity.request_id(uuid4(), proposal)
    assert identity.worker_id != WorkerIdentity().worker_id


def test_heartbeat_separates_liveness_from_database_progress():
    output = []
    reporter = Reporter(output.append)
    state = WorkerState(uuid4())
    assert reporter.emit("heartbeat", state)
    initial = json.loads(output[-1])
    assert initial["event"] == "heartbeat"
    assert initial["database_ok"] is False and initial["last_successful_scan"] is None
    state.database_ok = True
    state.last_successful_scan = datetime(2026, 1, 1, tzinfo=UTC)
    assert reporter.emit("scan", state)
    assert json.loads(output[-1])["last_successful_scan"] == "2026-01-01T00:00:00Z"


def test_candidate_failure_publishes_only_typed_metadata():
    output = []
    state = WorkerState(uuid4())
    result = CandidateResult(
        uuid4(), "expiry.safe-id", Outcome.FAILED, 1, 0.01, FailureReason.COMMIT_UNKNOWN
    )
    assert Reporter(output.append).emit("candidate", state, result=result)
    assert json.loads(output[0])["candidate"] == {
        "proposal_id": result.proposal_id.hex,
        "request_id": "expiry.safe-id",
        "outcome": "FAILED",
        "attempts": 1,
        "duration_seconds": 0.01,
        "reason": "COMMIT_UNKNOWN",
    }


def test_reporting_failure_does_not_raise_or_publish_exception_details(caplog):
    def broken_sink(message):
        raise OSError("private-token-sql-and-consultation-content")

    state = WorkerState(uuid4())
    assert Reporter(broken_sink).emit("heartbeat", state) is False
    assert state.reporting_failures == 1
    assert "private-token" not in caplog.text
    output = []
    Reporter(output.append).emit("heartbeat", state)
    assert json.loads(output[0])["reporting_failures"] == 1
