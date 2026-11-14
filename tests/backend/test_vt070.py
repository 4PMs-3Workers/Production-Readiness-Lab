"""VT-070: exercise the real ASGI app and outcome service with DB test doubles."""

import logging
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import API_V1_PREFIX
from app.dependencies.auth import require_csrf
from app.dependencies.database import get_db_session
from app.dependencies.staff import require_staff
from app.domain.auth import AuthContext
from app.factory import create_app
from app.models import AuditEvent, Notification
from app.services import notifications
from app.services import staff_appointments as service

STAFF, STUDENT, APPT = (UUID(int=n) for n in (11, 12, 13))
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
EVENT = "APPOINTMENT_NO_SHOW"
PATH = f"{API_V1_PREFIX}/staff/me/appointments/{APPT}/outcome"
BODY = b'{"status":"NO_SHOW"}'


def payload(size):
    assert size >= len(BODY)
    return BODY + b" " * (size - len(BODY))


class Harness:
    def __init__(self, settings, *, delivery_error=None, shared_state=True):
        self.app = create_app(settings)
        self.row = self.new_row()
        self.shared_state = shared_state
        self.primary = []
        self.deliveries = []
        self.sent = False
        self.failures = delivery_error
        self.delivery_attempts = 0
        self.primary_commits = 0
        self.primary_failure: SQLAlchemyError | None = None
        self.foreign = False
        self.now = NOW
        self.context = AuthContext(UUID(int=14), STAFF, "staff@example.test", "Staff", "STAFF", NOW)
        self.app.dependency_overrides[require_staff] = lambda: self.context
        self.app.dependency_overrides[require_csrf] = lambda: self.context
        self.app.dependency_overrides[get_db_session] = self.primary_db
        self.app.state.session_factory = self.delivery_db

    @staticmethod
    def new_row():
        return SimpleNamespace(
            id=APPT,
            staff_id=STAFF,
            student_id=STUDENT,
            status="UPCOMING",
            outcome_at=None,
            outcome_by=None,
            ends_at=NOW - timedelta(hours=1),
        )

    async def primary_db(self):
        row = self.row if self.shared_state else self.new_row()
        staged = []
        before = vars(row).copy()
        active = True

        async def commit():
            nonlocal active
            if self.primary_failure:
                raise self.primary_failure
            self.primary.extend(staged)
            self.primary_commits += 1
            active = False

        async def rollback():
            nonlocal active
            vars(row).update(before)
            active = False

        yield SimpleNamespace(
            row=row,
            begin=AsyncMock(return_value=SimpleNamespace(commit=commit)),
            add_all=staged.extend,
            flush=AsyncMock(),
            rollback=rollback,
            in_transaction=lambda: active,
            invalidate=AsyncMock(),
        )

    @asynccontextmanager
    async def delivery_db(self):
        assert self.primary_commits and self.sent, "Delivery must follow commit and response"
        self.delivery_attempts += 1
        staged = []

        @asynccontextmanager
        async def transaction():
            yield
            if self.failures:
                raise self.failures
            self.deliveries.extend(staged)

        yield SimpleNamespace(begin=transaction, add=staged.append)

    async def asgi(self, scope, receive, send):
        async def record(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self.sent = True

        await self.app(scope, receive, record)

    @contextmanager
    def bind(self):
        with pytest.MonkeyPatch.context() as p:
            p.setattr(service.appointments, "lock_service_settings", AsyncMock(return_value=True))
            p.setattr(
                service.appointments,
                "find_staff_appointment_accounts",
                AsyncMock(return_value=None if self.foreign else self.row),
            )
            p.setattr(service.appointments, "lock_appointment", lambda db, _: lookup(db.row))
            p.setattr(
                service.users,
                "lock_by_id",
                AsyncMock(return_value=SimpleNamespace(role="STAFF", is_active=True)),
            )
            p.setattr(service.users, "utc_now", AsyncMock(return_value=self.now))
            yield

    def client(self):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.asgi), base_url="http://test"
        )


async def lookup(row):
    return row


