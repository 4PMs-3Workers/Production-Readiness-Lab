import pytest

from app.core.config import Settings


def pytest_addoption(parser):
    parser.addoption("--require-mysql", action="store_true", help="Fail if MySQL tests cannot run")


@pytest.fixture
def auth_settings():
    return Settings.model_validate(
        {
            "DATABASE_URL": "mysql+asyncmy://test:placeholder@127.0.0.1:1/test",
            "APPLICATION_ORIGIN": "http://localhost:3000",
            "COOKIE_SECRET": "test-only-cookie-secret",
            "CSRF_SECRET": "test-only-csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
            "COOKIE_SECURE": False,
            "ENVIRONMENT": "test",
        }
    )
