from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import admin_accounts


async def test_account_version_timestamp_advances_when_database_clock_repeats(monkeypatch):
    current = datetime(2026, 11, 5, 12, 0, 0, 123456, tzinfo=UTC)

    async def database_now(_db):
        return current

    monkeypatch.setattr(admin_accounts.users, "utc_now", database_now)

    updated_at = await admin_accounts._next_updated_at(
        cast(AsyncSession, object()), cast(User, SimpleNamespace(updated_at=current))
    )

    assert updated_at == current + timedelta(microseconds=1)
