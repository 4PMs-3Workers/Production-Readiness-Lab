"""Integration tests create a unique schema and delete only that schema."""

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.core.database import create_database_engine
from app.models import Base


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
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP DATABASE `{schema}`"))
        await admin.dispose()
