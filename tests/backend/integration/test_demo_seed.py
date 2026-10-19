"""Required MySQL tests use the existing isolated, Alembic-migrated fixture."""

import os
import secrets
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.passwords import PasswordPolicy, PasswordService
from app.demo.data import SeedError, load_dataset, seed_id
from app.demo.loader import SCHEMA_REVISION, TABLES, SeedConfig, load_into_engine, run_seed
from app.factory import create_app
from app.models import (
    Appointment,
    AuditEvent,
    ChangeProposal,
    Notification,
    Purpose,
    ServiceHour,
    ServiceSetting,
    Session,
    StaffPurpose,
    StaffUnavailability,
    User,
)

pytestmark = pytest.mark.mysql
MODELS = (
    User,
    Purpose,
    StaffPurpose,
    ServiceSetting,
    ServiceHour,
    Session,
    Appointment,
    ChangeProposal,
    StaffUnavailability,
    Notification,
    AuditEvent,
)


def config_for(engine):
    return SeedConfig.from_env(
        {
            "ENVIRONMENT": "demo",
            "DATABASE_URL": engine.url.render_as_string(hide_password=False),
            "DEMO_SEED_DATABASE": engine.url.database,
            **{
                f"DEMO_{role}_PASSWORD": secrets.token_urlsafe(24)
                for role in ("ADMIN", "STAFF", "STUDENT")
            },
        }
    )


async def snapshot(engine):
    async with engine.connect() as connection:
        return {
            model.__tablename__: (await connection.execute(select(model.__table__))).all()
            for model in MODELS
        }


