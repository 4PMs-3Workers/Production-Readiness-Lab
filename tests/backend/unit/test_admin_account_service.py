from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
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


def test_non_student_account_cannot_receive_student_profile_fields():
    with pytest.raises(AppError) as error:
        admin_accounts._validate_student_profile_changes("STAFF", {"student_code": "S-123"})

    assert error.value.status_code == 422
    assert error.value.code == "STUDENT_PROFILE_ROLE_REQUIRED"
