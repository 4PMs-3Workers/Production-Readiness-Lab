from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.repositories import appointments, sessions


async def test_account_change_uses_exclusive_shared_settings_lock():
    class StatementCapture:
        statement: Select[Any] | None = None

        async def scalar(self, statement: Select[Any]) -> object:
            self.statement = statement
            return object()

    capture = StatementCapture()

    await appointments.lock_service_settings_for_account_change(cast(AsyncSession, capture))

    assert capture.statement is not None
    sql = str(capture.statement.compile(dialect=mysql.dialect())).upper()
    assert "FOR UPDATE" in sql
    assert "LOCK IN SHARE MODE" not in sql


async def test_admin_session_recheck_locks_only_an_active_owned_session():
    class StatementCapture:
        statement: Select[Any] | None = None

        async def scalar(self, statement: Select[Any]) -> object:
            self.statement = statement
            return object()

    capture = StatementCapture()
    await sessions.lock_active_by_id(
        cast(AsyncSession, capture), uuid4(), uuid4(), datetime.now(UTC)
    )

    assert capture.statement is not None
    sql = str(capture.statement.compile(dialect=mysql.dialect())).upper()
    assert "FOR UPDATE" in sql
    assert "REVOKED_AT IS NULL" in sql
    assert "EXPIRES_AT >" in sql
