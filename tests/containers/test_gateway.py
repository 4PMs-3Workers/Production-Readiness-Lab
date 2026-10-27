"""Gateway acceptance: RUN_GATEWAY_TESTS=1 python -m pytest tests/containers/test_gateway.py.

Real Nginx and controlled HTTP upstreams isolate forwarding from database state.
The later Compose smoke suite covers real application login and CSRF validation.
"""

import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "tools/deploy"
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_GATEWAY_TESTS") != "1", reason="opt-in Docker gateway acceptance"
)


def run(*args, env=None, timeout=180):
    result = subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("overlay", ["local", "https"])
def test_overlay_isolates_ports_and_request_id_trust(tmp_path, overlay):
    empty = tmp_path / "empty.env"
    empty.write_text("")
    env = dict(os.environ)
    for role in ("DB", "RUNTIME", "MIGRATION", "SEED"):
        env[f"PRL_{role}_ENV_FILE"] = str(empty)
    env.update(
        PRL_HTTPS_ORIGIN="https://localhost:8443",
        PRL_TLS_CERT_FILE=str(tmp_path / "external.crt"),
        PRL_TLS_KEY_FILE=str(tmp_path / "external.key"),
    )
    base = [
        "docker",
        "compose",
        "-f",
        str(DEPLOY / "compose.yaml"),
        "-f",
        str(DEPLOY / f"compose.{overlay}.yaml"),
    ]
    run(*base, "config", "--quiet", env=env)
    config = json.loads(run(*base, "config", "--no-env-resolution", "--format", "json", env=env))
    services = config["services"]
    assert [name for name, service in services.items() if service.get("ports")] == ["gateway"]
    assert config["networks"]["gateway_api"]["internal"]
    peers = {name for name, service in services.items() if "gateway_api" in service["networks"]}
    assert peers == {"api", "gateway"}
    gateway_address = services["gateway"]["networks"]["gateway_api"]["ipv4_address"]
    assert services["api"]["environment"]["TRUSTED_GATEWAY_CIDRS"] == gateway_address + "/32"
    assert "edge" in services["gateway"]["networks"]
    assert "edge" not in services["api"]["networks"]
    settings = services["api"]["environment"]
    assert settings["COOKIE_DOMAIN"] == ""
    assert settings["COOKIE_SAMESITE"] == "lax"
    assert settings["COOKIE_SECURE"] == ("true" if overlay == "https" else "false")
    assert settings["APPLICATION_ORIGIN"] == (
        "https://localhost:8443" if overlay == "https" else "http://localhost:8080"
    )


@pytest.fixture(scope="module")
def backend_image():
    image = "prl-gateway-test:" + uuid4().hex
    run("docker", "build", "-f", "Dockerfile.backend", "-t", image, ".", timeout=600)
    yield image
    run("docker", "image", "rm", image)


def preserve_gateway_failure(base, project, env):
    """Save diagnostics for this UUID-owned fixture before its teardown."""
    output = (
        ROOT
        / ".superpowers/sdd/2026-10-23-observability-foundation"
        / f"task-5-gateway-fixture-{project}.txt"
    )
    sections = []
    for label, command in (
        ("compose ps -a", [*base, "ps", "--all", "--format", "json"]),
        ("compose logs", [*base, "logs", "--no-color"]),
        (
            "spool metadata as UID 101 helper",
            [
                *base, "exec", "-T", "rotate_helper", "sh", "-c",
                'id; stat -c "%a %u:%g %n" /var/log/prl /var/log/prl/access.jsonl*',
            ],
        ),
    ):
        result = subprocess.run(
            command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30
        )
        sections.append(f"## {label} (exit {result.returncode})\n{result.stdout}\n{result.stderr}")
    for service in ("gateway", "collector"):
        for suffix in ("", ".1", ".2", ".3", ".4"):
            snapshot = output.with_name(f"{output.stem}-{service}-access.jsonl{suffix}")
            copied = subprocess.run(
                [
                    "docker", "cp",
                    f"{project}-{service}-1:/var/log/prl/access.jsonl{suffix}",
                    str(snapshot),
                ],
                cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
            )
            sections.append(
                f"## {service} file snapshot -> {snapshot.name} "
                f"(exit {copied.returncode})\n{copied.stdout}\n{copied.stderr}"
            )
    checkpoint_snapshot = output.with_name(f"{output.stem}-collector-file-storage")
    checkpoint_copy = subprocess.run(
        [
            "docker", "cp",
            f"{project}-collector-1:/var/lib/otelcol/file_storage",
            str(checkpoint_snapshot),
        ],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
    )
    sections.append(
        f"## Collector file_storage snapshot -> {checkpoint_snapshot.name} "
        f"(exit {checkpoint_copy.returncode})\n"
        f"{checkpoint_copy.stdout}\n{checkpoint_copy.stderr}"
    )
    ids_result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
    )
    container_ids = ids_result.stdout.split()
    if container_ids:
        inspected = subprocess.run(
            ["docker", "inspect", *container_ids], cwd=ROOT, env=env, text=True,
            capture_output=True, timeout=30,
        )
        sections.append(
            f"## docker inspect (exit {inspected.returncode})\n"
            f"{inspected.stdout}\n{inspected.stderr}"
        )
    volumes_result = subprocess.run(
        ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
    )
    volume_names = volumes_result.stdout.split()
    if volume_names:
        inspected_volumes = subprocess.run(
            ["docker", "volume", "inspect", *volume_names], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=30,
        )
        sections.append(
            f"## docker volume inspect (exit {inspected_volumes.returncode})\n"
            f"{inspected_volumes.stdout}\n{inspected_volumes.stderr}"
        )
    output.write_text("\n\n".join(sections), encoding="utf-8")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item.rep_call = report

def certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "server.crt", directory / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture(scope="function", params=["local", "https"])
def gateway(request, tmp_path_factory, backend_image):
    directory = tmp_path_factory.mktemp("gateway")
    cert, key = certificate(directory)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    mode = request.param
    origin = f"{'https' if mode == 'https' else 'http'}://localhost:{port}"
    project = "prl-gateway-" + uuid4().hex
    empty = directory / "empty.env"
    empty.write_text("")
    env = dict(
        os.environ,
        PRL_HTTP_PORT=str(port),
        PRL_HTTPS_PORT=str(port),
        PRL_HTTPS_ORIGIN=origin,
        PRL_TLS_CERT_FILE=str(cert),
        PRL_TLS_KEY_FILE=str(key),
    )
    for role in ("DB", "RUNTIME", "MIGRATION", "SEED"):
        env[f"PRL_{role}_ENV_FILE"] = str(empty)
    # Consume the actual merged overlay, replacing only upstream processes and
    # dependencies. Nginx mounts, address/trust policy and port mappings stay real.
    merged = json.loads(
        run(
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(DEPLOY / "compose.yaml"),
            "-f",
            str(DEPLOY / f"compose.{mode}.yaml"),
            "config",
            "--no-env-resolution",
            "--format",
            "json",
            env=env,
        )
    )
    merged["services"] = {
        k: v for k, v in merged["services"].items() if k in {"api", "frontend", "gateway"}
    }
    api_environment = dict(
        merged["services"]["api"]["environment"],
        DATABASE_URL="mysql+asyncmy://test:test@unused/prl",
        COOKIE_SECRET="ephemeral-test-only",
        CSRF_SECRET="ephemeral-test-only",
    )
    for role, listener in (("api", "8000"), ("frontend", "3000")):
        service = merged["services"][role]
        service.pop("build", None)
        service.pop("depends_on", None)
        service.pop("healthcheck", None)
        service["image"] = backend_image
        service["environment"] = api_environment
        service["entrypoint"] = ["python", "/test/upstream.py", listener, role]
        service["command"] = []
        service["volumes"] = [
            {
                "type": "bind",
                "source": str(ROOT / "tests/containers/gateway_upstream.py"),
                "target": "/test/upstream.py",
                "read_only": True,
            }
        ]
    compose_file = directory / "compose.json"
    compose_file.write_text(json.dumps(merged))
    base = ["docker", "compose", "-p", project, "-f", str(compose_file)]
    context = ssl.create_default_context(cafile=str(cert))
    try:
        run(*base, "up", "-d", env=env)
        run(*base, "exec", "-T", "gateway", "nginx", "-t", env=env)
        with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15) as client:
            for _ in range(60):
                try:
                    if client.get("/api/v1/probe").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail("gateway/upstream did not become ready")
            yield client, mode, base, env
    finally:
        report = getattr(request.node, "rep_call", None)
        if sys.exc_info()[0] is not None or (report is not None and report.failed):
            preserve_gateway_failure(base, project, env)
        run(*base, "down", "--remove-orphans", "--volumes", "--timeout", "5", env=env)


