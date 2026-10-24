"""Real bounded container checks for the opt-in observability deployment."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from tests.containers.test_compose_acceptance import (
    AcceptanceFailure,
    ComposeAcceptance,
    latest_worker_progress,
)
from tests.containers.test_compose_acceptance import (
    invoke as acceptance_invoke,
)
from tests.containers.test_compose_acceptance import (
    json_output as acceptance_json,
)
from tests.containers.test_compose_acceptance import (
    require_success as acceptance_success,
)

ROOT = Path(__file__).resolve().parents[2]
NGINX_IMAGE = (
    "nginx:stable-alpine" "@sha256:c59d1f8b1e0e42da75be973424cd72e23f7f8adfb54e982f4d1cd0216a5a5576"
)
GATEWAY_SUBNET = ipaddress.ip_network("172.30.34.0/29")
GATEWAY_API_IP = "172.30.34.3"
GATEWAY_IP = "172.30.34.2"
OWNER_LABEL = "org.prl.task6.owner"


def summarize_loki_signal(
    http_status: int | None,
    body: object,
    *,
    request_id: str,
    trace_id: str,
    expected_release: str,
) -> dict[str, object]:
    response = body if isinstance(body, dict) else {}
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    results = data.get("result") if isinstance(data.get("result"), list) else []
    streams = results[:100]
    rows: list[tuple[dict[str, object], dict[str, object]]] = []
    stream_label_keys: set[str] = set()
    field_keys: set[str] = set()
    finite_labels: set[tuple[str, str]] = set()
    entry_count = 0
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") if isinstance(stream.get("stream"), dict) else {}
        stream_label_keys.update(str(key) for key in labels)
        service = labels.get("service_name")
        environment = labels.get("deployment_environment_name")
        if service in {"api", "gateway", "worker"} and environment in {
            "demo",
            "development",
            "production",
        }:
            finite_labels.add((str(service), str(environment)))
        values = stream.get("values") if isinstance(stream.get("values"), list) else []
        for entry in values[:100]:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            entry_count += 1
            try:
                message = json.loads(entry[1]) if isinstance(entry[1], str) else {}
            except (json.JSONDecodeError, TypeError):
                message = {}
            fields = message if isinstance(message, dict) else {}
            metadata = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
            combined = {**fields, **metadata}
            field_keys.update(str(key) for key in combined)
            rows.append((labels, combined))

    request_match = any(fields.get("request_id") == request_id for _, fields in rows)
    trace_match = any(fields.get("trace_id") == trace_id for _, fields in rows)
    event_match = any(fields.get("event") == "gateway.request" for _, fields in rows)
    release_match = any(fields.get("release") == expected_release for _, fields in rows)
    trace_format = any(
        re.fullmatch(r"[a-f0-9]{32}", str(fields.get("trace_id", ""))) for _, fields in rows
    )
    finite_resource_identity_ok = any(
        labels.get("service_name") == "gateway"
        and labels.get("deployment_environment_name") == "demo"
        for labels, _ in rows
    )
    if http_status != 200:
        failure = "http_status"
    elif response.get("status") != "success":
        failure = "api_status"
    elif len(results) != 1 or entry_count != 1:
        failure = "event_cardinality"
    elif not finite_resource_identity_ok:
        failure = "finite_resource_identity_missing"
    elif not (request_match and event_match):
        failure = "request_event_missing"
    elif not (trace_match and trace_format):
        failure = "trace_correlation_missing"
    elif not release_match:
        failure = "release_missing"
    else:
        failure = None
    return {
        "http_status": http_status,
        "body_status_ok": response.get("status") == "success",
        "stream_count": len(results),
        "entry_count": entry_count,
        "query_result_stream_keys": sorted(stream_label_keys)[:40],
        "query_result_resource_identity": [
            {"service_name": service, "deployment_environment_name": environment}
            for service, environment in sorted(finite_labels)
        ],
        "structured_field_keys": sorted(field_keys)[:40],
        "request_id_match": request_match,
        "trace_id_match": trace_match,
        "trace_id_format_ok": trace_format,
        "event_match": event_match,
        "release_match": release_match,
        "failure_category": failure,
    }


def summarize_loki_index_series(
    http_status: int | None,
    body: object,
    *,
    expected_request_id: str,
) -> dict[str, object]:
    response = body if isinstance(body, dict) else {}
    series = response.get("data") if isinstance(response.get("data"), list) else []
    bounded_series = series[:1000]
    expected_keys = {"service_name", "deployment_environment_name"}
    key_sets: set[tuple[str, ...]] = set()
    identities: set[tuple[str, str]] = set()
    request_id_indexed = False
    schema_ok = bool(series) and len(series) <= 1000
    for labelset in bounded_series:
        if not isinstance(labelset, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in labelset.items()
        ):
            schema_ok = False
            continue
        keys = set(labelset)
        key_sets.add(tuple(sorted(keys)))
        service = labelset.get("service_name")
        environment = labelset.get("deployment_environment_name")
        if service in {
            "api",
            "gateway",
            "worker",
            "collector",
            "prometheus",
            "loki",
            "tempo",
            "grafana",
            "alertmanager",
            "local_alert_receiver",
        } and environment in {"demo", "local", "production", "development", "unknown"}:
            identities.add((str(service), str(environment)))
        if "request_id" in labelset or expected_request_id in labelset.values():
            request_id_indexed = True
        if keys != expected_keys or service != "gateway" or environment != "demo":
            schema_ok = False

    if http_status != 200:
        failure = "http_status"
    elif response.get("status") != "success":
        failure = "api_status"
    elif not isinstance(response.get("data"), list):
        failure = "invalid_response_shape"
    elif not series:
        failure = "empty_series"
    elif len(series) > 1000:
        failure = "series_count_limit"
    elif not schema_ok:
        failure = "index_schema"
    else:
        failure = None
    return {
        "http_status": http_status,
        "body_status_ok": response.get("status") == "success",
        "series_count": len(series),
        "index_label_keys": sorted({key for labels in key_sets for key in labels})[:40],
        "index_label_schemas": [list(labels) for labels in sorted(key_sets)[:16]],
        "index_resource_identity": [
            {"service_name": service, "deployment_environment_name": environment}
            for service, environment in sorted(identities)
        ],
        "index_schema_ok": schema_ok,
        "request_id_indexed": request_id_indexed,
        "failure_category": failure,
    }


def _tempo_trace_fields(value: object) -> tuple[list[str], dict[str, set[str]]]:
    trace_ids: list[str] = []
    resources: dict[str, set[str]] = {
        "service.name": set(),
        "deployment.environment.name": set(),
        "service.version": set(),
    }
    queue: list[tuple[object, int, bool]] = [(value, 0, False)]
    visited = 0
    while queue and visited < 10000:
        node, depth, is_span = queue.pop()
        visited += 1
        if depth > 16:
            continue
        if isinstance(node, dict):
            if is_span:
                for key in ("traceId", "traceID", "trace_id"):
                    item = node.get(key)
                    if isinstance(item, str):
                        trace_ids.append(item)
            for key, item in node.items():
                if key == "key" and item in resources:
                    raw_value = node.get("value")
                    if isinstance(raw_value, dict):
                        raw_value = raw_value.get("stringValue")
                    if isinstance(raw_value, str):
                        resources[item].add(raw_value)
                if isinstance(item, dict):
                    queue.append((item, depth + 1, False))
                elif isinstance(item, list):
                    in_span_list = key == "spans"
                    queue.extend(
                        (child, depth + 1, in_span_list)
                        for child in item[:1000]
                        if isinstance(child, (dict, list))
                    )
        elif isinstance(node, list):
            queue.extend(
                (item, depth + 1, is_span) for item in node[:1000] if isinstance(item, (dict, list))
            )
    finite_resources = {
        "service.name": {
            item for item in resources["service.name"] if item in {"api", "gateway", "worker"}
        },
        "deployment.environment.name": {
            item
            for item in resources["deployment.environment.name"]
            if item in {"demo", "development", "production"}
        },
        "service.version": resources["service.version"],
    }
    return trace_ids, finite_resources


def summarize_tempo_signal(
    http_status: int | None,
    body: object,
    expected_trace_id: str,
    *,
    expected_release: str | None = None,
) -> dict[str, object]:
    import base64

    response = body if isinstance(body, dict) else {}
    trace_ids, resources = _tempo_trace_fields(body)
    expected_hex = expected_trace_id.lower()
    try:
        expected_base64 = base64.b64encode(bytes.fromhex(expected_trace_id)).decode("ascii")
    except ValueError:
        expected_base64 = ""
    hex_match = any(item.lower() == expected_hex for item in trace_ids)
    base64_match = any(item == expected_base64 for item in trace_ids)
    if http_status != 200:
        failure = "http_status"
    elif not isinstance(body, dict):
        failure = "invalid_json_shape"
    elif not trace_ids:
        failure = "trace_id_field_missing"
    elif not (hex_match or base64_match):
        failure = "trace_id_not_found"
    else:
        failure = None
    return {
        "http_status": http_status,
        "top_level_keys": sorted(str(key) for key in response)[:40],
        "trace_id_field_count": len(trace_ids),
        "trace_id_hex_match": hex_match,
        "trace_id_base64_match": base64_match,
        "resource_services": sorted(resources["service.name"]),
        "resource_environments": sorted(resources["deployment.environment.name"]),
        "resource_release_match": (
            expected_release in resources["service.version"] if expected_release else None
        ),
        "failure_category": failure,
    }


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OBSERVABILITY_TESTS") != "1",
    reason="opt-in Docker observability acceptance",
)


def invoke(args: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def make_certificate(tmp_path: Path, hostname: str = "app.example.invalid") -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    filename = re.sub(r"[^A-Za-z0-9_-]", "_", hostname)
    cert_path = tmp_path / f"{filename}.crt"
    key_path = tmp_path / f"{filename}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _assert_test_subnet_is_unused() -> None:
    listed = invoke(["docker", "network", "ls", "-q"])
    assert listed.returncode == 0, "Docker network inventory was unavailable"
    for network_id in listed.stdout.splitlines():
        result = invoke(
            [
                "docker",
                "network",
                "inspect",
                "--format",
                "{{range .IPAM.Config}}{{println .Subnet}}{{end}}",
                network_id,
            ]
        )
        assert result.returncode == 0, "Docker network subnet inventory was incomplete"
        for value in result.stdout.splitlines():
            if not value:
                continue
            assert not GATEWAY_SUBNET.overlaps(
                ipaddress.ip_network(value)
            ), "the fixed gateway fixture subnet overlaps an existing Docker network"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _LocalTLSConnection(http.client.HTTPSConnection):
    """Connect to loopback while preserving the requested TLS verification name."""

    def connect(self) -> None:
        raw_socket = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _https_probe(port: int, cert: Path) -> tuple[int, bytes]:
    context = ssl.create_default_context(cafile=str(cert))
    connection = _LocalTLSConnection("app.example.invalid", port, timeout=2, context=context)
    try:
        connection.request("GET", "/health/ready", headers={"Host": "app.example.invalid"})
        response = connection.getresponse()
        return response.status, response.read(4096)
    finally:
        connection.close()


def _wait_https_ready(port: int, cert: Path, timeout: float = 15) -> tuple[int, bytes] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, body = _https_probe(port, cert)
            if status == 200:
                return status, body
        except (OSError, ssl.SSLError, http.client.HTTPException):
            pass
        time.sleep(0.2)
    return None


def _inspect_owned_container(name: str, token: str) -> dict[str, object] | None:
    inspected = invoke(["docker", "inspect", name])
    if inspected.returncode:
        return None
    records = json.loads(inspected.stdout)
    if len(records) != 1:
        return None
    record = records[0]
    labels = record.get("Config", {}).get("Labels") or {}
    if record.get("Name") != "/" + name or labels.get(OWNER_LABEL) != token:
        return None
    return record


def _remove_owned_container(name: str, token: str) -> None:
    record = _inspect_owned_container(name, token)
    if record is None:
        return
    if record.get("State", {}).get("Running"):
        invoke(["docker", "stop", "--timeout", "0", name])
    invoke(["docker", "rm", name])


def _start_gateway(
    *,
    name: str,
    token: str,
    network: str,
    port: int,
    certificate: Path,
    key: Path,
    entrypoint: Path,
) -> subprocess.CompletedProcess[str]:
    deploy_nginx = ROOT / "tools/deploy/nginx"
    return invoke(
        [
            "docker",
            "run",
            "--pull=never",
            "--detach",
            "--name",
            name,
            "--label",
            f"{OWNER_LABEL}={token}",
            "--network",
            network,
            "--ip",
            GATEWAY_IP,
            "--user",
            "101:101",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
            "--tmpfs",
            "/var/log/prl:rw,nosuid,nodev,noexec,size=2m,uid=101,gid=101,mode=1770",
            "--publish",
            f"127.0.0.1:{port}:8443",
            "-e",
            "PRL_GATEWAY_ENVIRONMENT=demo",
            "-e",
            "SERVICE_RELEASE=" + "a" * 40,
            "-e",
            "PRL_OPS_HOST=ops.example.invalid",
            "-e",
            "PRL_OPS_CIDRS=192.0.2.5/32",
            "-v",
            f"{deploy_nginx / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
            "-v",
            f"{deploy_nginx}:/etc/nginx/prl:ro",
            "-v",
            f"{deploy_nginx / 'https.conf'}:/etc/nginx/server.conf:ro",
            "-v",
            f"{entrypoint}:/usr/local/sbin/prl-gateway-entrypoint:ro",
            "-v",
            f"{certificate}:/etc/nginx/tls/server.crt:ro",
            "-v",
            f"{key}:/etc/nginx/tls/server.key:ro",
            "-v",
            f"{certificate}:/etc/nginx/tls/ops.crt:ro",
            "-v",
            f"{key}:/etc/nginx/tls/ops.key:ro",
            NGINX_IMAGE,
            "/bin/sh",
            "/usr/local/sbin/prl-gateway-entrypoint",
        ],
        timeout=45,
    )


def _wait_container_exit(name: str, token: str, timeout: float = 15) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = _inspect_owned_container(name, token)
        if record is None:
            return None
        state = record.get("State", {})
        if state.get("Status") in {"exited", "dead"}:
            return int(state.get("ExitCode", -1))
        time.sleep(0.2)
    return None


def test_gateway_readiness_requires_real_https_response_without_grafana_dns(
    tmp_path: Path,
) -> None:
    """Prove app traffic works, and prove the old literal Grafana proxy blocks startup."""
    token = uuid4().hex
    prefix = f"prl-task6-opsdns-{token}"
    network = prefix
    api_name = f"{prefix}-api"
    gateway_name = f"{prefix}-gateway"
    old_gateway_name = f"{prefix}-literal"
    owner = f"{OWNER_LABEL}={token}"
    cert, key = make_certificate(tmp_path)
    entrypoint = ROOT / "tools/deploy/nginx/gateway-entrypoint.sh"
    original = entrypoint.read_text(encoding="utf-8")
    dynamic_lines = (
        "        set $prl_ops_upstream http://grafana:3000;\n"
        "        proxy_pass $prl_ops_upstream;"
    )
    assert original.count(dynamic_lines) == 1, "the dynamic Grafana proxy seam changed"
    old_literal = tmp_path / "old-literal-entrypoint.sh"
    old_literal.write_bytes(
        original.replace(
            dynamic_lines,
            "        proxy_pass http://grafana:3000;",
            1,
        ).encode("utf-8")
    )
    api_config = tmp_path / "api-fixture.conf"
    api_config.write_text(
        "pid /tmp/nginx.pid;\n"
        "error_log /dev/stderr emerg;\n"
        "events { worker_connections 32; }\n"
        "http { server { listen 8000; default_type application/json; "
        'location = /health/ready { return 200 \'{"status":"ready"}\'; } } }\n',
        encoding="utf-8",
    )
    ids = invoke(["docker", "ps", "-aq", "--filter", f"name=^{api_name}$"])
    assert ids.returncode == 0 and not ids.stdout.strip()
    assert invoke(["docker", "image", "inspect", NGINX_IMAGE]).returncode == 0
    _assert_test_subnet_is_unused()

    network_created = False
    try:
        created = invoke(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--subnet",
                str(GATEWAY_SUBNET),
                "--label",
                owner,
                network,
            ]
        )
        assert created.returncode == 0, "the exact UUID-owned gateway fixture network failed"
        network_created = True

        api_started = invoke(
            [
                "docker",
                "run",
                "--pull=never",
                "--detach",
                "--name",
                api_name,
                "--label",
                owner,
                "--network",
                network,
                "--ip",
                GATEWAY_API_IP,
                "--user",
                "101:101",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "64m",
                "--memory-swap",
                "64m",
                "--cpus",
                "0.25",
                "--pids-limit",
                "32",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=4m,uid=101,gid=101,mode=0700",
                "--tmpfs",
                "/var/cache/nginx:rw,nosuid,nodev,noexec,size=4m,uid=101,gid=101,mode=0700",
                "-v",
                f"{api_config}:/etc/nginx/nginx.conf:ro",
                "--entrypoint",
                "nginx",
                NGINX_IMAGE,
                "-g",
                "daemon off;",
            ]
        )
        assert api_started.returncode == 0, "the bounded fixture API did not start"

        good_port = _free_loopback_port()
        good_started = _start_gateway(
            name=gateway_name,
            token=token,
            network=network,
            port=good_port,
            certificate=cert,
            key=key,
            entrypoint=entrypoint,
        )
        assert good_started.returncode == 0, "the pinned gateway fixture did not launch"
        ready = _wait_https_ready(good_port, cert)
        if ready != (200, b'{"status":"ready"}'):
            gateway_state = _inspect_owned_container(gateway_name, token)
            api_state = _inspect_owned_container(api_name, token)
            gateway_logs = invoke(["docker", "logs", gateway_name])
            api_logs = invoke(["docker", "logs", api_name])
            pytest.fail(
                "verified HTTPS application readiness did not succeed through the gateway; "
                f"probe={ready!r}, gateway_state="
                f"{(gateway_state or {}).get('State')!r}, api_state="
                f"{(api_state or {}).get('State')!r}, "
                f"gateway_log_tail={gateway_logs.stdout[-2000:]!r}, "
                f"api_log_tail={api_logs.stdout[-2000:]!r}, "
                f"api_stderr_tail={api_logs.stderr[-2000:]!r}"
            )
        _remove_owned_container(gateway_name, token)

        old_port = _free_loopback_port()
        old_started = _start_gateway(
            name=old_gateway_name,
            token=token,
            network=network,
            port=old_port,
            certificate=cert,
            key=key,
            entrypoint=old_literal,
        )
        assert old_started.returncode == 0, "the old-literal control fixture did not launch"
        old_exit = _wait_container_exit(old_gateway_name, token)
        assert old_exit not in (
            None,
            0,
        ), "the negative control did not detect the literal Grafana DNS dependency"
        old_logs = invoke(["docker", "logs", old_gateway_name])
        assert (
            old_logs.returncode == 0 and "grafana" in (old_logs.stdout + old_logs.stderr).lower()
        ), "the literal proxy control did not fail while resolving Grafana"
    finally:
        for name in (old_gateway_name, gateway_name, api_name):
            _remove_owned_container(name, token)
        if network_created:
            inspected = invoke(
                [
                    "docker",
                    "network",
                    "inspect",
                    "--format",
                    '{{index .Labels "org.prl.task6.owner"}}|{{.Name}}',
                    network,
                ]
            )
            if inspected.returncode == 0 and inspected.stdout.strip() == f"{token}|{network}":
                invoke(["docker", "network", "rm", network])


FULL_STACK_FLAGS = (
    "RUN_CONTAINER_TESTS",
    "RUN_COMPOSE_TESTS",
    "RUN_GATEWAY_TESTS",
    "RUN_COMPOSE_ACCEPTANCE",
    "RUN_OBSERVABILITY_TESTS",
)


def _full_stack_enabled() -> bool:
    return all(os.environ.get(name) == "1" for name in FULL_STACK_FLAGS)


def _https_request(
    hostname: str, port: int, certificate: Path, method: str, path: str
) -> tuple[int, dict[str, str], bytes]:
    context = ssl.create_default_context(cafile=str(certificate))
    connection = _LocalTLSConnection(hostname, port, timeout=4, context=context)
    try:
        connection.request(method, path, headers={"Host": hostname, "Connection": "close"})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read(1_000_000)
    finally:
        connection.close()


class FullStackRuntime:
    """One bounded UUID-owned Compose run covering the Task 6 wire-up."""

    def __init__(self, scratch: Path) -> None:
        self.app_host = "app.example.invalid"
        self.stack = ComposeAcceptance(scratch, origin_scheme="https", origin_host=self.app_host)
        token = os.environ.get("PRL_TASK6_ACCEPTANCE_TOKEN", "")
        if token:
            if not re.fullmatch(r"[0-9a-f]{32}", token):
                raise AcceptanceFailure("The runtime acceptance token is invalid")
            self.stack.project = "prl-task6-" + token
            self.stack.image_tag = "task6-" + token[:20]
            self.stack.env["PRL_IMAGE_TAG"] = self.stack.image_tag
            self.stack.volume_name = f"{self.stack.project}_mysql_data"
            self.stack._ensure_unique_ownership()
            self.stack._ensure_gateway_subnet_available()

        self.port = self.stack.port
        self.ops_host = "ops.example.invalid"
        self.app_origin = self.stack.origin
        self.ops_origin = f"https://{self.ops_host}:{self.port}"
        self.app_cert, self.app_key = make_certificate(scratch, self.app_host)
        self.ops_cert, self.ops_key = make_certificate(scratch, self.ops_host)
        self.admin_user = "task6-admin"
        self.admin_password = "synthetic-" + uuid4().hex
        self.admin_user_file = scratch / "grafana-admin-user"
        self.admin_password_file = scratch / "grafana-admin-password"
        self.admin_user_file.write_text(self.admin_user + "\n", encoding="utf-8")
        self.admin_password_file.write_text(self.admin_password + "\n", encoding="utf-8")

        deploy = ROOT / "tools/deploy"
        self.stack.base = [
            "docker",
            "compose",
            "--project-name",
            self.stack.project,
            "-f",
            str(deploy / "compose.yaml"),
            "-f",
            str(deploy / "compose.https.yaml"),
            "-f",
            str(deploy / "compose.observability.yaml"),
        ]
        self.stack.env.update(
            PRL_HTTPS_BIND="127.0.0.1",
            PRL_HTTPS_PORT=str(self.port),
            PRL_HTTPS_ORIGIN=self.app_origin,
            PRL_TLS_CERT_FILE=str(self.app_cert.resolve()),
            PRL_TLS_KEY_FILE=str(self.app_key.resolve()),
            SERVICE_RELEASE=self.stack.expected_sha,
            PRL_OPS_HOST=self.ops_host,
            PRL_OPS_ORIGIN=self.ops_origin,
            PRL_OPS_CIDRS="127.0.0.1/32",
            PRL_OPS_TLS_CERT_FILE=str(self.ops_cert.resolve()),
            PRL_OPS_TLS_KEY_FILE=str(self.ops_key.resolve()),
            PRL_GRAFANA_ADMIN_USER_FILE=str(self.admin_user_file.resolve()),
            PRL_GRAFANA_ADMIN_PASSWORD_FILE=str(self.admin_password_file.resolve()),
            PRL_EXPECTED_WORKER_SLOTS="1",
            DOCKER_DEFAULT_PLATFORM="linux/amd64",
        )
        self.probe_file = scratch / "task6-probes.compose.yaml"
        self.api_id = ""
        self.target_info_schema: list[list[str]] = []
        self.heartbeat_observations: list[dict[str, object]] = []
        self.observed_worker_id: str | None = None
        self.worker_stream_diagnostic: dict[str, object] | None = None

    def wait_app_gateway(self) -> None:
        deadline = time.monotonic() + 90
        last = {
            "gateway": {"status": None, "body_status": None, "error_class": None},
            "api": {"status": None, "body_status": None, "error_class": None},
        }
        while time.monotonic() < deadline:
            for name, path in (
                ("gateway", "/health/gateway"),
                ("api", "/health/ready"),
            ):
                try:
                    status, _headers, body = _https_request(
                        self.app_host, self.port, self.app_cert, "GET", path
                    )
                    try:
                        body_status = json.loads(body).get("status")
                    except (json.JSONDecodeError, AttributeError):
                        body_status = None
                    last[name] = {
                        "status": status,
                        "body_status": body_status,
                        "error_class": None,
                    }
                except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                    last[name] = {
                        "status": None,
                        "body_status": None,
                        "error_class": type(exc).__name__,
                    }
            if last["gateway"]["status"] == 200 and last["api"]["status"] == 200:
                return
            time.sleep(0.5)
        raise AcceptanceFailure(
            "Verified HTTPS gateway/API readiness did not complete: "
            + json.dumps(last, sort_keys=True, separators=(",", ":"))
        )

    def compose(self, *args: str, timeout: float = 180):
        return self.stack.compose(*args, timeout=timeout)

    def api_json(self, url: str, *, method: str = "GET", body: object | None = None):
        script = (
            "import json,sys,urllib.request; "
            "url,method,raw=sys.argv[1:4]; "
            "data=raw.encode() if raw else None; "
            "req=urllib.request.Request(url,data=data,method=method,"
            "headers={'Content-Type':'application/json'} if data else {}); "
            "res=urllib.request.urlopen(req,timeout=5); "
            "print(json.dumps(json.loads(res.read()),separators=(',',':')))"
        )
        result = acceptance_invoke(
            [
                "docker",
                "exec",
                self.api_id,
                "python",
                "-c",
                script,
                url,
                method,
                json.dumps(body, separators=(",", ":")) if body is not None else "",
            ],
            timeout=15,
        )
        if result.returncode:
            raise AcceptanceFailure("A bounded in-stack telemetry API request failed")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise AcceptanceFailure("A telemetry API returned invalid JSON") from None

    def api_json_observation(self, url: str) -> dict[str, object]:
        script = (
            "import json,sys,urllib.request,urllib.error\n"
            "url=sys.argv[1]; status=None; raw=b''; error=None\n"
            "try:\n"
            " response=urllib.request.urlopen(url,timeout=5)\n"
            " status=response.status; raw=response.read(1048577)\n"
            "except urllib.error.HTTPError as exc:\n"
            " status=exc.code; raw=exc.read(1048577)\n"
            "except (OSError,urllib.error.URLError,TimeoutError):\n"
            " error='TransportError'\n"
            "truncated=len(raw)>1048576; raw=raw[:1048576]\n"
            "try:\n"
            " body=json.loads(raw); valid=True\n"
            "except (ValueError,UnicodeDecodeError):\n"
            " body=None; valid=False\n"
            "print(json.dumps({'http_status':status,'body':body,'json_valid':valid,'truncated':truncated,'error_category':error},separators=(',',':')))"
        )
        try:
            result = acceptance_invoke(
                ["docker", "exec", self.api_id, "python", "-c", script, url], timeout=15
            )
        except (OSError, subprocess.SubprocessError):
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "SubprocessError",
            }
        if result.returncode:
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "SubprocessError",
            }
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "InvalidEnvelope",
            }
        if not isinstance(envelope, dict):
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "InvalidEnvelope",
            }
        return envelope

    def observe_loki_request(self, request_id: str, trace_id: str) -> dict[str, object]:
        query = (
            '{service_name="gateway",deployment_environment_name="demo"} '
            f'| request_id="{request_id}"'
        )
        now_ns = time.time_ns()
        params = urlencode(
            {
                "query": query,
                "start": str(now_ns - 600_000_000_000),
                "end": str(now_ns + 60_000_000_000),
                "limit": "20",
            }
        )
        observation = self.api_json_observation(
            "http://loki:3100/loki/api/v1/query_range?" + params
        )
        summary = summarize_loki_signal(
            observation.get("http_status"),
            observation.get("body"),
            request_id=request_id,
            trace_id=trace_id,
            expected_release=self.stack.expected_sha,
        )
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
        return summary

    def observe_loki_index_series(self, *, expected_request_id: str) -> dict[str, object]:
        now_ns = time.time_ns()
        params = urlencode(
            {
                "match[]": '{service_name="gateway",deployment_environment_name="demo"}',
                "start": str(now_ns - 604_800_000_000_000),
                "end": str(now_ns),
            }
        )
        observation = self.api_json_observation("http://loki:3100/loki/api/v1/series?" + params)
        summary = summarize_loki_index_series(
            observation.get("http_status"),
            observation.get("body"),
            expected_request_id=expected_request_id,
        )
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
        return summary

    def observe_tempo_trace(self, trace_id: str) -> dict[str, object]:
        observation = self.api_json_observation(f"http://tempo:3200/api/traces/{trace_id}")
        summary = summarize_tempo_signal(
            observation.get("http_status"),
            observation.get("body"),
            trace_id,
            expected_release=self.stack.expected_sha,
        )
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
        return summary

    def _collector_counter_snapshot(self) -> dict[str, object]:
        expected_names = {
            f"otelcol_{stage}_{kind}"
            for stage in (
                "receiver_accepted",
                "receiver_refused",
                "exporter_sent",
                "exporter_send_failed",
            )
            for kind in ("log_records", "spans")
        }
        selector = (
            '{__name__=~"otelcol_(receiver_(accepted|refused)|'
            'exporter_(sent|send_failed))_(log_records|spans)"}'
        )
        query = "http://prometheus:9090/api/v1/query?" + urlencode({"query": selector})
        observation = self.api_json_observation(query)
        body = observation.get("body")
        response = body if isinstance(body, dict) else {}
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        series = data.get("result") if isinstance(data.get("result"), list) else []
        grouped: dict[str, list[dict[str, object]]] = {name: [] for name in expected_names}
        finite_values = {
            "receiver": {"otlp", "filelog/gateway"},
            "exporter": {"otlphttp/loki", "otlp/tempo"},
            "data_type": {"logs", "spans"},
        }
        for sample in series[:1000]:
            if not isinstance(sample, dict):
                continue
            labels = sample.get("metric") if isinstance(sample.get("metric"), dict) else {}
            name = labels.get("__name__")
            if name not in grouped:
                continue
            raw_sample = sample.get("value")
            try:
                number = float(raw_sample[1]) if isinstance(raw_sample, (list, tuple)) else 0.0
            except (ValueError, TypeError, IndexError):
                number = 0.0
            safe_dimensions = {
                key: value
                for key, value in labels.items()
                if key in finite_values and value in finite_values[key]
            }
            grouped[name].append(
                {
                    "value": number,
                    "label_keys": sorted(str(key) for key in labels)[:40],
                    "finite_dimensions": safe_dimensions,
                }
            )
        metrics = []
        for name in sorted(expected_names):
            samples = grouped[name]
            dimension_rows = {
                json.dumps(item["finite_dimensions"], sort_keys=True, separators=(",", ":"))
                for item in samples
            }
            metrics.append(
                {
                    "name": name,
                    "series_count": len(samples),
                    "value_sum": round(sum(float(item["value"]) for item in samples), 6),
                    "label_keys": sorted({key for item in samples for key in item["label_keys"]})[
                        :40
                    ],
                    "finite_dimensions": [json.loads(item) for item in sorted(dimension_rows)],
                }
            )
        return {
            "http_status": observation.get("http_status"),
            "prometheus_api_status_ok": response.get("status") == "success",
            "query_series_count": len(series),
            "metrics": metrics,
            "error_category": observation.get("error_category"),
            "json_valid": observation.get("json_valid"),
            "body_truncated": observation.get("truncated"),
        }

    def signal_boundary_diagnostic(
        self,
        *,
        route_status: int | None,
        request_id: str,
        trace_id: str,
        sink_observations: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        try:
            api_record = self.inspect_service("api")
            environment = (api_record.get("Config") or {}).get("Env") or []
            config = dict(
                item.split("=", 1)
                for item in environment
                if isinstance(item, str)
                and "=" in item
                and item.split("=", 1)[0]
                in {
                    "TELEMETRY_ENABLED",
                    "TELEMETRY_OTLP_ENDPOINT",
                    "SERVICE_RELEASE",
                    "ENVIRONMENT",
                }
            )
        except Exception:
            config = {}
        endpoint = urlparse(config.get("TELEMETRY_OTLP_ENDPOINT", ""))
        api_config = {
            "telemetry_enabled": config.get("TELEMETRY_ENABLED") == "true",
            "otlp_endpoint_host_is_collector": endpoint.hostname == "collector",
            "environment_is_demo": config.get("ENVIRONMENT") == "demo",
            "release_matches_source": config.get("SERVICE_RELEASE") == self.stack.expected_sha,
        }
        services: dict[str, dict[str, object]] = {}
        for name in (
            "api",
            "worker",
            "frontend",
            "gateway",
            "collector",
            "prometheus",
            "loki",
            "tempo",
            "grafana",
            "alertmanager",
            "local_alert_receiver",
            "gateway_log_rotate",
        ):
            try:
                record = self.inspect_service(name)
                state = record.get("State") or {}
                services[name] = {
                    "status": state.get("Status"),
                    "restart_count": record.get("RestartCount"),
                    "oom_killed": bool(state.get("OOMKilled")),
                    "exit_code": state.get("ExitCode"),
                    "error_present": bool(state.get("Error")),
                    "health": (state.get("Health") or {}).get("Status"),
                }
            except Exception:
                services[name] = {"inspection_available": False}
        return {
            "route": {
                "http_status": route_status,
                "request_id_present": bool(request_id),
                "request_id_format_ok": bool(re.fullmatch(r"[a-f0-9]{32}", request_id)),
                "trace_id_present": bool(trace_id),
                "trace_id_format_ok": bool(re.fullmatch(r"[a-f0-9]{32}", trace_id)),
            },
            "api_config": api_config,
            "loki_query": sink_observations.get("loki_query", {"failure_category": "not_observed"}),
            "loki_index_series": sink_observations.get(
                "loki_index_series", {"failure_category": "not_observed"}
            ),
            "tempo": sink_observations.get("tempo", {"failure_category": "not_observed"}),
            "collector_counters": self._safe_collector_counter_snapshot(),
            "services": services,
        }

    def _safe_collector_counter_snapshot(self) -> dict[str, object]:
        try:
            return self._collector_counter_snapshot()
        except Exception:
            return {"available": False}

    def volume_snapshot(self) -> dict[str, dict[str, object]]:
        listed = acceptance_success(
            acceptance_invoke(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={self.stack.project}",
                ]
            ),
            "The UUID-owned Compose volumes could not be listed",
        )
        names = [name for name in listed.splitlines() if name]
        records = (
            acceptance_json(
                acceptance_invoke(["docker", "volume", "inspect", *names]),
                "The UUID-owned volume identities could not be read",
            )
            if names
            else []
        )
        if len(records) != len(names):
            raise AcceptanceFailure("A UUID-owned Compose volume identity is missing")
        result = {}
        for item in records:
            labels = item.get("Labels") or {}
            if labels.get("com.docker.compose.project") != self.stack.project:
                raise AcceptanceFailure("A volume failed the UUID ownership guard")
            result[item["Name"]] = {
                "driver": item.get("Driver"),
                "mountpoint": item.get("Mountpoint"),
                "options": item.get("Options") or {},
                "labels": labels,
                "created": item.get("CreatedAt"),
            }
        return result

    def inspect_service(self, service: str) -> dict[str, object]:
        identifier = acceptance_success(
            self.compose("ps", "-q", service), f"The UUID-owned {service} container is missing"
        )
        records = acceptance_json(
            acceptance_invoke(["docker", "inspect", identifier]),
            f"The UUID-owned {service} container could not be inspected",
        )
        if len(records) != 1 or (
            records[0].get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
            != self.stack.project
        ):
            raise AcceptanceFailure(f"The {service} container failed its exact owner check")
        return records[0]

    def wait_targets(self) -> list[dict[str, str]]:
        expected = {
            "api",
            "collector",
            "application-otel",
            "local-alert-receiver",
            "prometheus-self",
            "loki-self",
            "tempo-self",
            "grafana-self",
            "alertmanager-self",
        }
        observed = []

        def ready() -> bool:
            nonlocal observed
            response = self.api_json("http://prometheus:9090/api/v1/targets?state=active")
            observed = response.get("data", {}).get("activeTargets", [])
            up = {
                target.get("labels", {}).get("job")
                for target in observed
                if target.get("health") == "up"
            }
            return up == expected and len(observed) == len(expected)

        self.stack.wait_for(
            ready, seconds=90, failure="The complete Prometheus target matrix did not become UP"
        )
        return [
            {
                "job": target["labels"]["job"],
                "health": target["health"],
                "instance": target["labels"]["instance"],
            }
            for target in sorted(observed, key=lambda item: item["labels"]["job"])
        ]

    def worker_metric_state(self):
        response = self.api_json("http://prometheus:9090/api/v1/label/__name__/values")
        names = sorted(
            name
            for name in response.get("data", [])
            if isinstance(name, str) and name.startswith("prl_worker_")
        )
        roles = {
            "started": "started_timestamp_seconds",
            "heartbeat": "heartbeat_timestamp_seconds",
            "last_successful_scan": "last_successful_scan_timestamp_seconds",
            "last_progress": "last_progress_timestamp_seconds",
            "backlog_count": "backlog_count",
            "successful_scans": "successful_scans_total",
        }
        selected = {
            role: next((name for name in names if fragment in name), "")
            for role, fragment in roles.items()
        }
        selected = {role: name for role, name in selected.items() if name}
        state = {}
        for role, metric_name in selected.items():
            response = self.api_json(
                "http://prometheus:9090/api/v1/query?" + urlencode({"query": metric_name})
            )
            samples = response.get("data", {}).get("result", [])
            candidates = [
                sample
                for sample in samples
                if sample.get("metric", {}).get("service_name") == "worker"
                and sample.get("metric", {}).get("deployment_environment_name") == "demo"
                and sample.get("metric", {}).get("worker_slot") == "1"
            ]
            if candidates:
                state[role] = {
                    "name": metric_name,
                    "labels": candidates[0]["metric"],
                    "value": float(candidates[0]["value"][1]),
                }
        return selected, state

    def worker_series_schema(self) -> list[dict[str, object]]:
        response = self.api_json(
            "http://prometheus:9090/api/v1/series?"
            + urlencode({"match[]": '{__name__=~"prl_worker_.*"}'})
        )
        forbidden = {
            "request_id",
            "trace_id",
            "worker_id",
            "run_id",
            "current_run_id",
            "release",
            "source_sha",
            "uuid",
            "service_instance_id",
        }
        series = []
        for labels in response.get("data", []):
            keys = set(labels)
            if keys & forbidden:
                raise AcceptanceFailure("A worker metric exposed an unbounded identity label")
            if (
                labels.get("service_name") != "worker"
                or labels.get("deployment_environment_name") != "demo"
                or labels.get("worker_slot") != "1"
            ):
                raise AcceptanceFailure(
                    "A worker metric lost its finite service/environment/slot labels"
                )
            series.append(
                {
                    "name": labels.get("__name__"),
                    "labels": sorted(keys),
                    "service_name": labels["service_name"],
                    "deployment_environment_name": labels["deployment_environment_name"],
                    "worker_slot": labels["worker_slot"],
                }
            )
        if not series:
            raise AcceptanceFailure("The Collector exposed no worker metric series")
        target_info = self.api_json(
            "http://prometheus:9090/api/v1/series?"
            + urlencode({"match[]": '{__name__="target_info"}'})
        ).get("data", [])
        target_forbidden = forbidden | {"service_instance_id", "service_version", "service_id"}
        target_info_labels = []
        for labels in target_info:
            keys = set(labels)
            if keys & target_forbidden:
                raise AcceptanceFailure("Collector target_info exposed an unbounded identity")
            target_info_labels.append(sorted(keys))
        self.target_info_schema = target_info_labels
        return sorted(series, key=lambda item: str(item["name"]))

    def query_loki_request(self, request_id: str, trace_id: str) -> dict[str, object]:
        query = self.observe_loki_request(request_id, trace_id)
        index = self.observe_loki_index_series(expected_request_id=request_id)
        if query.get("failure_category") is not None:
            raise AcceptanceFailure("A gateway request did not produce one correlated Loki event")
        if index.get("failure_category") is not None:
            raise AcceptanceFailure("Loki gateway series did not retain the finite index schema")
        return {
            "query_result_stream_keys": query.get("query_result_stream_keys", []),
            "query_result_resource_identity": query.get("query_result_resource_identity", []),
            "structured_field_keys": query.get("structured_field_keys", []),
            "event": "gateway.request",
            "request_id_match": query.get("request_id_match") is True,
            "trace_id_format_ok": query.get("trace_id_format_ok") is True,
            "release_match": query.get("release_match") is True,
            "index_label_keys": index.get("index_label_keys", []),
            "index_resource_identity": index.get("index_resource_identity", []),
            "index_series_count": index.get("series_count"),
            "request_id_indexed": index.get("request_id_indexed") is True,
        }

    def query_loki_healthchecks(self) -> dict[str, object]:
        query = (
            '{service_name="gateway",deployment_environment_name="demo"} '
            '| event="gateway.healthcheck"'
        )
        now_ns = time.time_ns()
        params = urlencode(
            {
                "query": query,
                "start": str(now_ns - 600_000_000_000),
                "end": str(now_ns + 60_000_000_000),
                "limit": "100",
            }
        )
        response = self.api_json("http://loki:3100/loki/api/v1/query_range?" + params)
        events = []
        for stream in response.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for entry in stream.get("values", []):
                try:
                    body = json.loads(entry[1])
                except (TypeError, json.JSONDecodeError, IndexError):
                    body = {}
                metadata = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
                fields = {**body, **metadata}
                if (
                    labels.get("service_name") != "gateway"
                    or labels.get("deployment_environment_name") != "demo"
                    or fields.get("event") != "gateway.healthcheck"
                    or fields.get("classification") != "out_of_scope"
                ):
                    raise AcceptanceFailure("A Loki healthcheck lost its finite event contract")
                events.append((int(entry[0]), fields))
        events.sort(key=lambda event: event[0])
        if len(events) < 2:
            raise AcceptanceFailure("Loki did not receive recurring genuine gateway healthchecks")
        interval = (events[-1][0] - events[-2][0]) / 1_000_000_000
        if not 1 <= interval <= 15:
            raise AcceptanceFailure("The Loki gateway healthcheck heartbeat cadence changed")
        return {
            "event": "gateway.healthcheck",
            "classification": "out_of_scope",
            "labels": {"service_name": "gateway", "deployment_environment_name": "demo"},
            "observed_events": len(events),
            "interval_seconds": round(interval, 2),
        }

    def write_probe_compose(self) -> None:
        gateway = self.inspect_service("gateway")
        networks = gateway.get("NetworkSettings", {}).get("Networks", {})
        ops_networks = [
            (name, values.get("IPAddress"))
            for name, values in networks.items()
            if name.endswith("_ops_grafana")
        ]
        if len(ops_networks) != 1 or not ops_networks[0][1]:
            raise AcceptanceFailure("The UUID-owned operations network identity is unavailable")
        _ops_network, gateway_ip = ops_networks[0]
        positive_script = """import base64
