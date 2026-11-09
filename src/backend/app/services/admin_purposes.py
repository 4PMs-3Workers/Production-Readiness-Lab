"""Manage the catalog without removing records referenced by appointments."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import Settings
from app.domain.auth import AuthContext
from app.models import AuditEvent, Purpose, ServiceSetting
from app.repositories import catalog, users
from app.schemas.admin_purposes import (
    AdminPurpose,
    AdminPurposeQuery,
    PurposeCreate,
    PurposeReplace,
)
from app.schemas.common import PageResponse
from app.services.admin_access import lock_admin, read_under_policy_lock
from app.services.admin_writes import write_under_policy_lock


async def list_purposes(
    db: AsyncSession, query: AdminPurposeQuery, *, context: AuthContext, settings: Settings
) -> PageResponse[AdminPurpose]:
    async with read_under_policy_lock(db, context, settings):
        rows, total = await catalog.read_purpose_listing(db, query, is_active=query.is_active)
        return PageResponse(
            items=[AdminPurpose.model_validate(row) for row in rows],
            page=query.page,
            page_size=query.page_size,
            total=total,
        )


async def _purpose(db: AsyncSession, identifier: UUID) -> Purpose:
    purpose = await db.get(Purpose, identifier, populate_existing=True)
    if purpose is None:
        raise AppError(404, "PURPOSE_NOT_FOUND", "Purpose was not found", "business")
    return purpose


async def get_purpose(
    db: AsyncSession, identifier: UUID, *, context: AuthContext, settings: Settings
) -> AdminPurpose:
    async with read_under_policy_lock(db, context, settings):
        return AdminPurpose.model_validate(await _purpose(db, identifier))


async def create_purpose(
    db: AsyncSession,
    request: PurposeCreate,
    *,
    context: AuthContext,
    settings: Settings,
    request_id: str,
) -> AdminPurpose:
    duplicate = AppError(409, "PURPOSE_CODE_CONFLICT", "Purpose code is already in use", "input")

    async def operation(_: ServiceSetting) -> tuple[AdminPurpose, bool]:
        await lock_admin(db, context, settings)
        existing = await catalog.find_purpose_by_code(db, request.code)
        values = request.model_dump()
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise duplicate
            return AdminPurpose.model_validate(existing), False

        now = await users.utc_now(db)
        purpose = Purpose(id=uuid4(), created_at=now, updated_at=now, **values)
        db.add_all(
            [
                purpose,
                AuditEvent(
                    actor_id=context.user_id,
                    action="PURPOSE_CREATED",
                    entity_type="PURPOSE",
                    entity_id=str(purpose.id),
                    request_id=request_id,
                    event_metadata={"is_active": request.is_active},
                ),
            ]
        )
        return AdminPurpose.model_validate(purpose), True

    return await write_under_policy_lock(
        db, operation, settings=settings, duplicate_error=duplicate
    )


async def replace_purpose(
    db: AsyncSession,
    identifier: UUID,
    request: PurposeReplace,
    *,
    context: AuthContext,
    settings: Settings,
    request_id: str,
) -> AdminPurpose:
    async def operation(_: ServiceSetting) -> tuple[AdminPurpose, bool]:
        await lock_admin(db, context, settings)
        purpose = await _purpose(db, identifier)
        changes = {
            key: value
            for key, value in request.model_dump(exclude={"expected_updated_at"}).items()
            if getattr(purpose, key) != value
        }
        if not changes:
            return AdminPurpose.model_validate(purpose), False
        if purpose.updated_at != request.expected_updated_at:
            raise AppError(409, "PURPOSE_VERSION_CONFLICT", "Purpose changed; reload it", "input")

        for key, value in changes.items():
            setattr(purpose, key, value)
        purpose.updated_at = max(
            await users.utc_now(db), purpose.updated_at + timedelta(microseconds=1)
        )
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="PURPOSE_UPDATED",
                entity_type="PURPOSE",
                entity_id=str(purpose.id),
                request_id=request_id,
                event_metadata={"changed_fields": sorted(changes)},
            )
        )
        return AdminPurpose.model_validate(purpose), True

    return await write_under_policy_lock(db, operation, settings=settings)
