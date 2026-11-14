from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.api.errors import AppError
from app.models import User
from app.repositories import users
from app.schemas.admin_accounts import AccountLifecycleRequest, AccountQuery
from app.services import admin_accounts

DELETED_AT = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)


def test_lifecycle_request_requires_timezone_aware_version():
    request = AccountLifecycleRequest.model_validate(
        {"expected_updated_at": "2026-11-14T12:00:00Z"}
    )

    assert request.expected_updated_at == DELETED_AT

    with pytest.raises(ValueError):
        AccountLifecycleRequest.model_validate({"expected_updated_at": "2026-11-14T12:00:00"})


def test_soft_delete_remembers_the_previous_active_state():
    user = cast(
        User,
        SimpleNamespace(
            is_active=True,
            deleted_at=None,
            is_active_before_delete=None,
        ),
    )

    admin_accounts.mark_account_deleted(user, DELETED_AT)

    assert user.deleted_at == DELETED_AT
    assert user.is_active is False
    assert user.is_active_before_delete is True


def test_restore_recovers_the_previous_active_state():
    user = cast(
        User,
        SimpleNamespace(
            is_active=False,
            deleted_at=DELETED_AT,
            is_active_before_delete=True,
        ),
    )

    admin_accounts.restore_account_state(user)

    assert user.deleted_at is None
    assert user.is_active is True
    assert user.is_active_before_delete is None


def test_restore_keeps_a_previously_inactive_account_inactive():
    user = cast(
        User,
        SimpleNamespace(
            is_active=False,
            deleted_at=DELETED_AT,
            is_active_before_delete=False,
        ),
    )

    admin_accounts.restore_account_state(user)

    assert user.is_active is False
    assert user.deleted_at is None
    assert user.is_active_before_delete is None


def test_soft_delete_rejects_a_second_delete():
    user = cast(
        User,
        SimpleNamespace(
            is_active=False,
            deleted_at=DELETED_AT,
            is_active_before_delete=True,
        ),
    )

    with pytest.raises(AppError) as error:
        admin_accounts.mark_account_deleted(user, DELETED_AT)

    assert error.value.code == "ACCOUNT_ALREADY_DELETED"


def test_restore_rejects_an_account_that_is_not_deleted():
    user = cast(
        User,
        SimpleNamespace(
            is_active=True,
            deleted_at=None,
            is_active_before_delete=None,
        ),
    )

    with pytest.raises(AppError) as error:
        admin_accounts.restore_account_state(user)

    assert error.value.code == "ACCOUNT_NOT_DELETED"


class StatementCapture:
    def __init__(self) -> None:
        self.statements: list[Select[Any]] = []

    async def scalar(self, statement: Select[Any]) -> int:
        self.statements.append(statement)
        return 0

    async def scalars(self, statement: Select[Any]) -> list[Any]:
        self.statements.append(statement)
        return []


@pytest.mark.asyncio
async def test_account_page_excludes_soft_deleted_accounts_by_default():
    capture = StatementCapture()

    await users.read_account_page(
        cast(AsyncSession, capture),
        AccountQuery.model_validate({"page": 1, "page_size": 20}),
    )

    sql = "\\n".join(
        str(statement.compile(dialect=mysql.dialect())).lower() for statement in capture.statements
    )
    assert "deleted_at is null" in sql


@pytest.mark.asyncio
async def test_account_page_can_include_deleted_accounts_explicitly():
    capture = StatementCapture()

    await users.read_account_page(
        cast(AsyncSession, capture),
        AccountQuery.model_validate({"page": 1, "page_size": 20, "include_deleted": True}),
    )

    sql = "\\n".join(
        str(statement.compile(dialect=mysql.dialect())).lower() for statement in capture.statements
    )
    assert "deleted_at is null" not in sql
