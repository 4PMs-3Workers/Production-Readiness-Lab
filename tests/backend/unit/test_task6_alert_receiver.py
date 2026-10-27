import importlib.util
import json
import socket
from datetime import datetime
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "src/backend/app/observability/alert_receiver.py"


def load():
    spec = importlib.util.spec_from_file_location("task6_alert_receiver", MODULE)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("value,expected", [("1", (1,)), ("8,2", (2, 8)), ("1,2,3", (1, 2, 3))])
def test_expected_worker_slots_are_finite_unique_and_normalized(value, expected):
    assert load().parse_expected_slots(value) == expected


@pytest.mark.parametrize("value", ["", "0", "9", "1,1", "1,,2", "1, 2", "1\n2"])
def test_expected_worker_slots_reject_invalid_input(value):
    with pytest.raises(ValueError):
        load().parse_expected_slots(value)


@pytest.fixture
def alert_receiver_server():
    module = load()
    server_type = getattr(module, "BoundedHTTPServer", module.HTTPServer)
    server = server_type(("127.0.0.1", 0), module.Handler)
    server.timeout = 0.1
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()


def _read_response(connection):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            return bytes(data)
        data.extend(chunk)
    head, body = data.split(b"\r\n\r\n", 1)
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    while len(body) < length:
        chunk = connection.recv(4096)
        if not chunk:
            break
        body += chunk
    return head + b"\r\n\r\n" + body


def _status(response):
    try:
        return int(response.split(b"\r\n", 1)[0].split()[1])
    except (IndexError, ValueError):
        return None


def _health_request(port, timeout=2.5):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(
            b"GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        try:
            return _read_response(client)
        except TimeoutError:
            return b""


def _post_alerts(port, alerts):
    body = json.dumps({"alerts": alerts}, separators=(",", ":")).encode("utf-8")
    request = (
        b"POST /alerts HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Content-Type: application/json\r\n\r\n"
        + body
    )
    with socket.create_connection(("127.0.0.1", port), timeout=2.5) as client:
        client.settimeout(2.5)
        client.sendall(request)
        return _read_response(client)


def _get_alerts(port):
    with socket.create_connection(("127.0.0.1", port), timeout=2.5) as client:
        client.settimeout(2.5)
        client.sendall(b"GET /alerts HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        response = _read_response(client)
    return json.loads(response.split(b"\r\n\r\n", 1)[1])["alerts"]


def test_idle_http11_keepalive_cannot_starve_health_route(alert_receiver_server):
    port = alert_receiver_server
    with socket.create_connection(("127.0.0.1", port), timeout=2.5) as idle:
        idle.settimeout(2.5)
        idle.sendall(
            b"GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
        )
        assert _status(_read_response(idle)) == 200
        assert _status(_health_request(port)) == 200


def test_incomplete_content_length_body_cannot_starve_health_route(alert_receiver_server):
    port = alert_receiver_server
    with socket.create_connection(("127.0.0.1", port), timeout=2.5) as slow_body:
        slow_body.settimeout(2.5)
        slow_body.sendall(
            b"POST /alerts HTTP/1.1\r\nHost: localhost\r\nContent-Length: 40\r\n"
            b'Content-Type: application/json\r\n\r\n{"alerts":['
        )
        assert _status(_health_request(port)) == 200


def test_non_object_alert_member_is_rejected_without_breaking_server(alert_receiver_server):
    port = alert_receiver_server
    body = b'{"alerts":[17]}'
    request = (
        b"POST /alerts HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Content-Type: application/json\r\n\r\n"
        + body
    )
    with socket.create_connection(("127.0.0.1", port), timeout=2.5) as client:
        client.settimeout(2.5)
        client.sendall(request)
        response = _read_response(client)
    assert _status(response) == 400
    assert _status(_health_request(port)) == 200


def test_received_alerts_expose_only_bounded_sanitized_summaries(alert_receiver_server):
    port = alert_receiver_server
    response = _post_alerts(
        port,
        [
            {
                "status": "resolved",
                "labels": {
                    "alertname": "WorkerStalled",
                    "severity": "warning",
                    "tenant": "private-alert-label",
                },
                "annotations": {"description": "private-alert-annotation"},
            }
        ],
    )
    assert _status(response) == 202
    payload = _get_alerts(port)
    assert len(payload) == 1
    item = payload[0]
    assert set(item) == {"alertname", "severity", "status", "received_at"}
    assert item["alertname"] == "WorkerStalled"
    assert item["severity"] == "warning"
    assert item["status"] == "resolved"
    assert isinstance(datetime.fromisoformat(item["received_at"]), datetime)
    assert "private-alert" not in json.dumps(payload)


@pytest.mark.parametrize("fingerprint", ["0" * 16, "a" * 64])
def test_received_alert_exposes_only_validated_alertmanager_identity(fingerprint):
    item = load().sanitize_alert_summary(
        {
            "status": "firing",
            "fingerprint": fingerprint,
            "startsAt": "2026-10-27T03:04:05+02:00",
            "labels": {
                "alertname": "WorkerHeartbeatStale",
                "severity": "critical",
                "worker_slot": "1",
                "tenant": "private-alert-label",
            },
            "annotations": {"description": "private-alert-annotation"},
        }
    )
    assert set(item) == {
        "alertname",
        "severity",
        "status",
        "received_at",
        "fingerprint",
        "starts_at",
    }
    assert item["fingerprint"] == fingerprint
    assert item["starts_at"] == "2026-10-27T01:04:05.000Z"
    assert "private-alert" not in json.dumps(item)
    assert "worker_slot" not in json.dumps(item)


def test_legacy_alert_without_identity_remains_supported():
    item = load().sanitize_alert_summary(
        {
            "status": "firing",
            "labels": {"alertname": "WorkerHeartbeatStale", "severity": "critical"},
        }
    )

    assert set(item) == {"alertname", "severity", "status", "received_at"}


@pytest.mark.parametrize(
    "identity",
    [
        {"fingerprint": "0123456789abcdef0123456789abcdef"},
        {"startsAt": "2026-10-27T03:04:05Z"},
        {"fingerprint": "0123456789abcde", "startsAt": "2026-10-27T03:04:05Z"},
        {"fingerprint": "a" * 65, "startsAt": "2026-10-27T03:04:05Z"},
        {"fingerprint": "A123456789abcdef", "startsAt": "2026-10-27T03:04:05Z"},
        {"fingerprint": "g123456789abcdef", "startsAt": "2026-10-27T03:04:05Z"},
        {"fingerprint": "0123456789abcdef", "startsAt": "2026-10-27T03:04:05"},
        {"fingerprint": "0123456789abcdef", "startsAt": "not-a-timestamp"},
        {"fingerprint": "0123456789abcdef", "startsAt": "2026-10-27T03:04:05." + "1" * 30 + "Z"},
    ],
)
def test_invalid_or_partial_alertmanager_identity_is_rejected(identity):
    with pytest.raises(ValueError):
        load().sanitize_alert_summary(
            {
                "status": "firing",
                **identity,
                "labels": {"alertname": "WorkerHeartbeatStale", "severity": "critical"},
            }
        )
