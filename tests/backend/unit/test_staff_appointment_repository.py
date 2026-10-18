from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import appointments
from app.schemas.staff_appointments import StaffAppointmentListQuery


@pytest.mark.parametrize("total", [0, 1])
async def test_staff_page_does_not_encode_offset_when_page_is_out_of_range(total):
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = total
    db.execute.side_effect = AssertionError("out-of-range page must not query rows")

    rows, count = await appointments.read_staff_appointment_page(
        db, StaffAppointmentListQuery(page=10**18, page_size=100), staff_id=uuid4()
    )

    assert list(rows) == []
    assert count == total
    db.execute.assert_not_awaited()
