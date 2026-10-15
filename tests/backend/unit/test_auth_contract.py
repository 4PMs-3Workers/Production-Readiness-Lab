from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALUES = {
    "DATABASE_URL": "mysql+asyncmy://test:placeholder@127.0.0.1:1/test",
    "APPLICATION_ORIGIN": "http://localhost:3000",
    "COOKIE_SECRET": "test-cookie-secret",
    "CSRF_SECRET": "test-csrf-secret",
    "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
    "COOKIE_SECURE": False,
}


def test_login_preserves_password_and_normalizes_email():
    from app.schemas.auth import LoginRequest

    value = LoginRequest.model_validate({"email": " A@Example.invalid ", "password": "  AbC!  "})
    assert value.email == "a@example.invalid"
    assert value.password.get_secret_value() == "  AbC!  "
    assert "AbC!" not in repr(value)


@pytest.mark.parametrize(
    "data",
    [
        {"email": " ", "password": "a"},
        {"email": "a", "password": ""},
        {"email": 123, "password": "a"},
        {"email": "a", "password": 123},
        {"email": "a", "password": "a", "role": "ADMIN"},
        {"email": "a" * 255, "password": "a"},
        {"email": "a", "password": "a" * 1025},
    ],
)
def test_login_rejects_invalid_credentials_shape(data):
    from app.schemas.auth import LoginRequest

    with pytest.raises(ValidationError):
        LoginRequest.model_validate(data)


def test_public_user_has_only_approved_fields():
    from app.schemas.auth import PublicUser

    assert set(PublicUser(id=uuid4(), email="a", name="A", role="STUDENT").model_dump()) == {
        "id",
        "email",
        "name",
        "role",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"SESSION_LIFETIME_SECONDS": 0},
        {"SESSION_LIFETIME_SECONDS": 2592001},
        {"LOGIN_MAX_BODY_BYTES": 0},
        {"LOGIN_IP_ATTEMPTS": 0},
        {"LOGIN_EMAIL_ATTEMPTS": 0},
        {"LOGIN_WINDOW_SECONDS": 0},
        {"LOGIN_STATE_MAX_ENTRIES": 0},
        {"PASSWORD_MAX_CONCURRENCY": 0},
        {"COOKIE_DOMAIN": "example.invalid"},
        {"COOKIE_SAMESITE": "strict"},
    ],
)
def test_auth_configuration_rejects_unsafe_policy(changes):
    with pytest.raises(ValidationError):
        Settings.model_validate(VALUES | changes)


def test_auth_error_uses_shared_envelope_contract():
    from app.api.auth_errors import auth_error

    error = auth_error("LOGIN_THROTTLED", retry_after=60)
    assert (error.status_code, error.error_class) == (429, "input")
    assert error.headers is not None
    assert error.headers["Retry-After"] == "60"
    assert auth_error("INVALID_CREDENTIALS").message == "Invalid email or password"
