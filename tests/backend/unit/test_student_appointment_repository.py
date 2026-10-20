from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import appointments
from app.schemas.appointments import AppointmentListQuery


@pytest.mark.parametrize("total", [0, 5])
async def test_huge_student_page_returns_total_without_data_select(total):
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = total
    db.execute.side_effect = AssertionError("out-of-range page must not query rows")
    query = AppointmentListQuery(page=10**18, page_size=100)

    rows, count = await appointments.read_student_appointment_page(db, query, student_id=uuid4())

    assert rows == ()
    assert count == total
    db.scalar.assert_awaited_once()
    db.execute.assert_not_awaited()
