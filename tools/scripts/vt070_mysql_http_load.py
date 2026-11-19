"""VT-070 only: run loopback HTTP against a fresh, migrated MySQL lab database."""

import asyncio
import json
import os
import socket
from collections import Counter
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import ceil
from time import perf_counter
from uuid import UUID

import httpx
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.api import API_V1_PREFIX
from app.core.config import Settings
from app.dependencies.auth import require_csrf
from app.dependencies.staff import require_staff
from app.domain.auth import AuthContext
from app.factory import create_app
from app.models import (
    Appointment,
    AuditEvent,
    Notification,
    Purpose,
    ServiceSetting,
    StaffPurpose,
    User,
)

N = 150
WAVES = 2
BODY = b'{"status":"NO_SHOW"}'
START = datetime(2026, 10, 19, 8, tzinfo=UTC)
PURPOSE = UUID(int=71000)


def ids(index: int) -> tuple[UUID, UUID, UUID]:
    first = 100_000 + 3 * index
    return UUID(int=first), UUID(int=first + 1), UUID(int=first + 2)


async def seed(factory, count: int) -> None:
    async with factory() as db, db.begin():
        if await db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Use a fresh migrated lab database; no existing users are allowed.")
        db.add(Purpose(id=PURPOSE, code="VT070", name="Lab", duration_minutes=45))
        db.add(
            ServiceSetting(
                timezone="Asia/Ho_Chi_Minh", start_step_minutes=15, booking_horizon_days=7
            )
        )
        for i in range(count):
            _, student, staff = ids(i)
            for uid, role in ((student, "STUDENT"), (staff, "STAFF")):
                db.add(
                    User(
                        id=uid,
                        email=f"{uid.hex}@example.invalid",
                        name="Synthetic",
                        role=role,
                        password_hash="unused",
                    )
                )
        await db.flush()
        for i in range(count):
            _, _, staff = ids(i)
            db.add(StaffPurpose(staff_id=staff, purpose_id=PURPOSE))
        await db.flush()
        for i in range(count):
            appointment, student, staff = ids(i)
            db.add(
                Appointment(
                    id=appointment,
                    student_id=student,
                    staff_id=staff,
                    purpose_id=PURPOSE,
                    duration_minutes=45,
                    starts_at=START,
                    ends_at=START + timedelta(minutes=45),
                    status="UPCOMING",
                    creation_request_key=appointment.hex,
                    creation_request_hash=sha256(appointment.bytes).digest(),
                )
            )


async def wave(client: httpx.AsyncClient, offset: int, size: int):
    gate = asyncio.Event()
    ready = active = peak = 0
    body = BODY + b" " * (size - len(BODY))

    async def send(i: int):
        nonlocal ready, active, peak
        appointment, _, staff = ids(i)
        ready += 1
        if ready == N:
            gate.set()
        await gate.wait()
        start = perf_counter()
        active += 1
        peak = max(peak, active)
        try:
            response = await client.post(
                f"{API_V1_PREFIX}/staff/me/appointments/{appointment}/outcome",
                content=body,
                headers={"Content-Type": "application/json", "X-Lab-Staff": str(staff)},
            )
            code = str(response.status_code)
            if response.status_code == 200:
                result = response.json()
                if result.get("id") != str(appointment) or result.get("status") != "NO_SHOW":
                    code = "bad_mapping"
            return code, perf_counter() - start
        except httpx.TimeoutException:
            return "client_timeout", perf_counter() - start
        except httpx.RequestError:
            return "transport_error", perf_counter() - start
        finally:
            active -= 1

    return await asyncio.gather(*(send(i) for i in range(offset, offset + N))), peak


async def summarize_db(factory, start: int, count: int):
    appointment_ids = [ids(i)[0] for i in range(start, start + count)]
    async with factory() as db:
        outcomes = await db.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(Appointment.id.in_(appointment_ids), Appointment.status == "NO_SHOW")
        )
        audits = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "APPOINTMENT_OUTCOME_RECORDED",
                AuditEvent.entity_id.in_([str(item) for item in appointment_ids]),
            )
        )
        events = dict(
            (
                await db.execute(
                    select(Notification.event_type, func.count())
                    .where(Notification.appointment_id.in_(appointment_ids))
                    .group_by(Notification.event_type)
                )
            ).all()
        )
    return {
        "outcome_rows": outcomes,
        "audit_rows": audits,
        "generic_notices": events.get("APPOINTMENT_OUTCOME_RECORDED", 0),
        "extra_notices": events.get("APPOINTMENT_NO_SHOW", 0),
    }


