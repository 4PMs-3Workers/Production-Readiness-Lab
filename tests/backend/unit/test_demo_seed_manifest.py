"""Manifest review, configuration guards and sanitized command failures."""

import secrets

import pytest
from pydantic import ValidationError

from app.demo.data import Dataset, SeedError, load_dataset, seed_id
from app.demo.loader import SeedConfig


def environment():
    return {
        "ENVIRONMENT": "demo",
        "DATABASE_URL": "mysql+asyncmy://seed:unused@localhost/prl_demo",
        "DEMO_SEED_DATABASE": "prl_demo",
        **{
            f"DEMO_{role}_PASSWORD": secrets.token_urlsafe(24)
            for role in ("ADMIN", "STAFF", "STUDENT")
        },
    }


def test_reviewed_dataset():
    data = load_dataset()
    assert len(data.accounts) == 42
    assert len(data.purposes) == 5
    assert len(data.assignments) == 16
    assert len(data.hours) == 10
    assert {w.weekday for w in data.hours} == {1, 2, 3, 4, 5}
    assert seed_id("users", data.accounts[0].email) == seed_id("users", data.accounts[0].email)
    assert len({seed_id("users", a.email) for a in data.accounts}) == 42


@pytest.mark.parametrize(
    "field,value",
    [
        ("ENVIRONMENT", "production"),
        ("ENVIRONMENT", ""),
        ("ENVIRONMENT", "test"),
        ("DEMO_SEED_DATABASE", ""),
        ("DEMO_SEED_DATABASE", "mysql"),
        ("DEMO_SEED_DATABASE", "information_schema"),
        ("DEMO_SEED_DATABASE", "performance_schema"),
        ("DEMO_SEED_DATABASE", "SYS"),
        ("DEMO_SEED_DATABASE", "other_database"),
        ("DATABASE_URL", "mysql+asyncmy://localhost"),
        ("DATABASE_URL", "sqlite:///prl_demo"),
        ("DATABASE_URL", "not-a-url"),
        ("DEMO_ADMIN_PASSWORD", ""),
        ("DEMO_STAFF_PASSWORD", "short"),
        ("DEMO_STUDENT_PASSWORD", "replace-with-local-student-password"),
        ("DEMO_STUDENT_PASSWORD", " " * 20),
        ("DEMO_STUDENT_PASSWORD", "x" * 1025),
    ],
)
def test_configuration_guards(field, value):
    env = environment()
    env[field] = value
    with pytest.raises(SeedError):
        SeedConfig.from_env(env)


def test_configuration_does_not_expose_secrets():
    env = environment()
    config = SeedConfig.from_env(env)
    assert "unused" not in repr(config)
    for role in ("ADMIN", "STAFF", "STUDENT"):
        assert env[f"DEMO_{role}_PASSWORD"] not in repr(config)


@pytest.mark.parametrize(
    "defect", ["count", "email", "role", "other", "coverage", "hours", "extra"]
)
def test_invalid_manifest_rejected(defect):
    raw = load_dataset().model_dump(mode="json")
    if defect == "count":
        raw["accounts"].pop()
    elif defect == "email":
        raw["accounts"][1]["email"] = raw["accounts"][0]["email"]
    elif defect == "role":
        raw["assignments"][0]["staff_email"] = raw["accounts"][0]["email"]
    elif defect == "other":
        raw["purposes"][-1]["requires_free_text"] = False
    elif defect == "coverage":
        raw["assignments"] = raw["assignments"][:1]
    elif defect == "hours":
        raw["hours"][1] = {"weekday": 1, "opens_at": "09:00:00", "closes_at": "12:00:00"}
    else:
        raw["accounts"][0]["password"] = "forbidden"
    with pytest.raises((SeedError, ValidationError)):
        Dataset.model_validate(raw).review()


def test_command_sanitizes_unexpected_exceptions(monkeypatch, capsys):
    import runpy

    script = runpy.run_path("tools/scripts/seed_demo.py")
    main = script["main"]
    env = environment()
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    async def fail(*args):
        raise RuntimeError("credential-bearing-url-and-hash")

    monkeypatch.setitem(main.__globals__, "run_seed", fail)
    assert main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert "credential-bearing-url-and-hash" not in output.err
    assert "no changes committed" in output.err


@pytest.mark.parametrize("failure", [False, True])
async def test_engine_always_disposed(monkeypatch, failure):
    from unittest.mock import AsyncMock

    from app.demo import loader

    engine = AsyncMock()
    operation = AsyncMock(return_value={})
    if failure:
        operation.side_effect = SeedError("Schema: incompatible revision")
    monkeypatch.setattr(loader, "create_database_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(loader, "load_into_engine", operation)
    config = SeedConfig.from_env(environment())
    if failure:
        with pytest.raises(SeedError):
            await loader.run_seed(config)
    else:
        assert await loader.run_seed(config) == {}
    engine.dispose.assert_awaited_once()