import http.client
import json
import socket
import ssl

class Connection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)

context = ssl.create_default_context(cafile="/run/prl/ops.crt")
user = open("/run/prl/admin-user", encoding="utf-8").read().strip()
password = open("/run/prl/admin-password", encoding="utf-8").read().strip()
credential = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()

def request(path, authenticated=False):
    connection = Connection("ops.example.invalid", 8443, timeout=4, context=context)
    headers = {"Host": "ops.example.invalid", "Connection": "close"}
    if authenticated:
        headers["Authorization"] = credential
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    status = response.status
    raw = response.read()
    connection.close()
    try:
        return status, json.loads(raw)
    except Exception:
        return status, {}

health, _ = request("/api/health")
unauthenticated, _ = request("/api/org")
admin_status, _ = request("/api/org", True)
datasource_status, datasources = request("/api/datasources", True)
ops_business, _ = request("/api/v1/health-shaped", False)
ops_health, _ = request("/health/gateway", False)
print("TASK6_PROBE:" + json.dumps({
    "health": health,
    "unauth_org": unauthenticated,
    "admin_org": admin_status,
    "datasource_status": datasource_status,
    "datasource_uids": sorted(item.get("uid", "") for item in datasources),
    "ops_business_status": ops_business,
    "ops_health_status": ops_health,
}, separators=(",", ":")))
"""
        negative_script = """import http.client
