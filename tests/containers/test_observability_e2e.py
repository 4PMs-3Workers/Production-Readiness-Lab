"""Task 8 live-authenticated business and recovery acceptance helpers."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
import yaml
from tests.containers.test_compose_acceptance import AcceptanceFailure
from tools.deploy.worker_status import WorkerProgress, WorkerProgressTracker, latest_worker_progress

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OBSERVABILITY_TESTS") != "1",
    reason="opt-in Docker observability acceptance",
)


def test_collector_down_has_one_warning_rule() -> None:
    document = yaml.safe_load(
        (ROOT / "tools/infra/prometheus-rules.yml").read_text(encoding="utf-8")
    )
    matches = [
        rule["alert"]
        for group in document["groups"]
        for rule in group["rules"]
        if "alert" in rule
        and "up{" in str(rule.get("expr", ""))
        and "collector" in str(rule.get("expr", ""))
    ]
    assert matches == ["TelemetryTargetDown"]
    target_down = next(
        rule
        for group in document["groups"]
        for rule in group["rules"]
        if rule.get("alert") == "TelemetryTargetDown"
    )
    assert "prometheus-self" in target_down["expr"]


def test_filelog_promotes_gateway_timestamp_to_otlp_event_time() -> None:
    config = yaml.safe_load((ROOT / "tools/infra/otel-collector.yaml").read_text(encoding="utf-8"))
    parser = next(
        operator
        for operator in config["receivers"]["filelog/gateway"]["operators"]
        if operator.get("id") == "gateway_json"
    )
    assert parser["timestamp"] == {
        "parse_from": "attributes.timestamp",
        "layout_type": "gotime",
        "layout": "2006-01-02T15:04:05-07:00",
    }


def test_loki_healthchecks_require_recent_producer_timestamp() -> None:
    from tests.containers.test_telemetry_stack import FullStackRuntime

    recovery_ns = time.time_ns() - 8_000_000_000
    receipt_ns = recovery_ns + 1_000_000_000
    entries = [
        [
            str(receipt_ns + offset),
            json.dumps(
                {
                    "event": "gateway.healthcheck",
                    "classification": "out_of_scope",
                    "timestamp": datetime.fromtimestamp(
                        (recovery_ns - 10_000_000_000 + offset) / 1_000_000_000, UTC
                    ).isoformat(),
                }
            ),
        ]
        for offset in (0, 5_000_000_000)
    ]
    runtime = object.__new__(FullStackRuntime)

    def fake_api_json(_url: str) -> dict[str, Any]:
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "stream": {
                            "service_name": "gateway",
                            "deployment_environment_name": "demo",
                        },
                        "values": entries,
                    }
                ]
            },
        }

    runtime.api_json = fake_api_json  # type: ignore[method-assign]
    with pytest.raises(AcceptanceFailure, match="recurring genuine gateway healthchecks"):
        runtime.query_loki_healthchecks(start_ns=recovery_ns)


@dataclass(frozen=True)
class ApiResult:
    status: int
    headers: dict[str, str]
    body: dict[str, Any]
    elapsed_ms: float


class LiveApiSession:
    """Cookie and signed-CSRF session over fixture-CA-verified gateway HTTPS."""

    def __init__(
        self, runtime: Any, email: str, password: str, *, expected_role: Literal["STAFF", "STUDENT"]
    ) -> None:
        from tests.containers.test_telemetry_stack import _LocalTLSConnection

        self.runtime = runtime
        self.email = email
        self.password = password
        self.expected_role = expected_role
        self.connection_type = _LocalTLSConnection
        self.host = runtime.app_host
        self.port = runtime.port
        self.context = ssl.create_default_context(cafile=str(runtime.app_cert))
        self.cookies: dict[str, str] = {}
        self.csrf_token = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        csrf: bool = False,
        idempotency_key: str | None = None,
    ) -> ApiResult:
        headers = {"Accept": "application/json", "Origin": self.runtime.app_origin}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = "; ".join(
                f"{name}={value}" for name, value in sorted(self.cookies.items())
            )
        if csrf:
            if not self.csrf_token:
                raise AcceptanceFailure("A live write lacked signed CSRF session state")
            headers["X-CSRF-Token"] = self.csrf_token
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        connection = self.connection_type(self.host, self.port, timeout=6, context=self.context)
        started = time.perf_counter()
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            content = response.read(1_000_000)
            elapsed_ms = (time.perf_counter() - started) * 1000
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            self._store_cookies(response.getheaders())
            try:
                parsed = json.loads(content) if content else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            return ApiResult(response.status, response_headers, parsed, elapsed_ms)
        except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
            raise AcceptanceFailure("A bounded fixture HTTPS API request failed") from None
        finally:
            connection.close()

    def _store_cookies(self, headers: list[tuple[str, str]]) -> None:
        for key, value in headers:
            if key.lower() != "set-cookie":
                continue
            parsed = SimpleCookie()
            try:
                parsed.load(value)
            except Exception:
                continue
            for name, morsel in parsed.items():
                if name in {"prl_session", "prl_csrf"}:
                    self.cookies[name] = morsel.value

    def sign_in(self) -> None:
        login = self.request(
            "POST",
            "/api/v1/auth/login",
            body={"email": self.email, "password": self.password},
        )
        if login.status != 200 or not self.cookies.get("prl_session"):
            raise AcceptanceFailure("A fictional live account could not authenticate")
        csrf = self.request("GET", "/api/v1/auth/csrf")
        token = csrf.body.get("csrf_token")
        if csrf.status != 200 or not isinstance(token, str) or not self.cookies.get("prl_csrf"):
            raise AcceptanceFailure("The live session did not issue signed CSRF state")
        self.csrf_token = token

        if login.body.get("role") != self.expected_role:
            raise AcceptanceFailure("The live session returned the wrong fixture role")


def test_live_session_role_comes_from_fixture_actor_not_email_text():
    session = object.__new__(LiveApiSession)
    session.email = "ha.le@demo.example.invalid"
    session.password = "fictional-password"
    session.expected_role = "STAFF"
    session.cookies = {}
    session.csrf_token = ""
    responses = iter(
        (
            ApiResult(200, {}, {"role": "STAFF"}, 1.0),
            ApiResult(200, {}, {"csrf_token": "fictional-signed-csrf"}, 1.0),
        )
    )

    def fake_request(method: str, path: str, **_kwargs: object) -> ApiResult:
        response = next(responses)
        if path.endswith("/auth/login"):
            session.cookies["prl_session"] = "fictional-session-cookie"
        else:
            session.cookies["prl_csrf"] = "fictional-csrf-cookie"
        return response

    session.request = fake_request  # type: ignore[method-assign]
    session.sign_in()
    assert session.csrf_token == "fictional-signed-csrf"


class LiveBusinessProbe:
    """Exercise committed business routes and verify side effects by scoped MySQL reads."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.student_email = runtime.stack.student_email
        self.student_password = runtime.stack.student_password
        self.staff_email = runtime.stack.staff_email
        self.staff_password = runtime.stack.staff_password
        self.write_ms: list[float] = []

    def _session(self, *, staff: bool = False) -> LiveApiSession:
        session = LiveApiSession(
            self.runtime,
            self.staff_email if staff else self.student_email,
            self.staff_password if staff else self.student_password,
            expected_role="STAFF" if staff else "STUDENT",
        )
        session.sign_in()
        return session

    def _expect(self, result: ApiResult, status: int, operation: str) -> ApiResult:
        if result.status != status:
            raise AcceptanceFailure(f"The live {operation} returned HTTP {result.status}")
        return result

    def _write(self, result: ApiResult, status: int, operation: str) -> ApiResult:
        self._expect(result, status, operation)
        self.write_ms.append(result.elapsed_ms)
        if result.elapsed_ms > 3000:
            raise AcceptanceFailure(f"The live {operation} exceeded the 3-second request budget")
        if not re.fullmatch(r"[a-f0-9]{32}", result.headers.get("x-request-id", "")):
            raise AcceptanceFailure(f"The live {operation} omitted a safe request identifier")
        return result

    def _calendar(self, student: LiveApiSession) -> tuple[dict[str, str], list[dict[str, Any]]]:
        context = self._expect(
            student.request("GET", "/api/v1/booking-context"), 200, "booking context"
        ).body
        purposes = self._expect(
            student.request("GET", "/api/v1/purposes?page=1&page_size=50"),
            200,
            "purpose catalog",
        ).body.get("items", [])
        purpose = next((item for item in purposes if item.get("code") == "CAREER_INTERNSHIP"), None)
        if not isinstance(purpose, dict) or not isinstance(purpose.get("id"), str):
            raise AcceptanceFailure("The fixture career purpose was not available")
        staff_items = self._expect(
            student.request(
                "GET",
                "/api/v1/staff?"
                + urlencode({"purpose_id": purpose["id"], "page": 1, "page_size": 50}),
            ),
            200,
            "staff catalog",
        ).body.get("items", [])
        staff = next((item for item in staff_items if item.get("name") == "Lê Thu Hà"), None)
        if not isinstance(staff, dict) or not isinstance(staff.get("id"), str):
            raise AcceptanceFailure("The fictional assigned staff member was not available")
        try:
            selected_date = datetime.fromisoformat(context["min_booking_date"]).date()
            last_date = datetime.fromisoformat(context["max_booking_date"]).date()
        except (KeyError, TypeError, ValueError):
            raise AcceptanceFailure(
                "The booking calendar returned an invalid fixture horizon"
            ) from None
        while selected_date <= last_date:
            response = self._expect(
                student.request(
                    "GET",
                    "/api/v1/availability?"
                    + urlencode(
                        {
                            "purpose_id": purpose["id"],
                            "staff_id": staff["id"],
                            "date": selected_date.isoformat(),
                        }
                    ),
                ),
                200,
                "available slots",
            )
            slots = response.body.get("slots", [])
            if isinstance(slots, list) and len(slots) >= 2:
                return (
                    {"purpose_id": purpose["id"], "staff_id": staff["id"]},
                    slots,
                )
            selected_date += timedelta(days=1)
        raise AcceptanceFailure("The fixture calendar had fewer than two free slots")

    def _create_booking(
        self,
        student: LiveApiSession,
        identity: dict[str, str],
        slot: dict[str, Any],
    ) -> tuple[ApiResult, str]:
        key = "task8-booking-" + uuid4().hex
        payload: dict[str, object] = {
            "purpose_id": identity["purpose_id"],
            "staff_id": identity["staff_id"],
            "starts_at": slot["starts_at"],
        }
        created = self._write(
            student.request(
                "POST", "/api/v1/appointments", body=payload, csrf=True, idempotency_key=key
            ),
            201,
            "booking create",
        )
        appointment_id = created.body.get("id")
        if not isinstance(appointment_id, str):
            raise AcceptanceFailure("The booking route returned no stable receipt")
        replay = self._write(
            student.request(
                "POST", "/api/v1/appointments", body=payload, csrf=True, idempotency_key=key
            ),
            200,
            "booking replay",
        )
        if created.body != replay.body:
            raise AcceptanceFailure("The live booking replay changed its original receipt")
        return created, appointment_id

    def _create_proposal(
        self,
        staff: LiveApiSession,
        appointment_id: str,
        proposed_start: str,
    ) -> tuple[ApiResult, str]:
        key = "task8-proposal-" + uuid4().hex
        payload = {
            "expected_proposal_version": 0,
            "expected_pending_proposal_id": None,
            "proposed_starts_at": proposed_start,
            "proposed_location": None,
        }
        path = f"/api/v1/staff/me/appointments/{appointment_id}/change-proposals"
        created = self._write(
            staff.request("POST", path, body=payload, csrf=True, idempotency_key=key),
            201,
            "proposal create",
        )
        proposal_id = created.body.get("id")
        if not isinstance(proposal_id, str):
            raise AcceptanceFailure("The proposal route returned no stable receipt")
        replay = self._write(
            staff.request("POST", path, body=payload, csrf=True, idempotency_key=key),
            200,
            "proposal replay",
        )
        if replay.body != created.body:
            raise AcceptanceFailure("The live proposal replay changed its original receipt")
        return created, proposal_id

    def _count(self, query: str) -> int:
        try:
            return int(self.runtime.stack.sql(query).strip())
        except (ValueError, TypeError):
            raise AcceptanceFailure("A scoped MySQL side-effect count was invalid") from None

    def _business_side_effects(
        self, appointment_id: str, proposal_id: str, *, expired: bool
    ) -> dict[str, int | str]:
        appointment, proposal = str(UUID(appointment_id)), str(UUID(proposal_id))
        app_sql_id = f"UNHEX(REPLACE('{appointment}', '-', ''))"
        expected_reason = "CHANGE_PROPOSAL_EXPIRED" if expired else "Task 8 acceptance cleanup"
        row = self.runtime.stack.sql(
            "SELECT CONCAT(status, ':', COALESCE(cancel_reason, '-')) FROM appointments "
            f"WHERE id = {app_sql_id};"
        ).strip()
        if row != f"CANCELLED:{expected_reason}":
            raise AcceptanceFailure("The committed appointment state did not match the live flow")
        proposal_row = self.runtime.stack.sql(
            "SELECT status FROM change_proposals "
            f"WHERE id = UNHEX(REPLACE('{proposal}', '-', ''));"
        ).strip()
        expected_proposal = "EXPIRED" if expired else "ACCEPTED"
        if proposal_row != expected_proposal:
            raise AcceptanceFailure("The committed proposal state did not match the live flow")

        def audit(action: str, entity: str, entity_id: str) -> int:
            safe_id = str(UUID(entity_id))
            return self._count(
                "SELECT COUNT(*) FROM audit_events "
                f"WHERE action = '{action}' AND entity_type = '{entity}' "
                f"AND entity_id = '{safe_id}';"
            )

        audit_counts = (
            {
                "appointment_created": audit("APPOINTMENT_CREATED", "APPOINTMENT", appointment),
                "proposal_created": audit("CHANGE_PROPOSAL_CREATED", "CHANGE_PROPOSAL", proposal),
                "proposal_expired": audit("CHANGE_PROPOSAL_EXPIRED", "CHANGE_PROPOSAL", proposal),
            }
            if expired
            else {
                "appointment_created": audit("APPOINTMENT_CREATED", "APPOINTMENT", appointment),
                "proposal_created": audit("CHANGE_PROPOSAL_CREATED", "CHANGE_PROPOSAL", proposal),
                "proposal_accepted": audit("CHANGE_PROPOSAL_ACCEPTED", "CHANGE_PROPOSAL", proposal),
                "appointment_cancelled": audit("APPOINTMENT_CANCELLED", "APPOINTMENT", appointment),
            }
        )
        if any(value != 1 for value in audit_counts.values()):
            raise AcceptanceFailure("A live business flow wrote missing or duplicate audit events")
        event_types = (
            ("APPOINTMENT_CREATED", "CHANGE_PROPOSAL_CREATED", "CHANGE_PROPOSAL_EXPIRED")
            if expired
            else (
                "APPOINTMENT_CREATED",
                "CHANGE_PROPOSAL_CREATED",
                "CHANGE_PROPOSAL_ACCEPTED",
                "APPOINTMENT_CANCELLED",
            )
        )
        notice_counts = {
            event: self._count(
                "SELECT COUNT(*) FROM notifications "
                f"WHERE appointment_id = {app_sql_id} AND event_type = '{event}';"
            )
            for event in event_types
        }
        if any(value != 2 for value in notice_counts.values()):
            raise AcceptanceFailure("A live business flow wrote missing or duplicate notifications")
        return {
            "appointment_status": "CANCELLED",
            "proposal_status": expected_proposal,
            "audit_event_count": sum(audit_counts.values()),
            "notification_count": sum(notice_counts.values()),
        }

    def _set_expiry_due_window(self, proposal_id: str) -> float:
        proposal = str(UUID(proposal_id))
        sql_id = f"UNHEX(REPLACE('{proposal}', '-', ''))"
        lines = (
            self.runtime.stack.sql(
                "SET time_zone = '+00:00';\n"
                "SET @task8_expiry_now := UTC_TIMESTAMP(6);\n"
                "UPDATE change_proposals SET expires_at = "
                "TIMESTAMPADD(SECOND, 2, @task8_expiry_now) "
                f"WHERE id = {sql_id} AND status = 'PENDING';\n"
                "SELECT ROW_COUNT();\n"
                "SELECT TIMESTAMPDIFF(MICROSECOND, UTC_TIMESTAMP(6), expires_at) / 1000000, "
                "expires_at > created_at "
                f"FROM change_proposals WHERE id = {sql_id};"
            )
            .strip()
            .splitlines()
        )
        if len(lines) != 2 or lines[0] != "1":
            raise AcceptanceFailure("The bounded expiry fixture was not updated exactly once")
        measurement = lines[1].split("\t")
        if len(measurement) != 2 or measurement[1] != "1":
            raise AcceptanceFailure("The expiry fixture did not preserve its data check")
        try:
            due_seconds = float(measurement[0])
        except ValueError:
            raise AcceptanceFailure("The due expiry timestamp could not be measured") from None
        if not 0 < due_seconds <= 2.1:
            raise AcceptanceFailure("The worker expiry fixture was outside its bounded due window")
        return due_seconds

    def _wait_for_later_worker_scan(
        self,
        *,
        excluded_worker_id: str | None = None,
        expected_worker_id: str | None = None,
        timeout_seconds: float = 45,
    ) -> tuple[WorkerProgress, WorkerProgress]:
        deadline = time.monotonic() + timeout_seconds
        baseline: WorkerProgress | None = None
        tracker = WorkerProgressTracker(excluded_worker_id=excluded_worker_id)
        while time.monotonic() < deadline:
            observation = _latest_worker_scan_observation(self.runtime.stack.worker_log_output())
            if observation is None:
                time.sleep(0.5)
                continue
            progress, _scan_timestamp = observation
            if progress.worker_id == excluded_worker_id:
                time.sleep(0.5)
                continue
            if expected_worker_id is not None and progress.worker_id != expected_worker_id:
                raise AcceptanceFailure(
                    "The worker process changed during the expiry follow-up scan"
                )
            baseline = progress
            tracker.observe(progress, running=True)
            break
        if baseline is None:
            raise AcceptanceFailure("The running worker did not report a healthy scan baseline")

        while time.monotonic() < deadline:
            observation = _latest_worker_scan_observation(self.runtime.stack.worker_log_output())
            if observation is None:
                time.sleep(0.5)
                continue
            progress, _scan_timestamp = observation
            if (
                progress.worker_id == excluded_worker_id
                or progress.worker_id != baseline.worker_id
                or (expected_worker_id is not None and progress.worker_id != expected_worker_id)
            ):
                raise AcceptanceFailure(
                    "The worker process changed during the expiry follow-up scan"
                )
            if tracker.observe(progress, running=True):
                return baseline, progress
            time.sleep(0.5)
        raise AcceptanceFailure("The same worker process did not complete a later successful scan")

    def _expire_through_worker(self, appointment_id: str, proposal_id: str) -> dict[str, object]:
        worker_before = self.runtime.inspect_service("worker")
        worker_id = worker_before.get("Id")
        labels = (worker_before.get("Config") or {}).get("Labels") or {}
        if (
            not isinstance(worker_id, str)
            or labels.get("com.docker.compose.project") != self.runtime.stack.project
            or (worker_before.get("State") or {}).get("Status") != "running"
        ):
            raise AcceptanceFailure("The expiry worker did not have verified UUID ownership")
        result = self.runtime.compose("stop", "--timeout", "10", "worker", timeout=30)
        if result.returncode:
            raise AcceptanceFailure("The UUID-owned worker did not stop for expiry setup")
        stopped = self.runtime.inspect_service("worker")
        if (
            stopped.get("Id") != worker_id
            or (stopped.get("State") or {}).get("Status") != "exited"
            or ((stopped.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project")
            != self.runtime.stack.project
        ):
            raise AcceptanceFailure("The stopped expiry worker lost UUID ownership")
        proposal = str(UUID(proposal_id))
        sql_id = f"UNHEX(REPLACE('{proposal}', '-', ''))"
        due_seconds = self._set_expiry_due_window(proposal_id)
        time.sleep(due_seconds + 0.2)
        before_restart = self.runtime.stack.sql(
            "SELECT CONCAT(cp.status, ':', a.status) "
            "FROM change_proposals cp JOIN appointments a ON a.id = cp.appointment_id "
            f"WHERE cp.id = {sql_id};"
        ).strip()
        if before_restart != "PENDING:CHANGE_PENDING":
            raise AcceptanceFailure("A stopped worker changed the due proposal state")
        restarted_ns = time.time_ns()
        started = self.runtime.compose("start", "worker", timeout=30)
        if started.returncode:
            raise AcceptanceFailure("The UUID-owned worker did not restart for due expiry")
        deadline = time.monotonic() + 40
        transition: str | None = None
        while time.monotonic() < deadline:
            state = self.runtime.stack.sql(
                "SELECT CONCAT(cp.status, ':', a.status, ':', COALESCE(a.cancel_reason, '-')) "
                "FROM change_proposals cp JOIN appointments a ON a.id = cp.appointment_id "
                f"WHERE cp.id = {sql_id};"
            ).strip()
            if state == "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED":
                transition = state
                break
            time.sleep(0.5)
        if transition is None:
            raise AcceptanceFailure("The actual worker did not process the persisted expiry")
        effects = self._business_side_effects(appointment_id, proposal_id, expired=True)
        before_followup_scan, after_followup_scan = self._wait_for_later_worker_scan()
        if self._business_side_effects(appointment_id, proposal_id, expired=True) != effects:
            raise AcceptanceFailure("A later worker scan duplicated the expiry side effects")
        worker_after = self.runtime.inspect_service("worker")
        if (
            worker_after.get("Id") != worker_id
            or (worker_after.get("State") or {}).get("Status") != "running"
        ):
            raise AcceptanceFailure("The actual expiry worker changed its owned container")
        return {
            "expiry_fixture_due_seconds": round(due_seconds, 6),
            "stopped_worker_container_preserved": True,
            "due_row_unchanged_while_stopped": True,
            "worker_restarted_unix_ns": restarted_ns,
            "actual_worker_transition": transition,
            "followup_scan_counter_before": before_followup_scan.successful_scans,
            "followup_scan_counter_after": after_followup_scan.successful_scans,
            "followup_scan_worker_process_continuity": before_followup_scan.worker_id
            == after_followup_scan.worker_id,
            "expiry_side_effects_stable_after_followup_scan": True,
            "worker_container_preserved": True,
        }

    def verify_trace_correlation(self, context: dict[str, str]) -> dict[str, object]:
        from tests.containers.test_telemetry_stack import summarize_tempo_signal

        request_id, trace_id = context["request_id"], context["trace_id"]
        gateway = self.runtime.observe_loki_request(request_id, trace_id)
        if gateway.get("failure_category") is not None:
            raise AcceptanceFailure("Gateway Loki evidence did not match the business request")
        now_ns = time.time_ns()
        query = _business_request_loki_query(request_id)
        observation = self.runtime.api_json_observation(
            "http://loki:3100/loki/api/v1/query_range?"
            + urlencode(
                {
                    "query": query,
                    "start": str(now_ns - 120_000_000_000),
                    "end": str(now_ns + 10_000_000_000),
                    "limit": "50",
                }
            )
        )
        matching_count = _matching_business_api_request_log_count(
            observation.get("http_status"),
            observation.get("body"),
            request_id=request_id,
            trace_id=trace_id,
            expected_release=self.runtime.stack.expected_sha,
        )
        if matching_count != 1:
            raise AcceptanceFailure(
                f"The API log returned {matching_count} exact business request "
                "matches; expected one"
            )
        observation = self.runtime.api_json_observation(f"http://tempo:3200/api/traces/{trace_id}")
        trace = observation.get("body")
        summary = summarize_tempo_signal(
            observation.get("http_status"),
            trace,
            trace_id,
            expected_release=self.runtime.stack.expected_sha,
        )
        if summary.get("failure_category") is not None:
            raise AcceptanceFailure("Tempo did not retain the business request trace")
        spans: list[dict[str, Any]] = []
        pending = [trace]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if node.get("name") == "db.query":
                    spans.append(node)
                pending.extend(value for value in node.values() if isinstance(value, (dict, list)))
            elif isinstance(node, list):
                pending.extend(node[:2000])
        mysql_spans = [span for span in spans if _span_attribute(span, "db.system.name") == "mysql"]
        if not mysql_spans:
            raise AcceptanceFailure("The correlated business trace omitted its MySQL span")
        if any(_forbidden_sql_attribute_names(span) for span in mysql_spans):
            raise AcceptanceFailure("A correlated MySQL span exposed SQL text or parameters")
        return {
            "gateway_request_log_matches": True,
            "api_request_log_matches": True,
            "api_log_route": "/api/v1/appointments",
            "trace_id": trace_id,
            "request_id": request_id,
            "tempo_trace_stored": True,
            "mysql_db_query_span_present": True,
            "mysql_span_count": len(mysql_spans),
            "sql_statement_and_parameter_attributes_absent": True,
        }

    def run_case(self, name: str) -> tuple[dict[str, object], dict[str, str]]:
        from tests.containers.test_telemetry_stack import _https_request

        self.write_ms = []
        ready = _https_request(
            self.runtime.app_host, self.runtime.port, self.runtime.app_cert, "GET", "/health/ready"
        )
        gateway_ready = _https_request(
            self.runtime.app_host,
            self.runtime.port,
            self.runtime.app_cert,
            "GET",
            "/health/gateway",
        )
        if ready[0] != 200 or gateway_ready[0] != 200:
            raise AcceptanceFailure(f"Application readiness failed in telemetry state {name}")
        student, staff = self._session(), self._session(staff=True)
        identity, slots = self._calendar(student)
        created, appointment_id = self._create_booking(student, identity, slots[0])
        request_id = created.headers.get("x-request-id", "")
        trace_id = created.headers.get("x-trace-id", "")
        if not re.fullmatch(r"[a-f0-9]{32}", request_id) or not re.fullmatch(
            r"[a-f0-9]{32}", trace_id
        ):
            raise AcceptanceFailure(
                "The live business write omitted safe request/trace identifiers"
            )
        proposal, proposal_id = self._create_proposal(
            staff, appointment_id, str(slots[1]["starts_at"])
        )
        decision_path = (
            f"/api/v1/appointments/{appointment_id}/change-proposals/{proposal_id}/accept"
        )
        accepted = self._write(
            student.request("POST", decision_path, body={}, csrf=True), 200, "proposal accept"
        )
        accepted_replay = self._write(
            student.request("POST", decision_path, body={}, csrf=True),
            200,
            "proposal accept replay",
        )
        if accepted.body != accepted_replay.body or accepted.body.get("status") != "ACCEPTED":
            raise AcceptanceFailure("The live proposal acceptance replay changed its resolution")
        cancelled = self._write(
            student.request(
                "POST",
                f"/api/v1/appointments/{appointment_id}/cancel",
                body={"reason": "Task 8 acceptance cleanup"},
                csrf=True,
            ),
            200,
            "appointment cancellation",
        )
        accepted_effects = self._business_side_effects(appointment_id, proposal_id, expired=False)
        _expiry_booking, expiry_appointment = self._create_booking(student, identity, slots[0])
        _expiry_proposal, expiry_proposal_id = self._create_proposal(
            staff, expiry_appointment, str(slots[1]["starts_at"])
        )
        expiry = self._expire_through_worker(expiry_appointment, expiry_proposal_id)
        expired_effects = self._business_side_effects(
            expiry_appointment, expiry_proposal_id, expired=True
        )
        if any(value > 3000 for value in self.write_ms):
            raise AcceptanceFailure("A live write exceeded the 3-second request budget")
        return (
            {
                "state": name,
                "application_readiness_http": ready[0],
                "gateway_readiness_http": gateway_ready[0],
                "authenticated_student_and_staff_sessions": True,
                "booking_replay_receipt_stable": True,
                "proposal_replay_receipt_stable": True,
                "proposal_accept_replay_stable": True,
                "successful_cancellation_route": cancelled.status,
                "accepted_business_effects": accepted_effects,
                "actual_worker_expiry": expiry,
                "expired_business_effects": expired_effects,
                "write_latency_max_ms": round(max(self.write_ms), 2),
                "write_latency_count": len(self.write_ms),
            },
            {"request_id": request_id, "trace_id": trace_id},
        )

    def prepare_due_expiry_fixture(self) -> dict[str, object]:
        """Create a live authenticated due proposal and leave its worker stopped."""
        student, staff = self._session(), self._session(staff=True)
        identity, slots = self._calendar(student)
        _booking, appointment_id = self._create_booking(student, identity, slots[0])
        _proposal, proposal_id = self._create_proposal(
            staff, appointment_id, str(slots[1]["starts_at"])
        )
        worker = self.runtime.inspect_service("worker")
        worker_id = worker.get("Id")
        labels = (worker.get("Config") or {}).get("Labels") or {}
        if (
            not isinstance(worker_id, str)
            or labels.get("com.docker.compose.project") != self.runtime.stack.project
            or (worker.get("State") or {}).get("Status") != "running"
        ):
            raise AcceptanceFailure("The recovery worker did not have verified UUID ownership")
        stopped = self.runtime.compose("stop", "--timeout", "10", "worker", timeout=30)
        if stopped.returncode:
            raise AcceptanceFailure("The UUID-owned worker did not stop for MySQL recovery")
        after_stop = self.runtime.inspect_service("worker")
        if (
            after_stop.get("Id") != worker_id
            or (after_stop.get("State") or {}).get("Status") != "exited"
            or ((after_stop.get("Config") or {}).get("Labels") or {}).get(
                "com.docker.compose.project"
            )
            != self.runtime.stack.project
        ):
            raise AcceptanceFailure("The stopped recovery worker lost UUID ownership")
        proposal_sql_id = f"UNHEX(REPLACE('{str(UUID(proposal_id))}', '-', ''))"
        remaining = self._set_expiry_due_window(proposal_id)
        time.sleep(remaining + 0.2)
        state = self.runtime.stack.sql(
            "SELECT CONCAT(cp.status, ':', a.status) "
            "FROM change_proposals cp JOIN appointments a ON a.id = cp.appointment_id "
            f"WHERE cp.id = {proposal_sql_id};"
        ).strip()
        if state != "PENDING:CHANGE_PENDING":
            raise AcceptanceFailure("The stopped worker changed the pending recovery fixture")
        return {
            "appointment_id": appointment_id,
            "proposal_id": proposal_id,
            "worker_container_id": worker_id,
            "due_while_worker_stopped": "true",
            "expiry_fixture_due_seconds": round(remaining, 6),
        }


def test_expiry_fixture_anchors_to_current_utc_database_time():
    statements: list[str] = []

    def sql(statement: str) -> str:
        statements.append(statement)
        return "1\n1.75\t1\n"

    probe = object.__new__(LiveBusinessProbe)
    probe.runtime = SimpleNamespace(stack=SimpleNamespace(sql=sql))

    remaining = probe._set_expiry_due_window("00000000-0000-4000-8000-000000000001")

    assert remaining == 1.75
    assert len(statements) == 1
    statement = statements[0]
    assert "SET time_zone = '+00:00'" in statement
    assert "SET @task8_expiry_now := UTC_TIMESTAMP(6)" in statement
    assert "TIMESTAMPADD(SECOND, 2, @task8_expiry_now)" in statement
    assert "SELECT ROW_COUNT()" in statement
    assert "TIMESTAMPDIFF(MICROSECOND, UTC_TIMESTAMP(6), expires_at)" in statement
    assert "expires_at > created_at" in statement
    assert "created_at + INTERVAL 2 SECOND" not in statement


def _worker_scan_record(
    worker_id: str,
    successful_scans: int,
    last_successful_scan: str = "2026-10-26T00:00:00Z",
) -> str:
    return json.dumps(
        {
            "event": "scan",
            "worker_id": worker_id,
            "phase": "running",
            "database_ok": True,
            "successful_scans": successful_scans,
            "last_successful_scan": last_successful_scan,
        }
    )


def test_expiry_followup_scan_requires_same_worker_and_greater_counter():
    worker_id = "a" * 32

    class FakeStack:
        outputs = [_worker_scan_record(worker_id, 7), _worker_scan_record(worker_id, 8)]

        def worker_log_output(self) -> str:
            return self.outputs.pop(0)

    probe = object.__new__(LiveBusinessProbe)
    probe.runtime = SimpleNamespace(stack=FakeStack())

    before, after = probe._wait_for_later_worker_scan()

    assert before.worker_id == after.worker_id == worker_id
    assert before.successful_scans == 7
    assert after.successful_scans == 8


def test_expiry_followup_scan_preserves_baseline_across_unavailable_observation():
    worker_id = "a" * 32

    class FakeStack:
        outputs = [
            _worker_scan_record(worker_id, 7),
            "not-json",
            _worker_scan_record(worker_id, 8),
        ]

        def worker_log_output(self) -> str:
            return self.outputs.pop(0)

    probe = object.__new__(LiveBusinessProbe)
    probe.runtime = SimpleNamespace(stack=FakeStack())

    before, after = probe._wait_for_later_worker_scan()

    assert before.successful_scans == 7
    assert after.successful_scans == 8


def test_expiry_followup_scan_rejects_a_different_worker_process():
    class FakeStack:
        outputs = [
            _worker_scan_record("a" * 32, 7),
            _worker_scan_record("b" * 32, 8),
        ]

        def worker_log_output(self) -> str:
            return self.outputs.pop(0)

    probe = object.__new__(LiveBusinessProbe)
    probe.runtime = SimpleNamespace(stack=FakeStack())

    with pytest.raises(AcceptanceFailure, match="worker process changed"):
        probe._wait_for_later_worker_scan()


def test_new_owner_scan_barrier_uses_generation_and_counter_not_host_clock():
    old_worker_id = "a" * 32
    new_worker_id = "b" * 32

    class FakeStack:
        outputs = [
            _worker_scan_record(old_worker_id, 100, "2026-10-26T00:00:09Z"),
            _worker_scan_record(new_worker_id, 1, "2026-10-26T00:00:09Z"),
            _worker_scan_record(new_worker_id, 2, "2026-10-26T00:00:11Z"),
            _worker_scan_record(new_worker_id, 3, "2026-10-26T00:00:12Z"),
        ]

        def worker_log_output(self) -> str:
            return self.outputs.pop(0)

    probe = object.__new__(LiveBusinessProbe)
    probe.runtime = SimpleNamespace(stack=FakeStack())

    before, after = probe._wait_for_later_worker_scan(excluded_worker_id=old_worker_id)

    assert before.worker_id == after.worker_id == new_worker_id
    assert before.successful_scans == 1
    assert after.successful_scans == 2


def test_new_owner_scan_barrier_rejects_a_later_process_generation():
    new_worker_id = "b" * 32

    class FakeStack:
        outputs = [
            _worker_scan_record(new_worker_id, 2),
            _worker_scan_record("c" * 32, 3),
        ]

        def worker_log_output(self) -> str:
            return self.outputs.pop(0)

    probe = object.__new__(LiveBusinessProbe)
    probe.runtime = SimpleNamespace(stack=FakeStack())

    with pytest.raises(AcceptanceFailure, match="worker process changed"):
        probe._wait_for_later_worker_scan(expected_worker_id=new_worker_id)


@pytest.mark.parametrize(
    "timeout_seconds,ready_after,succeeds", [(90, 61, True), (45, 61, False), (90, 91, False)]
)
def test_database_recovery_scan_barrier_covers_backoff_but_remains_bounded(
    monkeypatch, timeout_seconds, ready_after, succeeds
):
    from tests.containers import test_observability_e2e as probe_module

    now = [0.0]
    old_worker_id, new_worker_id = "a" * 32, "b" * 32

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(
        probe_module, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
    )

    class FakeStack:
        scans = 0

        def worker_log_output(self) -> str:
            if now[0] < ready_after:
                return _worker_scan_record(old_worker_id, 100)
            self.scans += 1
            return _worker_scan_record(new_worker_id, self.scans)

    probe = object.__new__(LiveBusinessProbe)
    probe.runtime = SimpleNamespace(stack=FakeStack())
    if succeeds:
        before, after = probe._wait_for_later_worker_scan(
            excluded_worker_id=old_worker_id, timeout_seconds=timeout_seconds
        )
        assert before.worker_id == after.worker_id == new_worker_id
        assert (before.successful_scans, after.successful_scans) == (1, 2)
        assert now[0] == ready_after
    else:
        with pytest.raises(AcceptanceFailure, match="healthy scan baseline"):
            probe._wait_for_later_worker_scan(
                excluded_worker_id=old_worker_id, timeout_seconds=timeout_seconds
            )
        assert now[0] == timeout_seconds


def _latest_worker_scan_observation(output: str) -> tuple[WorkerProgress, float] | None:
    progress = latest_worker_progress(output, require_heartbeat=False)
    if progress is None:
        return None
    for line in reversed(output.splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            not isinstance(record, dict)
            or record.get("worker_id") != progress.worker_id
            or record.get("successful_scans") != progress.successful_scans
        ):
            continue
        value = record.get("last_successful_scan")
        if not isinstance(value, str):
            return None
        try:
            scanned_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if scanned_at.tzinfo is None or scanned_at.utcoffset() is None:
            return None
        try:
            return progress, scanned_at.timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _business_request_loki_query(request_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise AcceptanceFailure("The business request identifier is invalid for Loki correlation")
    return (
        '{service_name="api",deployment_environment_name="demo"} '
        f'|= {json.dumps("http.server.request")} | request_id={json.dumps(request_id)}'
    )


def _native_loki_metadata(entry: object) -> dict[str, object]:
    if not isinstance(entry, (list, tuple)) or len(entry) < 3:
        return {}
    raw_metadata = entry[2]
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, list):
        return {
            item["key"]: item.get("value")
            for item in raw_metadata[:100]
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
    return {}


def _string_keyed_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


_BUSINESS_API_LOG_FIELDS = frozenset({"request_id", "trace_id", "route", "method", "status"})
_RELEASE_ATTRIBUTE_NAMES = ("service_version", "service.version", "release")


def _metadata_values_agree(name: str, left: object, right: object) -> bool:
    if name == "status" and type(left) in (int, str) and type(right) in (int, str):
        return str(left) == str(right)
    return type(left) is type(right) and left == right


def _merge_business_api_log_metadata(
    stream: dict[str, object], entry_metadata: dict[str, object]
) -> dict[str, object] | None:
    merged: dict[str, object] = {}
    sources = (stream, entry_metadata)
    for name in _BUSINESS_API_LOG_FIELDS:
        values = [source[name] for source in sources if name in source]
        if values and any(
            not _metadata_values_agree(name, values[0], value) for value in values[1:]
        ):
            return None
        if values:
            merged[name] = values[0]

    releases = [
        source[name] for source in sources for name in _RELEASE_ATTRIBUTE_NAMES if name in source
    ]
    if releases and any(value != releases[0] for value in releases[1:]):
        return None
    if releases:
        merged["service_version"] = releases[0]
    return merged


def _matching_business_api_request_log_count(
    http_status: object,
    body: object,
    *,
    request_id: str,
    trace_id: str,
    expected_release: str,
) -> int:
    from tests.containers.test_telemetry_stack import summarize_loki_native_application_log

    summary = summarize_loki_native_application_log(
        http_status if type(http_status) is int else None,
        body,
        expected_service="api",
        expected_event="http.server.request",
        expected_release=expected_release,
    )
    if summary.get("failure_category") is not None:
        return 0
    response = _string_keyed_mapping(body)
    data = _string_keyed_mapping(response.get("data"))
    raw_results = data.get("result")
    if not isinstance(raw_results, list):
        return 0
    matches = 0
    for value in raw_results[:100]:
        stream = _string_keyed_mapping(value)
        labels = _string_keyed_mapping(stream.get("stream"))
        if (
            labels.get("service_name") != "api"
            or labels.get("deployment_environment_name") != "demo"
        ):
            continue
        raw_values = stream.get("values")
        if not isinstance(raw_values, list):
            continue
        values = raw_values
        for entry in values[:100]:
            if (
                not isinstance(entry, (list, tuple))
                or len(entry) < 2
                or entry[1] != "http.server.request"
            ):
                continue
            metadata = _native_loki_metadata(entry)
            fields = _merge_business_api_log_metadata(labels, metadata)
            if (
                fields is not None
                and fields.get("service_version") == expected_release
                and fields.get("request_id") == request_id
                and fields.get("trace_id") == trace_id
                and fields.get("route") == "/api/v1/appointments"
                and fields.get("method") == "POST"
                and fields.get("status") in (201, "201")
            ):
                matches += 1
    return matches


def _span_attribute(span: dict[str, Any], key: str) -> object | None:
    attributes = span.get("attributes")
    if isinstance(attributes, dict):
        return attributes.get(key)
    if isinstance(attributes, list):
        for item in attributes:
            if not isinstance(item, dict) or item.get("key") != key:
                continue
            value = item.get("value")
            if isinstance(value, dict):
                return value.get("stringValue")
            return value
    return None


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            keys.update(str(key) for key in item)
            attribute_name = item.get("key")
            if isinstance(attribute_name, str) and "value" in item:
                keys.add(attribute_name)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item[:2000])
    return keys


def _business_api_log_fixture(
    metadata_as_list: bool = False,
) -> tuple[dict[str, Any], str, str, str]:
    request_id = "a" * 32
    trace_id = "b" * 32
    release = "c" * 40
    values: dict[str, str] = {
        "request_id": request_id,
        "trace_id": trace_id,
        "route": "/api/v1/appointments",
        "method": "POST",
        "status": "201",
        "service_version": release,
    }
    metadata: object = (
        [{"key": key, "value": value} for key, value in values.items()]
        if metadata_as_list
        else values
    )
    body: dict[str, Any] = {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {
                        "service_name": "api",
                        "deployment_environment_name": "demo",
                    },
                    "values": [["1792906956309689000", "http.server.request", metadata]],
                }
            ]
        },
    }
    return body, request_id, trace_id, release