def test_real_routing_headers_and_spoofed_identity(gateway):
    client, mode, _, _ = gateway
    page = client.get("/login?private=secret-query")
    assert page.json()["role"] == "frontend"
    for path in ("/api/v1", "/health/live", "/health/ready"):
        assert client.get(path).json()["role"] == "api"
    response = client.post(
        "/api/v1/probe?private=secret-query",
        content=b"sensitive-body",
        headers={
            "Origin": str(client.base_url).rstrip("/"),
            "Referer": str(client.base_url) + "login",
            "Cookie": "secret-cookie=value",
            "X-CSRF-Token": "csrf-token",
            "Idempotency-Key": "key-123",
            "X-Request-ID": "spoofed",
            "Forwarded": "for=198.51.100.55;proto=https",
            "X-Real-IP": "198.51.100.55",
            "X-Forwarded-For": "198.51.100.55",
            "X-Forwarded-Proto": "fake",
            "X-Forwarded-Host": "attacker",
            "X-Forwarded-Port": "9999",
        },
    )
    data = response.json()
    assert data["role"] == "api" and data["path"] == "/api/v1/probe"
    assert data["query"] == "private=secret-query"
    assert data["body_size"] == 14
    identifier = response.headers["x-request-id"]
    assert re.fullmatch("[a-f0-9]{32}", identifier) and identifier != "spoofed"
    assert data["request_id"] == data["headers"]["x-request-id"] == identifier
    assert data["peer"] == "172.30.34.2"
    headers = data["headers"]
    assert "forwarded" not in headers and "x-real-ip" not in headers
    assert "198.51.100.55" not in headers["x-forwarded-for"]
    assert headers["x-forwarded-proto"] == ("https" if mode == "https" else "http")
    assert headers["x-forwarded-host"] == headers["host"] == client.base_url.netloc.decode()
    assert "x-forwarded-port" not in headers
    assert headers["origin"] == str(client.base_url).rstrip("/")
    assert headers["referer"] == str(client.base_url) + "login"
    assert headers["cookie"] == "secret-cookie=value"
    assert headers["x-csrf-token"] == "csrf-token" and headers["idempotency-key"] == "key-123"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["retry-after"] == "7"


@pytest.mark.parametrize(
    "path,limit",
    [("/api/v1/auth/login", 4096), ("/api/v1/auth/login/", 4096), ("/api/v1/probe", 32768)],
)
def test_real_body_boundaries_and_json_errors(gateway, path, limit):
    client, _, _, _ = gateway
    assert client.post(path, content=b"x" * limit).status_code == 200
    response = client.post(path, content=b"x" * (limit + 1))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["x-error-class"] == "input"
    assert response.headers["cache-control"] == "private, no-store"


def test_real_cookie_and_csrf_policies_survive_proxy(gateway):
    client, mode, _, _ = gateway
    response = client.get("/api/v1/cookies")
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Domain=" not in cookie and "SameSite=lax" in cookie for cookie in cookies)
    assert all(("; Secure" in cookie) == (mode == "https") for cookie in cookies)
    assert "HttpOnly" in cookies[0] and "HttpOnly" not in cookies[1]
    origin = str(client.base_url).rstrip("/")
    token = client.cookies["prl_csrf"]
    assert (
        client.post("/api/v1/mutate", headers={"Origin": origin, "X-CSRF-Token": token}).status_code
        == 200
    )
    invalid = client.post("/api/v1/mutate", headers={"Origin": origin, "X-CSRF-Token": "wrong"})
    assert invalid.status_code == 403 and invalid.json()["error"]["code"] == "INVALID_CSRF"
    invalid = client.post(
        "/api/v1/mutate", headers={"Origin": "https://attacker.example", "X-CSRF-Token": token}
    )
    assert invalid.status_code == 403 and invalid.json()["error"]["code"] == "INVALID_ORIGIN"
    assert (
        client.post(
            "/api/v1/mutate", headers={"Referer": origin + "/login", "X-CSRF-Token": token}
        ).status_code
        == 200
    )