import json
import ssl

context = ssl.create_default_context(cafile="/run/prl/ops.crt")
connection = http.client.HTTPSConnection(
    "ops.example.invalid", 8443, timeout=4, context=context
)
connection.request("GET", "/api/org", headers={
    "Host": "ops.example.invalid",
    "X-Forwarded-For": "127.0.0.1",
    "X-Real-IP": "127.0.0.1",
    "Forwarded": "for=127.0.0.1",
    "Connection": "close",
})
response = connection.getresponse()
status = response.status
response.read()
connection.close()
print("TASK6_PROBE:" + json.dumps({"spoofed_forwarded_status": status}))
"""

        def mount(source: Path, target: str) -> str:
            return (
                "      - type: bind\n"
                f"        source: {json.dumps(str(source.resolve()))}\n"
                f"        target: {target}\n"
                "        read_only: true\n"
            )

        lines = [
            "services:",
            "  task6_ops_probe:",
            f"    image: {json.dumps('prl-backend:' + self.stack.image_tag)}",
            "    profiles: [task6-ops-probe]",
            "    network_mode: service:gateway",
            "    depends_on: {gateway: {condition: service_started}}",
            f"    entrypoint: {json.dumps(['python', '-c', positive_script])}",
            "    volumes:",
            mount(self.ops_cert, "/run/prl/ops.crt").rstrip(),
            mount(self.admin_user_file, "/run/prl/admin-user").rstrip(),
            mount(self.admin_password_file, "/run/prl/admin-password").rstrip(),
            "    mem_limit: 128m",
            "    memswap_limit: 128m",
            "    cpus: 0.5",
            "    pids_limit: 32",
            "    read_only: true",
            '    tmpfs: ["/tmp:size=8m,mode=1777"]',
            "    cap_drop: [ALL]",
            "    security_opt: [no-new-privileges:true]",
            "  task6_ops_denied_probe:",
            f"    image: {json.dumps('prl-backend:' + self.stack.image_tag)}",
            "    profiles: [task6-ops-denied-probe]",
            "    networks: [ops_grafana]",
            f"    extra_hosts: {json.dumps(['ops.example.invalid:' + gateway_ip])}",
            f"    entrypoint: {json.dumps(['python', '-c', negative_script])}",
            "    volumes:",
            mount(self.ops_cert, "/run/prl/ops.crt").rstrip(),
            "    mem_limit: 128m",
            "    memswap_limit: 128m",
            "    cpus: 0.5",
            "    pids_limit: 32",
            "    read_only: true",
            '    tmpfs: ["/tmp:size=8m,mode=1777"]',
            "    cap_drop: [ALL]",
            "    security_opt: [no-new-privileges:true]",
        ]
        self.probe_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.stack.base.extend(["-f", str(self.probe_file.resolve())])

    def run_probe(self, profile: str, service: str) -> dict[str, object]:
        result = self.compose("--profile", profile, "run", "--rm", "-T", service)
        if result.returncode:
            raise AcceptanceFailure("A bounded UUID-owned operations probe failed")
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("TASK6_PROBE:"):
                try:
                    return json.loads(line.removeprefix("TASK6_PROBE:"))
                except json.JSONDecodeError:
                    break
        raise AcceptanceFailure("An operations probe returned no sanitized result")

    def gateway_operations_records(self) -> list[dict[str, object]]:
        gateway_id = acceptance_success(
            self.compose("ps", "-q", "gateway"),
            "The UUID-owned gateway container is missing",
        )
        result = acceptance_invoke(["docker", "logs", gateway_id], timeout=15)
        if result.returncode:
            raise AcceptanceFailure("The sanitized gateway operations records could not be read")
        required = {
            "timestamp",
            "service",
            "environment",
            "release",
            "event",
            "route",
            "method",
            "status",
            "duration_seconds",
            "classification",
        }
        records = []
        for line in (result.stdout + result.stderr).splitlines():
            if '"event":"gateway.operations"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                set(record) != required
                or record.get("service") != "gateway"
                or record.get("environment") != "demo"
                or record.get("release") != self.stack.expected_sha
                or record.get("event") != "gateway.operations"
                or record.get("route") != "__unmatched__"
                or record.get("classification") != "out_of_scope"
            ):
                raise AcceptanceFailure(
                    "An OPS-vhost record violated its finite out-of-scope schema"
                )
            records.append(record)
        return records

    def synthetic_alert_summaries(self) -> list[dict[str, str]]:
        name = "Task6SyntheticDeliveryProbe"
        for status in ("firing", "resolved"):
            self.api_json(
                "http://local_alert_receiver:8088/alerts",
                method="POST",
                body={
                    "alerts": [
                        {
                            "status": status,
                            "labels": {"alertname": name, "severity": "warning"},
                            "annotations": {"summary": "Synthetic local delivery check"},
                        }
                    ]
                },
            )
        summaries = self.api_json("http://local_alert_receiver:8088/alerts")["alerts"]
        matching = [item for item in summaries if item.get("alertname") == name]
        if [item.get("status") for item in matching] != ["firing", "resolved"]:
            raise AcceptanceFailure("The receiver lost synthetic firing/resolved summaries")
        for item in matching:
            if set(item) != {"alertname", "severity", "status", "received_at"}:
                raise AcceptanceFailure("The receiver returned fields outside its safe summary")
            if item.get("severity") != "warning":
                raise AcceptanceFailure("The receiver lost the finite alert severity")
            try:
                datetime.fromisoformat(item["received_at"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                raise AcceptanceFailure("The receiver summary has no receipt timestamp") from None
        return matching

    def start(self) -> None:
        self.stack.wait_gateway = self.wait_app_gateway
        original_worker_log_output = self.stack.worker_log_output

        def capture_worker_progress() -> str:
            output = original_worker_log_output()
            progress = latest_worker_progress(output, require_heartbeat=False)
            observation: dict[str, object] = {"parsed": progress is not None}
            if progress is not None:
                observation["successful_scans"] = progress.successful_scans
                if self.observed_worker_id is None:
                    self.observed_worker_id = progress.worker_id
                    observation["same_worker"] = True
                else:
                    observation["same_worker"] = progress.worker_id == self.observed_worker_id
            self.heartbeat_observations.append(observation)
            del self.heartbeat_observations[:-8]
            if progress is None and self.worker_stream_diagnostic is None:
                worker_id = acceptance_success(
                    self.compose("ps", "-q", "worker"),
                    "The UUID-owned worker container is missing during stream diagnostics",
                )
                if re.fullmatch(r"[0-9a-f]{64}", worker_id):
                    inspected = acceptance_json(
                        acceptance_invoke(["docker", "inspect", worker_id]),
                        "The UUID-owned worker configuration could not be inspected",
                    )
                    direct = acceptance_invoke(
                        ["docker", "logs", "--tail", "400", worker_id], timeout=15
                    )

                    def event_class(stream: str) -> str:
                        lines = [line for line in stream.splitlines() if line.strip()]
                        if not lines:
                            return "empty"
                        try:
                            record = json.loads(lines[-1])
                        except json.JSONDecodeError:
                            return "non_json"
                        if not isinstance(record, dict):
                            return "non_object_json"
                        event = record.get("event")
                        if event in {
                            "starting",
                            "ready",
                            "heartbeat",
                            "scan",
                            "candidate",
                            "stopping",
                            "stopped",
                            "fatal",
                        }:
                            return "known_event"
                        return "unknown_event"

                    stdout_progress = latest_worker_progress(direct.stdout, require_heartbeat=False)
                    stderr_progress = latest_worker_progress(direct.stderr, require_heartbeat=False)
                    stream_diagnostic: dict[str, object] = {
                        "compose_merged_parsed": progress is not None,
                        "compose_log_succeeded": True,
                        "direct_log_succeeded": direct.returncode == 0,
                        "container_tty": bool((inspected[0].get("Config") or {}).get("Tty")),
                        "direct_stdout_parsed": stdout_progress is not None,
                        "direct_stdout_scans": (
                            stdout_progress.successful_scans
                            if stdout_progress is not None
                            else None
                        ),
                        "direct_stdout_tail": event_class(direct.stdout),
                        "direct_stderr_parsed": stderr_progress is not None,
                        "direct_stderr_tail": event_class(direct.stderr),
                    }
                    if (
                        stdout_progress is not None
                        or stderr_progress is not None
                        or len(self.heartbeat_observations) >= 8
                    ):
                        self.worker_stream_diagnostic = stream_diagnostic
            return output

        self.stack.worker_log_output = capture_worker_progress
        original_wait_heartbeat = self.stack.wait_heartbeat

        def wait_heartbeat_with_safe_diagnostics(*, excluded_worker_id: str | None = None):
            try:
                return original_wait_heartbeat(excluded_worker_id=excluded_worker_id)
            except AcceptanceFailure:
                try:
                    diagnostic = self.safe_worker_diagnostic()
                except AcceptanceFailure:
                    diagnostic = {"available": False}
                print("TASK6_WORKER_DIAGNOSTIC:" + json.dumps(diagnostic, separators=(",", ":")))
                raise

        self.stack.wait_heartbeat = wait_heartbeat_with_safe_diagnostics
        self.stack.start()
        self.api_id = acceptance_success(
            self.compose("ps", "-q", "api"),
            "The UUID-owned API container is missing after readiness",
        )
        acceptance_success(
            self.compose(
                "build",
                "--build-arg",
                f"PRL_SOURCE_SHA={self.stack.expected_sha}",
                "collector",
                timeout=900,
            ),
            "The exact-source Collector image could not be built",
        )
        services = (
            "collector",
            "prometheus",
            "loki",
            "tempo",
            "grafana",
            "alertmanager",
            "local_alert_receiver",
            "gateway_log_rotate",
        )
        acceptance_success(
            self.compose("up", "-d", "--wait", "--wait-timeout", "180", *services, timeout=240),
            "The UUID-owned telemetry services did not start",
        )
        self.write_probe_compose()
        self.wait_targets()

    def safe_worker_diagnostic(self) -> dict[str, object]:
        worker_id = acceptance_success(
            self.compose("ps", "-a", "-q", "worker"),
            "The UUID-owned worker container is missing during diagnostics",
        ).splitlines()[-1]
        if not re.fullmatch(r"[0-9a-f]{64}", worker_id):
            raise AcceptanceFailure("The UUID-owned worker identity is invalid during diagnostics")
        inspected = acceptance_json(
            acceptance_invoke(["docker", "inspect", worker_id]),
            "The UUID-owned worker state could not be inspected for diagnostics",
        )
        state = inspected[0].get("State") or {}
        settings: dict[str, str] = {}
        environment = (inspected[0].get("Config") or {}).get("Env") or []
        for item in environment:
            if isinstance(item, str) and "=" in item:
                name, value = item.split("=", 1)
                if name in {
                    "ENVIRONMENT",
                    "WORKER_POLL_INTERVAL_SECONDS",
                    "WORKER_HEARTBEAT_INTERVAL_SECONDS",
                } and re.fullmatch(r"[A-Za-z0-9._-]{1,32}", value):
                    settings[name] = value
        logs = self.compose(
            "logs", "--no-color", "--no-log-prefix", "--tail", "40", "worker", timeout=15
        )
        records: list[dict[str, object]] = []
        for line in (logs.stdout + logs.stderr).splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("event") not in {
                "starting",
                "ready",
                "heartbeat",
                "scan",
                "candidate",
                "stopping",
                "stopped",
                "fatal",
            }:
                continue
            record: dict[str, object] = {"event": item["event"]}
            for name in (
                "phase",
                "database_ok",
                "successful_scans",
                "failed_scans",
                "reporting_failures",
            ):
                value = item.get(name)
                if name == "phase" and isinstance(value, str) and len(value) <= 24:
                    record[name] = value
                elif name != "phase" and type(value) in (bool, int):
                    record[name] = value
            error_code = item.get("error_code")
            if isinstance(error_code, str) and re.fullmatch(r"[a-z_]{1,40}", error_code):
                record["error_code"] = error_code
            reason = item.get("reason")
            if isinstance(reason, str) and reason in {
                "configuration_invalid",
                "schema_incompatible",
                "deadline_exceeded",
                "unexpected_failure",
            }:
                record["reason"] = reason
            last_scan = item.get("last_successful_scan")
            if isinstance(last_scan, str):
                try:
                    timestamp = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                except ValueError:
                    pass
                else:
                    if timestamp.tzinfo is not None:
                        record["last_successful_scan"] = last_scan
            records.append(record)
        return {
            "running": state.get("Running") is True,
            "status": state.get("Status") if isinstance(state.get("Status"), str) else "unknown",
            "restart_count": inspected[0].get("RestartCount"),
            "oom_killed": state.get("OOMKilled") is True,
            "exit_code": state.get("ExitCode") if isinstance(state.get("ExitCode"), int) else None,
            "settings": settings,
            "heartbeat_observations": self.heartbeat_observations,
            "stream_diagnostic": self.worker_stream_diagnostic,
            "log_command_succeeded": logs.returncode == 0,
            "records": records[-8:],
        }

    def cleanup(self) -> None:
        project = self.stack.project
        if project == "prl-demo" or not re.fullmatch(r"prl-task6-[0-9a-f]{32}", project):
            raise AcceptanceFailure("Refusing cleanup without the UUID-owned project identity")
        listed = acceptance_success(
            acceptance_invoke(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ]
            ),
            "The UUID-owned containers could not be enumerated for cleanup",
        )
        ids = [item for item in listed.splitlines() if item]
        if ids:
            records = acceptance_json(
                acceptance_invoke(["docker", "inspect", *ids]),
                "Container ownership could not be verified before cleanup",
            )
            if any(
                record.get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
                != project
                for record in records
            ):
                raise AcceptanceFailure("A container failed the UUID cleanup guard")
        before = self.volume_snapshot()
        for volume in before.values():
            if volume["labels"].get("com.docker.compose.project") != project:
                raise AcceptanceFailure("A volume failed the UUID cleanup guard")
        if ids:
            acceptance_success(
                self.compose("down", "--remove-orphans", "--timeout", "30", timeout=60),
                "The UUID-owned containers and networks could not be stopped safely",
            )
        if before != self.volume_snapshot():
            raise AcceptanceFailure("Compose cleanup changed or removed a named volume")
        self.stack.assert_demo_untouched()


@pytest.mark.skipif(
    not _full_stack_enabled(),
    reason="requires all five opt-in Compose runtime gates",
)
def test_full_observability_stack_runtime_acceptance(tmp_path: Path) -> None:
    token = os.environ.get("PRL_TASK6_ACCEPTANCE_TOKEN", "")
    if token:
        print(f"Task 6 runtime owner: prl-task6-{token}")
    runtime = FullStackRuntime(tmp_path)
    evidence: dict[str, object] = {}
    sink_observations: dict[str, dict[str, object]] = {}
    failure_context: dict[str, object] = {
        "stage": "runtime_start",
        "route_status": None,
        "request_id": "",
        "trace_id": "",
    }
    accepted = False
    try:
        failure_context["stage"] = "stack_start_and_self_health"
        runtime.start()
        evidence["project"] = runtime.stack.project
        evidence["source_sha"] = runtime.stack.expected_sha
        evidence["target_matrix"] = runtime.wait_targets()
        expected_backend_image = "prl-backend:" + runtime.stack.image_tag
        expected_frontend_image = "prl-frontend:" + runtime.stack.image_tag
        if (
            runtime.inspect_service("api").get("Config", {}).get("Image") != expected_backend_image
            or runtime.inspect_service("worker").get("Config", {}).get("Image")
            != expected_backend_image
            or runtime.inspect_service("frontend").get("Config", {}).get("Image")
            != expected_frontend_image
        ):
            raise AcceptanceFailure("Derived app images do not use the UUID-owned tag")
        evidence["derived_images"] = {
            "backend": expected_backend_image,
            "frontend": expected_frontend_image,
        }
        expected_caps = {
            "mysql": (1024, 1_000_000_000, 256),
            "api": (512, 1_000_000_000, 128),
            "worker": (512, 1_000_000_000, 128),
            "frontend": (512, 1_000_000_000, 128),
            "gateway": (128, 500_000_000, 64),
            "collector": (512, 1_000_000_000, 128),
            "prometheus": (512, 1_000_000_000, 128),
            "loki": (768, 1_000_000_000, 128),
            "tempo": (512, 1_000_000_000, 128),
            "grafana": (512, 1_000_000_000, 128),
            "alertmanager": (128, 500_000_000, 64),
            "local_alert_receiver": (128, 500_000_000, 64),
            "gateway_log_rotate": (128, 250_000_000, 32),
        }
        actual_caps = {}
        for service, (memory_mib, nano_cpus, pids_limit) in expected_caps.items():
            host = runtime.inspect_service(service).get("HostConfig", {})
            actual = (host.get("Memory"), host.get("NanoCpus"), host.get("PidsLimit"))
            expected = (memory_mib * 1024 * 1024, nano_cpus, pids_limit)
            if actual != expected:
                raise AcceptanceFailure(f"The UUID-owned {service} resource caps changed")
            actual_caps[service] = {
                "memory_mib": memory_mib,
                "nano_cpus": nano_cpus,
                "pids_limit": pids_limit,
            }
        evidence["steady_resource_caps"] = actual_caps

        failure_context["stage"] = "application_https_readiness"
        app_status, headers, body = _https_request(
            runtime.app_host, runtime.port, runtime.app_cert, "GET", "/health/ready"
        )
        if app_status != 200 or json.loads(body).get("status") != "ready":
            raise AcceptanceFailure("Verified application HTTPS readiness failed")
        lower_headers = {name.lower(): value for name, value in headers.items()}
        request_id = lower_headers.get("x-request-id", "")
        trace_id = lower_headers.get("x-trace-id", "")
        failure_context["route_status"] = app_status
        failure_context["request_id"] = request_id
        failure_context["trace_id"] = trace_id
        if not re.fullmatch(r"[a-f0-9]{32}", request_id) or not re.fullmatch(
            r"[a-f0-9]{32}", trace_id
        ):
            raise AcceptanceFailure("The real HTTPS request omitted safe trace correlation")
        gateway_event: dict[str, object] = {}

        def observe_boundary(name: str, operation) -> None:
            try:
                sink_observations[name] = operation()
            except Exception as exc:
                sink_observations[name] = {"failure_category": type(exc).__name__}

        def signals_stored() -> bool:
            observe_boundary(
                "loki_query",
                lambda: runtime.observe_loki_request(request_id, trace_id),
            )
            observe_boundary(
                "loki_index_series",
                lambda: runtime.observe_loki_index_series(expected_request_id=request_id),
            )
            observe_boundary(
                "tempo",
                lambda: runtime.observe_tempo_trace(trace_id),
            )
            return all(
                sink_observations[name].get("failure_category") is None
                for name in ("loki_query", "loki_index_series", "tempo")
            )

        failure_context["stage"] = "loki_query_index_and_tempo_delivery"
        runtime.stack.wait_for(
            signals_stored,
            seconds=45,
            failure="The verified application request did not reach Loki and Tempo",
        )
        gateway_event = runtime.query_loki_request(request_id, trace_id)
        evidence["gateway_loki_event"] = gateway_event
        evidence["tempo_trace"] = "stored"

        failure_context["stage"] = "worker_signal_contract"
        selected_names: dict[str, str] = {}
        baseline: dict[str, dict[str, object]] = {}

        def worker_signals_ready() -> bool:
            nonlocal selected_names, baseline
            selected_names, baseline = runtime.worker_metric_state()
            return {"started", "heartbeat", "last_successful_scan", "last_progress"}.issubset(
                baseline
            )

        failure_context["stage"] = "prometheus_receiver_schema"
        runtime.stack.wait_for(
            worker_signals_ready,
            seconds=60,
            failure="The actual worker exposition omitted required signals",
        )
        evidence["worker_metric_names"] = selected_names
        evidence["worker_series_schema"] = runtime.worker_series_schema()
        evidence["target_info_label_schema"] = runtime.target_info_schema
        receiver_series = (
            runtime.api_json(
                "http://prometheus:9090/api/v1/query?"
                + urlencode({"query": "prl_expected_worker_slot"})
            )
            .get("data", {})
            .get("result", [])
        )
        receiver_slot = next(
            (
                item.get("metric", {})
                for item in receiver_series
                if item.get("metric", {}).get("slot") == "1"
                and item.get("metric", {}).get("job") == "local-alert-receiver"
            ),
            None,
        )
        if receiver_slot is None or receiver_slot.get("instance") != "local_alert_receiver:8088":
            raise AcceptanceFailure("The receiver slot series lost its actual scrape identity")
        evidence["receiver_slot_labels"] = receiver_slot

        failure_context["stage"] = "ops_tls_auth_cidr_and_receiver"
        volumes_before = runtime.volume_snapshot()
        expected_volumes = {
            "mysql_data",
            "gateway_logs",
            "collector_file_storage",
            "prometheus_data",
            "loki_data",
            "tempo_data",
            "grafana_data",
            "alertmanager_data",
        }
        suffixes = {name.removeprefix(runtime.stack.project + "_") for name in volumes_before}
        if suffixes != expected_volumes:
            raise AcceptanceFailure(
                "The UUID-owned stack did not create the exact persistent volumes"
            )
        gateway_volume = next(
            volume for name, volume in volumes_before.items() if name.endswith("_gateway_logs")
        )
        options = gateway_volume["options"]
        if (
            gateway_volume["driver"] != "local"
            or options.get("type") != "tmpfs"
            or "size=64m" not in options.get("o", "")
        ):
            raise AcceptanceFailure("The gateway log spool is not bounded by its 64 MiB tmpfs")
        collector = runtime.inspect_service("collector")
        collector_config = collector.get("Config") or {}
        collector_mount = next(
            (
                item
                for item in collector.get("Mounts", [])
                if item.get("Destination") == "/var/log/prl"
            ),
            None,
        )
        if (
            collector_mount is None
            or collector_mount.get("RW") is not False
            or collector_config.get("User") != "10001:10001"
            or (collector.get("HostConfig") or {}).get("GroupAdd") != ["101"]
        ):
            raise AcceptanceFailure(
                "The Collector is not using its read-only group-101 spool mount"
            )
        gateway_id = acceptance_success(
            runtime.compose("ps", "-q", "gateway"),
            "The UUID-owned gateway container is missing",
        )
        spool = acceptance_success(
            acceptance_invoke(
                [
                    "docker",
                    "exec",
                    gateway_id,
                    "sh",
                    "-c",
                    "stat -c '%a:%u:%g' /var/log/prl/access.jsonl",
                ]
            ),
            "The JSONL spool file could not be inspected",
        ).strip()
        if spool != "640:101:101":
            raise AcceptanceFailure("The actual gateway spool file permissions/group changed")
        evidence["gateway_spool"] = {
            "mode_uid_gid": spool,
            "collector_user": collector_config.get("User"),
            "collector_group_add": (collector.get("HostConfig") or {}).get("GroupAdd"),
            "read_only": True,
        }

        positive = runtime.run_probe("task6-ops-probe", "task6_ops_probe")
        if (
            positive.get("health") != 200
            or positive.get("unauth_org") != 401
            or positive.get("admin_org") != 200
            or positive.get("datasource_status") != 200
            or positive.get("datasource_uids") != ["prl-loki", "prl-prometheus", "prl-tempo"]
        ):
            raise AcceptanceFailure("The verified OPS HTTPS/auth/datasource probe failed")
        if not all(
            isinstance(positive.get(name), int)
            for name in ("ops_business_status", "ops_health_status")
        ):
            raise AcceptanceFailure("The OPS-vhost boundary probes did not complete")
        operations_records = runtime.gateway_operations_records()
        required_statuses = [
            positive["ops_business_status"],
            positive["ops_health_status"],
        ]
        if any(
            sum(record.get("status") == status for record in operations_records)
            < required_statuses.count(status)
            for status in set(required_statuses)
        ):
            raise AcceptanceFailure("OPS business/health paths were not logged as operations")
        evidence["operations_vhost"] = {
            "business_shaped_path_status": positive["ops_business_status"],
            "health_shaped_path_status": positive["ops_health_status"],
            "event": "gateway.operations",
            "classification": "out_of_scope",
            "route": "__unmatched__",
            "log_fields": sorted(operations_records[-1]),
        }
        grafana = runtime.inspect_service("grafana")
        grafana_env = set((grafana.get("Config") or {}).get("Env") or [])
        if (
            "GF_AUTH_ANONYMOUS_ENABLED=false" not in grafana_env
            or "GF_USERS_ALLOW_SIGN_UP=false" not in grafana_env
            or "GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana-admin-password"
            not in grafana_env
        ):
            raise AcceptanceFailure("Grafana anonymous/signup/admin-file auth settings changed")
        evidence["grafana"] = {
            "anonymous_org": positive["unauth_org"],
            "authenticated_admin_org": positive["admin_org"],
            "datasources": positive["datasource_uids"],
            "anonymous_auth_disabled": True,
            "signup_disabled": True,
        }
        negative = runtime.run_probe("task6-ops-denied-probe", "task6_ops_denied_probe")
        if negative.get("spoofed_forwarded_status") != 403:
            raise AcceptanceFailure("The OPS CIDR guard trusted a spoofed forwarded address")
        evidence["ops_cidr"] = {
            "allowed_loopback_admin": True,
            "spoofed_forwarded_peer_status": 403,
        }

        health = runtime.api_json("http://local_alert_receiver:8088/health/telemetry")
        if health.get("status") != "passed" or health.get("unhealthy") != []:
            raise AcceptanceFailure("The receiver did not observe the telemetry backends healthy")
        evidence["receiver_self_health"] = health
        evidence["alert_summaries"] = runtime.synthetic_alert_summaries()

        health_logs = []
        health_deadline = time.monotonic() + 15
        while time.monotonic() < health_deadline:
            state = acceptance_json(
                acceptance_invoke(["docker", "inspect", gateway_id]),
                "The gateway Docker healthcheck could not be inspected",
            )[0].get("State", {})
            health_logs = (state.get("Health") or {}).get("Log", [])
            if len(health_logs) >= 2:
                break
            time.sleep(0.5)
        if len(health_logs) < 2 or any(item.get("ExitCode") != 0 for item in health_logs[-2:]):
            raise AcceptanceFailure("The gateway did not produce recurring healthy self-checks")
        first = datetime.fromisoformat(health_logs[-2]["Start"].replace("Z", "+00:00"))
        second = datetime.fromisoformat(health_logs[-1]["Start"].replace("Z", "+00:00"))
        interval = (second - first).total_seconds()
        if not 1 <= interval <= 15:
            raise AcceptanceFailure("The genuine gateway healthcheck cadence changed")
        evidence["gateway_healthcheck"] = {
            "successful_runs": 2,
            "docker_interval_seconds": round(interval, 2),
            "loki": runtime.query_loki_healthchecks(),
        }

        failure_context["stage"] = "grafana_outage_isolation"
        acceptance_success(
            runtime.compose("stop", "--timeout", "10", "grafana", timeout=30),
            "The UUID-owned Grafana could not stop for outage isolation",
        )
        outage = _https_request(
            runtime.app_host, runtime.port, runtime.app_cert, "GET", "/health/ready"
        )
        if outage[0] != 200:
            raise AcceptanceFailure("Application HTTPS readiness depended on Grafana availability")
        acceptance_success(
            runtime.compose("up", "-d", "--wait", "--wait-timeout", "90", "grafana", timeout=120),
            "Grafana did not recover after its isolated outage",
        )
        runtime.wait_targets()
        evidence["grafana_outage_isolation"] = "application HTTPS remained ready"

        old_volumes = runtime.volume_snapshot()
        old_started = baseline["started"]["value"]
        old_scan = baseline["last_successful_scan"]["value"]
        old_progress = baseline["last_progress"]["value"]
        acceptance_success(
            runtime.compose("stop", "--timeout", "10", "mysql", timeout=30),
            "The UUID-owned database could not stop for the same-slot observation",
        )
        failure_context["stage"] = "worker_and_database_restart"
        acceptance_success(
            runtime.compose("restart", "--timeout", "20", "worker", timeout=45),
            "The worker could not restart for the same-slot observation",
        )
        new_state: dict[str, dict[str, object]] = {}

        def observed_restart() -> bool:
            nonlocal new_state
            _names, new_state = runtime.worker_metric_state()
            return (
                new_state.get("started", {}).get("value", 0) > old_started
                and "last_successful_scan" in new_state
                and "last_progress" in new_state
            )

        runtime.stack.wait_for(
            observed_restart,
            seconds=45,
            failure="The new worker start and cached same-slot points were not observed",
        )
        if (
            new_state["last_successful_scan"]["value"] != old_scan
            or new_state["last_progress"]["value"] != old_progress
        ):
            raise AcceptanceFailure(
                "Collector cache did not retain omitted same-slot worker points"
            )
        evidence["same_slot_restart"] = {
            "slot": "1",
            "started_timestamp_advanced": True,
            "last_successful_scan_retained_while_unknown": True,
            "last_progress_retained_while_unknown": True,
            "new_started_timestamp": new_state["started"]["value"],
            "counter_name": selected_names.get("successful_scans"),
        }
        acceptance_success(
            runtime.compose("up", "-d", "--wait", "--wait-timeout", "120", "mysql", timeout=150),
            "The UUID-owned database did not recover after the observation",
        )
        runtime.stack.wait_for(
            lambda: runtime.worker_metric_state()[1].get("heartbeat", {}).get("value", 0)
            > baseline["heartbeat"]["value"],
            seconds=45,
            failure="The restarted worker heartbeat did not advance after database recovery",
        )
        if old_volumes != runtime.volume_snapshot():
            raise AcceptanceFailure(
                "A persistent volume identity changed across worker/database restart"
            )

        failure_context["stage"] = "collector_restart_and_persistence"
        acceptance_success(
            runtime.compose("restart", "--timeout", "20", "collector", timeout=45),
            "The UUID-owned Collector could not restart",
        )
        runtime.wait_targets()
        if old_volumes != runtime.volume_snapshot():
            raise AcceptanceFailure("Collector restart changed a persistent volume identity")
        after_collector = _https_request(
            runtime.app_host, runtime.port, runtime.app_cert, "GET", "/health/ready"
        )
        if after_collector[0] != 200:
            raise AcceptanceFailure("The app gateway did not recover after the Collector restart")
        if runtime.query_loki_request(request_id, trace_id) != evidence["gateway_loki_event"]:
            raise AcceptanceFailure(
                "The persisted gateway event changed or duplicated after Collector restart"
            )
        evidence["persistent_volumes"] = sorted(
            name.removeprefix(runtime.stack.project + "_") for name in runtime.volume_snapshot()
        )
        evidence["collector_restart"] = "same file-storage volume; HTTPS and Loki event persisted"

        print(
            "TASK6_RUNTIME_EVIDENCE=" + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        )
        accepted = True
    finally:
        if not accepted:
            try:
                diagnostic = runtime.signal_boundary_diagnostic(
                    route_status=failure_context["route_status"],
                    request_id=str(failure_context["request_id"]),
                    trace_id=str(failure_context["trace_id"]),
                    sink_observations=sink_observations,
                )
                diagnostic["failure_stage"] = failure_context["stage"]
                print(
                    "TASK6_PRE_CLEANUP_FAILURE_SNAPSHOT:"
                    + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
                )
            except Exception as exc:
                print(
                    "TASK6_PRE_CLEANUP_FAILURE_SNAPSHOT:"
                    + json.dumps(
                        {
                            "failure_stage": failure_context["stage"],
                            "snapshot_available": False,
                            "diagnostic_error_category": type(exc).__name__,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
        runtime.cleanup()
