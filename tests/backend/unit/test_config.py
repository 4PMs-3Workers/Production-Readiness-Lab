"""Configuration errors must fail startup without disclosing supplied values."""

import pytest

from app.core.config import ConfigurationError, Settings, load_settings

VALID = {
    "DATABASE_URL": "mysql+asyncmy://user:password@localhost:3306/prl",
    "APPLICATION_ORIGIN": "http://localhost:3000",
    "COOKIE_SECRET": "cookie-secret",
    "CSRF_SECRET": "csrf-secret",
    "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
}


def test_settings_accept_initial_revision_and_empty_gateway_list():
    settings = Settings.model_validate(VALID)
    assert settings.accepted_schema_revisions == frozenset({"a1b2c3d4e5f6"})
    assert settings.trusted_gateway_cidrs == ()
    assert settings.readiness_timeout_seconds == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("DATABASE_URL", "sqlite:///tmp.db"),
        ("APPLICATION_ORIGIN", "https://example.com/path"),
        ("COOKIE_SECRET", "   "),
        ("CSRF_SECRET", ""),
        ("ACCEPTED_SCHEMA_REVISIONS", " "),
        ("TRUSTED_GATEWAY_CIDRS", "not-a-cidr"),
        ("DB_POOL_SIZE", 0),
        ("DB_MAX_OVERFLOW", -1),
        ("READINESS_TIMEOUT_SECONDS", 0),
        ("DB_QUERY_TIMEOUT_SECONDS", -1),
    ],
)
def test_settings_reject_invalid_values(field, value):
    with pytest.raises(ValueError):
        Settings.model_validate({**VALID, field: value})


def test_missing_required_secret_is_rejected():
    values = {name: value for name, value in VALID.items() if name != "COOKIE_SECRET"}
    with pytest.raises(ValueError):
        Settings.model_validate(values)


def test_startup_configuration_error_does_not_echo_password(monkeypatch):
    for name, value in VALID.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:supersecret@localhost:3306/prl")
    monkeypatch.setenv("APPLICATION_ORIGIN", "https://example.com/private-path")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "supersecret" not in str(error.value)
    assert "private-path" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"ENVIRONMENT": "production", "COOKIE_SECURE": False},
        {"ENVIRONMENT": "demo", "APPLICATION_ORIGIN": "http://example.com"},
        {"COOKIE_SAMESITE": "none", "COOKIE_SECURE": False},
        {"COOKIE_DOMAIN": "https://example.com"},
    ],
)
def test_cookie_policy_rejects_insecure_or_malformed_values(changes):
    with pytest.raises(ValueError):
        Settings.model_validate({**VALID, **changes})


def test_startup_reports_invalid_cross_field_policy_without_leaking_values(monkeypatch):
    for name, value in VALID.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "policy" in str(error.value)
    assert "cookie-secret" not in str(error.value)
