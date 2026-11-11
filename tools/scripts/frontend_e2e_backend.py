"""Real frontend E2E harness. Owns one disposable, Alembic-initialized MySQL schema.

No application routes or business rules are replaced. Explicit probes truncate or hold
one successful HTTP response *after* the real application has finished and committed.
Never run against a shared database; MYSQL_TEST_URL must allow schema creation.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool
from starlette.types import Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.passwords import PasswordPolicy, PasswordService
from app.core.schema_lifecycle import migration_connection
from app.factory import create_app
from app.models import (
    Appointment,
    AuditEvent,
    ChangeProposal,
    Notification,
    Purpose,
    ServiceHour,
    ServiceSetting,
    Session,
    StaffPurpose,
    StaffUnavailability,
    User,
)
from app.worker.models import Outcome
from app.worker.processing import process_candidate
from app.worker.runtime import run_worker

RUNTIME = Path("build/frontend-api")
FIXTURE = RUNTIME / "fixture.json"
SETTINGS_SNAPSHOT = RUNTIME / "service-settings-snapshot.json"
LOSS = RUNTIME / "lose-next-response"
HOLD = RUNTIME / "hold-next-response"
HELD = RUNTIME / "held-response"
PASSWORD = os.getenv("FRONTEND_E2E_PASSWORD", "Fictional-e2e-password!42")
BASE_ACCOUNTS = (
    ("student", "STUDENT", "Sinh viên kiểm thử A"),
    ("other", "STUDENT", "Sinh viên kiểm thử B"),
    ("staff", "STAFF", "Cố vấn kiểm thử"),
    ("otherstaff", "STAFF", "Cố vấn kiểm thử B"),
    ("admin", "ADMIN", "Quản trị kiểm thử"),
)


def admin_url():
    value = os.environ.get("MYSQL_TEST_URL")
    if not value:
        raise RuntimeError("MYSQL_TEST_URL is required; real frontend E2E must not skip")
    return make_url(value).set(database=None)


def read_fixture():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not re.fullmatch(r"prl_frontend_e2e_[a-f0-9]{32}", fixture["schema"]):
        raise RuntimeError("Refusing to use a schema not owned by this E2E harness")
    return fixture


def engine_for(fixture):
    return create_database_engine(admin_url().set(database=fixture["schema"]), poolclass=NullPool)


def settings_for(fixture):
    return Settings.model_validate(
        {
            "DATABASE_URL": admin_url()
            .set(database=fixture["schema"])
            .render_as_string(hide_password=False),
            "APPLICATION_ORIGIN": "http://127.0.0.1:3101",
            "COOKIE_SECRET": "frontend-e2e-only-cookie-secret",
            "CSRF_SECRET": "frontend-e2e-only-csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": "20261019_1200_proposal_replay",
            "COOKIE_SECURE": False,
            "ENVIRONMENT": "test",
            "LOGIN_IP_ATTEMPTS": 200,
            "LOGIN_EMAIL_ATTEMPTS": 200,
        }
    )


def migrate(connection):
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


async def prepare():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    SETTINGS_SNAPSHOT.unlink(missing_ok=True)
    fixture: dict[str, Any] = {
        "schema": "prl_frontend_e2e_" + uuid4().hex,
        **{
            name: str(uuid4())
            for name in ("student", "other", "staff", "otherstaff", "admin", "purpose")
        },
    }
    admin = create_database_engine(admin_url(), poolclass=NullPool)
    try:
        async with admin.begin() as db:
            version = (await db.execute(text("SELECT VERSION()"))).scalar_one()
            if not version.startswith("8.4."):
                raise RuntimeError("Frontend E2E requires MySQL 8.4")
            await db.execute(
                text(
                    f"CREATE DATABASE `{fixture['schema']}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        FIXTURE.write_text(json.dumps(fixture), encoding="utf-8")
        engine = engine_for(fixture)
        try:
            async with migration_connection(engine) as db:
                await db.run_sync(migrate)
            passwords = await PasswordService.create(PasswordPolicy(), 2)
            encoded = await passwords.hash(PASSWORD)
            async with async_sessionmaker(engine).begin() as db:
                for name, role, display in BASE_ACCOUNTS:
                    db.add(
                        User(
                            id=UUID(fixture[name]),
                            email=f"{name}@frontend.example.invalid",
                            name=display,
                            role=role,
                            password_hash=encoded,
                            student_code=f"TEST-{name.upper()}" if role == "STUDENT" else None,
                            faculty="Khoa kiểm thử" if role == "STUDENT" else None,
                            major="Ngành kiểm thử" if role == "STUDENT" else None,
                            gender="UNDISCLOSED" if role == "STUDENT" else None,
                        )
                    )
                db.add(
                    Purpose(
                        id=UUID(fixture["purpose"]),
                        code="E2E_CUSTOM_CODE",
                        name="Tư vấn học tập kiểm thử",
                        duration_minutes=30,
                        requires_free_text=True,
                    )
                )
                db.add(
                    ServiceSetting(
                        timezone="Asia/Ho_Chi_Minh", start_step_minutes=30, booking_horizon_days=7
                    )
                )
                for weekday in range(1, 8):
                    db.add(ServiceHour(weekday=weekday, opens_at=time(8), closes_at=time(17)))
                await db.flush()
                db.add(
                    StaffPurpose(
                        staff_id=UUID(fixture["staff"]), purpose_id=UUID(fixture["purpose"])
                    )
                )
                db.add(
                    StaffPurpose(
                        staff_id=UUID(fixture["otherstaff"]),
                        purpose_id=UUID(fixture["purpose"]),
                        is_accepting=False,
                    )
                )
                now = (
                    (await db.execute(text("SELECT UTC_TIMESTAMP(6)")))
                    .scalar_one()
                    .replace(tzinfo=UTC)
                )
                fixture["date"] = str(
                    now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date() + timedelta(days=1)
                )
                fixture["mysqlVersion"] = version
                await db.flush()
                fixture["catalog_baseline"] = await catalog_snapshot(db, fixture)
            FIXTURE.write_text(json.dumps(fixture), encoding="utf-8")
        finally:
            await engine.dispose()
        return fixture
    except BaseException:
        async with admin.begin() as db:
            await db.execute(text(f"DROP DATABASE IF EXISTS `{fixture['schema']}`"))
        raise
    finally:
        await admin.dispose()


async def cleanup(fixture):
    if SETTINGS_SNAPSHOT.exists():
        fixture_engine = engine_for(fixture)
        try:
            async with async_sessionmaker(fixture_engine, expire_on_commit=False).begin() as db:
                await restore_service_settings(db)
        finally:
            await fixture_engine.dispose()
        SETTINGS_SNAPSHOT.unlink(missing_ok=True)
    engine = create_database_engine(admin_url(), poolclass=NullPool)
    try:
        async with engine.begin() as db:
            await db.execute(text(f"DROP DATABASE IF EXISTS `{fixture['schema']}`"))
    finally:
        await engine.dispose()
        FIXTURE.unlink(missing_ok=True)
        SETTINGS_SNAPSHOT.unlink(missing_ok=True)
        LOSS.unlink(missing_ok=True)
        HOLD.unlink(missing_ok=True)
        HELD.unlink(missing_ok=True)


class ProbeCommittedResponse:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] != "http"
            or scope["method"] not in {"POST", "PATCH", "PUT"}
            or not (
                (
                    scope["method"] == "PUT"
                    and (
                        scope["path"] == "/api/v1/admin/booking-configuration"
                        or re.fullmatch(
                            r"/api/v1/admin/purposes/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}",
                            scope["path"],
                        )
                    )
                )
                or (
                    scope["method"] == "PATCH"
                    and re.fullmatch(r"/api/v1/admin/accounts/[a-f0-9-]{36}", scope["path"])
                )
                or (
                    scope["method"] == "POST"
                    and (
                        scope["path"]
                        in {
                            "/api/v1/appointments",
                            "/api/v1/staff/me/unavailability",
                            "/api/v1/admin/accounts",
                            "/api/v1/admin/purposes",
                        }
                        or re.fullmatch(
                            r"/api/v1/admin/accounts/[a-f0-9-]{36}/password", scope["path"]
                        )
                        or re.fullmatch(r"/api/v1/notifications/[a-f0-9-]{36}/read", scope["path"])
                        or re.fullmatch(
                            r"/api/v1/(?:staff/me/)?appointments/[a-f0-9-]{36}/change-proposals"
                            r"(?:/[a-f0-9-]{36}/(?:accept|decline))?",
                            scope["path"],
                        )
                    )
                )
            )
            or not (LOSS.exists() or HOLD.exists())
        ):
            await self.app(scope, receive, send)
            return
        hold = HOLD.exists()
        (HOLD if hold else LOSS).unlink()
        messages: list[Message] = []

        async def capture(message: Message):
            messages.append(message)

        await self.app(scope, receive, capture)
        if hold and messages and messages[0].get("status") in (200, 201):
            HELD.write_text("committed", encoding="ascii")
            try:
                async with asyncio.timeout(30):
                    while HELD.exists():
                        await asyncio.sleep(0.025)
            except TimeoutError:
                pass
            finally:
                HELD.unlink(missing_ok=True)
            for message in messages:
                await send(message)
        elif messages and messages[0].get("status") in (200, 201):
            headers = [
                (key, value) for key, value in messages[0]["headers"] if key != b"content-length"
            ]
            # No fabricated receipt: browser sees an unreadable body after the real commit.
            await send(
                {
                    "type": "http.response.start",
                    "status": messages[0]["status"],
                    "headers": headers + [(b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
        else:
            for message in messages:
                await send(message)


async def operate(args):
    if args.action == "cleanup":
        if FIXTURE.exists():
            await cleanup(read_fixture())
        return
    fixture = read_fixture()
    if args.action == "worker-once":
        # Each CLI invocation owns a fresh worker lifecycle and scans durable rows.
        # Running this before/after making a proposal due exercises restart recovery.
        result = await run_worker(settings_for(fixture), once=True)
        if result != 0:
            raise RuntimeError("The real proposal expiry worker failed")
        return
    if args.action == "arm-loss":
        LOSS.write_text("once", encoding="ascii")
        return
    if args.action == "arm-hold":
        HELD.unlink(missing_ok=True)
        HOLD.write_text("once", encoding="ascii")
        return
    if args.action == "release-response":
        HELD.unlink(missing_ok=True)
        return
    engine = engine_for(fixture)
    clear_settings_snapshot = False
    proposal_for_expiry = None
    try:
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            if args.action == "reset":
                clear_settings_snapshot = await restore_service_settings(db)
                for model in (
                    Notification,
                    AuditEvent,
                    ChangeProposal,
                    Appointment,
                    StaffUnavailability,
                    Session,
                ):
                    await db.execute(delete(model))
                await restore_catalog_baseline(db, fixture)
                baseline_ids = [UUID(fixture[name]) for name, _, _ in BASE_ACCOUNTS]
                await db.execute(
                    delete(StaffPurpose).where(StaffPurpose.staff_id.not_in(baseline_ids))
                )
                await db.execute(delete(User).where(User.id.not_in(baseline_ids)))
                passwords = await PasswordService.create(PasswordPolicy(), 2)
                encoded = await passwords.hash(PASSWORD)
                for name, role, display in BASE_ACCOUNTS:
                    await db.execute(
                        update(User)
                        .where(User.id == UUID(fixture[name]))
                        .values(
                            email=f"{name}@frontend.example.invalid",
                            name=display,
                            role=role,
                            is_active=True,
                            password_hash=encoded,
                            student_code=f"TEST-{name.upper()}" if role == "STUDENT" else None,
                            faculty="Khoa kiểm thử" if role == "STUDENT" else None,
                            major="Ngành kiểm thử" if role == "STUDENT" else None,
                            gender="UNDISCLOSED" if role == "STUDENT" else None,
                            updated_at=datetime.now(UTC),
                        )
                    )
                LOSS.unlink(missing_ok=True)
                HOLD.unlink(missing_ok=True)
                HELD.unlink(missing_ok=True)
            elif args.action == "inspect-admin-catalog":
                snapshot = await catalog_snapshot(db, fixture)
                events = (
                    await db.scalars(
                        select(AuditEvent)
                        .where(
                            AuditEvent.action.in_(
                                [
                                    "PURPOSE_CREATED",
                                    "PURPOSE_UPDATED",
                                    "BOOKING_CONFIGURATION_UPDATED",
                                ]
                            )
                        )
                        .order_by(AuditEvent.created_at, AuditEvent.id)
                    )
                ).all()
                for event in events:
                    metadata = event.event_metadata
                    assert set(metadata) <= {
                        "is_active",
                        "changed_fields",
                        "windows_added",
                        "windows_removed",
                    }
                    for key, value in metadata.items():
                        if key == "changed_fields":
                            assert isinstance(value, list) and set(value) <= {
                                "name",
                                "duration_minutes",
                                "requires_free_text",
                                "is_active",
                                "timezone",
                                "start_step_minutes",
                                "booking_horizon_days",
                                "windows",
                            }
                        elif key == "is_active":
                            assert isinstance(value, bool)
                        else:
                            assert isinstance(value, int) and not isinstance(value, bool)
                print(
                    json.dumps(
                        {
                            "baseline_matches": snapshot == fixture["catalog_baseline"],
                            "purpose_count": await db.scalar(
                                select(func.count()).select_from(Purpose)
                            ),
                            "staff_links": await db.scalar(
                                select(func.count()).select_from(StaffPurpose)
                            ),
                            "audit_actions": [event.action for event in events],
                            "metadata_safe": True,
                        }
                    )
                )
            elif args.action == "inspect-admin-appointment":
                appointment = await db.get(Appointment, UUID(args.id))
                if appointment is None or appointment.student_id != UUID(fixture["student"]):
                    raise RuntimeError("Appointment must belong to the owned fixture student")
                print(
                    json.dumps(
                        {
                            "starts_at": appointment.starts_at.isoformat(),
                            "ends_at": appointment.ends_at.isoformat(),
                            "duration_minutes": appointment.duration_minutes,
                        }
                    )
                )
            elif args.action == "seed-admin":
                baseline = await db.get(User, UUID(fixture["student"]))
                assert baseline is not None
                for name, role, active in [
                    ("admin2", "ADMIN", True),
                    ("target", "STUDENT", True),
                    ("inactive", "STUDENT", False),
                ] + [(f"paging{index:02}", "STUDENT", True) for index in range(22)]:
                    db.add(
                        User(
                            id=uuid4(),
                            email=f"{name}@frontend.example.invalid",
                            name=f"Tài khoản {name}",
                            role=role,
                            is_active=active,
                            password_hash=baseline.password_hash,
                            student_code="ADMIN-E2E-TARGET" if name == "target" else None,
                        )
                    )
            elif args.action == "admin-last-staff":
                await db.execute(
                    update(User)
                    .where(User.id == UUID(fixture["otherstaff"]))
                    .values(is_active=False)
                )
            elif args.action == "inspect-admin":
                account = await db.get(User, UUID(args.id))
                if account is None:
                    raise RuntimeError("Account must exist in the owned test schema")
                events = (
                    await db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.entity_type == "USER",
                            AuditEvent.entity_id == str(account.id),
                        )
                    )
                ).all()
                allowed_keys = {
                    "changed_fields",
                    "role",
                    "is_active",
                    "from_role",
                    "to_role",
                    "sessions_revoked",
                }
                allowed_fields = {
                    "name",
                    "email",
                    "role",
                    "is_active",
                    "student_code",
                    "faculty",
                    "major",
                    "gender",
                }
                for event in events:
                    metadata = event.event_metadata
                    assert set(metadata) <= allowed_keys
                    for key, value in metadata.items():
                        if key == "changed_fields":
                            assert isinstance(value, list) and set(value) <= allowed_fields
                        elif key in {"is_active", "sessions_revoked"}:
                            assert isinstance(value, bool)
                        else:
                            assert value in {"STUDENT", "STAFF", "ADMIN"}
                sessions = (
                    await db.scalars(select(Session).where(Session.user_id == account.id))
                ).all()
                print(
                    json.dumps(
                        {
                            "id": str(account.id),
                            "email": account.email,
                            "name": account.name,
                            "role": account.role,
                            "is_active": account.is_active,
                            "student_code": account.student_code,
                            "updated_at": account.updated_at.isoformat(),
                            "audits": [
                                {
                                    "action": event.action,
                                    "actor": str(event.actor_id),
                                    "entity": event.entity_id,
                                }
                                for event in events
                            ],
                            "revoked_sessions": sum(row.revoked_at is not None for row in sessions),
                            "active_sessions": sum(
                                row.revoked_at is None and row.expires_at > datetime.now(UTC)
                                for row in sessions
                            ),
                            "metadata_safe": True,
                        }
                    )
                )
            elif args.action == "settings-off":
                setting = await db.get(ServiceSetting, 1)
                if setting is None:
                    raise RuntimeError("Cannot disable settings without the singleton row")
                if not SETTINGS_SNAPSHOT.exists():
                    SETTINGS_SNAPSHOT.write_text(
                        json.dumps(
                            {
                                "timezone": setting.timezone,
                                "start_step_minutes": setting.start_step_minutes,
                                "booking_horizon_days": setting.booking_horizon_days,
                                "updated_at": setting.updated_at.isoformat(),
                            }
                        ),
                        encoding="utf-8",
                    )
                await db.delete(setting)
            elif args.action == "settings-restore":
                clear_settings_snapshot = await restore_service_settings(db)
            elif args.action == "seed-timezone-appointment":
                starts_at = datetime(2026, 10, 18, 18, tzinfo=UTC)
                identifier = uuid4()
                db.add(
                    Appointment(
                        id=identifier,
                        student_id=UUID(fixture["student"]),
                        staff_id=UUID(fixture["staff"]),
                        purpose_id=UUID(fixture["purpose"]),
                        starts_at=starts_at,
                        ends_at=starts_at + timedelta(minutes=30),
                        duration_minutes=30,
                        status="CANCELLED",
                        cancelled_at=datetime.now(UTC),
                        creation_request_key="frontend-e2e-timezone",
                        creation_request_hash=hashlib.sha256(b"frontend-e2e-timezone").digest(),
                    )
                )
                await db.flush()
                print(json.dumps({"id": str(identifier)}))
            elif args.action == "seed-proposal-appointment":
                setting = await db.get(ServiceSetting, 1)
                if setting is None:
                    raise RuntimeError("Cannot seed a proposal appointment without settings")
                now = (
                    (await db.execute(text("SELECT UTC_TIMESTAMP(6)")))
                    .scalar_one()
                    .replace(tzinfo=UTC)
                )
                zone = ZoneInfo(setting.timezone)
                date = now.astimezone(zone).date() + timedelta(days=2)
                starts_at = datetime.combine(date, time(10), zone).astimezone(UTC)
                identifier = uuid4()
                db.add(
                    Appointment(
                        id=identifier,
                        student_id=UUID(fixture["student"]),
                        staff_id=UUID(fixture["staff"]),
                        purpose_id=UUID(fixture["purpose"]),
                        starts_at=starts_at,
                        ends_at=starts_at + timedelta(minutes=30),
                        duration_minutes=30,
                        location="  Phòng gốc A204  ",
                        status="UPCOMING",
                        creation_request_key="proposal-portal-fixture",
                        creation_request_hash=hashlib.sha256(b"proposal-portal-fixture").digest(),
                    )
                )
                # Current catalog duration must not replace the reservation's 30 minutes.
                await db.execute(
                    update(Purpose)
                    .where(Purpose.id == UUID(fixture["purpose"]))
                    .values(duration_minutes=60)
                )
                await db.flush()
                print(
                    json.dumps(
                        {
                            "id": str(identifier),
                            "date": str(date),
                            "nextDate": str(date + timedelta(days=1)),
                            "startsAt": starts_at.isoformat(),
                            "location": "  Phòng gốc A204  ",
                        }
                    )
                )
            elif args.action == "block-proposed-slot":
                proposal = await db.get(ChangeProposal, UUID(args.id))
                if proposal is None or proposal.created_by != UUID(fixture["staff"]):
                    raise RuntimeError("Slot blocking requires the fixture staff proposal")
                db.add(
                    StaffUnavailability(
                        staff_id=UUID(fixture["staff"]),
                        starts_at=proposal.proposed_starts_at,
                        ends_at=proposal.proposed_ends_at,
                    )
                )
            elif args.action == "inspect-proposals":
                appointment = await db.get(Appointment, UUID(args.id))
                if appointment is None or appointment.student_id != UUID(fixture["student"]):
                    raise RuntimeError("Inspection requires the fixture student's appointment")
                proposals = (
                    await db.scalars(
                        select(ChangeProposal)
                        .where(ChangeProposal.appointment_id == appointment.id)
                        .order_by(ChangeProposal.created_version, ChangeProposal.id)
                    )
                ).all()
                actions = (
                    "CHANGE_PROPOSAL_CREATED",
                    "CHANGE_PROPOSAL_REPLACED",
                    "CHANGE_PROPOSAL_ACCEPTED",
                    "CHANGE_PROPOSAL_DECLINED",
                    "CHANGE_PROPOSAL_EXPIRED",
                )
                print(
                    json.dumps(
                        {
                            "appointmentStatus": appointment.status,
                            "startsAt": appointment.starts_at.isoformat(),
                            "endsAt": appointment.ends_at.isoformat(),
                            "location": appointment.location,
                            "durationMinutes": appointment.duration_minutes,
                            "proposalVersion": appointment.proposal_version,
                            "proposals": [
                                {
                                    "id": str(proposal.id),
                                    "status": proposal.status,
                                    "location": proposal.proposed_location,
                                    "startsAt": proposal.proposed_starts_at.isoformat(),
                                    "endsAt": proposal.proposed_ends_at.isoformat(),
                                }
                                for proposal in proposals
                            ],
                            "audits": {
                                action: await db.scalar(
                                    select(func.count())
                                    .select_from(AuditEvent)
                                    .where(
                                        AuditEvent.entity_id.in_([str(p.id) for p in proposals]),
                                        AuditEvent.action == action,
                                    )
                                )
                                for action in actions
                            },
                            "notifications": {
                                action: await db.scalar(
                                    select(func.count())
                                    .select_from(Notification)
                                    .where(
                                        Notification.appointment_id == appointment.id,
                                        Notification.event_type == action,
                                    )
                                )
                                for action in actions
                            },
                        }
                    )
                )
            elif args.action == "seed-staff-appointments":
                setting = await db.get(ServiceSetting, 1)
                if setting is None:
                    raise RuntimeError("Cannot seed staff appointments without service settings")
                zone = ZoneInfo(setting.timezone)
                now = (
                    (await db.execute(text("SELECT UTC_TIMESTAMP(6)")))
                    .scalar_one()
                    .replace(tzinfo=UTC)
                )
                business_today = now.astimezone(zone).date()
                future_date = business_today + timedelta(days=1)
                past_date = business_today - timedelta(days=2)
                future_start = datetime.combine(future_date, time(10), zone).astimezone(UTC)
                past_start = datetime.combine(past_date, time(10), zone).astimezone(UTC)
                future_id, past_id = uuid4(), uuid4()
                for identifier, starts_at, key in (
                    (future_id, future_start, "staff-portal-future"),
                    (past_id, past_start, "staff-portal-past"),
                ):
                    db.add(
                        Appointment(
                            id=identifier,
                            student_id=UUID(fixture["student"]),
                            staff_id=UUID(fixture["staff"]),
                            purpose_id=UUID(fixture["purpose"]),
                            starts_at=starts_at,
                            ends_at=starts_at + timedelta(minutes=30),
                            duration_minutes=30,
                            status="UPCOMING",
                            creation_request_key=key,
                            creation_request_hash=hashlib.sha256(key.encode()).digest(),
                        )
                    )
                await db.flush()
                print(
                    json.dumps(
                        {
                            "cancelId": str(future_id),
                            "outcomeId": str(past_id),
                            "futureDate": str(future_date),
                            "nextDate": str(future_date + timedelta(days=1)),
                            "staffId": fixture["staff"],
                            "purposeId": fixture["purpose"],
                        }
                    )
                )
            elif args.action == "inspect-staff-portal":
                appointment = await db.get(Appointment, UUID(args.id))
                print(
                    json.dumps(
                        {
                            "appointmentStatus": appointment.status if appointment else None,
                            "unavailability": await db.scalar(
                                select(func.count())
                                .select_from(StaffUnavailability)
                                .where(StaffUnavailability.staff_id == UUID(fixture["staff"]))
                            ),
                            "periodIds": [
                                str(period_id)
                                for period_id in (
                                    await db.scalars(
                                        select(StaffUnavailability.id)
                                        .where(
                                            StaffUnavailability.staff_id == UUID(fixture["staff"])
                                        )
                                        .order_by(StaffUnavailability.id)
                                    )
                                ).all()
                            ],
                            **{
                                action: await db.scalar(
                                    select(func.count())
                                    .select_from(AuditEvent)
                                    .where(
                                        AuditEvent.action == action,
                                        AuditEvent.actor_id == UUID(fixture["staff"]),
                                    )
                                )
                                for action in (
                                    "STAFF_UNAVAILABILITY_CREATED",
                                    "STAFF_UNAVAILABILITY_DELETED",
                                )
                            },
                        }
                    )
                )
            elif args.action == "manual-catalog":
                # Expand only this disposable schema for human UI review. Keep the
                # original purpose for existing appointment history and default E2E.
                await db.execute(
                    update(ServiceSetting)
                    .where(ServiceSetting.id == 1)
                    .values(booking_horizon_days=7)
                )
                await db.execute(
                    update(Purpose)
                    .where(Purpose.id == UUID(fixture["purpose"]))
                    .values(is_active=False)
                )
                for code, name, duration, free_text in (
                    ("CAREER_INTERNSHIP", "Nghề nghiệp và thực tập", 30, False),
                    ("THESIS_RESEARCH", "Khóa luận và nghiên cứu", 60, False),
                    ("ACADEMIC_ADVISOR", "Cố vấn học tập", 30, False),
                    ("GENERAL_ADVICE", "Tư vấn chung", 45, False),
                    ("OTHER", "Nhu cầu khác", 30, True),
                ):
                    purpose = await db.scalar(select(Purpose).where(Purpose.code == code))
                    if purpose is None:
                        purpose = Purpose(
                            code=code,
                            name=name,
                            duration_minutes=duration,
                            requires_free_text=free_text,
                        )
                        db.add(purpose)
                        await db.flush()
                    purpose.is_active = True
                    staff_id = UUID(fixture["staff"])
                    association = await db.get(StaffPurpose, (staff_id, purpose.id))
                    if association is None:
                        db.add(StaffPurpose(staff_id=staff_id, purpose_id=purpose.id))
                print(json.dumps({"manual_catalog_purposes": 5, "booking_horizon_days": 7}))
            elif args.action == "expire":
                await db.execute(
                    update(Session)
                    .where(Session.user_id == UUID(fixture[args.user]))
                    .values(revoked_at=datetime.now(UTC))
                )
            elif args.action == "inspect":
                identifier = UUID(args.id)
                result = {
                    "appointments": await db.scalar(
                        select(func.count())
                        .select_from(Appointment)
                        .where(Appointment.id == identifier)
                    ),
                    **{
                        action: await db.scalar(
                            select(func.count())
                            .select_from(AuditEvent)
                            .where(
                                AuditEvent.entity_id == str(identifier), AuditEvent.action == action
                            )
                        )
                        for action in ("APPOINTMENT_CREATED", "APPOINTMENT_CANCELLED")
                    },
                    **{
                        f"{action}_notifications": await db.scalar(
                            select(func.count())
                            .select_from(Notification)
                            .where(
                                Notification.appointment_id == identifier,
                                Notification.event_type == action,
                            )
                        )
                        for action in ("APPOINTMENT_CREATED", "APPOINTMENT_CANCELLED")
                    },
                }
                print(json.dumps(result))
            elif args.action == "history":
                now = datetime.now(UTC)
                for index in range(24):
                    starts = now - timedelta(days=index + 2)
                    db.add(
                        Appointment(
                            student_id=UUID(fixture["student"]),
                            staff_id=UUID(fixture["staff"]),
                            purpose_id=UUID(fixture["purpose"]),
                            starts_at=starts,
                            ends_at=starts + timedelta(minutes=30),
                            duration_minutes=30,
                            status="CANCELLED",
                            cancelled_at=now,
                            creation_request_key=f"historical-{index}",
                            creation_request_hash=hashlib.sha256(str(index).encode()).digest(),
                        )
                    )
            elif args.action == "seed-notification-history":
                appointment = await db.get(Appointment, UUID(args.id))
                if appointment is None or appointment.student_id != UUID(fixture["student"]):
                    raise RuntimeError(
                        "Notification history requires the fixture student's appointment"
                    )
                now = (
                    (await db.execute(text("SELECT UTC_TIMESTAMP(6)")))
                    .scalar_one()
                    .replace(tzinfo=UTC)
                )
                for index in range(21):
                    db.add(
                        Notification(
                            recipient_id=UUID(fixture["student"]),
                            appointment_id=appointment.id,
                            event_type="APPOINTMENT_CREATED",
                            created_at=now - timedelta(minutes=index + 1),
                        )
                    )
            elif args.action in {"expire-proposal", "make-proposal-due"}:
                proposal_id = UUID(args.id)
                proposal = await db.get(ChangeProposal, proposal_id)
                if proposal is None or proposal.created_by != UUID(fixture["staff"]):
                    raise RuntimeError("Expiry requires a proposal created by the fixture staff")
                now = (
                    (await db.execute(text("SELECT UTC_TIMESTAMP(6)")))
                    .scalar_one()
                    .replace(tzinfo=UTC)
                )
                proposal.created_at = now - timedelta(days=1)
                proposal.expires_at = now - timedelta(minutes=1)
                if args.action == "expire-proposal":
                    proposal_for_expiry = proposal_id
        if proposal_for_expiry is not None:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            result = await process_candidate(
                sessions, proposal_for_expiry, settings_for(fixture), "expiry.frontend-e2e"
            )
            if result.outcome != Outcome.EXPIRED:
                raise RuntimeError(f"Worker did not expire the proposal: {result.outcome}")
    finally:
        await engine.dispose()
    if clear_settings_snapshot:
        SETTINGS_SNAPSHOT.unlink(missing_ok=True)


async def catalog_snapshot(db, fixture):
    """Capture only the owned fixture's catalog, policy, hours and staff links."""
    purpose = await db.get(Purpose, UUID(fixture["purpose"]))
    setting = await db.get(ServiceSetting, 1)
    if purpose is None or setting is None:
        raise RuntimeError("The owned catalog baseline requires its purpose and singleton")
    windows = (
        await db.scalars(select(ServiceHour).order_by(ServiceHour.weekday, ServiceHour.opens_at))
    ).all()
    links = (
        await db.scalars(
            select(StaffPurpose).order_by(StaffPurpose.staff_id, StaffPurpose.purpose_id)
        )
    ).all()
    return {
        "purpose": {
            "code": purpose.code,
            "name": purpose.name,
            "duration_minutes": purpose.duration_minutes,
            "requires_free_text": purpose.requires_free_text,
            "is_active": purpose.is_active,
            "created_at": purpose.created_at.isoformat(),
            "updated_at": purpose.updated_at.isoformat(),
        },
        "policy": {
            "timezone": setting.timezone,
            "start_step_minutes": setting.start_step_minutes,
            "booking_horizon_days": setting.booking_horizon_days,
            "updated_at": setting.updated_at.isoformat(),
        },
        "windows": [
            {
                "id": str(w.id),
                "weekday": w.weekday,
                "opens_at": w.opens_at.isoformat(),
                "closes_at": w.closes_at.isoformat(),
                "created_at": w.created_at.isoformat(),
                "updated_at": w.updated_at.isoformat(),
            }
            for w in windows
        ],
        "links": [
            {
                "staff_id": str(link.staff_id),
                "purpose_id": str(link.purpose_id),
                "is_accepting": link.is_accepting,
                "created_at": link.created_at.isoformat(),
                "updated_at": link.updated_at.isoformat(),
            }
            for link in links
        ],
    }


