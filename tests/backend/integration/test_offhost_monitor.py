from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from offhost_probe.config import CheckConfig
from offhost_probe.probe import HttpsTransport, ProbePolicy
from offhost_probe.receiver import ReceiverServer


def _certificate(tmp_path: Path, *, expired: bool = False) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before((now - timedelta(days=3 if expired else 1)).replace(tzinfo=None))
        .not_valid_after(
            (now - timedelta(days=1) if expired else now + timedelta(days=2)).replace(tzinfo=None)
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.pem", tmp_path / "server-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


async def _https_server(
    tmp_path: Path,
    *,
    status: int = 200,
    body: bytes = b"ok",
    delay: float = 0,
    redirect: str | None = None,
    expired: bool = False,
) -> tuple[asyncio.AbstractServer, int, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = _certificate(tmp_path, expired=expired)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readuntil(b"\r\n\r\n")
        if delay:
            await asyncio.sleep(delay)
        headers = [f"HTTP/1.1 {status} Test", f"Content-Length: {len(body)}", "Connection: close"]
        if redirect:
            headers.append(f"Location: {redirect}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
        try:
            await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(respond, "127.0.0.1", 0, ssl=context)
    return server, server.sockets[0].getsockname()[1], cert_path, key_path


def _check(port: int, limit: int = 1024) -> CheckConfig:
    return CheckConfig("gateway", f"https://127.0.0.1:{port}/health/ready", frozenset({200}), limit)


def _trusted(cert: Path) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(cert))


@pytest.mark.asyncio
async def test_https_tls_validation_accepts_trusted_and_rejects_untrusted_or_expired(tmp_path):
    server, port, cert, _ = await _https_server(tmp_path / "valid")
    expired_server, expired_port, expired_cert, _ = await _https_server(
        tmp_path / "expired", expired=True
    )
    try:
        trusted = await HttpsTransport(ssl_context=_trusted(cert)).probe(
            _check(port), ProbePolicy(3, 1, 1)
        )
        untrusted = await HttpsTransport().probe(_check(port), ProbePolicy(3, 1, 1))
        expired = await HttpsTransport(ssl_context=_trusted(expired_cert)).probe(
            _check(expired_port), ProbePolicy(3, 1, 1)
        )
    finally:
        server.close()
        expired_server.close()
        await server.wait_closed()
        await expired_server.wait_closed()
    assert trusted.success and trusted.outcome == "ok"
    assert untrusted.outcome == "tls_untrusted"
    assert expired.outcome == "tls_untrusted"


@pytest.mark.asyncio
async def test_https_probe_rejects_upstream_error_redirect_and_response_over_limit(tmp_path):
    servers = [
        await _https_server(tmp_path / "upstream", status=503),
        await _https_server(tmp_path / "redirect", status=302, redirect="https://127.0.0.1/other"),
        await _https_server(tmp_path / "large", body=b"x" * 2048),
    ]
    try:
        results = []
        for _server, port, cert, _key in servers:
            results.append(
                await HttpsTransport(ssl_context=_trusted(cert)).probe(
                    _check(port, limit=128), ProbePolicy(3, 1, 1)
                )
            )
    finally:
        for server, _, _, _ in servers:
            server.close()
            await server.wait_closed()
    assert (results[0].outcome, results[0].status_code) == ("upstream_error", 503)
    assert (results[1].outcome, results[1].status_code) == ("upstream_error", 302)
    assert results[2].outcome == "response_too_large"


@pytest.mark.asyncio
async def test_https_probe_bounds_slow_read_and_unreachable_gateway(tmp_path):
    server, port, cert, _ = await _https_server(tmp_path, delay=0.2)
    try:
        slow = await HttpsTransport(ssl_context=_trusted(cert)).probe(
            _check(port), ProbePolicy(0.1, 0.05, 0.05)
        )
    finally:
        server.close()
        await server.wait_closed()
    probe_socket = socket.socket()
    probe_socket.bind(("127.0.0.1", 0))
    unreachable_port = probe_socket.getsockname()[1]
    probe_socket.close()
    unreachable = await HttpsTransport().probe(_check(unreachable_port), ProbePolicy(1, 0.5, 0.5))
    assert slow.outcome == "timeout"
    assert unreachable.outcome in {"network_error", "timeout"}


def test_independent_subprocess_fires_within_three_minutes_and_resolves_after_recovery(tmp_path):
    cert_path, key_path = _certificate(tmp_path)
    probe_socket = socket.socket()
    probe_socket.bind(("127.0.0.1", 0))
    target_port = probe_socket.getsockname()[1]
    probe_socket.close()
    receiver = ReceiverServer(0)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    config_path = tmp_path / "probe.toml"
    config_path.write_text(
        f"""[monitor]
interval_seconds = 5
timeout_seconds = 3
connect_timeout_seconds = 1
read_timeout_seconds = 1
failure_threshold = 1
recovery_threshold = 2
state_path = {json.dumps((tmp_path / "state.json").as_posix())}
ca_bundle_path = {json.dumps(cert_path.as_posix())}
delivery_timeout_seconds = 1
delivery_cycle_timeout_seconds = 2
delivery_retries = 0

[notification]
webhook_url = "http://127.0.0.1:{receiver.server_address[1]}/alerts"

[[checks]]
name = "gateway"
url = "https://127.0.0.1:{target_port}/health/ready"
expected_statuses = [200]
max_response_bytes = 1024
""",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[3]
    process = subprocess.Popen(
        [sys.executable, "-m", "offhost_probe.cli", "run", "--config", str(config_path)],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(root / "tools/infra/offhost")},
    )
    started = time.monotonic()
    target_server: ThreadingHTTPServer | None = None
    target_thread: threading.Thread | None = None
    try:
        deadline = started + 150
        while time.monotonic() < deadline and not any(
            e["status"] == "firing" for e in receiver.events
        ):
            if process.poll() is not None:
                pytest.fail("The independent monitor process exited before firing")
            time.sleep(0.1)
        firing = next((e for e in receiver.events if e["status"] == "firing"), None)
        assert firing is not None and time.monotonic() - started < 180
        firing_latency = (
            datetime.fromisoformat(firing["received_at"])
            - datetime.fromisoformat(firing["occurred_at"])
        ).total_seconds()
        assert 0 <= firing_latency < 180

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args: object) -> None:
                return

        target_server = ThreadingHTTPServer(("127.0.0.1", target_port), HealthHandler)
        target_server.socket = context.wrap_socket(target_server.socket, server_side=True)
        target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
        target_thread.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not any(
            e["status"] == "resolved" for e in receiver.events
        ):
            if process.poll() is not None:
                pytest.fail("The independent monitor process exited before recovery")
            time.sleep(0.1)
        resolved = next((e for e in receiver.events if e["status"] == "resolved"), None)
        assert resolved is not None and firing["event_id"] != resolved["event_id"]
        resolved_latency = (
            datetime.fromisoformat(resolved["received_at"])
            - datetime.fromisoformat(resolved["occurred_at"])
        ).total_seconds()
        assert 0 <= resolved_latency < 180
        state_document = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state_document["timestamps"]["probe_started"]
        assert state_document["timestamps"]["probe_completed"]
        assert state_document["timestamps"]["evaluation"]
        assert state_document["timestamps"]["delivery_attempt"]
        assert state_document["timestamps"]["delivery_success"]
        assert state_document["timestamps"]["resolution"]
        assert "https://" not in json.dumps(state_document)
    finally:
        process.terminate()
        process.wait(timeout=10)
        if target_server:
            target_server.shutdown()
            target_server.server_close()
        if target_thread:
            target_thread.join(timeout=5)
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=5)