async def test_seed_contents_repeatability_preservation_and_login(mysql_engine, auth_settings):
    config = config_for(mysql_engine)
    data = load_dataset()
    factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    async with factory.begin() as db:
        db.add(
            User(
                email="unrelated@example.invalid",
                name="Existing Account",
                role="ADMIN",
                password_hash="untouched",
                is_active=False,
            )
        )
    result = await run_seed(config)
    assert [result[t]["inserted"] for t in TABLES] == [42, 5, 16, 1, 10]
    passwords = await PasswordService.create(PasswordPolicy(), 2)
    async with factory() as db:
        accounts = (await db.scalars(select(User))).all()
        seeded = [a for a in accounts if a.email != "unrelated@example.invalid"]
        assert Counter(a.role for a in seeded) == {"ADMIN": 2, "STAFF": 10, "STUDENT": 30}
        assert all(a.is_active for a in seeded)
        assert all(
            [await passwords.verify(config.passwords[a.role], a.password_hash) for a in seeded]
        )
        purposes = (await db.scalars(select(Purpose))).all()
        assert {
            p.code: (p.name, p.duration_minutes, p.requires_free_text, p.is_active)
            for p in purposes
        } == {
            p.code: (p.name, p.duration_minutes, p.requires_free_text, p.is_active)
            for p in data.purposes
        }
        pairs = (await db.scalars(select(StaffPurpose))).all()
        assert {(p.staff_id, p.purpose_id, p.is_accepting) for p in pairs} == {
            (seed_id("users", a.staff_email), seed_id("purposes", a.purpose_code), True)
            for a in data.assignments
        }
        setting = await db.get(ServiceSetting, 1)
        assert setting is not None
        assert all(
            getattr(setting, key) == value for key, value in data.settings.model_dump().items()
        )
        hours = (await db.scalars(select(ServiceHour))).all()
        assert {(w.weekday, w.opens_at, w.closes_at) for w in hours} == {
            (w.weekday, w.opens_at, w.closes_at) for w in data.hours
        }
        for model in (
            Session,
            Appointment,
            ChangeProposal,
            StaffUnavailability,
            Notification,
            AuditEvent,
        ):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
    # Account state, hashes and optional profile fields are application-owned after insertion.
    async with factory.begin() as db:
        account = await db.get(User, seed_id("users", data.accounts[0].email))
        assert account is not None
        account.is_active = False
        account.password_hash = await passwords.hash(secrets.token_urlsafe(24))
        account.faculty = "Khoa đã cập nhật"
        starts_at = datetime(2030, 1, 7, 1, tzinfo=UTC)
        db.add(
            Appointment(
                student_id=seed_id("users", data.accounts[12].email),
                staff_id=seed_id("users", data.assignments[0].staff_email),
                purpose_id=seed_id("purposes", "CAREER_INTERNSHIP"),
                duration_minutes=45,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=45),
                creation_request_key="previously-committed-booking",
                creation_request_hash=secrets.token_bytes(32),
            )
        )
        db.add(
            Session(
                user_id=account.id,
                token_hash=secrets.token_bytes(32),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.add(ServiceHour(weekday=6, opens_at=time(9), closes_at=time(10)))
    before = await snapshot(mysql_engine)
    again = await run_seed(config)
    assert all(v["inserted"] == 0 for v in again.values())
    assert [again[t]["existing"] for t in TABLES] == [42, 5, 16, 1, 10]
    assert await snapshot(mysql_engine) == before
    settings = auth_settings.model_copy(update={"accepted_schema_revisions_raw": SCHEMA_REVISION})
    app = create_app(settings, engine_factory=lambda *a, **kw: mysql_engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
        ) as client:
            response = await client.post(
                "/api/v1/auth/login",
                headers={"Origin": "http://localhost:3000"},
                json={"email": data.accounts[12].email, "password": config.passwords["STUDENT"]},
            )
            assert response.status_code == 200
            assert response.json()["role"] == "STUDENT"


@pytest.mark.parametrize(
    "conflict", ["email", "identifier", "role", "name", "purpose", "setting", "window", "pair"]
)
async def test_conflicts_never_partially_write(mysql_engine, conflict):
    data = load_dataset()
    config = config_for(mysql_engine)
    factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    if conflict in {"role", "name", "pair"}:
        await load_into_engine(mysql_engine, config, data)
    async with factory.begin() as db:
        if conflict in {"email", "identifier"}:
            db.add(
                User(
                    id=uuid4() if conflict == "email" else seed_id("users", data.accounts[0].email),
                    email=(
                        data.accounts[0].email if conflict == "email" else "someone@example.invalid"
                    ),
                    name=data.accounts[0].name,
                    role="ADMIN",
                    password_hash="preserved",
                )
            )
        elif conflict in {"role", "name"}:
            account = await db.get(User, seed_id("users", data.accounts[0].email))
            assert account is not None
            setattr(account, conflict, "STAFF" if conflict == "role" else "Changed Name")
        elif conflict == "purpose":
            db.add(Purpose(code="OTHER", name="Existing category", duration_minutes=15))
        elif conflict == "setting":
            db.add(ServiceSetting(id=1, booking_horizon_days=14))
        elif conflict == "window":
            db.add(ServiceHour(weekday=1, opens_at=time(9), closes_at=time(12)))
        else:
            pair = await db.get(
                StaffPurpose,
                (
                    seed_id("users", data.assignments[0].staff_email),
                    seed_id("purposes", "CAREER_INTERNSHIP"),
                ),
            )
            assert pair is not None
            pair.is_accepting = False
    before = await snapshot(mysql_engine)
    with pytest.raises(SeedError, match="Conflict"):
        await load_into_engine(mysql_engine, config, data)
    assert await snapshot(mysql_engine) == before


async def test_insertion_failure_rolls_back_all_tables(mysql_engine):
    before = await snapshot(mysql_engine)

    def fail_last_table(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO service_hours"):
            raise RuntimeError("injected final-table failure")

    event.listen(mysql_engine.sync_engine, "before_cursor_execute", fail_last_table)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await load_into_engine(mysql_engine, config_for(mysql_engine), load_dataset())
    finally:
        event.remove(mysql_engine.sync_engine, "before_cursor_execute", fail_last_table)
    assert await snapshot(mysql_engine) == before


@pytest.mark.parametrize("state", ["missing", "old", "future", "wrong_target"])
async def test_schema_and_selected_database_guards(mysql_engine, state):
    config = config_for(mysql_engine)
    if state == "wrong_target":
        config = SeedConfig(config.url, "different_database", config.passwords)
    else:
        async with mysql_engine.begin() as connection:
            if state == "missing":
                await connection.execute(text("DROP TABLE alembic_version"))
            else:
                await connection.execute(
                    text("UPDATE alembic_version SET version_num = :revision"),
                    {"revision": "a1b2c3d4e5f6" if state == "old" else "future_revision"},
                )
    before = await snapshot(mysql_engine)
    with pytest.raises((SeedError, SQLAlchemyError)):
        await load_into_engine(mysql_engine, config, load_dataset())
    assert await snapshot(mysql_engine) == before


async def test_root_cli_twice(mysql_engine):
    import asyncio

    config = config_for(mysql_engine)
    env = {
        **os.environ,
        "DATABASE_URL": config.url.render_as_string(hide_password=False),
        "ENVIRONMENT": "demo",
        "DEMO_SEED_DATABASE": config.target,
        **{f"DEMO_{role}_PASSWORD": value for role, value in config.passwords.items()},
    }
    for inserted, existing in [(42, 0), (0, 42)]:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "tools/scripts/seed_demo.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert (
            result.returncode == 0
        ), "Seed command failed (output withheld to avoid credential leaks)"
        assert f"users: inserted={inserted} existing={existing}" in result.stdout
        assert not result.stderr
        for value in config.passwords.values():
            assert value not in result.stdout + result.stderr