@pytest.mark.parametrize("status", ["NO_SHOW", "COMPLETED"], ids=["TC01", "TC02"])
async def test_outcome_contract(auth_settings, status):
    h = Harness(auth_settings)
    with h.bind():
        async with h.client() as client:
            response = await client.post(PATH, json={"status": status})
    assert response.status_code == 200, response.text
    body = response.json()
    # Independent oracle: two valid enum values are distinct.
    assert body["status"] == status
    assert body["id"] == str(APPT)
    assert datetime.fromisoformat(body["outcome_at"]) == NOW
    assert h.row.status == status and h.primary_commits == 1
    assert len([x for x in h.primary if isinstance(x, AuditEvent)]) == 1
    generic = [x for x in h.primary if isinstance(x, Notification)]
    assert [(x.recipient_id, x.event_type) for x in generic] == [
        (STUDENT, "APPOINTMENT_OUTCOME_RECORDED"),
        (STAFF, "APPOINTMENT_OUTCOME_RECORDED"),
    ]
    expected = [(STUDENT, APPT, EVENT)] if status == "NO_SHOW" else []
    assert [(x.recipient_id, x.appointment_id, x.event_type) for x in h.deliveries] == expected


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(SQLAlchemyError("do-not-log-test-payload"), id="TC03"),
        pytest.param(TimeoutError("do-not-log-test-payload"), id="TC13"),
    ],
)
async def test_delivery_failure_after_commit(auth_settings, caplog, monkeypatch, error):
    h = Harness(auth_settings, delivery_error=error)
    # In-process Alembic migrations can disable loggers created during test collection.
    logger = logging.getLogger(notifications.__name__)
    monkeypatch.setattr(logger, "disabled", False)
    with h.bind(), caplog.at_level(logging.WARNING, logger=logger.name):
        async with h.client() as client:
            response = await client.post(PATH, json={"status": "NO_SHOW"})
    assert response.status_code == 200
    assert response.json()["status"] == "NO_SHOW"
    assert h.delivery_attempts == 1
    assert h.primary_commits == 1 and h.row.status == "NO_SHOW"
    assert len(h.primary) == 3
    assert not h.deliveries
    assert "no_show_notice_failed" in caplog.text
    assert "do-not-log-test-payload" not in caplog.text


async def test_TC04_same_intent_replay_no_duplicate(auth_settings):
    h = Harness(auth_settings)
    with h.bind():
        async with h.client() as client:
            first = await client.post(PATH, json={"status": "NO_SHOW"})
            replay = await client.post(PATH, json={"status": "NO_SHOW"})
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert h.primary_commits == h.delivery_attempts == len(h.deliveries) == 1


@pytest.mark.parametrize(
    ("input_body", "expected"),
    [
        pytest.param(b'{"status":"LATE"}', 422, id="TC05"),
        pytest.param(payload(32769), 413, id="TC06"),
    ],
)
async def test_invalid_request_no_writes(auth_settings, input_body, expected):
    h = Harness(auth_settings)
    with h.bind():
        async with h.client() as client:
            response = await client.post(
                PATH, content=input_body, headers={"Content-Type": "application/json"}
            )
    assert response.status_code == expected, response.text
    assert h.primary_commits == h.delivery_attempts == 0
    assert h.row.status == "UPCOMING"


async def test_TC07_large_valid_body(auth_settings):
    h = Harness(auth_settings)
    with h.bind():
        async with h.client() as client:
            response = await client.post(
                PATH, content=payload(32768), headers={"Content-Type": "application/json"}
            )
    assert response.status_code == 200
    assert response.json()["status"] == "NO_SHOW"
    assert h.delivery_attempts == len(h.deliveries) == 1


@pytest.mark.parametrize(
    ("scenario", "status"),
    [
        pytest.param("foreign", 404, id="TC08"),
        pytest.param("not_ended", 409, id="TC09"),
        pytest.param("main_failure", 503, id="TC10"),
    ],
)
async def test_no_delivery_if_main_transition_rejected(auth_settings, scenario, status):
    h = Harness(auth_settings)
    if scenario == "foreign":
        h.foreign = True
    elif scenario == "not_ended":
        h.now = h.row.ends_at
    else:
        h.primary_failure = SQLAlchemyError("test commit failure")
    with h.bind():
        async with h.client() as client:
            response = await client.post(PATH, json={"status": "NO_SHOW"})
    assert response.status_code == status, response.text
    assert h.primary_commits == h.delivery_attempts == 0
    assert not h.primary and not h.deliveries and h.row.status == "UPCOMING"


@pytest.mark.parametrize("owns", [True, False], ids=["TC11", "TC12"])
async def test_inbox_projects_new_event(auth_settings, monkeypatch, owns):
    from app.schemas.notifications import NotificationListQuery

    @asynccontextmanager
    async def fake_read(*args):
        yield

    notice = Notification(
        id=UUID(int=15), recipient_id=STUDENT, appointment_id=APPT, event_type=EVENT, created_at=NOW
    )

    class Row:
        appointment_student_id = STUDENT if owns else STAFF
        appointment_starts_at = NOW
        appointment_status = "NO_SHOW"

        def __getitem__(self, index):
            assert index == 0
            return notice

    monkeypatch.setattr(notifications, "read_transaction", fake_read)
    monkeypatch.setattr(
        notifications.notification_repository,
        "read_notification_page",
        AsyncMock(return_value=([Row()], 1)),
    )
    actor = AuthContext(UUID(int=16), STUDENT, "student@example.test", "Student", "STUDENT", NOW)
    # The read boundary is stubbed; an unbound session supplies the service contract.
    async with AsyncSession() as db:
        inbox = await notifications.list_notifications(
            db,
            context=actor,
            query=NotificationListQuery(),
            settings=auth_settings,
        )
    item = inbox.items[0]
    assert item.appointment_id == (APPT if owns else None)
    assert item.summary == (
        "Lịch hẹn được ghi nhận vắng mặt" if owns else "Bạn có một thông báo mới"
    )
