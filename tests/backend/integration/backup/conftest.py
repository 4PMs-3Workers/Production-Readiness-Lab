from dataclasses import replace
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tests.backend.integration.test_mysql_models import graph

from app.backup.config import BackupConfig

__all__ = ["graph"]


@pytest_asyncio.fixture
async def recovery_config(mysql_engine, tmp_path):
    target = "prl_restore_test_" + uuid4().hex
    admin = create_async_engine(mysql_engine.url.set(database=None), poolclass=NullPool)
    created = False
    try:
        async with admin.begin() as db:
            await db.execute(
                text(f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            )
            created = True
        source = BackupConfig(mysql_engine.url.render_as_string(hide_password=False), tmp_path)
        yield replace(
            source,
            target_url=mysql_engine.url.set(database=target).render_as_string(hide_password=False),
        )
    finally:
        if created:
            async with admin.begin() as db:
                await db.execute(text(f"DROP DATABASE `{target}`"))
        await admin.dispose()