async def restore_catalog_baseline(db, fixture):
    """Called only after appointment dependents are cleared in the owned schema."""
    baseline = fixture["catalog_baseline"]
    await db.execute(delete(StaffPurpose))
    await db.execute(delete(Purpose).where(Purpose.id != UUID(fixture["purpose"])))
    values = dict(baseline["purpose"])
    for field in ("created_at", "updated_at"):
        values[field] = datetime.fromisoformat(values[field])
    await db.execute(update(Purpose).where(Purpose.id == UUID(fixture["purpose"])).values(**values))
    await db.execute(delete(ServiceHour))
    await db.execute(delete(ServiceSetting))
    db.add(
        ServiceSetting(
            id=1,
            **{
                **baseline["policy"],
                "updated_at": datetime.fromisoformat(baseline["policy"]["updated_at"]),
            },
        )
    )
    for window in baseline["windows"]:
        db.add(
            ServiceHour(
                id=UUID(window["id"]),
                weekday=window["weekday"],
                opens_at=time.fromisoformat(window["opens_at"]),
                closes_at=time.fromisoformat(window["closes_at"]),
                created_at=datetime.fromisoformat(window["created_at"]),
                updated_at=datetime.fromisoformat(window["updated_at"]),
            )
        )
    for link in baseline["links"]:
        db.add(
            StaffPurpose(
                staff_id=UUID(link["staff_id"]),
                purpose_id=UUID(link["purpose_id"]),
                is_accepting=link["is_accepting"],
                created_at=datetime.fromisoformat(link["created_at"]),
                updated_at=datetime.fromisoformat(link["updated_at"]),
            )
        )
    await db.flush()


