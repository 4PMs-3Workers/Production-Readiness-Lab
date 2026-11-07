import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.backup import offhost
from tools.backup.offhost_contract import RemoteConfig, canonical
from tools.backup.offhost_lab import certificates

from app.backup.storage import write_private


def test_supervisor_bounds_a_real_hung_https_request_and_cleans_owned_workspace(
    age_executable, tmp_path, capsys
):
    entered, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(30)

        def log_message(self, format, *args):
            pass

    trust = certificates(tmp_path / "tls")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "tls/public.crt", tmp_path / "tls/private.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = RemoteConfig(
        endpoint=f"https://127.0.0.1:{server.server_port}",
        region="us-east-1",
        bucket="isolated-test",
        prefix="managed-backups",
        environment="lab",
        store="s3",
        source_id="a" * 64,
        recipient="age1" + "a" * 58,
        ca_file=str(trust),
        max_seconds=8,
        request_seconds=30,
        attempts=1,
    )
    config_file, credentials = tmp_path / "config.json", tmp_path / "credentials.json"
    write_private(config_file, canonical(config))
    write_private(
        credentials,
        json.dumps(dict(access_key="fictional-access", secret_key="fictional-secret")).encode(),
    )
    started = time.monotonic()
    try:
        result = offhost.main(
            [
                "status",
                "--config",
                str(config_file),
                "--credentials",
                str(credentials),
                "--local-store",
                str(tmp_path / "store"),
            ]
        )
        elapsed = time.monotonic() - started
        report = json.loads(capsys.readouterr().out)
        assert entered.is_set(), "Fault must reach the real HTTPS request, not expire during setup"
        assert result == 1 and report["reason"] == "REMOTE_DEADLINE_EXCEEDED"
        assert elapsed < config.max_seconds + 4
        assert not list((tmp_path / "store/.offhost-jobs").glob("job-*"))
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
