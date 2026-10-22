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
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
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


@pytest.fixture(scope="module", params=["local", "https"])
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
        run(*base, "down", "--remove-orphans", "--timeout", "5", env=env)


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


@pytest.mark.parametrize("path,limit", [("/api/v1/auth/login", 4096), ("/api/v1/probe", 32768)])
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
    assert '"path":"/api/v1/probe"' in logs


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
        response = client.get("/api/v1/probe?secret-query=hidden")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
        assert response.headers["x-error-class"] == "service"
        assert response.headers["cache-control"] == "private, no-store"
        assert "secret-query" not in run(*base, "logs", "--no-log-prefix", "gateway", env=env)
    finally:
        run(*base, "start", "api", env=env)
