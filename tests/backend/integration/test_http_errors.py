import pytest
from fastapi import Body
from fastapi.testclient import TestClient

from app.api.errors import BusinessConflict, ServiceUnavailable
from app.core.config import Settings
from app.factory import create_app


def make_app():
    app = create_app(
        Settings.model_validate(
            {
                "DATABASE_URL": "mysql+asyncmy://user:password@127.0.0.1:1/prl",
                "APPLICATION_ORIGIN": "http://localhost:3000",
                "COOKIE_SECRET": "cookie-secret",
                "CSRF_SECRET": "csrf-secret",
                "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
            }
        )
    )

    @app.get("/__test/method", include_in_schema=False)
    def method():
        return {"ok": True}

    @app.post("/__test/validate", include_in_schema=False)
    def validate(value: int = Body()):
        return {"value": value}

    @app.get("/__test/conflict", include_in_schema=False)
    def conflict():
        raise BusinessConflict("STAFF_UNAVAILABLE", "The selected slot is unavailable")

    @app.get("/__test/unavailable", include_in_schema=False)
    def unavailable():
        raise ServiceUnavailable()

    @app.get("/__test/crash", include_in_schema=False)
    def crash():
        raise RuntimeError("SQL SELECT secret_token=hidden trace")

    return app


@pytest.mark.parametrize(
    ("method", "path", "json", "status", "code", "error_class"),
    [
        ("GET", "/missing", None, 404, "NOT_FOUND", "input"),
        ("POST", "/__test/method", None, 405, "METHOD_NOT_ALLOWED", "input"),
        ("POST", "/__test/validate", "secret_input", 422, "VALIDATION_ERROR", "input"),
        ("GET", "/__test/conflict", None, 409, "STAFF_UNAVAILABLE", "business"),
        ("GET", "/__test/unavailable", None, 503, "SERVICE_UNAVAILABLE", "service"),
        ("GET", "/__test/crash", None, 500, "INTERNAL_SERVER_ERROR", "service"),
    ],
)
def test_error_envelope_and_headers(method, path, json, status, code, error_class):
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        result = client.request(method, path, json=json)
    assert result.status_code == status
    assert result.headers["X-Error-Class"] == error_class
    assert result.json()["error"]["code"] == code
    assert result.json()["error"]["request_id"] == result.headers["X-Request-ID"]
    if status == 405:
        assert "GET" in result.headers["Allow"]
    if status in {422, 500}:
        assert "secret_input" not in result.text
        assert "secret_token" not in result.text
