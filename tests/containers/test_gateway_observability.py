"""Real pinned-Nginx observability and file-pipeline acceptance."""

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

pytest_plugins = ["tests.containers.test_gateway"]
ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_GATEWAY_TESTS") != "1",
    reason="opt-in Docker gateway acceptance",
)

COLLECTOR_IMAGE = (
    "otel/opentelemetry-collector-contrib:0.163.2"
    "@sha256:7f22d2a6748110ffb6a59bc0df448bccbf501cdb667d610ede4c61d3863d5849"
)


@pytest.fixture(scope="module")
def collector_test_image():
    image = "prl-gateway-collector-test:" + uuid4().hex
    run(
        "docker",
        "build",
        "-f",
        str(ROOT / "tests/containers/Dockerfile.gateway-collector"),
        "-t",
        image,
        str(ROOT / "tests/containers"),
        env=dict(os.environ),
        timeout=600,
    )
    yield image
    run("docker", "image", "rm", image, env=dict(os.environ))


COLLECTOR_CONFIG = """\
extensions:
  file_storage:
    directory: /var/lib/otelcol/file_storage
receivers:
  filelog/gateway:
    include:
      - /var/log/prl/access.jsonl
      - /var/log/prl/access.jsonl.*
    start_at: beginning
    poll_interval: 100ms
    storage: file_storage
    operators:
      - type: json_parser
        parse_from: body
        parse_to: attributes
        timestamp:
          parse_from: attributes.timestamp
          layout: '%Y-%m-%dT%H:%M:%S%z'
exporters:
  debug:
    verbosity: detailed
service:
  extensions: [file_storage]
  pipelines:
    logs/gateway:
      receivers: [filelog/gateway]
      exporters: [debug]
"""


