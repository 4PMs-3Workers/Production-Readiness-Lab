"""Stateful gateway protocol fixture; never substitutes for Linux/MySQL acceptance."""

import json
from uuid import uuid4

import httpx
from tools.deploy.rollout_http import SmokeCredentials, SmokeSession
from tools.deploy.rollout_runtime import Deadline

ACCOUNT = SmokeCredentials("private@example.invalid", "private-password", "CAREER")


class SmokeAPI:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.keys: dict[str, tuple[dict, dict]] = {}
        self.fail_detail = False
        self.fail_cancel = False
        self.crash_after_create: type[BaseException] | None = None
        self.logged_out = False

    def session(self):
        return SmokeSession(
            "http://localhost:8080", Deadline(60), transport=httpx.MockTransport(self.handle)
        )

    def effects(self, identifier):
        return (1, 2, 4) if self.rows[identifier]["status"] == "CANCELLED" else (1, 1, 2)

    def handle(self, request):
        path = request.url.path
        body, status = {}, 200
        if path == "/login":
            pass
        elif path.endswith("auth/login"):
            self.logged_out = False
        elif path.endswith("auth/csrf"):
            body = dict(csrf_token="signed-token")
        elif path.endswith("auth/logout"):
            self.logged_out, status = True, 204
        elif path == "/api/v1/booking-context":
            body = dict(min_booking_date="2026-11-06", max_booking_date="2026-11-07")
        elif path == "/api/v1/purposes":
            body = dict(
                items=[
                    dict(
                        id="1" * 8 + "-1111-1111-1111-" + "1" * 12,
                        code="CAREER",
                        requires_free_text=False,
                    )
                ]
            )
        elif path == "/api/v1/staff":
            body = dict(items=[dict(id="22222222-2222-2222-2222-222222222222")])
        elif path == "/api/v1/availability":
            body = dict(slots=[dict(starts_at="2026-11-06T09:00:00Z")])
        elif path == "/api/v1/appointments" and request.method == "POST":
            key, payload = request.headers["Idempotency-Key"], json.loads(request.content)
            if key in self.keys:
                previous, body = self.keys[key]
                status = 200 if payload == previous else 409
            elif sum(row["status"] == "UPCOMING" for row in self.rows.values()) >= 3:
                status = 409
            else:
                identifier = str(uuid4())
                body = dict(id=identifier, status="UPCOMING", created_at="2026-11-05T00:00:00Z")
                self.rows[identifier] = dict(body)
                self.keys[key] = (payload, dict(body))
                status = 201
                if self.crash_after_create is not None:
                    raise self.crash_after_create()
        elif path == "/api/v1/appointments":
            status, body = (401 if self.logged_out else 200), dict(items=[])
        elif path.endswith("/cancel"):
            identifier = path.split("/")[-2]
            if self.fail_cancel:
                status = 503
            else:
                self.rows[identifier]["status"] = "CANCELLED"
                body = dict(id=identifier, status="CANCELLED")
        elif path.startswith("/api/v1/appointments/"):
            identifier = path.split("/")[-1]
            if self.fail_detail:
                status = 503
            else:
                body = dict(self.rows[identifier])
        else:
            raise AssertionError("Unexpected unit gateway route")
        return httpx.Response(
            status,
            json=body if status != 204 else None,
            headers={"Cache-Control": "private, no-store", "X-Request-ID": "r"},
        )
