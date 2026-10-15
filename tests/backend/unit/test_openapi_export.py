import os
import subprocess
import sys
from pathlib import Path

import yaml


def test_openapi_export_is_deterministic_without_database(tmp_path):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / "openapi.yaml"
    env = dict(os.environ, DATABASE_URL="mysql+asyncmy://user:password@127.0.0.1:1/unavailable")
    command = [
        sys.executable,
        str(root / "tools/scripts/export_openapi.py"),
        "--output",
        str(output),
    ]
    subprocess.run(command, cwd=root, env=env, check=True, capture_output=True, text=True)
    first = output.read_bytes()
    subprocess.run(command, cwd=root, env=env, check=True, capture_output=True, text=True)
    assert output.read_bytes() == first
    document = yaml.safe_load(first)
    assert set(document["paths"]) == {
        "/health/live",
        "/health/ready",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/auth/csrf",
        "/api/v1/auth/logout",
    }
    schemes = document["components"]["securitySchemes"]
    assert any(
        s["type"] == "apiKey" and s["in"] == "cookie" and s["name"] == "prl_session"
        for s in schemes.values()
    )
    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/auth/csrf",
        "/api/v1/auth/logout",
    ):
        method = "get" if path.endswith(("/me", "/csrf")) else "post"
        assert "ErrorResponse" in str(document["paths"][path][method]["responses"])
    assert "Retry-After" in str(document["paths"]["/api/v1/auth/login"]["post"]["responses"]["429"])
    assert "200" in document["paths"]["/health/live"]["get"]["responses"]
    assert "503" in document["paths"]["/health/ready"]["get"]["responses"]
    assert "ErrorResponse" in document["components"]["schemas"]
    for path, statuses in {"/health/live": ("500",), "/health/ready": ("500", "503")}.items():
        for status in statuses:
            headers = document["paths"][path]["get"]["responses"][status]["headers"]
            assert "X-Error-Class" in headers


def test_openapi_export_ignores_invalid_runtime_environment(tmp_path):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / "openapi.yaml"
    env = dict(
        os.environ,
        TRUSTED_GATEWAY_CIDRS="not-a-cidr",
        ENVIRONMENT="production",
        COOKIE_SECURE="false",
        READINESS_TIMEOUT_SECONDS="inf",
    )
    result = subprocess.run(
        [sys.executable, str(root / "tools/scripts/export_openapi.py"), "--output", str(output)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(output.read_text(encoding="utf-8"))["paths"]