def run(*args, env, timeout=180):
    result = subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def gateway_records(base, env):
    output = subprocess.run(
        [*base, "logs", "--no-log-prefix", "gateway"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout
    records = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records, output


def wait_record(base, env, request_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records, output = gateway_records(base, env)
        for record in records:
            if record.get("request_id") == request_id:
                return record, output
        time.sleep(0.1)
    pytest.fail(f"gateway did not emit access event for request id {request_id}")


def open_gateway_connection(gateway):
    client, mode, _, env = gateway
    port = client.base_url.port
    connection = socket.create_connection(("127.0.0.1", port), timeout=5)
    if mode == "https":
        context = ssl.create_default_context(cafile=env["PRL_TLS_CERT_FILE"])
        connection = context.wrap_socket(connection, server_hostname="localhost")
    connection.settimeout(5)
    return connection, port


def read_raw_http_response(connection):
    chunks = []
    while True:
        try:
            chunk = connection.recv(4096)
        except (ConnectionResetError, TimeoutError):
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def parse_raw_http_response(raw):
    if b"\r\n\r\n" not in raw:
        pytest.fail(f"gateway returned an incomplete raw HTTP response: {raw!r}")
    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            headers[name.decode().lower()] = value.decode().strip()
    return status, headers, body


def wait_new_gateway_record(base, env, previous_ids, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records, output = gateway_records(base, env)
        for record in records:
            if record.get("request_id") not in previous_ids and predicate(record):
                return record, output
        time.sleep(0.1)
    pytest.fail("gateway did not emit the expected new access event")


def wait_for_upstream_file(base, env, path, timeout=5):
    deadline = time.monotonic() + timeout
    command = [*base, "exec", "-T", "api", "test", "-f", path]
    while time.monotonic() < deadline:
        result = subprocess.run(
            command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return
        if result.returncode != 1:
            pytest.fail(result.stdout + result.stderr)
        time.sleep(0.05)
    pytest.fail("controlled API fixture did not confirm the delayed request")


def reconfigure_nginx(gateway, tmp_path, *, routes=None, proxy=None, sink=None, nginx=None):
    _, _, base, env = gateway
    compose_file = Path(base[-1])
    merged = json.loads(compose_file.read_text())
    gateway_config = merged["services"]["gateway"]
    directory_mount = next(
        volume for volume in gateway_config["volumes"] if volume.get("target") == "/etc/nginx/prl"
    )
    copied = tmp_path / "nginx"
    shutil.copytree(directory_mount["source"], copied)
    if routes is not None:
        (copied / "routes.conf").write_text(routes)
    if proxy is not None:
        (copied / "proxy.conf").write_text(proxy)
    if sink is not None:
        (copied / "file-sink.conf").write_text(sink)
    directory_mount["source"] = str(copied)
    if nginx is not None:
        main_mount = next(
            volume
            for volume in gateway_config["volumes"]
            if volume.get("target") == "/etc/nginx/nginx.conf"
        )
        main_config = tmp_path / "nginx-main.conf"
        main_config.write_text(nginx)
        main_mount["source"] = str(main_config)
    compose_file.write_text(json.dumps(merged))
    run(*base, "config", "--quiet", env=env)
    run(*base, "up", "-d", "--no-deps", "--force-recreate", "gateway", env=env)
    run(*base, "exec", "-T", "gateway", "nginx", "-t", env=env)
    client = gateway[0]
    for _ in range(60):
        try:
            if client.get("/health/gateway").status_code == 200:
                return copied
        except Exception:
            pass
        time.sleep(0.25)
    pytest.fail("reconfigured Nginx did not become ready")


def configure_file_pipeline(
    gateway,
    tmp_path,
    *,
    rotate_bytes=10 * 1024 * 1024,
    rotate_files=4,
    rotate_interval="0.2",
    collector=False,
    collector_image=None,
):
    client, _, base, env = gateway
    compose_file = Path(base[-1])
    merged = json.loads(compose_file.read_text())
    gateway_config = merged["services"]["gateway"]
    directory_mount = next(
        volume for volume in gateway_config["volumes"] if volume.get("target") == "/etc/nginx/prl"
    )
    copied = tmp_path / "nginx-file-pipeline"
    shutil.copytree(directory_mount["source"], copied)
    (copied / "file-sink.conf").write_text("access_log /var/log/prl/access.jsonl prl_gateway;\n")
    directory_mount["source"] = str(copied)
    gateway_config["volumes"].append(
        {"type": "volume", "source": "gateway_logs", "target": "/var/log/prl"}
    )
    merged.setdefault("volumes", {})["gateway_logs"] = {
        "driver": "local",
        "driver_opts": {
            "type": "tmpfs",
            "device": "tmpfs",
            "o": "size=64m,uid=101,gid=101,mode=1770",
        },
    }

    helper_path = ROOT / "tools/infra/gateway-log-rotate.sh"
    merged["services"]["rotate_helper"] = {
        "image": gateway_config["image"],
        "user": "101:101",
        "init": True,
        "read_only": True,
        "tmpfs": ["/tmp:size=8m,mode=1777"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 32,
        "mem_limit": "32m",
        "cpus": 0.1,
        "network_mode": "none",
        "pid": "service:gateway",
        "entrypoint": ["/bin/sh", "/usr/local/sbin/prl-gateway-log-rotate"],
        "environment": {
            "PRL_GATEWAY_ROTATE_BYTES": str(rotate_bytes),
            "PRL_GATEWAY_ROTATE_FILES": str(rotate_files),
            "PRL_GATEWAY_ROTATE_INTERVAL": str(rotate_interval),
        },
        "volumes": [
            {
                "type": "bind",
                "source": str(helper_path),
                "target": "/usr/local/sbin/prl-gateway-log-rotate",
                "read_only": True,
                "bind": {"create_host_path": False},
            },
            {"type": "volume", "source": "gateway_logs", "target": "/var/log/prl"},
        ],
    }

    if collector:
        collector_config = tmp_path / "gateway-collector.yaml"
        collector_config.write_text(COLLECTOR_CONFIG)
        merged["volumes"]["collector_state"] = {"driver": "local"}
        merged["services"]["collector"] = {
            "image": collector_image or COLLECTOR_IMAGE,
            "group_add": ["101"],
            "read_only": True,
            "tmpfs": ["/tmp:size=16m,mode=1777"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 64,
            "mem_limit": "512m",
            "cpus": 0.5,
            "network_mode": "none",
            "command": ["--config=/etc/otelcol-contrib/config.yaml"],
            "volumes": [
                {
                    "type": "bind",
                    "source": str(collector_config),
                    "target": "/etc/otelcol-contrib/config.yaml",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                },
                {
                    "type": "volume",
                    "source": "gateway_logs",
                    "target": "/var/log/prl",
                    "read_only": True,
                },
                {
                    "type": "volume",
                    "source": "collector_state",
                    "target": "/var/lib/otelcol/file_storage",
                },
            ],
        }

    compose_file.write_text(json.dumps(merged))
    run(*base, "config", "--quiet", env=env)
    run(
        *base,
        "up",
        "-d",
        "--no-deps",
        "gateway",
        "rotate_helper",
        env=env,
    )
    run(*base, "exec", "-T", "rotate_helper", "/bin/sh", "-c", "test -r /proc/1/comm", env=env)
    run(*base, "exec", "-T", "gateway", "nginx", "-t", env=env)
    for _ in range(60):
        try:
            if client.get("/health/gateway").status_code == 200:
                return copied
        except Exception:
            pass
        time.sleep(0.25)
    pytest.fail("gateway with file sink did not become ready")


def gateway_file_records(base, env):
    result = subprocess.run(
        [
            *base,
            "exec",
            "-T",
            "gateway",
            "sh",
            "-c",
            'for file in /var/log/prl/access.jsonl*; do [ -f "$file" ] && cat "$file"; done',
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    records = []
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records, result.stdout


def wait_file_record(base, env, request_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records, output = gateway_file_records(base, env)
        for record in records:
            if record.get("request_id") == request_id:
                return record, output
        time.sleep(0.1)
    pytest.fail(f"gateway file sink did not emit request id {request_id}")


def collector_output(base, env):
    return subprocess.run(
        [*base, "logs", "--no-log-prefix", "collector"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout


def collector_record_count(output, request_id):
    attribute = f"-> request_id: Str({request_id})"
    return sum(
        any(line.strip() == attribute for line in record.splitlines())
        for record in output.split("LogRecord #")[1:]
    )


def collector_checkpoint_volume(base, env):
    container_id = run(*base, "ps", "-q", "collector", env=env).strip()
    assert container_id
    containers = json.loads(run("docker", "inspect", container_id, env=env))
    mount = next(
        item
        for item in containers[0]["Mounts"]
        if item["Destination"] == "/var/lib/otelcol/file_storage"
    )
    assert mount["Type"] == "volume", mount
    volumes = json.loads(run("docker", "volume", "inspect", mount["Name"], env=env))
    assert volumes[0]["Driver"] == "local", volumes
    assert not volumes[0].get("Options"), volumes
    return mount["Name"]


def wait_collector_requests(base, env, request_ids, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = collector_output(base, env)
        if all(request_id in output for request_id in request_ids):
            return output
        time.sleep(0.2)
    pytest.fail(f"Collector did not emit file events for {request_ids}")


def test_request_event_is_normalized_and_client_metadata_is_untrusted(gateway):
    client, mode, base, env = gateway
    response = client.get(
        "/api/v1/purposes?private=secret-query-canary",
        headers={
            "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
            "tracestate": "private=trace-state-canary",
            "baggage": "secret=baggage-canary",
            "X-PRL-Route": "/api/v1/staff/me/context",
            "X-PRL-Outcome": "login_throttled",
        },
    )
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert "x-prl-route" not in response.headers
    assert "x-prl-outcome" not in response.headers

    record, raw_logs = wait_record(base, env, request_id)
    assert record["event"] == "gateway.request"
    assert record["service"] == "gateway"
    assert record["environment"] == ("local" if mode == "local" else "demo")
    assert record["release"] == "development"
    assert record["route"] == "/api/v1/purposes"
    assert record["method"] == "GET"
    assert record["status"] == 200
    assert record["upstream_identity"] == "api"
    assert record["classification"] == "success"
    assert record["error_class"] == "unknown"
    assert re.fullmatch(r"[a-f0-9]{32}", record["request_id"])
    assert re.fullmatch(r"[a-f0-9]{32}", record["trace_id"])
    assert int(record["trace_id"], 16) != 0
    assert float(record["duration_seconds"]) >= 0
    timestamp = datetime.fromisoformat(record["timestamp"])
    assert timestamp.utcoffset().total_seconds() == 0
    for secret in (
        "secret-query-canary",
        "trace-state-canary",
        "baggage-canary",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "/api/v1/staff/me/context",
        "login_throttled",
    ):
        assert secret not in raw_logs
    assert "path" not in record


@pytest.mark.parametrize("fixture", ["trace_invalid", "trace_zero"])
def test_invalid_or_zero_api_trace_is_not_logged(gateway, fixture):
    client, _, base, env = gateway
    response = client.get(f"/api/v1/purposes?prl_test={fixture}")
    assert response.status_code == 200
    record, _ = wait_record(base, env, response.headers["x-request-id"])
    assert record["upstream_identity"] == "api"
    assert record["trace_id"] == ""


@pytest.mark.parametrize(
    (
        "method",
        "path",
        "expected_status",
        "classification",
        "outcome",
        "error_class",
        "route",
    ),
    [
        (
            "GET",
            "/api/v1/purposes?prl_test=input",
            422,
            "excluded",
            "input",
            "input",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=business_422",
            422,
            "excluded",
            "business",
            "business",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=service_4xx",
            400,
            "service_failure",
            "service_error",
            "service",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=service_2xx",
            200,
            "service_failure",
            "service_error",
            "service",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=unclassified_4xx",
            400,
            "service_failure",
            "unknown",
            "unknown",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=unknown_class_4xx",
            400,
            "service_failure",
            "unknown",
            "unknown",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=object_404",
            404,
            "excluded",
            "input",
            "input",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=published_wrong_marker",
            200,
            "service_failure",
            "route_mismatch",
            "unknown",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=published_missing_marker",
            404,
            "service_failure",
            "route_mismatch",
            "unknown",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=service",
            503,
            "service_failure",
            "service_error",
            "service",
            "/api/v1/purposes",
        ),
        (
            "GET",
            "/api/v1/purposes?prl_test=capacity_429",
            429,
            "service_failure",
            "capacity_or_unclassified_429",
            "input",
            "/api/v1/purposes",
        ),
        (
            "POST",
            "/api/v1/auth/login?prl_test=login_throttled",
            429,
            "excluded",
            "api_login_throttled",
            "input",
            "/api/v1/auth/login",
        ),
        (
            "POST",
            "/api/v1/auth/login?prl_test=login_throttled_missing_class",
            429,
            "service_failure",
            "capacity_or_unclassified_429",
            "unknown",
            "/api/v1/auth/login",
        ),
        (
            "POST",
            "/api/v1/auth/login?prl_test=login_throttled_business_class",
            429,
            "service_failure",
            "capacity_or_unclassified_429",
            "business",
            "/api/v1/auth/login",
        ),
        (
            "POST",
            "/api/v1/auth/login?prl_test=login_throttled_service_class",
            429,
            "service_failure",
            "service_error",
            "service",
            "/api/v1/auth/login",
        ),
        (
            "GET",
            "/api/v1/not-a-published-operation",
            404,
            "out_of_scope",
            "unknown",
            "unknown",
            "__unmatched__",
        ),
    ],
)
def test_classification_truth_table(
    gateway,
    method,
    path,
    expected_status,
    classification,
    outcome,
    error_class,
    route,
):
    client, _, base, env = gateway
    response = client.request(method, path, content=b"{}" if method == "POST" else None)
    assert response.status_code == expected_status
    request_id = response.headers["x-request-id"]
    assert "x-prl-route" not in response.headers
    assert "x-prl-outcome" not in response.headers
    record, raw_logs = wait_record(base, env, request_id)
    assert record["route"] == route
    assert record["upstream_identity"] == "api"
    assert record["classification"] == classification, record
    assert record["upstream_outcome"] == outcome, record
    assert record["error_class"] == error_class, record
    assert re.fullmatch(r"[a-f0-9]{32}", record["trace_id"]), record
    assert int(record["trace_id"], 16) != 0
    assert "secret-error-class-canary" not in raw_logs


def test_upstream_read_timeout_is_a_safe_service_failure(gateway, tmp_path):
    client, _, base, env = gateway
    proxy = (ROOT / "tools/deploy/nginx/proxy.conf").read_text()
    assert "proxy_read_timeout 10s;" in proxy
    proxy = proxy.replace("proxy_read_timeout 10s;", "proxy_read_timeout 250ms;", 1)
    reconfigure_nginx(gateway, tmp_path, proxy=proxy)

    response = client.get("/api/v1/purposes?prl_test=upstream_timeout&private=timeout-canary")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    record, raw_logs = wait_record(base, env, response.headers["x-request-id"])
    assert record["status"] == 503
    assert record["route"] == "/api/v1/purposes"
    assert record["upstream_identity"] == "api"
    assert record["upstream_outcome"] == "upstream_timeout"
    assert record["classification"] == "service_failure"
    assert record["error_class"] == "service"
    assert "timeout-canary" not in raw_logs


def test_published_client_disconnect_is_excluded_before_api_headers(gateway):
    client, _, base, env = gateway
    probe = uuid4().hex
    marker = f"/tmp/prl-client-disconnect-{probe}"
    previous_ids = {record["request_id"] for record in gateway_records(base, env)[0]}
    connection, port = open_gateway_connection(gateway)
    request = (
        f"GET /api/v1/purposes?prl_test=client_disconnect&probe={probe}"
        "&private=client-disconnect-canary HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        "Connection: close\r\n\r\n"
    )
    try:
        connection.sendall(request.encode())
        wait_for_upstream_file(base, env, marker)
    finally:
        connection.close()

    record, raw_logs = wait_new_gateway_record(
        base,
        env,
        previous_ids,
        lambda value: (
            value.get("route") == "/api/v1/purposes"
            and value.get("upstream_outcome") == "client_disconnect"
        ),
    )
    assert record["status"] == 499
    assert record["method"] == "GET"
    assert record["upstream_identity"] == "api"
    assert record["classification"] == "excluded"
    assert record["trace_id"] == ""
    assert "client-disconnect-canary" not in raw_logs


def test_malformed_published_request_is_local_input_400(gateway):
    _, _, base, env = gateway
    previous_ids = {record["request_id"] for record in gateway_records(base, env)[0]}
    connection, port = open_gateway_connection(gateway)
    request = (
        "POST /api/v1/auth/login?private=malformed-url-canary HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        "X-Invalid Header: malformed-header-canary\r\n"
        "Content-Length: 2\r\n"
        "Connection: close\r\n\r\n{}"
    )
    try:
        connection.sendall(request.encode())
        raw = read_raw_http_response(connection)
    finally:
        connection.close()
    status, headers, body = parse_raw_http_response(raw)
    assert status == 400
    assert json.loads(body)["error"]["code"] == "HTTP_ERROR"
    record, raw_logs = wait_new_gateway_record(
        base,
        env,
        previous_ids,
        lambda value: value.get("status") == 400 and value.get("route") == "/api/v1/auth/login",
    )
    assert record["upstream_identity"] == "none"
    assert record["upstream_outcome"] == "gateway_input"
    assert record["classification"] == "excluded"
    assert record["error_class"] == "input"
    assert headers["x-error-class"] == "input"
    assert "malformed-url-canary" not in raw_logs
    assert "malformed-header-canary" not in raw_logs


def test_incomplete_login_body_is_local_input_408(gateway, tmp_path):
    _, _, base, env = gateway
    nginx = (ROOT / "tools/deploy/nginx/nginx.conf").read_text()
    assert "client_body_timeout 10s;" in nginx
    nginx = nginx.replace("client_body_timeout 10s;", "client_body_timeout 250ms;", 1)
    reconfigure_nginx(gateway, tmp_path, nginx=nginx)
    previous_ids = {record["request_id"] for record in gateway_records(base, env)[0]}

    connection, port = open_gateway_connection(gateway)
    request = (
        "POST /api/v1/auth/login?private=body-timeout-canary HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 10\r\n"
        "Connection: close\r\n\r\n{}"
    )
    try:
        connection.sendall(request.encode())
        raw = read_raw_http_response(connection)
    finally:
        connection.close()
    # This pinned Nginx closes after its core client-body timeout without a wire
    # response; the access event still carries the generated local 408 status.
    assert raw == b""
    record, raw_logs = wait_new_gateway_record(
        base,
        env,
        previous_ids,
        lambda value: value.get("status") == 408 and value.get("route") == "/api/v1/auth/login",
    )
    assert record["upstream_identity"] == "none"
    assert record["upstream_outcome"] == "gateway_input"
    assert record["classification"] == "excluded"
    assert record["error_class"] == "input"
    assert "body-timeout-canary" not in raw_logs


def test_nonlogin_local_429_is_not_a_login_throttle(gateway, tmp_path):
    client, _, base, env = gateway
    routes = (ROOT / "tools/deploy/nginx/routes.conf").read_text()
    local_limiter = (
        "location = /api/v1/purposes {\n"
        "    limit_req zone=login burst=1 nodelay;\n"
        "    include /etc/nginx/prl/proxy.conf;\n"
        "    proxy_pass http://172.30.34.3:8000;\n"
        "}\n\n"
        "location = /api/v1 {"
    )
    routes = routes.replace("location = /api/v1 {", local_limiter, 1)
    reconfigure_nginx(gateway, tmp_path, routes=routes)

    responses = [client.get("/api/v1/purposes?private=local-capacity") for _ in range(5)]
    response = next((candidate for candidate in responses if candidate.status_code == 429), None)
    assert response is not None, [candidate.status_code for candidate in responses]
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert response.headers["x-error-class"] == "service"
    record, raw_logs = wait_record(base, env, response.headers["x-request-id"])
    assert record["upstream_identity"] == "none"
    assert record["upstream_outcome"] == "capacity_or_unclassified_429"
    assert record["classification"] == "service_failure"
    assert record["error_class"] == "service"
    assert "local-capacity" not in raw_logs


def test_local_body_rejection_and_exact_login_limit_are_finite(gateway):
    client, _, base, env = gateway
    rejected = client.post("/api/v1/auth/login", content=b"x" * 4097)
    assert rejected.status_code == 413
    record, _ = wait_record(base, env, rejected.headers["x-request-id"])
    assert record["upstream_identity"] == "none"
    assert record["upstream_outcome"] == "gateway_input"
    assert record["classification"] == "excluded"
    assert record["error_class"] == "input"

    responses = [client.post("/api/v1/auth/login", content=b"{}") for _ in range(14)]
    throttled = next(response for response in responses if response.status_code == 429)
    assert throttled.json()["error"]["code"] == "LOGIN_THROTTLED"
    record, _ = wait_record(base, env, throttled.headers["x-request-id"])
    assert record["upstream_identity"] == "none"
    assert record["upstream_outcome"] == "gateway_login_throttled"
    assert record["classification"] == "excluded"
    assert record["error_class"] == "input"


def test_frontend_with_and_without_a_copied_route_marker_is_a_misroute(gateway, tmp_path):
    client, _, base, env = gateway
    compose_file = Path(base[-1])
    merged = json.loads(compose_file.read_text())
    gateway_config = merged["services"]["gateway"]
    directory_mount = next(
        volume for volume in gateway_config["volumes"] if volume.get("target") == "/etc/nginx/prl"
    )
    copied = tmp_path / "nginx"
    shutil.copytree(directory_mount["source"], copied)
    routes = (copied / "routes.conf").read_text()
    routes = routes.replace(
        "proxy_pass http://172.30.34.3:8000;",
        "set $frontend http://frontend:3000;\n    proxy_pass $frontend;",
    )
    (copied / "routes.conf").write_text(routes)
    directory_mount["source"] = str(copied)
    compose_file.write_text(json.dumps(merged))
    run(*base, "config", "--quiet", env=env)
    run(*base, "up", "-d", "--no-deps", "--force-recreate", "gateway", env=env)
    run(*base, "exec", "-T", "gateway", "nginx", "-t", env=env)

    missing = client.get("/api/v1/purposes?private=misroute-query-canary")
    copied_marker = client.get("/api/v1/purposes?prl_test=copy_marker")
    assert missing.status_code == copied_marker.status_code == 200
    assert missing.json()["role"] == copied_marker.json()["role"] == "frontend"
    for response in (missing, copied_marker):
        assert "x-prl-route" not in response.headers
        record, raw_logs = wait_record(base, env, response.headers["x-request-id"])
        assert record["upstream_identity"] == "frontend"
        assert record["route"] == "/api/v1/purposes"
        assert record["classification"] == "service_failure"
        assert record["upstream_outcome"] == "route_mismatch"
        assert "misroute-query-canary" not in raw_logs
        assert record["trace_id"] == ""


def test_wrong_host_same_port_with_matching_marker_is_a_misroute(gateway, tmp_path):
    client, _, base, env = gateway
    compose_file = Path(base[-1])
    merged = json.loads(compose_file.read_text())
    impostor = dict(merged["services"]["frontend"])
    impostor["entrypoint"] = ["python", "/test/upstream.py", "8000", "frontend"]
    impostor["command"] = []
    merged["services"]["api_impostor"] = impostor
    compose_file.write_text(json.dumps(merged))
    run(*base, "config", "--quiet", env=env)
    run(*base, "up", "-d", "--no-deps", "api_impostor", env=env)

    gateway_config = merged["services"]["gateway"]
    directory_mount = next(
        volume for volume in gateway_config["volumes"] if volume.get("target") == "/etc/nginx/prl"
    )
    copied = tmp_path / "nginx"
    shutil.copytree(directory_mount["source"], copied)
    routes = (copied / "routes.conf").read_text()
    routes = routes.replace(
        "proxy_pass http://172.30.34.3:8000;",
        "set $wrong_api http://api_impostor:8000;\n    proxy_pass $wrong_api;",
    )
    (copied / "routes.conf").write_text(routes)
    directory_mount["source"] = str(copied)
    compose_file.write_text(json.dumps(merged))
    run(*base, "config", "--quiet", env=env)
    run(*base, "up", "-d", "--no-deps", "--force-recreate", "gateway", env=env)
    run(*base, "exec", "-T", "gateway", "nginx", "-t", env=env)

    response = client.get(
        "/api/v1/purposes?prl_test=copy_marker",
        headers={"X-PRL-Route": "/api/v1/purposes"},
    )
    assert response.status_code == 200 and response.json()["role"] == "frontend"
    assert "x-prl-route" not in response.headers
    record, _ = wait_record(base, env, response.headers["x-request-id"])
    assert record["upstream_identity"] == "other"
    assert record["route"] == "/api/v1/purposes"
    assert record["upstream_outcome"] == "route_mismatch"
    assert record["classification"] == "service_failure"
    assert record["trace_id"] == ""


def test_real_gateway_healthcheck_emits_only_out_of_sli_heartbeat(gateway):
    client, _, base, env = gateway
    health = client.get("/health/gateway")
    assert health.status_code == 200
    health_record, _ = wait_record(base, env, health.headers["x-request-id"])
    assert health_record["event"] == "gateway.healthcheck"
    assert health_record["route"] == "/health/gateway"
    assert health_record["classification"] == "out_of_scope"

    business = client.get("/api/v1/purposes")
    assert business.status_code == 200
    business_record, _ = wait_record(base, env, business.headers["x-request-id"])
    assert business_record["event"] == "gateway.request"
    assert business_record["classification"] == "success"
    assert health_record["request_id"] != business_record["request_id"]


def test_healthcheck_heartbeat_uses_file_pipeline_without_business_sample(gateway, tmp_path):
    client, _, base, env = gateway
    configure_file_pipeline(gateway, tmp_path)

    response = client.get("/health/gateway")
    assert response.status_code == 200
    health_record, _ = wait_file_record(base, env, response.headers["x-request-id"])
    records, _ = gateway_file_records(base, env)
    assert health_record["event"] == "gateway.healthcheck"
    assert health_record["route"] == "/health/gateway"
    assert health_record["classification"] == "out_of_scope"
    assert records
    assert all(record["event"] == "gateway.healthcheck" for record in records)
    assert all(record["classification"] == "out_of_scope" for record in records)
    assert all(record["route"] == "/health/gateway" for record in records)


def test_collector_late_start_checkpoint_restart_and_rotation(
    gateway, tmp_path, collector_test_image
):
    client, _, base, env = gateway
    configure_file_pipeline(
        gateway,
        tmp_path,
        rotate_bytes=1800,
        rotate_files=4,
        collector=True,
        collector_image=collector_test_image,
    )

    early = [client.get("/health/gateway"), client.get("/api/v1/purposes")]
    assert [response.status_code for response in early] == [200, 200]
    early_ids = [response.headers["x-request-id"] for response in early]
    for request_id in early_ids:
        wait_file_record(base, env, request_id)

    run(*base, "up", "-d", "--no-deps", "collector", env=env)
    checkpoint_volume = collector_checkpoint_volume(base, env)
    output = wait_collector_requests(base, env, early_ids)
    assert all(collector_record_count(output, request_id) == 1 for request_id in early_ids)

    rotated_ids = []
    for index in range(12):
        path = (
            "/health/gateway"
            if index % 3 == 0
            else f"/api/v1/purposes?private=rotation-canary-{index}"
        )
        response = client.get(path)
        assert response.status_code == 200
        request_id = response.headers["x-request-id"]
        rotated_ids.append(request_id)
        wait_file_record(base, env, request_id)
        if len(rotated_ids) % 4 == 0:
            output = wait_collector_requests(base, env, rotated_ids[-4:])

    names = run(
        *base,
        "exec",
        "-T",
        "gateway",
        "sh",
        "-c",
        "ls -1 /var/log/prl/access.jsonl*",
        env=env,
    ).splitlines()
    assert any(name.endswith(".1") for name in names), names
    assert any(name.endswith(".2") for name in names), names
    spool_metadata = run(
        *base,
        "exec",
        "-T",
        "gateway",
        "sh",
        "-c",
        'stat -c "%a %u:%g %n" /var/log/prl /var/log/prl/access.jsonl*',
        env=env,
    ).splitlines()
    assert spool_metadata[0] == "1770 101:101 /var/log/prl", spool_metadata
    assert all(
        line.startswith("640 101:101 /var/log/prl/access.jsonl") for line in spool_metadata[1:]
    ), spool_metadata

    all_ids = early_ids + rotated_ids
    output = wait_collector_requests(base, env, all_ids)
    assert all(collector_record_count(output, request_id) == 1 for request_id in all_ids)
    run(*base, "restart", "-t", "2", "collector", env=env)
    assert collector_checkpoint_volume(base, env) == checkpoint_volume
    post_restart = client.get("/api/v1/purposes?private=collector-post-restart")
    assert post_restart.status_code == 200
    post_restart_id = post_restart.headers["x-request-id"]
    wait_file_record(base, env, post_restart_id)
    restarted_output = wait_collector_requests(base, env, [post_restart_id])
    assert all(collector_record_count(restarted_output, request_id) == 1 for request_id in all_ids)
    assert collector_record_count(restarted_output, post_restart_id) == 1


def test_spool_pressure_is_bounded_keeps_responses_and_recovers(gateway, tmp_path):
    client, _, base, env = gateway
    configure_file_pipeline(gateway, tmp_path, rotate_bytes=10 * 1024 * 1024)
    run(*base, "pause", "rotate_helper", env=env)

    fill = subprocess.run(
        [
            *base,
            "exec",
            "-T",
            "gateway",
            "sh",
            "-c",
            "dd if=/dev/zero of=/var/log/prl/cap-probe bs=1M count=64",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert fill.returncode != 0
    assert "No space left on device" in fill.stdout + fill.stderr
    filesystem = run(*base, "exec", "-T", "gateway", "df", "-Pk", "/var/log/prl", env=env)
    total_kib = int(filesystem.splitlines()[-1].split()[1])
    assert 64000 <= total_kib <= 65536, filesystem

    pressured = client.get("/health/gateway?private=spool-pressure-query-canary")
    assert pressured.status_code == 200
    pressure_record, stdout = wait_record(base, env, pressured.headers["x-request-id"])
    assert pressure_record["event"] == "gateway.healthcheck"
    assert pressure_record["classification"] == "out_of_scope"
    assert "spool-pressure-query-canary" not in stdout
    gateway_logs = subprocess.run(
        [*base, "logs", "--no-log-prefix", "gateway"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout
    assert "spool-pressure-query-canary" not in gateway_logs

    run(
        *base,
        "exec",
        "-T",
        "gateway",
        "rm",
        "/var/log/prl/cap-probe",
        env=env,
    )
    run(*base, "unpause", "rotate_helper", env=env)
    recovered = client.get("/health/gateway")
    assert recovered.status_code == 200
    record, file_output = wait_file_record(base, env, recovered.headers["x-request-id"])
    assert record["event"] == "gateway.healthcheck"
    assert record["classification"] == "out_of_scope"
    assert "spool-pressure-query-canary" not in file_output


def test_real_router_canonical_slash_redirect_is_excluded(real_backend_gateway):
    client, _, _, base, env, _ = real_backend_gateway
    response = client.get(
        "/api/v1/purposes/?private=canonical-slash-canary", follow_redirects=False
    )
    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/v1/purposes?private=canonical-slash-canary")
    assert "x-prl-route" not in response.headers
    assert "x-prl-outcome" not in response.headers
    record, raw_logs = wait_record(base, env, response.headers["x-request-id"])
    assert record["route"] == "/api/v1/purposes"
    assert record["upstream_identity"] == "api"
    assert record["upstream_outcome"] == "canonical_redirect"
    assert record["classification"] == "excluded"
    assert "canonical-slash-canary" not in raw_logs


def test_generated_gateway_route_map_matches_business_openapi():
    result = subprocess.run(
        [sys.executable, "tools/infra/generate_gateway_routes.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_notification_gateway_operations_use_finite_routes_and_redact_raw_values(
    real_backend_gateway,
):
    client, _, _, base, env, _ = real_backend_gateway
    notification_id = "00000000-0000-0000-0000-000000000001"
    query_value = "notification-query-canary"
    inbox_route = "/api/v1/notifications"
    read_route = "/api/v1/notifications/{notification_id}/read"
    read_path = f"/api/v1/notifications/{notification_id}/read"
    cases = (
        ("GET", inbox_route, None, inbox_route, False),
        ("POST", read_path, {}, read_route, False),
        ("GET", inbox_route + "/", None, inbox_route, True),
        ("POST", read_path + "/", {}, read_route, True),
    )

    for method, path, payload, expected_route, is_redirect in cases:
        request_path = f"{path}?private={query_value}"
        kwargs = {"json": payload} if payload is not None else {}
        response = client.request(method, request_path, follow_redirects=False, **kwargs)
        request_id = response.headers["x-request-id"]
        assert "x-prl-route" not in response.headers

        if is_redirect:
            canonical_path = path.rstrip("/")
            assert response.status_code == 307
            assert response.headers["location"].endswith(f"{canonical_path}?private={query_value}")
        else:
            assert 400 <= response.status_code < 500
            assert response.headers["cache-control"] == "private, no-store"
            assert response.json()["error"]["request_id"] == request_id

        record, raw_logs = wait_record(base, env, request_id)
        assert record["route"] == expected_route
        assert record["upstream_identity"] == "api"
        assert query_value not in raw_logs
        assert notification_id not in raw_logs
        if is_redirect:
            assert record["upstream_outcome"] == "canonical_redirect"
            assert record["classification"] == "excluded"
        else:
            assert record["classification"] == "excluded"
