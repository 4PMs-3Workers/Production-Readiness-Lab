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
        "/api/v1/purposes",
        "/api/v1/staff",
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
    for path in ("/api/v1/purposes", "/api/v1/staff"):
        operation = document["paths"][path]["get"]
        assert operation["security"] == [{"prl_session": []}]
        assert "ErrorResponse" in str(operation["responses"])
        for parameter in operation["parameters"]:
            if parameter["name"] == "page":
                assert parameter["schema"]["default"] == 1
                assert parameter["schema"]["minimum"] == 1
            if parameter["name"] == "page_size":
                assert parameter["schema"]["default"] == 20
                assert parameter["schema"]["minimum"] == 1
                assert parameter["schema"]["maximum"] == 100
        if path.endswith("/staff"):
            purpose_id = next(p for p in operation["parameters"] if p["name"] == "purpose_id")
            assert purpose_id["schema"]["format"] == "uuid"
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert (
            response_schema["$ref"].endswith("PageResponse_PurposeItem_")
            if path.endswith("/purposes")
            else response_schema["$ref"].endswith("PageResponse_StaffItem_")
        )
        for status in ("401", "403", "404", "422", "500", "503"):
            assert {"Cache-Control", "X-Error-Class", "X-Request-ID"} <= set(
                operation["responses"][status]["headers"]
            )
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
