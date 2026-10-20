"""Staff-facing view over the shared database-backed business calendar."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.staff_context import StaffContextResponse
from app.services.booking_context import get_booking_context


async def get_staff_context(
    db: AsyncSession, *, settings: Settings, clock: Callable[[], datetime]
) -> StaffContextResponse:
    """Reuse the authoritative settings read and business-time calculation."""
    context = await get_booking_context(db, settings=settings, clock=clock)
    return StaffContextResponse(timezone=context.timezone, business_date=context.business_date)