def test_real_login_rate_limit_and_sanitized_logs(gateway):
    client, _, base, env = gateway
    results = [client.post("/api/v1/auth/login", content=b"{}") for _ in range(14)]
    throttled = [response for response in results if response.status_code == 429]
    assert throttled
    # Forwarded headers cannot create a new throttle identity. A real second
    # peer on the proxy network must still get its own independent allowance.
    assert (
        client.post("/api/v1/auth/login", headers={"X-Forwarded-For": "203.0.113.90"}).status_code
        == 429
    )
    scheme = client.base_url.scheme
    port = 8443 if scheme == "https" else 8080
    probe = (
        "import urllib.request,ssl; "
        f"request=urllib.request.Request('{scheme}://gateway:{port}/api/v1/auth/login', "
        "data=b'{}'); print(urllib.request.urlopen(request, "
        "context=ssl._create_unverified_context()).status)"
    )
    second = run(*base, "exec", "-T", "frontend", "python", "-c", probe, env=env)
    assert second.strip() == "200"
    response = throttled[0]
    assert response.json()["error"]["code"] == "LOGIN_THROTTLED"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["retry-after"] == "60"
    logs = run(*base, "logs", "--no-log-prefix", "gateway", env=env)
    for private in ("secret-query", "secret-cookie", "sensitive-body", "csrf-token", "spoofed"):
        assert private not in logs
    assert '"route":"__unmatched__"' in logs


def test_real_upstream_errors_pass_without_interception(gateway):
    client, _, _, _ = gateway
    response = client.get("/api/v1/upstream-error")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["retry-after"] == "7"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-error-class"] == "service"


def test_untrusted_frontend_peer_cannot_choose_api_request_id(gateway):
    _, _, base, env = gateway
    response = json.loads(
        run(
            *base,
            "exec",
            "-T",
            "frontend",
            "python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen(urllib.request.Request("
            "'http://api:8000/api/v1/probe', headers={'X-Request-ID':'spoofed-peer'}))"
            ".read().decode())",
            env=env,
        )
    )
    assert response["headers"]["x-request-id"] == "spoofed-peer"
    assert response["request_id"] != "spoofed-peer"
    assert response["peer"] != "172.30.34.2"


def test_real_gateway_generates_safe_error_when_api_stops(gateway):
    client, _, base, env = gateway
    run(*base, "stop", "--timeout", "5", "api", env=env)
    try:
        response = client.get("/api%2fv%31/%70urposes?secret-query=hidden")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
        assert response.headers["x-error-class"] == "service"
        assert response.headers["cache-control"] == "private, no-store"
        logs = run(*base, "logs", "--no-log-prefix", "gateway", env=env)
        assert "secret-query" not in logs
        records = []
        for line in logs.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(value, dict)
                and value.get("request_id") == response.headers["x-request-id"]
            ):
                records.append(value)
        assert len(records) == 1
        record = records[0]
        assert record["route"] == "/api/v1/purposes"
        assert record["upstream_identity"] == "api"
        assert record["upstream_outcome"] == "service_error"
        assert record["classification"] == "service_failure"
        assert "/api%2fv%31/%70urposes" not in logs
    finally:
        run(*base, "start", "api", env=env)


