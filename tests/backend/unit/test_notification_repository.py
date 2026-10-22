import asyncio
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import notifications


class _Rows:
    def all(self):
        return []


class _Session:
    def __init__(self, total=0):
        self.total = total
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.total

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows()


def test_notification_page_scopes_recipient_filters_unread_and_orders_stably():
    db = _Session(total=4)
    recipient_id = uuid4()

    rows, total = asyncio.run(
        notifications.read_notification_page(
            cast(AsyncSession, db),
            recipient_id=recipient_id,
            page=1,
            page_size=2,
            unread_only=True,
        )
    )

    assert rows == []
    assert total == 4
    count_sql, page_sql = (str(statement) for statement in db.statements)
    for sql in (count_sql, page_sql):
        assert "notifications.recipient_id =" in sql
        assert "notifications.read_at IS NULL" in sql
    assert "ORDER BY notifications.created_at DESC, notifications.id DESC" in page_sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql


def test_notification_page_beyond_end_skips_data_query():
    db = _Session(total=3)

    rows, total = asyncio.run(
        notifications.read_notification_page(
            cast(AsyncSession, db),
            recipient_id=uuid4(),
            page=1000,
            page_size=20,
            unread_only=False,
        )
    )

    assert rows == ()
    assert total == 3
    assert len(db.statements) == 1
