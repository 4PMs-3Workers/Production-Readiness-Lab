"""Administrator account reads and transactional account creation."""

import asyncio
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, ServiceUnavailable
from app.core.config import Settings
from app.core.passwords import PasswordService
from app.core.transactions import (
    RETRYABLE_MYSQL_ERRORS,
    CommitOutcomeUnknown,
    commit_safely,
    mysql_error_code,
    read_transaction,
    rollback,
)
from app.domain.auth import AuthContext
from app.models import AuditEvent, User
from app.repositories import appointments, users
from app.schemas.admin_accounts import AccountCreateRequest, AccountItem, AccountQuery
from app.schemas.common import PageResponse


def account_error(code: str, message: str, status: int = 409) -> AppError:
    error_class = "input" if status < 500 else "service"
    return AppError(status, code, message, error_class)


def _require_admin(user: User | None) -> User:
    if user is None or user.role != "ADMIN" or not user.is_active:
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")
    return user


async def _lock_admin(db: AsyncSession, context: AuthContext, settings: Settings) -> User:
    async with asyncio.timeout(settings.db_lock_timeout_seconds):
        actor = await users.lock_by_id(db, context.user_id)
    return _require_admin(actor)


async def list_accounts(
    db: AsyncSession,
    query: AccountQuery,
    *,
    context: AuthContext,
    settings: Settings,
) -> PageResponse[AccountItem]:
    async with read_transaction(db, settings):
        await _lock_admin(db, context, settings)
        rows, total = await users.read_account_page(db, query)
        items = [AccountItem.model_validate(row) for row in rows]
    return PageResponse(items=items, page=query.page, page_size=query.page_size, total=total)


async def create_account(
    db: AsyncSession,
    request: AccountCreateRequest,
    *,
    context: AuthContext,
    settings: Settings,
    request_id: str,
    passwords: PasswordService,
) -> AccountItem:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.request_timeout_seconds
    try:
        async with asyncio.timeout_at(deadline):
            password_hash = await passwords.hash(request.password.get_secret_value())
            user_id = uuid4()
            for attempt in range(settings.max_deadlock_retries + 1):
                try:
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        transaction = await db.begin()
                        try:
                            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                                policy = (
                                    await appointments.lock_service_settings_for_account_change(db)
                                )
                            if policy is None:
                                raise ServiceUnavailable()
                            await _lock_admin(db, context, settings)
                            user = User(
                                id=user_id,
                                email=request.email,
                                name=request.name,
                                role=request.role,
                                is_active=request.is_active,
                                password_hash=password_hash,
                                student_code=request.student_code,
                                faculty=request.faculty,
                                major=request.major,
                                gender=request.gender,
                            )
                            await users.add_account(db, user)
                            db.add(
                                AuditEvent(
                                    actor_id=context.user_id,
                                    action="ACCOUNT_CREATED",
                                    entity_type="USER",
                                    entity_id=str(user.id),
                                    request_id=request_id,
                                    event_metadata={
                                        "role": request.role,
                                        "is_active": request.is_active,
                                    },
                                )
                            )
                            await db.flush()
                            await commit_safely(db, transaction)
                            return AccountItem.model_validate(user)
                        except BaseException:
                            await rollback(db)
                            raise
                except SQLAlchemyError as error:
                    await rollback(db)
                    code = mysql_error_code(error)
                    if code == 1062:
                        raise account_error(
                            "EMAIL_ALREADY_EXISTS", "An account with this email already exists"
                        ) from None
                    if (
                        code not in RETRYABLE_MYSQL_ERRORS
                        or attempt >= settings.max_deadlock_retries
                    ):
                        raise ServiceUnavailable() from None
                    delay = min(
                        0.01 * 2**attempt,
                        max(0.0, deadline - asyncio.get_running_loop().time()),
                    )
                    if delay <= 0:
                        raise ServiceUnavailable() from None
                    await asyncio.sleep(delay)
            raise ServiceUnavailable()
    except AppError:
        raise
    except (SQLAlchemyError, TimeoutError, CommitOutcomeUnknown):
        await rollback(db)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await rollback(db)
        raise
