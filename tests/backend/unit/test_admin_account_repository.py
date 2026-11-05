from typing import Any, cast

from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.repositories import appointments


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