@pytest.mark.parametrize("metadata_as_list", [False, True])
def test_business_api_log_query_matches_plain_event_and_structured_metadata(metadata_as_list):
    body, request_id, trace_id, release = _business_api_log_fixture(metadata_as_list)

    query = _business_request_loki_query(request_id)

    assert query == (
        '{service_name="api",deployment_environment_name="demo"} '
        '|= "http.server.request" '
        f'| request_id="{request_id}"'
    )
    assert (
        _matching_business_api_request_log_count(
            200,
            body,
            request_id=request_id,
            trace_id=trace_id,
            expected_release=release,
        )
        == 1
    )


def test_business_api_log_counts_only_exact_metadata_match():
    body, request_id, trace_id, release = _business_api_log_fixture(metadata_as_list=True)
    streams = body["data"]["result"]
    values = streams[0]["values"]
    valid = values[0]
    decoy_metadata = [
        {"key": item["key"], "value": "d" * 32 if item["key"] == "trace_id" else item["value"]}
        for item in valid[2]
    ]
    values.append([valid[0], valid[1], decoy_metadata])

    assert (
        _matching_business_api_request_log_count(
            200,
            body,
            request_id=request_id,
            trace_id=trace_id,
            expected_release=release,
        )
        == 1
    )


def test_business_api_log_matches_query_stream_metadata_shape():
    body, request_id, trace_id, release = _business_api_log_fixture()
    result = body["data"]["result"][0]
    entry = result["values"][0]
    result["stream"].update(entry[2])
    result["values"][0] = entry[:2]

    assert (
        _matching_business_api_request_log_count(
            200,
            body,
            request_id=request_id,
            trace_id=trace_id,
            expected_release=release,
        )
        == 1
    )