async def restore_service_settings(db):
    """Restore the singleton values captured before an outage scenario."""
    if not SETTINGS_SNAPSHOT.exists():
        return False
    snapshot = json.loads(SETTINGS_SNAPSHOT.read_text(encoding="utf-8"))
    updated_at = datetime.fromisoformat(snapshot["updated_at"])
    setting = await db.get(ServiceSetting, 1)
    if setting is None:
        setting = ServiceSetting(
            id=1,
            timezone=snapshot["timezone"],
            start_step_minutes=snapshot["start_step_minutes"],
            booking_horizon_days=snapshot["booking_horizon_days"],
            updated_at=updated_at,
        )
        db.add(setting)
    else:
        setting.timezone = snapshot["timezone"]
        setting.start_step_minutes = snapshot["start_step_minutes"]
        setting.booking_horizon_days = snapshot["booking_horizon_days"]
        setting.updated_at = updated_at
    await db.flush()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "serve",
            "reset",
            "seed-admin",
            "inspect-admin",
            "inspect-admin-catalog",
            "inspect-admin-appointment",
            "admin-last-staff",
            "expire",
            "inspect",
            "history",
            "seed-notification-history",
            "expire-proposal",
            "make-proposal-due",
            "worker-once",
            "arm-loss",
            "arm-hold",
            "release-response",
            "manual-catalog",
            "settings-off",
            "settings-restore",
            "seed-timezone-appointment",
            "seed-staff-appointments",
            "seed-proposal-appointment",
            "block-proposed-slot",
            "inspect-proposals",
            "inspect-staff-portal",
            "cleanup",
        ),
    )
    parser.add_argument("--id")
    parser.add_argument(
        "--user", choices=("student", "other", "staff", "otherstaff", "admin"), default="student"
    )
    args = parser.parse_args()
    if args.action != "serve":
        asyncio.run(operate(args))
        return
    fixture = asyncio.run(prepare())
    settings = settings_for(fixture)
    try:
        uvicorn.run(
            ProbeCommittedResponse(create_app(settings)),
            host="127.0.0.1",
            port=8101,
            # Longer than Node's idle socket window; avoid reusing a socket as
            # the upstream closes it at Uvicorn's default five-second boundary.
            timeout_keep_alive=30,
            access_log=False,
            log_level="warning",
        )
    finally:
        asyncio.run(cleanup(fixture))


if __name__ == "__main__":
    main()