@pytest.fixture(scope="module", params=[("local", 2), ("https", 2), ("local", 100), ("https", 100)])
def real_backend_gateway(request, tmp_path_factory, backend_image):
    mode, login_attempts = request.param
    directory = tmp_path_factory.mktemp("real-backend-gateway")
    cert, key = certificate(directory)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"{'https' if mode == 'https' else 'http'}://localhost:{port}"
    project = "prl-real-gateway-" + uuid4().hex
    empty = directory / "empty.env"
    empty.write_text("")
    env = dict(
        os.environ,
        PRL_HTTP_PORT=str(port),
        PRL_HTTPS_PORT=str(port),
        PRL_HTTPS_ORIGIN=origin,
        PRL_TLS_CERT_FILE=str(cert),
        PRL_TLS_KEY_FILE=str(key),
    )
    for role in ("DB", "RUNTIME", "MIGRATION", "SEED"):
        env[f"PRL_{role}_ENV_FILE"] = str(empty)
    merged = json.loads(
        run(
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(DEPLOY / "compose.yaml"),
            "-f",
            str(DEPLOY / f"compose.{mode}.yaml"),
            "config",
            "--no-env-resolution",
            "--format",
            "json",
            env=env,
        )
    )
    merged["services"] = {
        name: service
        for name, service in merged["services"].items()
        if name in {"api", "frontend", "gateway"}
    }
    api_environment = dict(
        merged["services"]["api"]["environment"],
        DATABASE_URL="mysql+asyncmy://test:test@unused/prl",
        COOKIE_SECRET="ephemeral-test-only",
        CSRF_SECRET="ephemeral-test-only",
        LOGIN_IP_ATTEMPTS=str(login_attempts),
        LOGIN_EMAIL_ATTEMPTS="100",
    )
    api = merged["services"]["api"]
    api.pop("build", None)
    api.pop("depends_on", None)
    api.pop("healthcheck", None)
    api["image"] = backend_image
    api["environment"] = api_environment

    frontend = merged["services"]["frontend"]
    frontend.pop("build", None)
    frontend.pop("depends_on", None)
    frontend.pop("healthcheck", None)
    frontend["image"] = backend_image
    frontend["environment"] = api_environment
    frontend["entrypoint"] = ["python", "/test/upstream.py", "3000", "frontend"]
    frontend["command"] = []
    frontend["volumes"] = [
        {
            "type": "bind",
            "source": str(ROOT / "tests/containers/gateway_upstream.py"),
            "target": "/test/upstream.py",
            "read_only": True,
        }
    ]

    compose_file = directory / "compose.json"
    compose_file.write_text(json.dumps(merged))
    base = ["docker", "compose", "-p", project, "-f", str(compose_file)]
    context = ssl.create_default_context(cafile=str(cert))
    try:
        run(*base, "up", "-d", env=env)
        run(*base, "exec", "-T", "gateway", "nginx", "-t", env=env)
        with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15) as client:
            for _ in range(60):
                try:
                    if client.get("/health/live").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail("real backend did not become ready through the gateway")
            yield client, mode, login_attempts, base, env, origin
    finally:
        subprocess.run(
            [*base, "down", "--remove-orphans", "--timeout", "5"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )


def test_real_backend_uses_trusted_client_identity_and_external_scheme(real_backend_gateway):
    client, mode, login_attempts, base, env, origin = real_backend_gateway
    login = "/api/v1/auth/login"
    headers = {"Origin": origin, "X-Forwarded-For": "203.0.113.90"}

    redirect = client.get(login + "/?continue=%2Fhome")
    assert redirect.status_code == 307
    location = urlsplit(redirect.headers["location"])
    assert location.scheme == ("https" if mode == "https" else "http")
    assert location.path == login
    assert location.query == "continue=%2Fhome"
    if login_attempts == 100:
        statuses = []
        for index in range(14):
            path = login if index % 2 == 0 else login + "/"
            response = client.post(path, json={}, headers=headers)
            statuses.append(response.status_code)
        assert 429 in statuses
        assert set(statuses) <= {307, 422, 429}
    else:
        first = client.post(login, json={}, headers=headers)
        second = client.post(login + "/", json={}, headers=headers)
        assert first.status_code == 422
        assert second.status_code == 307

        forged = client.post(
            login,
            json={},
            headers={"Origin": origin, "X-Forwarded-For": "198.51.100.45"},
        )
        assert forged.status_code == 429
        assert forged.json()["error"]["code"] == "LOGIN_THROTTLED"

        scheme = "https" if mode == "https" else "http"
        port = 8443 if mode == "https" else 8080
        target = f"{scheme}://gateway:{port}{login}"
        probe = "\n".join(
            [
                "import urllib.request,urllib.error,ssl",
                f"target = {target!r}",
                f"origin = {origin!r}",
                "request = urllib.request.Request(",
                "    target,",
                "    data=b'{}',",
                "    headers={",
                "        'Content-Type':'application/json',",
                "        'Origin':origin,",
                "        'X-Forwarded-For':'198.51.100.45',",
                "    },",
                ")",
                "context = ssl._create_unverified_context()",
                "try:",
                "    print(urllib.request.urlopen(request, context=context).status)",
                "except urllib.error.HTTPError as error:",
                "    print(error.code)",
            ]
        )
        second_client_status = run(*base, "exec", "-T", "frontend", "python", "-c", probe, env=env)
        assert second_client_status.strip() == "422"