async def main() -> None:
    # The command never seeds a shared or remote database.
    url = make_url(os.environ["DATABASE_URL"])
    if (
        url.drivername != "mysql+asyncmy"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.database != "vt070_lab"
    ):
        raise SystemExit("DATABASE_URL must point to disposable vt070_lab on loopback.")
    settings = Settings.model_validate(
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "APPLICATION_ORIGIN": "http://127.0.0.1",
            "COOKIE_SECRET": "test-only-cookie-secret",
            "CSRF_SECRET": "test-only-csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
            "COOKIE_SECURE": False,
            "ENVIRONMENT": "test",
        }
    )
    from fastapi import Request

    def lab_actor(request: Request):
        staff = UUID(request.headers["X-Lab-Staff"])
        return AuthContext(UUID(int=14), staff, "staff@example.invalid", "Staff", "STAFF", START)

    app = create_app(settings)
    app.dependency_overrides[require_staff] = lab_actor
    app.dependency_overrides[require_csrf] = lab_actor
    classes = (("normal", len(BODY), 200), ("max_valid", 32768, 200), ("over_limit", 32769, 413))
    pending = asyncio.Event()
    pending.set()
    active_asgi = 0

    async def instrumented(scope, receive, send):
        nonlocal active_asgi
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        pending.clear()
        active_asgi += 1
        try:
            await app(scope, receive, send)
        finally:
            active_asgi -= 1
            if not active_asgi:
                pending.set()

    async with app.router.lifespan_context(app):
        factory = app.state.session_factory
        await seed(factory, N * (1 + WAVES * len(classes)))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(512)
            port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(instrumented, lifespan="off", access_log=False, log_level="critical")
            )
            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(10):
                    while not server.started:
                        await asyncio.sleep(0.01)
                limits = httpx.Limits(max_connections=N, max_keepalive_connections=N)
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", limits=limits, timeout=8, trust_env=False
                ) as client:
                    warmup, _ = await wave(client, 0, len(BODY))
                    await asyncio.wait_for(pending.wait(), timeout=10)
                    print(json.dumps({"warmup_status": Counter(code for code, _ in warmup)}))
                    for j, (name, size, expected) in enumerate(classes):
                        start = N + j * N * WAVES
                        samples = []
                        peaks = []
                        for w in range(WAVES):
                            result, peak = await wave(client, start + w * N, size)
                            samples.extend(result)
                            peaks.append(peak)
                            await asyncio.wait_for(pending.wait(), timeout=10)
                        counts = Counter(code for code, _ in samples)
                        timings = sorted(t for _, t in samples)
                        p95 = timings[ceil(0.95 * len(timings)) - 1]
                        db_counts = await summarize_db(factory, start, len(samples))
                        expected_count = len(samples) if expected == 200 else 0
                        observed_expected = counts[str(expected)]
                        oracle = (
                            observed_expected == len(samples)
                            and db_counts["outcome_rows"] == expected_count
                            and db_counts["audit_rows"] == expected_count
                            and db_counts["generic_notices"] == 2 * expected_count
                            and (expected != 413 or db_counts["extra_notices"] == 0)
                        )
                        print(
                            json.dumps(
                                {
                                    "class": name,
                                    "bytes": size,
                                    "n": len(samples),
                                    "peak": peaks,
                                    "status": counts,
                                    "p95_s_all": round(p95, 6),
                                    **db_counts,
                                    "oracle": oracle,
                                    "gate_pass": oracle
                                    and p95 <= 4.0
                                    and all(p == N for p in peaks),
                                }
                            )
                        )
            finally:
                server.should_exit = True
                await server_task


if __name__ == "__main__":
    asyncio.run(main())
