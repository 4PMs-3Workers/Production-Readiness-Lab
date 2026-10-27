"""Small internal Alertmanager webhook and telemetry verification endpoint."""

from __future__ import annotations

import json
import os
import re
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock
from urllib.request import urlopen

MAX_REQUEST_BYTES = 65536
MAX_ALERTS_PER_REQUEST = 20
CLIENT_TIMEOUT_SECONDS = 1.0
ALERT_RECORDS: deque[dict[str, str]] = deque(maxlen=20)
ALERT_LOCK = Lock()
TELEMETRY_TARGETS = (
    ("alertmanager", "http://alertmanager:9093/-/ready"),
    ("collector", "http://collector:13133/"),
    ("grafana", "http://grafana:3000/api/health"),
    ("loki", "http://loki:3100/ready"),
    ("prometheus", "http://prometheus:9090/-/ready"),
    ("tempo", "http://tempo:3200/ready"),
)


def sanitize_alert_summary(alert: object) -> dict[str, str]:
    """Validate an Alertmanager alert and retain only its bounded summary fields."""
    if not isinstance(alert, dict):
        raise ValueError("Invalid alert")
    labels = alert.get("labels", {})
    if not isinstance(labels, dict):
        raise ValueError("Invalid alert labels")
    name = labels.get("alertname", "")
    severity = labels.get("severity", "unknown")
    status = alert.get("status", "firing")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", name)
        or severity not in {"critical", "warning", "info", "unknown"}
        or status not in {"firing", "resolved"}
    ):
        raise ValueError("Invalid alert summary")

    has_fingerprint = "fingerprint" in alert
    has_starts_at = "startsAt" in alert
    if has_fingerprint != has_starts_at:
        raise ValueError("Incomplete alert identity")

    identity: dict[str, str] = {}
    if has_fingerprint:
        fingerprint = alert["fingerprint"]
        starts_at = alert["startsAt"]
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{16,64}", fingerprint):
            raise ValueError("Invalid alert fingerprint")
        if not isinstance(starts_at, str):
            raise ValueError("Invalid alert start timestamp")
        if not 1 <= len(starts_at) <= 40:
            raise ValueError("Invalid alert start timestamp")
        try:
            parsed_starts_at = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid alert start timestamp") from None
        if parsed_starts_at.tzinfo is None or parsed_starts_at.utcoffset() is None:
            raise ValueError("Invalid alert start timestamp")
        identity = {
            "fingerprint": fingerprint,
            "starts_at": (
                parsed_starts_at.astimezone(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            ),
        }

    received_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "alertname": name,
        "severity": severity,
        "status": status,
        "received_at": received_at,
        **identity,
    }


def parse_expected_slots(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not value or len(value) > 31:
        raise ValueError("Invalid expected worker slots")
    parts = value.split(",")
    if any(not re.fullmatch(r"[1-8]", part) for part in parts):
        raise ValueError("Invalid expected worker slots")
    slots = tuple(sorted(int(part) for part in parts))
    if len(set(slots)) != len(parts):
        raise ValueError("Invalid expected worker slots")
    return slots


def telemetry_status(timeout: float = 0.5) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    for name, url in TELEMETRY_TARGETS:
        try:
            with urlopen(url, timeout=timeout) as response:
                if response.status != 200:
                    failures.append(name)
        except Exception:
            failures.append(name)
    return not failures, tuple(failures)


class BoundedHTTPServer(HTTPServer):
    request_queue_size = 16

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(CLIENT_TIMEOUT_SECONDS)
        return request, address


class Handler(BaseHTTPRequestHandler):
    server_version = "PRL"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *_args):
        # Request bodies, credential headers and arbitrary paths never go to logs.
        return

    def _send(self, status: int, payload: bytes, content_type="application/json"):
        # A single handler thread must not be held by an idle HTTP/1.1 keep-alive.
        self.close_connection = True
        self.send_response(status)
        self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health/ready":
            self._send(200, b'{"status":"ready"}')
            return
        if self.path == "/health/telemetry":
            healthy, failures = telemetry_status()
            body = json.dumps(
                {"status": "passed" if healthy else "failed", "unhealthy": list(failures)},
                separators=(",", ":"),
            ).encode("ascii")
            self._send(200 if healthy else 503, body)
            return
        if self.path == "/metrics":
            slots = parse_expected_slots(os.environ.get("PRL_EXPECTED_WORKER_SLOTS", "1"))
            lines = [
                "# HELP prl_expected_worker_slot Configured worker slot expected by alert rules.",
                "# TYPE prl_expected_worker_slot gauge",
            ]
            lines.extend(f'prl_expected_worker_slot{{slot="{slot}"}} 1' for slot in slots)
            with ALERT_LOCK:
                record_count = len(ALERT_RECORDS)
            lines.extend(
                [
                    "# HELP prl_local_alert_receiver_alert_records In-memory alert summaries.",
                    "# TYPE prl_local_alert_receiver_alert_records gauge",
                    f"prl_local_alert_receiver_alert_records {record_count}",
                ]
            )
            self._send(200, ("\n".join(lines) + "\n").encode("ascii"), "text/plain; version=0.0.4")
            return
        if self.path == "/alerts":
            with ALERT_LOCK:
                summaries = list(ALERT_RECORDS)
            body = json.dumps({"alerts": summaries}, separators=(",", ":")).encode("ascii")
            self._send(200, body)
            return
        self._send(404, b'{"error":"not_found"}')

    def do_POST(self):
        if self.path != "/alerts":
            self._send(404, b'{"error":"not_found"}')
            return
        length = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,6}", length):
            self._send(400, b'{"error":"invalid_request"}')
            return
        size = int(length)
        if not 1 <= size <= MAX_REQUEST_BYTES:
            self._send(413, b'{"error":"request_too_large"}')
            return
        try:
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError
            value = json.loads(body)
            alerts = value["alerts"]
            if not isinstance(alerts, list) or len(alerts) > MAX_ALERTS_PER_REQUEST:
                raise ValueError
            summaries = [sanitize_alert_summary(alert) for alert in alerts]
        except TimeoutError:
            self._send(408, b'{"error":"request_timeout"}')
            return
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, b'{"error":"invalid_request"}')
            return
        with ALERT_LOCK:
            ALERT_RECORDS.extend(summaries)
        self._send(202, b'{"status":"accepted"}')


def main():
    parse_expected_slots(os.environ.get("PRL_EXPECTED_WORKER_SLOTS", "1"))
    server = BoundedHTTPServer(("0.0.0.0", 8088), Handler)
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
