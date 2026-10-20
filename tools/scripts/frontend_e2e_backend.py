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

RUNTIME = Path("build/frontend-api")
FIXTURE = RUNTIME / "fixture.json"
SETTINGS_SNAPSHOT = RUNTIME / "service-settings-snapshot.json"
LOSS = RUNTIME / "lose-next-response"
HOLD = RUNTIME / "hold-next-response"
HELD = RUNTIME / "held-response"
PASSWORD = os.getenv("FRONTEND_E2E_PASSWORD", "Fictional-e2e-password!42")


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


def migrate(connection):
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


async def prepare():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    SETTINGS_SNAPSHOT.unlink(missing_ok=True)
    fixture = {
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
            async with engine.begin() as db:
                await db.run_sync(migrate)
            passwords = await PasswordService.create(PasswordPolicy(), 2)
            encoded = await passwords.hash(PASSWORD)
            async with async_sessionmaker(engine).begin() as db:
                for name, role, display in (
                    ("student", "STUDENT", "Sinh viên kiểm thử A"),
                    ("other", "STUDENT", "Sinh viên kiểm thử B"),
                    ("staff", "STAFF", "Cố vấn kiểm thử"),
                    ("otherstaff", "STAFF", "Cố vấn kiểm thử B"),
                    ("admin", "ADMIN", "Quản trị kiểm thử"),
                ):
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
            or scope["method"] != "POST"
            or scope["path"] not in {"/api/v1/appointments", "/api/v1/staff/me/unavailability"}
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
                    "status": 201,
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
                await db.execute(
                    update(StaffPurpose)
                    .where(StaffPurpose.staff_id == UUID(fixture["staff"]))
                    .values(is_accepting=True)
                )
                await db.execute(
                    update(StaffPurpose)
                    .where(StaffPurpose.staff_id == UUID(fixture["otherstaff"]))
                    .values(is_accepting=False)
                )
                LOSS.unlink(missing_ok=True)
                HOLD.unlink(missing_ok=True)
                HELD.unlink(missing_ok=True)
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
    finally:
        await engine.dispose()
    if clear_settings_snapshot:
        SETTINGS_SNAPSHOT.unlink(missing_ok=True)


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
            "expire",
            "inspect",
            "history",
            "arm-loss",
            "arm-hold",
            "release-response",
            "manual-catalog",
            "settings-off",
            "settings-restore",
            "seed-timezone-appointment",
            "seed-staff-appointments",
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
    settings = Settings.model_validate(
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
