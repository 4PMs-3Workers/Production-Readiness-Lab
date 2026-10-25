"""Task 8 operational config regressions for deferred bounded-label and Alembic warnings."""

import pytest
from alembic.config import Config
from pydantic import ValidationError

from app.core.config import Settings
from app.observability.records import UNMATCHED_ROUTE

VALID_SETTINGS = {
    "DATABASE_URL": "mysql+asyncmy://user:password@localhost:3306/prl",
    "APPLICATION_ORIGIN": "http://localhost:3000",
    "COOKIE_SECRET": "cookie-secret",
    "CSRF_SECRET": "csrf-secret",
    "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
}


def test_attribute_character_floor_accepts_the_unmatched_route_fallback():
    assert len(UNMATCHED_ROUTE) == 13
    with pytest.raises(ValidationError):
        Settings.model_validate({**VALID_SETTINGS, "TELEMETRY_MAX_ATTRIBUTE_CHARS": 12})
    settings = Settings.model_validate({**VALID_SETTINGS, "TELEMETRY_MAX_ATTRIBUTE_CHARS": 13})
    assert settings.telemetry_max_attribute_chars == 13


def test_alembic_path_separator_uses_platform_path_semantics():
    config = Config("alembic.ini")
    assert config.get_main_option("path_separator") == "os"
