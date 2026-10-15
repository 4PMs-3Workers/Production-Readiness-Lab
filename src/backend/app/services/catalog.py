"""Catalog read transactions and explicit public DTO mapping."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_error
from app.api.errors import ServiceUnavailable
from app.core.config import Settings
from app.models import Purpose
from app.repositories import catalog
from app.schemas.catalog import PurposeItem, PurposeQuery, StaffItem, StaffQuery
from app.schemas.common import PageResponse


@asynccontextmanager
async def _read_transaction(db: AsyncSession, settings: Settings) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(settings.db_query_timeout_seconds):
            async with db.begin():
                yield
    except (SQLAlchemyError, TimeoutError):
        if db.in_transaction():
            await db.rollback()
        raise ServiceUnavailable() from None


def _purpose_item(row: Purpose) -> PurposeItem:
    return PurposeItem(
        id=row.id,
        code=row.code,
        name=row.name,
        duration_minutes=row.duration_minutes,
        requires_free_text=row.requires_free_text,
    )


async def list_purposes(
    db: AsyncSession, query: PurposeQuery, *, settings: Settings
) -> PageResponse[PurposeItem]:
    async with _read_transaction(db, settings):
        rows, total = await catalog.read_purpose_page(db, query)
        items = [_purpose_item(row) for row in rows]
    return PageResponse(items=items, page=query.page, page_size=query.page_size, total=total)


async def list_staff(
    db: AsyncSession, query: StaffQuery, *, settings: Settings
) -> PageResponse[StaffItem]:
    async with _read_transaction(db, settings):
        purpose = await catalog.find_active_purpose(db, query.purpose_id)
        if purpose is None:
            raise catalog_error("NOT_FOUND")
        rows, total = await catalog.read_staff_page(db, query)
        items = [StaffItem(id=staff_id, name=name) for staff_id, name in rows]
    return PageResponse(items=items, page=query.page, page_size=query.page_size, total=total)
