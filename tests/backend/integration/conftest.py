"""Integration tests create a unique schema and delete only that schema.

Schema initialization uses ``alembic upgrade head`` instead of
``Base.metadata.create_all()``.  This ensures the integration tests exercise
the migration path on every run, not just the ORM metadata snapshot.

Safety boundaries (unchanged from the original design):
- A uniquely named schema is created per test session; only that schema is
  dropped in the finally block, even when migration setup fails.
- The admin connection (database=None) creates and drops the schema;
  the test engine connects only to the isolated schema.
"""

import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import create_database_engine
from app.core.passwords import PasswordPolicy, PasswordService
from app.factory import create_app
from app.models import User


def _run_alembic_upgrade(connection: Connection) -> None:
    """Run ``alembic upgrade head`` using an already-open synchronous connection.

    The env.py detects ``config.attributes["connection"]`` and uses it directly,
    so migrations run inside the test schema's connection context.
    """
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def mysql_engine():
    url = os.getenv("MYSQL_TEST_URL")
    if not url:
        pytest.skip("Set MYSQL_TEST_URL to run tests against MySQL 8.4")
    schema = "prl_models_test_" + uuid4().hex
    admin = create_database_engine(
        make_url(url).set(database=None), poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    engine = None
    created = False
    try:
        async with admin.begin() as connection:
            version = (await connection.execute(text("SELECT VERSION()"))).scalar_one()
            assert version.startswith("8.4."), f"Expected MySQL 8.4, got {version}"
            await connection.execute(
                text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            )
            created = True
        engine = create_database_engine(
            make_url(url).set(database=schema),
            poolclass=NullPool,
            connect_args={"connect_timeout": 5},
        )
        # Replace Base.metadata.create_all() with alembic upgrade head.
        # This exercises the full migration path on every integration test run.
        async with engine.begin() as async_conn:
            await async_conn.run_sync(_run_alembic_upgrade)
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP DATABASE `{schema}`"))
        await admin.dispose()


@pytest_asyncio.fixture
async def auth_env(mysql_engine, auth_settings):
    settings = auth_settings.model_copy(
        update={"login_ip_attempts": 100, "login_email_attempts": 100}
    )
    passwords = await PasswordService.create(PasswordPolicy(), 2)
    factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    async with factory.begin() as db:
        user = User(
            email="student@example.invalid",
            name="Test Student",
            role="STUDENT",
            password_hash=await passwords.hash("  Good-password!  "),
            is_active=True,
        )
        db.add(user)
    app = create_app(settings, engine_factory=lambda *a, **kw: mysql_engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
        ) as client:
            yield client, factory, user.id, app
