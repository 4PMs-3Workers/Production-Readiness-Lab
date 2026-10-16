"""Oversized catalog offsets short-circuit after the count query."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.catalog import read_purpose_page, read_staff_page
from app.schemas.catalog import PurposeQuery, StaffQuery


async def test_huge_purpose_page_returns_total_without_data_select():
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = 7
    scalar_result = Mock()
    scalar_result.all.return_value = []
    db.scalars.return_value = scalar_result
    query = PurposeQuery(page=10**18, page_size=100)

    rows, total = await read_purpose_page(db, query)

    assert rows == ()
    assert total == 7
    db.scalar.assert_awaited_once()
    db.scalars.assert_not_awaited()


async def test_huge_staff_page_returns_total_without_data_select():
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = 5
    db.execute.return_value = []
    query = StaffQuery(purpose_id=uuid4(), page=10**18, page_size=100)

    rows, total = await read_staff_page(db, query)

    assert rows == ()
    assert total == 5
    db.scalar.assert_awaited_once()
    db.execute.assert_not_awaited()
