"""Task 8 live-authenticated business and recovery acceptance helpers."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
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

    def _wait_for_later_worker_scan(self) -> tuple[WorkerProgress, WorkerProgress]:
        deadline = time.monotonic() + 45
        baseline: WorkerProgress | None = None
        tracker = WorkerProgressTracker()
        while time.monotonic() < deadline:
            progress = latest_worker_progress(
                self.runtime.stack.worker_log_output(), require_heartbeat=False
            )
            if progress is not None:
                baseline = progress
                tracker.observe(progress, running=True)
                break
            time.sleep(0.5)
        if baseline is None:
            raise AcceptanceFailure("The running worker did not report a healthy scan baseline")

        while time.monotonic() < deadline:
            progress = latest_worker_progress(
                self.runtime.stack.worker_log_output(), require_heartbeat=False
            )
            if progress is None:
                time.sleep(0.5)
                continue
            if progress.worker_id != baseline.worker_id:
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
        query = (
            '{service_name="api",deployment_environment_name="demo"} '
            f'|= {json.dumps("http.server.request")} |= {json.dumps(request_id)}'
        )
        response = self.runtime.api_json(
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
        matching_logs = []
        for stream in response.get("data", {}).get("result", []):
            for entry in stream.get("values", []):
                try:
                    record = json.loads(entry[1])
                except (IndexError, TypeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(record, dict)
                    and record.get("event") == "http.server.request"
                    and record.get("request_id") == request_id
                    and record.get("trace_id") == trace_id
                    and record.get("route") == "/api/v1/appointments"
                    and record.get("method") == "POST"
                    and record.get("status") == 201
                ):
                    matching_logs.append(record)
        if len(matching_logs) != 1:
            raise AcceptanceFailure("The API log did not retain one matching business request")
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
        forbidden_sql_keys = {"db.statement", "db.statement.parameters", "db.query.text"}
        if any(forbidden_sql_keys & _nested_keys(span) for span in mysql_spans):
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


def _worker_scan_record(worker_id: str, successful_scans: int) -> str:
    return json.dumps(
        {
            "event": "scan",
            "worker_id": worker_id,
            "phase": "running",
            "database_ok": True,
            "successful_scans": successful_scans,
            "last_successful_scan": "2026-10-26T00:00:00Z",
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
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item[:2000])
    return keys


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
    request_id = str(event["request_id"])
    source_ns = int(event["source_timestamp_unix_ns"])
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


def capture_dashboard_screenshots(runtime: Any) -> dict[str, object]:
    """Capture the five live Grafana dashboards through fixture-only browser TLS."""
    from tests.containers.test_telemetry_stack import OPS_PROBE_DASHBOARD_UIDS

    node = Path("D:/appdata/nodejs/node.exe")
    if not node.is_file():
        raise AcceptanceFailure("The prepared local Node runtime was unavailable for screenshots")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = ROOT / "docs/evidence/observability-foundation/screenshots" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    scratch = ROOT / ".superpowers/sdd/2026-10-23-observability-foundation"
    scratch.mkdir(parents=True, exist_ok=True)
    script = r"""
const { chromium } = require('@playwright/test');
const origin = process.env.TASK8_GRAFANA_ORIGIN;
const user = process.env.TASK8_GRAFANA_USER;
const password = process.env.TASK8_GRAFANA_PASSWORD;
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
      httpCredentials: { username: user, password },
      viewport: { width: 1600, height: 1000 }
    });
    const results = [];
    for (const uid of uids) {
      const metadata = await context.request.get(`${origin}/api/dashboards/uid/${uid}`);
      if (metadata.status() !== 200) {
        throw new Error(`dashboard_api_status:${uid}:${metadata.status()}`);
      }
      const model = await metadata.json();
      if (model.dashboard?.uid !== uid) throw new Error(`dashboard_uid_mismatch:${uid}`);
      const page = await context.newPage();
      const response = await page.goto(`${origin}/d/${uid}/${uid}?orgId=1`, {
        waitUntil: 'domcontentloaded', timeout: 45000
      });
      if (!response || response.status() !== 200) {
        throw new Error(`dashboard_page_status:${uid}:${response?.status() ?? 'none'}`);
      }
      await page.waitForTimeout(4000);
      const filename = `${dir}/${uid}.png`;
      await page.screenshot({ path: filename, fullPage: true });
      results.push({ uid, title: String(model.dashboard.title).slice(0, 80), filename });
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
        PLAYWRIGHT_BROWSERS_PATH=str((ROOT / ".superpowers/playwright").resolve()),
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
        screenshots.append(
            {
                "uid": row["uid"],
                "title": row["title"],
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": digest,
            }
        )
    return {
        "browser": "Chromium via prepared Playwright cache",
        "fixture_host_mapping": "ops.example.invalid to 127.0.0.1, process-local browser resolver",
        "fixture_tls_allowance": (
            "ignoreHTTPS for screenshot browser only; dedicated TLS probes verify "
            "fixture CA and hostname"
        ),
        "viewport": "1600x1000",
        "screenshots": screenshots,
    }
