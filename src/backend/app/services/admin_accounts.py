"""Administrator account reads and transactional account creation."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

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
from app.models import AuditEvent, ServiceSetting, User
from app.repositories import appointments, users
from app.schemas.admin_accounts import (
    AccountCreateRequest,
    AccountItem,
    AccountLifecycleRequest,
    AccountQuery,
    AccountUpdateRequest,
    PasswordResetRequest,
)
from app.schemas.common import PageResponse
from app.services import auth
from app.services.admin_access import lock_active_session, lock_admin, require_admin
from app.services.admin_writes import write_under_policy_lock

T = TypeVar("T")
STUDENT_PROFILE_FIELDS = ("student_code", "faculty", "major", "gender")


def account_error(code: str, message: str, status: int = 409) -> AppError:
    error_class = "input" if status < 500 else "service"
    return AppError(status, code, message, error_class)


def _validate_student_profile_changes(role: str, requested: dict[str, object]) -> None:
    if role != "STUDENT" and any(
        requested.get(field) is not None for field in STUDENT_PROFILE_FIELDS
    ):
        raise account_error(
            "STUDENT_PROFILE_ROLE_REQUIRED",
            "Student profile fields require the STUDENT role",
            422,
        )


async def list_accounts(
    db: AsyncSession,
    query: AccountQuery,
    *,
    context: AuthContext,
    settings: Settings,
) -> PageResponse[AccountItem]:
    async with read_transaction(db, settings):
        await lock_admin(db, context, settings)
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
                            await lock_admin(db, context, settings)
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


async def _run_mutation(
    db: AsyncSession,
    operation: Callable[[], Awaitable[tuple[T, bool]]],
    *,
    settings: Settings,
    duplicate_code: str | None = None,
) -> T:
    async def apply(_: ServiceSetting) -> tuple[T, bool]:
        return await operation()

    duplicate = (
        account_error(duplicate_code, "An account with this email already exists")
        if duplicate_code is not None
        else None
    )
    return await write_under_policy_lock(
        db, apply, settings=settings, duplicate_error=duplicate, commit=commit_safely
    )


async def _lock_actor_and_target(
    db: AsyncSession, context: AuthContext, target_id: UUID, settings: Settings
) -> tuple[User, User]:
    locked: dict[UUID, User | None] = {}
    for user_id in sorted({context.user_id, target_id}, key=lambda value: value.bytes):
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            locked[user_id] = await users.lock_by_id(db, user_id)
    actor = require_admin(locked.get(context.user_id))
    await lock_active_session(db, context, settings)
    target = locked.get(target_id)
    if target is None:
        raise account_error("ACCOUNT_NOT_FOUND", "Account not found", 404)
    return actor, target


async def _next_updated_at(db: AsyncSession, user: User) -> datetime:
    """Use a strictly advancing database timestamp as the account version token."""
    now = await users.utc_now(db)
    return max(now, user.updated_at + timedelta(microseconds=1))


def mark_account_deleted(user: User, deleted_at: datetime) -> None:
    """Move an account into the deleted state while remembering its prior activity."""
    if user.deleted_at is not None:
        raise account_error("ACCOUNT_ALREADY_DELETED", "Account is already deleted")
    if user.is_active_before_delete is not None:
        raise ServiceUnavailable()
    user.is_active_before_delete = bool(user.is_active)
    user.is_active = False
    user.deleted_at = deleted_at


def restore_account_state(user: User) -> None:
    """Restore the pre-delete active flag without restoring any old session."""
    if user.deleted_at is None or user.is_active_before_delete is None:
        raise account_error("ACCOUNT_NOT_DELETED", "Account is not deleted")
    user.is_active = user.is_active_before_delete
    user.deleted_at = None
    user.is_active_before_delete = None


async def soft_delete_account(
    db: AsyncSession,
    account_id: UUID,
    request: AccountLifecycleRequest,
    *,
    context: AuthContext,
    settings: Settings,
    request_id: str,
) -> AccountItem:
    async def operation() -> tuple[AccountItem, bool]:
        _, user = await _lock_actor_and_target(db, context, account_id, settings)
        if user.deleted_at is not None:
            raise account_error("ACCOUNT_SOFT_DELETED", "Restore the account before editing it")
        if user.updated_at != request.expected_updated_at.astimezone(UTC):
            raise account_error(
                "ACCOUNT_VERSION_CONFLICT", "Account changed; reload before retrying"
            )
        if user.deleted_at is not None:
            raise account_error("ACCOUNT_ALREADY_DELETED", "Account is already deleted")
        if user.role == "STAFF" and await users.has_occupying_staff_appointments(db, user.id):
            raise account_error(
                "STAFF_HAS_OCCUPYING_APPOINTMENTS",
                "Staff with occupying appointments cannot be deleted",
            )
        if user.is_active and user.role in {"ADMIN", "STAFF"}:
            if await users.count_active_role(db, user.role) <= 1:
                code = "LAST_ACTIVE_ADMIN" if user.role == "ADMIN" else "LAST_ACTIVE_STAFF"
                raise account_error(code, "At least one active account of this role must remain")

        was_active = bool(user.is_active)
        mark_account_deleted(user, await users.utc_now(db))
        user.updated_at = await _next_updated_at(db, user)
        await auth.revoke_user_sessions(db, user.id)
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="ACCOUNT_SOFT_DELETED",
                entity_type="USER",
                entity_id=str(user.id),
                request_id=request_id,
                event_metadata={
                    "previous_is_active": was_active,
                    "sessions_revoked": True,
                    "linked_records_preserved": True,
                },
            )
        )
        return AccountItem.model_validate(user), True

    return await _run_mutation(db, operation, settings=settings)


async def restore_account(
    db: AsyncSession,
    account_id: UUID,
    request: AccountLifecycleRequest,
    *,
    context: AuthContext,
    settings: Settings,
    request_id: str,
) -> AccountItem:
    async def operation() -> tuple[AccountItem, bool]:
        _, user = await _lock_actor_and_target(db, context, account_id, settings)
        if user.updated_at != request.expected_updated_at.astimezone(UTC):
            raise account_error(
                "ACCOUNT_VERSION_CONFLICT", "Account changed; reload before retrying"
            )
        restore_account_state(user)
        user.updated_at = await _next_updated_at(db, user)
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="ACCOUNT_RESTORED",
                entity_type="USER",
                entity_id=str(user.id),
                request_id=request_id,
                event_metadata={
                    "restored_is_active": bool(user.is_active),
                    "old_sessions_remain_revoked": True,
                    "linked_records_preserved": True,
                },
            )
        )
        return AccountItem.model_validate(user), True

    return await _run_mutation(db, operation, settings=settings)


async def update_account(
    db: AsyncSession,
    account_id: UUID,
    request: AccountUpdateRequest,
    *,
    context: AuthContext,
    settings: Settings,
    request_id: str,
) -> AccountItem:
    async def operation() -> tuple[AccountItem, bool]:
        _, user = await _lock_actor_and_target(db, context, account_id, settings)
        if user.updated_at != request.expected_updated_at.astimezone(UTC):
            raise account_error(
                "ACCOUNT_VERSION_CONFLICT", "Account changed; reload before retrying"
            )

        requested = {
            field: getattr(request, field)
            for field in (
                "email",
                "name",
                "role",
                "is_active",
                "student_code",
                "faculty",
                "major",
                "gender",
            )
            if field in request.model_fields_set
        }
        new_role = requested.get("role", user.role)
        _validate_student_profile_changes(new_role, requested)
        if new_role != "STUDENT":
            for field in STUDENT_PROFILE_FIELDS:
                if getattr(user, field) is not None:
                    requested[field] = None
        changes = {
            field: value for field, value in requested.items() if getattr(user, field) != value
        }
        if not changes:
            return AccountItem.model_validate(user), False

        old_role = user.role
        new_role = changes.get("role", user.role)
        new_active = changes.get("is_active", user.is_active)
        role_changed = new_role != user.role
        active_changed = new_active != user.is_active
        if role_changed and await users.has_role_references(db, user.id):
            raise account_error(
                "ROLE_CHANGE_REFERENCED", "Role cannot change while account history references it"
            )
        if user.role == "STAFF" and user.is_active and not new_active:
            if await users.has_occupying_staff_appointments(db, user.id):
                raise account_error(
                    "STAFF_HAS_OCCUPYING_APPOINTMENTS",
                    "Staff with occupying appointments cannot be deactivated",
                )
        if user.is_active and (role_changed or not new_active):
            if (
                user.role in {"ADMIN", "STAFF"}
                and await users.count_active_role(db, user.role) <= 1
            ):
                code = "LAST_ACTIVE_ADMIN" if user.role == "ADMIN" else "LAST_ACTIVE_STAFF"
                raise account_error(code, "At least one active account of this role must remain")

        for field, value in changes.items():
            setattr(user, field, value)
        user.updated_at = await _next_updated_at(db, user)
        if role_changed or active_changed:
            await auth.revoke_user_sessions(db, user.id)

        metadata: dict[str, object] = {"changed_fields": sorted(changes)}
        if role_changed:
            metadata.update({"from_role": old_role, "to_role": new_role})
        if active_changed:
            metadata.update({"is_active": new_active})
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="ACCOUNT_UPDATED",
                entity_type="USER",
                entity_id=str(user.id),
                request_id=request_id,
                event_metadata=metadata,
            )
        )
        return AccountItem.model_validate(user), True

    return await _run_mutation(
        db,
        operation,
        settings=settings,
        duplicate_code=("EMAIL_ALREADY_EXISTS" if "email" in request.model_fields_set else None),
    )


async def reset_password(
    db: AsyncSession,
    account_id: UUID,
    request: PasswordResetRequest,
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

            async def operation() -> tuple[AccountItem, bool]:
                _, user = await _lock_actor_and_target(db, context, account_id, settings)
                if user.deleted_at is not None:
                    raise account_error("ACCOUNT_SOFT_DELETED", "Restore the account before resetting its password")
                if user.updated_at != request.expected_updated_at.astimezone(UTC):
                    raise account_error(
                        "ACCOUNT_VERSION_CONFLICT", "Account changed; reload before retrying"
                    )
                user.password_hash = password_hash
                user.updated_at = await _next_updated_at(db, user)
                await auth.revoke_user_sessions(db, user.id)
                db.add(
                    AuditEvent(
                        actor_id=context.user_id,
                        action="ACCOUNT_PASSWORD_RESET",
                        entity_type="USER",
                        entity_id=str(user.id),
                        request_id=request_id,
                        event_metadata={"sessions_revoked": True},
                    )
                )
                return AccountItem.model_validate(user), True

            return await _run_mutation(db, operation, settings=settings)
    except AppError:
        raise
    except (SQLAlchemyError, TimeoutError, CommitOutcomeUnknown):
        await rollback(db)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await rollback(db)
        raise
