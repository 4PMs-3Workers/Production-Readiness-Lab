"""Weekly schedule persistence under the singleton policy lock."""

from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ServiceHour

WindowKey = tuple[int, time, time]


async def read_week(db: AsyncSession) -> dict[WindowKey, ServiceHour]:
    statement = (
        select(ServiceHour)
        .order_by(ServiceHour.weekday, ServiceHour.opens_at, ServiceHour.closes_at)
        .execution_options(populate_existing=True)
    )
    return {(row.weekday, row.opens_at, row.closes_at): row for row in await db.scalars(statement)}


async def replace_week(
    db: AsyncSession, current: dict[WindowKey, ServiceHour], desired: set[WindowKey]
) -> tuple[int, int]:
    """Preserve row IDs and timestamps for windows present in both versions."""
    removed = current.keys() - desired
    added = desired - current.keys()
    for key in sorted(removed):
        await db.delete(current[key])
    db.add_all(
        [
            ServiceHour(weekday=weekday, opens_at=opens_at, closes_at=closes_at)
            for weekday, opens_at, closes_at in sorted(added)
        ]
    )
    return len(added), len(removed)
