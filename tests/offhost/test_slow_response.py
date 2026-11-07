"""Exercise a delayed, real MinIO acknowledgement over certificate-verified TLS."""

import hashlib
import ssl
import threading
import time
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from tools.backup.offhost_contract import Code, RemoteConfig, RemoteError
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_lab import age_keys
from tools.backup.offhost_s3 import S3


def test_bulk_copy_uses_operational_budget_for_delayed_store_acknowledgement(
    store_lab, age_executable, tmp_path
):
    _, recipient = age_keys(age_executable, tmp_path / "independent-vault")
    configured, credentials = store_lab.configure(
        "c" * 64, recipient, tmp_path / "credentials.json"
    )
    assert configured.request_seconds == RemoteConfig.model_fields["request_seconds"].default
    upstream = urlsplit(store_lab.endpoint)
    statuses, failures = [], []
    delay = 2.75

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            failures.append(True)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def forward(self, pause):
            connection = HTTPSConnection(
                "127.0.0.1",
                upstream.port,
                context=ssl.create_default_context(cafile=str(store_lab.ca)),
                timeout=10,
            )
            try:
                size = int(self.headers.get("Content-Length", "0"))
                assert 0 <= size <= 1024 * 1024
                body = self.rfile.read(size)
                # Preserve the signed Host/header values and bytes verbatim.
                headers = dict(self.headers.items())
                headers["Connection"] = "close"
                connection.request(self.command, self.path, body, headers)
                response = connection.getresponse()
                result = response.read()
                statuses.append(response.status)
                time.sleep(pause)
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in {"connection", "transfer-encoding", "content-length"}:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                # The short-budget client intentionally closes before its reply.
                self.close_connection = True
            finally:
                connection.close()

        def do_PUT(self):
            self.forward(delay)

        def do_GET(self):
            self.forward(0)

        def log_message(self, format, *args):
            pass

    server = QuietServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(
        store_lab.scratch / "certs/public.crt", store_lab.scratch / "certs/private.key"
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = RemoteConfig.model_validate(
        dict(
            configured.model_dump(),
            endpoint=f"https://127.0.0.1:{server.server_port}",
            attempts=1,
        )
    )
    payload = b"opaque fictional transport fixture" * 1024
    source = tmp_path / "ciphertext.age"
    source.write_bytes(payload)
    source.chmod(0o600)
    sha = hashlib.sha256(payload).hexdigest()
    prefix = config.scope + "points/slow-response/"
    short = RemoteConfig.model_validate(dict(config.model_dump(), request_seconds=2))
    try:
        with pytest.raises(RemoteError, match=Code.TRANSPORT.value):
            S3(short, Deadline(8), credentials=credentials).upload(
                prefix + "short.age", source, sha, len(payload)
            )
        assert statuses == [200], "The actual store must accept before its delayed reply"
        normal = S3(config, Deadline(15), credentials=credentials)
        started = time.monotonic()
        normal.upload(prefix + "normal.age", source, sha, len(payload))
        assert time.monotonic() - started >= delay
        normal.download(prefix + "normal.age", tmp_path / "verified.age", sha, len(payload))
        assert (tmp_path / "verified.age").read_bytes() == payload
        assert statuses == [200, 200, 200] and not failures
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
