"""Service-level persistence checks for the notification read transaction."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from app.core.config import Settings
from app.domain.auth import AuthContext
from app.schemas.notifications import NotificationListQuery
from app.services import notifications as notification_service
from app.services.notifications import mark_notification_read


class _Result:
    def __init__(self, *, rowcount: int = 0, read_at: datetime | None = None):
        self.rowcount = rowcount
        self.read_at = read_at

    def one_or_none(self):
        return SimpleNamespace(read_at=self.read_at)


class _Transaction:
    def __init__(self, db: "_Session"):
        self.db = db

    async def start(self):
        return None

    async def commit(self):
        self.db.committed = self.db.pending
        self.db.pending = None

    async def rollback(self):
        self.db.pending = None


class _Session:
    def __init__(self):
        self.pending: datetime | None = None
        self.committed: datetime | None = None
        self.timestamp = datetime(2026, 10, 22, 3, 4, 5, tzinfo=UTC)

    def begin(self):
        return _Transaction(self)

    async def execute(self, statement):
        if isinstance(statement, Update):
            if self.committed is None:
                self.pending = self.timestamp
                return _Result(rowcount=1)
            return _Result(rowcount=0)
        return _Result(read_at=self.pending or self.committed)


async def test_first_mark_read_commits_the_timestamp_it_returns():
    db = _Session()
    notification_id = uuid4()
    context = AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        email="student@example.invalid",
        name="Student",
        role="STUDENT",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    result = await mark_notification_read(
        cast(AsyncSession, db),
        notification_id=notification_id,
        context=context,
        settings=cast(Settings, SimpleNamespace(db_query_timeout_seconds=1)),
    )

    assert result.id == notification_id
    assert result.read_at == db.timestamp
    assert db.committed == db.timestamp


async def test_inbox_projects_orm_rows_before_read_transaction_rolls_back(monkeypatch):
    transaction_closed = False
    recipient_id = uuid4()
    appointment_identifier = uuid4()

    class NotificationRow:
        id = uuid4()
        created_at = datetime(2026, 10, 22, 3, tzinfo=UTC)
        read_at = None
        appointment_id = appointment_identifier

        @property
        def event_type(self):
            if transaction_closed:
                raise RuntimeError("ORM instance expired after rollback")
            return "APPOINTMENT_CREATED"

    class JoinedRow:
        appointment_student_id = recipient_id
        appointment_staff_id = uuid4()
        appointment_starts_at = datetime(2026, 10, 23, 3, tzinfo=UTC)
        appointment_status = "UPCOMING"

        def __getitem__(self, index):
            assert index == 0
            return NotificationRow()

    @asynccontextmanager
    async def expiring_transaction(_db, _settings):
        nonlocal transaction_closed
        yield
        transaction_closed = True

    async def page(_db, **_kwargs):
        return [JoinedRow()], 1

    monkeypatch.setattr(notification_service, "read_transaction", expiring_transaction)
    monkeypatch.setattr(
        notification_service.notification_repository, "read_notification_page", page
    )
    context = AuthContext(
        session_id=uuid4(),
        user_id=recipient_id,
        email="student@example.invalid",
        name="Student",
        role="STUDENT",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    result = await notification_service.list_notifications(
        cast(AsyncSession, object()),
        context=context,
        query=NotificationListQuery(),
        settings=cast(Settings, object()),
    )

    assert result.items[0].summary == "Lịch hẹn đã được tạo"
    assert result.items[0].appointment_id == appointment_identifier
