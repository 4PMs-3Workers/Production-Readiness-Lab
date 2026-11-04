from __future__ import annotations

import json
import re
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

_EVENT_ID = re.compile(r"[0-9a-f]{64}\Z")
_CHECK_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MAX_EVENTS = 128
_MAX_BODY_BYTES = 2048


class ReceiverServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int):
        self.events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
        self.event_ids: set[str] = set()
        super().__init__(("127.0.0.1", port), _ReceiverHandler)


class _ReceiverHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        server = cast(ReceiverServer, self.server)
        if self.path == "/health":
            self._respond(200, b'{"status":"ok"}')
            return
        if self.path == "/events":
            body = json.dumps({"events": list(server.events)}, separators=(",", ":")).encode()
            self._respond(200, body)
            return
        self._respond(404, b'{"error":"not_found"}')

    def do_POST(self) -> None:
        server = cast(ReceiverServer, self.server)
        if self.path != "/alerts":
            self._respond(404, b'{"error":"not_found"}')
            return
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isascii() or not raw_length.isdecimal() or len(raw_length) > 4:
            self._respond(400, b'{"error":"invalid_alert"}')
            return
        length = int(raw_length)
        if not 1 <= length <= _MAX_BODY_BYTES:
            self._respond(413, b'{"error":"invalid_alert"}')
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeError, json.JSONDecodeError):
            self._respond(400, b'{"error":"invalid_alert"}')
            return
        if not self._valid(payload):
            self._respond(400, b'{"error":"invalid_alert"}')
            return
        event_id = payload["event_id"]
        if self.headers.get("Idempotency-Key") != event_id:
            self._respond(400, b'{"error":"invalid_alert"}')
            return
        if event_id not in server.event_ids:
            if len(server.events) == _MAX_EVENTS:
                removed = server.events[0]["event_id"]
                server.event_ids.discard(removed)
            server.events.append(
                {**payload, "received_at": datetime.now(UTC).isoformat(timespec="milliseconds")}
            )
            server.event_ids.add(event_id)
        self._respond(204, b"")

    @staticmethod
    def _valid(payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and set(payload) == {"schema", "event_id", "check", "status", "occurred_at"}
            and isinstance(payload["schema"], int)
            and not isinstance(payload["schema"], bool)
            and payload["schema"] == 1
            and isinstance(payload["event_id"], str)
            and bool(_EVENT_ID.fullmatch(payload["event_id"]))
            and isinstance(payload["check"], str)
            and bool(_CHECK_NAME.fullmatch(payload["check"]))
            and isinstance(payload["status"], str)
            and payload["status"] in {"firing", "resolved"}
            and isinstance(payload["occurred_at"], str)
            and 1 <= len(payload["occurred_at"]) <= 40
            and _valid_time(payload["occurred_at"])
        )


def _valid_time(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None