def test_business_api_log_merges_stream_and_entry_metadata():
    body, request_id, trace_id, release = _business_api_log_fixture()
    result = body["data"]["result"][0]
    entry = result["values"][0]
    metadata = entry[2]
    result["stream"].update({key: metadata[key] for key in ("request_id", "route")})
    result["values"][0] = [
        entry[0],
        entry[1],
        {key: value for key, value in metadata.items() if key not in {"request_id", "route"}},
    ]

    assert (
        _matching_business_api_request_log_count(
            200,
            body,
            request_id=request_id,
            trace_id=trace_id,
            expected_release=release,
        )
        == 1
    )


def test_business_api_log_rejects_conflicting_identity_metadata():
    body, request_id, trace_id, release = _business_api_log_fixture()
    result = body["data"]["result"][0]
    entry = result["values"][0]
    result["stream"].update(entry[2])
    result["stream"]["trace_id"] = "d" * 32

    assert (
        _matching_business_api_request_log_count(
            200,
            body,
            request_id=request_id,
            trace_id=trace_id,
            expected_release=release,
        )
        == 0
    )


@pytest.mark.parametrize(
    "forbidden_name", ["db.statement", "db.statement.parameters", "db.query.text"]
)
@pytest.mark.parametrize("metadata_as_list", [False, True])
def test_mysql_span_rejects_forbidden_query_attribute_names_even_when_empty(
    forbidden_name, metadata_as_list
):
    if metadata_as_list:
        attributes: object = [
            {"key": "db.system.name", "value": {"stringValue": "mysql"}},
            {"key": forbidden_name, "value": {"stringValue": ""}},
        ]
    else:
        attributes = {"db.system.name": "mysql", forbidden_name: ""}
    span = {"name": "db.query", "attributes": attributes}

    assert _span_attribute(span, "db.system.name") == "mysql"
    assert _forbidden_sql_attribute_names(span) == {forbidden_name}


_FORBIDDEN_SQL_ATTRIBUTE_NAMES = frozenset(
    {"db.statement", "db.statement.parameters", "db.query.text"}
)


def _forbidden_sql_attribute_names(span: dict[str, Any]) -> set[str]:
    return set(_FORBIDDEN_SQL_ATTRIBUTE_NAMES & _nested_keys(span))


def run_backend_outage(probe: LiveBusinessProbe, service: str) -> dict[str, object]:
    runtime = probe.runtime
    before = runtime.inspect_service(service)
    container_id = before.get("Id")
    labels = (before.get("Config") or {}).get("Labels") or {}
    if (
        not isinstance(container_id, str)
        or labels.get("com.docker.compose.project") != runtime.stack.project
        or (before.get("State") or {}).get("Status") != "running"
    ):
        raise AcceptanceFailure(f"The {service} outage target did not have UUID ownership")
    started_ns = time.time_ns()
    stopped = runtime.compose("stop", "--timeout", "10", service, timeout=30)
    if stopped.returncode:
        raise AcceptanceFailure(f"The UUID-owned {service} service did not stop")
    stopped_service = runtime.inspect_service(service)
    if (
        stopped_service.get("Id") != container_id
        or (stopped_service.get("State") or {}).get("Status") != "exited"
        or ((stopped_service.get("Config") or {}).get("Labels") or {}).get(
            "com.docker.compose.project"
        )
        != runtime.stack.project
    ):
        raise AcceptanceFailure(f"The stopped {service} service lost UUID ownership")
    case, _trace = probe.run_case(f"{service}_stopped")
    recovered_ns = time.time_ns()
    restored = runtime.compose("start", service, timeout=30)
    if restored.returncode:
        raise AcceptanceFailure(f"The UUID-owned {service} service did not restart")
    targets = runtime.wait_targets()
    after = runtime.inspect_service(service)
    if (
        after.get("Id") != container_id
        or (after.get("State") or {}).get("Status") != "running"
        or ((after.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project")
        != runtime.stack.project
    ):
        raise AcceptanceFailure(f"The recovered {service} service changed UUID identity")
    return {
        "service": service,
        "stopped_container_preserved": True,
        "business_case": case,
        "outage_started_unix_ns": started_ns,
        "recovery_started_unix_ns": recovered_ns,
        "outage_elapsed_seconds": round((recovered_ns - started_ns) / 1_000_000_000, 2),
        "recovered_targets": targets,
    }


def api_telemetry_loss_totals(runtime: Any) -> dict[str, object]:
    script = """import json,re,urllib.request
raw=urllib.request.urlopen('http://127.0.0.1:8000/metrics',timeout=5).read(10000000).decode()
totals={'failed':0.0,'dropped':0.0}
seen=set()
for line in raw.splitlines():
 match=re.match(r'^prl_telemetry_(failed|dropped)_total(?:\\{[^}]*\\})?\\s+([0-9.eE+-]+)$',line)
 if match:
  kind,value=match.groups(); totals[kind]+=float(value); seen.add(kind)
print(json.dumps({'totals':totals,'families_seen':sorted(seen)},separators=(',',':')))
"""
    result = runtime.stack.compose("exec", "-T", "api", "python", "-c", script, timeout=20)
    if result.returncode:
        raise AcceptanceFailure("The API cumulative telemetry loss metrics were unavailable")
    try:
        value = json.loads(result.stdout)
        totals = value["totals"]
        seen = sorted(set(value["families_seen"]) & {"failed", "dropped"})
        normalized = {key: float(totals[key]) for key in ("failed", "dropped")}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise AcceptanceFailure("The API cumulative telemetry loss metrics were invalid") from None
    if any(not (number >= 0 and number < float("inf")) for number in normalized.values()):
        raise AcceptanceFailure("The API cumulative telemetry loss totals were nonfinite")
    return {"totals": normalized, "families_seen": seen}


def append_stale_gateway_event(runtime: Any) -> dict[str, object]:
    now_ns = time.time_ns()
    source_ns = now_ns - 360_000_000_000
    source_timestamp = datetime.fromtimestamp(source_ns / 1_000_000_000, UTC).isoformat()
    request_id = "task8-old-source-" + uuid4().hex
    record = {
        "timestamp": source_timestamp,
        "service": "gateway",
        "environment": "demo",
        "release": runtime.stack.expected_sha,
        "event": "gateway.request",
        "request_id": request_id,
        "trace_id": uuid4().hex,
        "route": "/api/v1/appointments",
        "method": "POST",
        "status": 201,
        "duration_seconds": 0.1,
        "upstream_identity": "api",
        "error_class": "unknown",
        "upstream_outcome": "success",
        "classification": "success",
    }
    payload = json.dumps(record, separators=(",", ":"))
    result = runtime.stack.compose(
        "exec",
        "-T",
        "gateway",
        "sh",
        "-c",
        "cat >> /var/log/prl/access.jsonl",
        input_text=payload + "\n",
        timeout=20,
    )
    if result.returncode:
        raise AcceptanceFailure("A synthetic old-source gateway record could not be staged")
    return {
        "request_id": request_id,
        "source_timestamp_unix_ns": source_ns,
        "staged_while_collector_stopped": True,
    }


def verify_stale_gateway_event(runtime: Any, event: dict[str, object]) -> dict[str, object]:
    request_id_value = event.get("request_id")
    source_ns_value = event.get("source_timestamp_unix_ns")
    if not isinstance(request_id_value, str) or type(source_ns_value) is not int:
        raise AcceptanceFailure("The staged old-source gateway event is malformed")
    request_id = request_id_value
    source_ns = source_ns_value
    selector = (
        '{service_name="gateway",deployment_environment_name="demo"} '
        '| json event="event", classification="classification", request_id="request_id" '
        f'| request_id="{request_id}" | event="gateway.request" | classification="success"'
    )
    historical = runtime.api_json(
        "http://loki:3100/loki/api/v1/query_range?"
        + urlencode(
            {
                "query": selector,
                "start": str(source_ns - 30_000_000_000),
                "end": str(source_ns + 30_000_000_000),
                "limit": "10",
            }
        )
    )
    values = [
        item
        for stream in historical.get("data", {}).get("result", [])
        for item in stream.get("values", [])
        if isinstance(item, list) and len(item) >= 2
    ]
    if len(values) != 1:
        raise AcceptanceFailure("The old-source event was not queryable at its historical time")
    try:
        entry_ns = int(values[0][0])
        body = json.loads(values[0][1])
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        raise AcceptanceFailure(
            "The historical source-time event had an invalid Loki record"
        ) from None
    if (
        not isinstance(body, dict)
        or body.get("request_id") != request_id
        or abs(entry_ns - source_ns) > 1_000_000_000
    ):
        raise AcceptanceFailure("Loki entry time did not preserve the old producer event time")
    five_minute = runtime.api_json(
        "http://loki:3100/loki/api/v1/query?"
        + urlencode({"query": f"sum(count_over_time({selector} [5m]))"})
    )
    if five_minute.get("status") != "success":
        raise AcceptanceFailure("The current five-minute SLI query was unavailable")
    current_results = five_minute.get("data", {}).get("result", [])
    if current_results:
        try:
            current_count = sum(float(item["value"][1]) for item in current_results)
        except (KeyError, TypeError, ValueError, IndexError):
            raise AcceptanceFailure("The current-window source-time count was invalid") from None
    else:
        current_count = 0.0
    historical_count_query = runtime.api_json(
        "http://loki:3100/loki/api/v1/query?"
        + urlencode(
            {
                "query": f"sum(count_over_time({selector} [1m]))",
                "time": str((source_ns + 1_000_000_000) / 1_000_000_000),
            }
        )
    )
    if historical_count_query.get("status") != "success":
        raise AcceptanceFailure("The true historical SLI-window query was unavailable")
    rows = historical_count_query.get("data", {}).get("result", [])
    try:
        historical_count = sum(float(item["value"][1]) for item in rows)
    except (KeyError, TypeError, ValueError, IndexError):
        raise AcceptanceFailure("The historical SLI-window count was invalid") from None
    if current_count != 0 or historical_count != 1:
        raise AcceptanceFailure(
            "Producer-time filtering did not separate current and historical SLI windows"
        )
    return {
        "historical_entry_unix_ns": entry_ns,
        "producer_timestamp_unix_ns": source_ns,
        "entry_matches_producer_time_within_seconds": round(
            abs(entry_ns - source_ns) / 1_000_000_000, 3
        ),
        "current_five_minute_sli_contribution": current_count,
        "historical_one_minute_sli_contribution": historical_count,
        "queryable_in_historical_window": True,
    }


def capture_dashboard_screenshots(
    runtime: Any, *, evidence_root: Path | None = None
) -> dict[str, object]:
    """Capture the five live Grafana dashboards through fixture-only browser TLS."""
    from tests.containers.test_telemetry_stack import OPS_PROBE_DASHBOARD_UIDS

    node = shutil.which("node")
    if node is None:
        raise AcceptanceFailure("The prepared local Node runtime was unavailable for screenshots")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_root = evidence_root or ROOT / "docs/evidence/observability-foundation/screenshots"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    scratch = ROOT / ".superpowers/sdd/2026-10-23-observability-foundation"
    scratch.mkdir(parents=True, exist_ok=True)
    script = r"""
const { chromium } = require('@playwright/test');
const origin = process.env.TASK8_GRAFANA_ORIGIN;
const opsOrigin = new URL(origin).origin;
const user = process.env.TASK8_GRAFANA_USER;
const password = process.env.TASK8_GRAFANA_PASSWORD;
const authorization = 'Basic ' + Buffer.from(user + ':' + password, 'utf8').toString('base64');
const uids = process.env.TASK8_DASHBOARD_UIDS.split(',');
const dir = process.env.TASK8_SCREENSHOT_DIR;
(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--host-resolver-rules=MAP ops.example.invalid 127.0.0.1']
  });
  try {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      viewport: { width: 1600, height: 1000 }
    });
    await context.route('**/*', async route => {
      const request = route.request();
      if (new URL(request.url()).origin !== opsOrigin) {
        await route.continue();
        return;
      }
      await route.continue({
        headers: { ...request.headers(), authorization }
      });
    });
    const results = [];
    for (const uid of uids) {
      const page = await context.newPage();
      const metadata = await page.goto(`${origin}/api/dashboards/uid/${uid}`, {
        waitUntil: 'domcontentloaded', timeout: 45000
      });
      if (!metadata || metadata.status() !== 200) {
        throw new Error(`dashboard_api_status:${uid}:${metadata?.status() ?? 'none'}`);
      }
      const model = await metadata.json();
      if (model.dashboard?.uid !== uid) throw new Error(`dashboard_uid_mismatch:${uid}`);
      const expectedTitle = String(model.dashboard.title || '');
      if (!expectedTitle) throw new Error(`dashboard_title_missing:${uid}`);
      const response = await page.goto(`${origin}/d/${uid}/${uid}?orgId=1`, {
        waitUntil: 'domcontentloaded', timeout: 45000
      });
      if (!response || response.status() !== 200) {
        throw new Error(`dashboard_page_status:${uid}:${response?.status() ?? 'none'}`);
      }
      const dashboardPath = new URL(page.url()).pathname;
      if (!dashboardPath.startsWith(`/d/${uid}/`)) {
        throw new Error(`dashboard_page_identity_mismatch:${uid}`);
      }
      const visibleTitle = page.getByText(expectedTitle, { exact: true }).first();
      await visibleTitle.waitFor({ state: 'visible', timeout: 30000 });
      if ((await visibleTitle.innerText()) !== expectedTitle) {
        throw new Error(`dashboard_title_mismatch:${uid}`);
      }
      await page.waitForTimeout(4000);
      const filename = `${dir}/${uid}.png`;
      await page.screenshot({ path: filename, clip: { x: 0, y: 0, width: 1600, height: 760 } });
      results.push({ uid, title: expectedTitle.slice(0, 80), dashboardPath, filename });
      await page.close();
    }
    process.stdout.write(JSON.stringify({ results }));
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => {
  process.stderr.write(
    `Task 8 dashboard screenshot failed: ${String(error?.message || 'browser_error')}\n`
  );
  process.exitCode = 1;
});
"""
    env = dict(os.environ)
    env.update(
        TASK8_GRAFANA_ORIGIN=runtime.ops_origin,
        TASK8_GRAFANA_USER=runtime.admin_user,
        TASK8_GRAFANA_PASSWORD=runtime.admin_password,
        TASK8_DASHBOARD_UIDS=",".join(sorted(OPS_PROBE_DASHBOARD_UIDS)),
        TASK8_SCREENSHOT_DIR=str(output_dir.resolve()),
    )
    completed = subprocess.run(
        [str(node), "-e", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
        check=False,
    )
    stem = f"task8-dashboard-screenshots-{run_id}"
    (scratch / f"{stem}.stdout.txt").write_bytes(completed.stdout.encode("utf-8"))
    (scratch / f"{stem}.stderr.txt").write_bytes(completed.stderr.encode("utf-8"))
    (scratch / f"{stem}.exit-code.txt").write_text(f"{completed.returncode}\n", encoding="utf-8")
    if completed.returncode:
        raise AcceptanceFailure("The actual Grafana dashboard screenshots could not be captured")
    try:
        result = json.loads(completed.stdout)
        rows = result["results"]
        if len(rows) != 5 or {row["uid"] for row in rows} != OPS_PROBE_DASHBOARD_UIDS:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise AcceptanceFailure(
            "The Grafana screenshot run did not cover exactly five dashboards"
        ) from None
    screenshots = []
    for row in rows:
        path = Path(row["filename"])
        if not path.is_file():
            raise AcceptanceFailure("A requested Grafana dashboard screenshot was missing")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            relative_path = path.relative_to(ROOT).as_posix()
        except ValueError:
            relative_path = path.relative_to(output_root).as_posix()
        screenshots.append(
            {
                "uid": row["uid"],
                "title": row["title"],
                "dashboard_path": row["dashboardPath"],
                "path": relative_path,
                "sha256": digest,
            }
        )
    return {
        "browser": "Chromium via Playwright",
        "fixture_host_mapping": "ops.example.invalid to 127.0.0.1, process-local browser resolver",
        "fixture_tls_allowance": (
            "ignoreHTTPS for screenshot browser only; dedicated TLS probes verify "
            "fixture CA and hostname"
        ),
        "viewport": "1600x1000",
        "capture_region": (
            "top dashboard overview at 1600x760; lower log panels omitted to exclude "
            "generated correlation and run identifiers"
        ),
        "screenshots": screenshots,
    }


def test_dashboard_screenshots_use_browser_resolver_for_metadata_and_pages(tmp_path: Path):
    import base64
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import urlsplit

    from tests.containers.test_telemetry_stack import OPS_PROBE_DASHBOARD_UIDS

    requested_paths: list[str] = []
    authorization_matches: list[bool] = []
    foreign_authorization_present: list[bool] = []
    expected_authorization = "Basic " + base64.b64encode(
        b"screenshot-test-user:screenshot-test-password"
    ).decode("ascii")

    class ForeignHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            foreign_authorization_present.append("Authorization" in self.headers)
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    foreign_server = ThreadingHTTPServer(("127.0.0.1", 0), ForeignHandler)
    foreign_thread = Thread(target=foreign_server.serve_forever, daemon=True)
    foreign_thread.start()

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            requested_paths.append(path)
            if path.startswith("/api/dashboards/uid/"):
                authorized = self.headers.get("Authorization") == expected_authorization
                authorization_matches.append(authorized)
                if not authorized:
                    # Model a bare Grafana API 401 without a Basic challenge.
                    self.send_response(401)
                    self.end_headers()
                    return
                uid = path.rsplit("/", 1)[-1]
                if uid not in OPS_PROBE_DASHBOARD_UIDS:
                    self.send_error(404)
                    return
                body = json.dumps({"dashboard": {"uid": uid, "title": f"Dashboard {uid}"}})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                return
            if path.startswith("/d/"):
                authorized = self.headers.get("Authorization") == expected_authorization
                authorization_matches.append(authorized)
                if not authorized:
                    self.send_response(401)
                    self.end_headers()
                    return
                uid = path.split("/", 3)[2]
                if uid not in OPS_PROBE_DASHBOARD_UIDS:
                    self.send_error(404)
                    return
                title = f"Dashboard {uid}"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    (
                        f"<!doctype html><title>{title}</title><main>{title}</main>"
                        f'<img src="http://127.0.0.1:{foreign_server.server_port}/resource">'
                    ).encode()
                )
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = SimpleNamespace(
        ops_origin=f"http://ops.example.invalid:{server.server_port}",
        admin_user="screenshot-test-user",
        admin_password="screenshot-test-password",
    )
    try:
        result = capture_dashboard_screenshots(
            runtime, evidence_root=tmp_path / "private-screenshots"
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        foreign_server.shutdown()
        foreign_thread.join(timeout=5)
        foreign_server.server_close()

    assert result["fixture_host_mapping"] == (
        "ops.example.invalid to 127.0.0.1, process-local browser resolver"
    )
    assert {path.rsplit("/", 1)[-1] for path in requested_paths if path.startswith("/api/")} == (
        OPS_PROBE_DASHBOARD_UIDS
    )
    assert sum(path.startswith("/d/") for path in requested_paths) == 5
    assert len(authorization_matches) == 10 and all(authorization_matches)
    assert foreign_authorization_present and not any(foreign_authorization_present)
    screenshots = result["screenshots"]
    assert isinstance(screenshots, list)
    screenshot_rows = [item for item in screenshots if isinstance(item, dict)]
    assert {item["uid"] for item in screenshot_rows} == OPS_PROBE_DASHBOARD_UIDS
    assert all(
        (tmp_path / "private-screenshots" / str(item["path"])).is_file() for item in screenshot_rows
    )
    for item in screenshot_rows:
        image = (tmp_path / "private-screenshots" / str(item["path"])).read_bytes()
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        assert int.from_bytes(image[16:20], "big") == 1600
        assert int.from_bytes(image[20:24], "big") == 760
    assert all(
        str(item["dashboard_path"]).startswith(f"/d/{item['uid']}/") for item in screenshot_rows
    )


def test_fullstack_ops_peer_fixture_uses_only_loopback_and_exact_edge_gateway() -> None:
    import ipaddress

    from tests.containers.test_compose_acceptance import GATEWAY_SUBNET
    from tests.containers.test_telemetry_stack import (
        OPS_EDGE_TEST_GATEWAY,
        OPS_EDGE_TEST_SUBNET,
        fullstack_ops_edge_network_overlay,
        fullstack_ops_peer_cidrs,
    )

    networks = [
        ipaddress.ip_network(item, strict=True) for item in fullstack_ops_peer_cidrs().split(",")
    ]
    assert [str(item) for item in networks] == [
        "127.0.0.1/32",
        f"{OPS_EDGE_TEST_GATEWAY}/32",
    ]
    assert all(item.prefixlen == item.max_prefixlen for item in networks)
    assert not GATEWAY_SUBNET.overlaps(OPS_EDGE_TEST_SUBNET)
    overlay = yaml.safe_load(fullstack_ops_edge_network_overlay())
    assert overlay == {
        "services": {},
        "networks": {
            "edge": {
                "ipam": {
                    "config": [
                        {
                            "subnet": str(OPS_EDGE_TEST_SUBNET),
                            "gateway": str(OPS_EDGE_TEST_GATEWAY),
                        }
                    ]
                }
            }
        },
    }


def test_worker_restart_sequence_stops_the_worker_before_database() -> None:
    from tests.containers.test_telemetry_stack import (
        restart_worker_while_database_is_unavailable,
    )

    events: list[tuple[str, object]] = []
    status = {"worker": "running", "mysql": "running"}
    container_ids = {"worker": "worker-id", "mysql": "mysql-id"}

    def compose(*args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        events.append(("compose", args))
        if args[0] == "stop":
            status[args[-1]] = "exited"
        elif args[0] == "up":
            status["worker"] = "running"
        return subprocess.CompletedProcess(args, 0, "", "")

    def inspect(service: str) -> dict[str, object]:
        events.append(("inspect", service))
        return {
            "Id": container_ids[service],
            "State": {"Status": status[service]},
        }

    baseline = {"last_successful_scan": {"value": 1_800.0}}

    def capture_baseline() -> dict[str, dict[str, object]]:
        events.append(("capture_baseline", None))
        return baseline

    result = restart_worker_while_database_is_unavailable(
        compose,
        inspect,
        worker_container_id="worker-id",
        mysql_container_id="mysql-id",
        capture_baseline=capture_baseline,
    )

    assert result is baseline
    assert [event for event in events if event[0] == "compose"] == [
        ("compose", ("stop", "--timeout", "10", "worker")),
        ("compose", ("stop", "--timeout", "10", "mysql")),
        ("compose", ("up", "-d", "--no-deps", "--no-recreate", "worker")),
    ]
    assert events.index(("inspect", "worker")) < events.index(
        ("compose", ("stop", "--timeout", "10", "mysql"))
    )
    assert events.index(("capture_baseline", None)) < events.index(
        ("compose", ("up", "-d", "--no-deps", "--no-recreate", "worker"))
    )
    assert events[-2:] == [
        ("inspect", "worker"),
        ("inspect", "mysql"),
    ]


def test_worker_restart_retention_uses_same_series_and_db_values() -> None:
    from tests.containers.test_telemetry_stack import FullStackRuntime

    labels = {
        "service_name": "worker",
        "deployment_environment_name": "demo",
        "worker_slot": "1",
        "instance": "worker:9464",
    }
    stopped_state = {
        "started": {
            "name": "prl_worker_started_timestamp_seconds",
            "labels": labels,
            "value": 1_793_010_902.478,
        },
        "last_successful_scan": {
            "name": "prl_worker_last_successful_scan_timestamp_seconds",
            "labels": labels,
            "value": 1_793_010_975.572,
        },
        "last_progress": {
            "name": "prl_worker_last_progress_timestamp_seconds",
            "labels": labels,
            "value": 1_793_010_975.572,
        },
    }
    restarted_state = {
        **stopped_state,
        "started": {**stopped_state["started"], "value": 1_793_010_965.706},
    }

    summary = FullStackRuntime.worker_restart_observation_summary(stopped_state, restarted_state)

    assert FullStackRuntime.worker_restart_retained_prior_points(summary) is True

    changed_series = {
        **restarted_state,
        "last_successful_scan": {
            **restarted_state["last_successful_scan"],
            "labels": {**labels, "instance": "replacement:9464"},
        },
    }
    changed_summary = FullStackRuntime.worker_restart_observation_summary(
        stopped_state, changed_series
    )
    assert FullStackRuntime.worker_restart_retained_prior_points(changed_summary) is False

    changed_value = {
        **restarted_state,
        "last_progress": {**restarted_state["last_progress"], "value": 1_793_010_976.0},
    }
    changed_value_summary = FullStackRuntime.worker_restart_observation_summary(
        stopped_state, changed_value
    )
    assert FullStackRuntime.worker_restart_retained_prior_points(changed_value_summary) is False


def test_worker_restart_rejects_mysql_running_during_no_dependency_start() -> None:
    from tests.containers.test_telemetry_stack import restart_worker_while_database_is_unavailable

    status = {"worker": "running", "mysql": "running"}
    container_ids = {"worker": "worker-id", "mysql": "mysql-id"}

    def compose(*args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        if args[0] == "stop":
            status[args[-1]] = "exited"
        elif args[0] == "up":
            status["worker"] = "running"
            status["mysql"] = "running"
        return subprocess.CompletedProcess(args, 0, "", "")

    def inspect(service: str) -> dict[str, object]:
        return {"Id": container_ids[service], "State": {"Status": status[service]}}

    with pytest.raises(AcceptanceFailure, match="mysql container did not remain exited"):
        restart_worker_while_database_is_unavailable(
            compose,
            inspect,
            worker_container_id="worker-id",
            mysql_container_id="mysql-id",
            capture_baseline=lambda: {"last_progress": {"value": 1_800.0}},
        )


def test_gateway_ops_allowlist_slash_guard_is_per_cidr() -> None:
    entrypoint = (ROOT / "tools/deploy/nginx/gateway-entrypoint.sh").read_text(encoding="utf-8")
    aggregate_guard = entrypoint.split('case "$ops_cidrs" in', 1)[1].split("esac", 1)[0]
    individual_guard = entrypoint.split('case "$cidr" in', 1)[1].split("esac", 1)[0]

    assert "*/*/*" not in aggregate_guard
    assert "*/*/*" in individual_guard
