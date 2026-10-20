"""Real MySQL configuration semantics, student visibility and booking races."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from tests.backend.integration.test_booking_service import _create, _payload
from tests.backend.integration.test_cancellation_concurrency import ordered_race

from app.core.errors import AppError
from app.domain.auth import AuthContext
from app.models import (
    Appointment,
    AuditEvent,
    Notification,
    Purpose,
    ServiceSetting,
    StaffPurpose,
    StaffUnavailability,
    User,
)
from app.repositories import users
from app.schemas.staff_availability import StaffPurposeRequest, UnavailabilityCreateRequest
from app.services import staff_availability as service

pytestmark = pytest.mark.mysql
BASE = "/api/v1/staff/me"
ORIGIN = "http://localhost:3000"


def actor(env):
    return AuthContext(
        session_id=uuid4(),
        user_id=env.staff_id,
        role="STAFF",
        name="Test Staff",
        email="staff@example.invalid",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def login(env, *, staff=True):
    async with env.factory.begin() as db:
        student = await db.get(User, env.student_id)
        staff_row = await db.get(User, env.staff_id)
        staff_row.password_hash = student.password_hash
    response = await env.client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={
            "email": "staff@example.invalid" if staff else "student@example.invalid",
            "password": "  Good-password!  ",
        },
    )
    assert response.status_code == 200


def headers(env):
    return {"Origin": ORIGIN, "X-CSRF-Token": env.client.cookies["prl_csrf"]}


async def counts(env):
    async with env.factory() as db:
        return {
            model.__tablename__: await db.scalar(select(func.count()).select_from(model))
            for model in (Appointment, StaffUnavailability, AuditEvent, Notification)
        }


async def mutate(
    env,
    kind,
    start,
    *,
    settings=None,
    db=None,
    accepting=False,
    purpose_id=None,
    identifier=None,
    end=None,
) -> Any:
    if db is None:
        async with env.factory() as fresh:
            return await mutate(
                env,
                kind,
                start,
                settings=settings,
                db=fresh,
                accepting=accepting,
                purpose_id=purpose_id,
                identifier=identifier,
                end=end,
            )
    settings = settings or env.app.state.settings
    kwargs = {
        "context": actor(env),
        "request_id": "staff-configuration-test",
        "settings": settings,
        "deadline": asyncio.get_running_loop().time() + settings.request_timeout_seconds,
    }
    if kind == "purpose":
        return await service.set_purpose(
            db, purpose_id or env.purpose_id, StaffPurposeRequest(is_accepting=accepting), **kwargs
        )
    if kind == "create":
        return await service.create_period(
            db,
            UnavailabilityCreateRequest(
                starts_at=start, ends_at=end or start + timedelta(minutes=30)
            ),
            **kwargs,
        )
    assert kind == "delete" and identifier is not None
    return await service.delete_period(db, identifier, **kwargs)


async def appointment(env, start, status="UPCOMING", *, staff_id=None):
    async with env.factory.begin() as db:
        row = Appointment(
            student_id=env.student_id,
            staff_id=staff_id or env.staff_id,
            purpose_id=env.purpose_id,
            duration_minutes=30,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            status=status,
            creation_request_key=uuid4().hex,
            creation_request_hash=bytes(32),
            other_reason="Private consultation content",
            cancelled_at=datetime.now(UTC) if status == "CANCELLED" else None,
            outcome_at=datetime.now(UTC) if status in {"COMPLETED", "NO_SHOW"} else None,
        )
        db.add(row)
        await db.flush()
        return row.id


async def assert_configuration_locks_released(env):
    async with asyncio.timeout(2):
        async with env.factory.begin() as db:
            await db.get(ServiceSetting, 1, with_for_update=True)
            await users.lock_by_id(db, env.staff_id)
            await db.scalar(select(StaffPurpose).with_for_update())
            await db.scalar(select(StaffUnavailability).with_for_update())


async def test_catalog_union_pagination_order_and_no_cross_staff_associations(booking_env):
    env, _, _ = booking_env
    inactive, foreign, active = UUID(int=21), UUID(int=22), UUID(int=23)
    async with env.factory.begin() as db:
        db.add_all(
            [
                Purpose(
                    id=inactive, code="RETAINED", name="Alpha", duration_minutes=30, is_active=False
                ),
                Purpose(
                    id=foreign, code="FOREIGN", name="Hidden", duration_minutes=30, is_active=False
                ),
                Purpose(id=active, code="ACTIVE", name="Alpha", duration_minutes=45),
            ]
        )
        await db.flush()
        db.add_all(
            [
                StaffPurpose(staff_id=env.staff_id, purpose_id=inactive, is_accepting=False),
                StaffPurpose(staff_id=env.other_staff_id, purpose_id=foreign, is_accepting=True),
                StaffPurpose(staff_id=env.other_staff_id, purpose_id=active, is_accepting=True),
            ]
        )
    await login(env)
    response = await env.client.get(BASE + "/purposes", params={"page_size": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4  # two fixture purposes, one retained and one unassociated active
    assert body["items"][0]["id"] == str(inactive)
    assert body["items"][0]["is_active"] is False
    second = (await env.client.get(BASE + "/purposes", params={"page": 2, "page_size": 1})).json()
    assert second["items"][0]["id"] == str(active)
    assert second["items"][0]["is_accepting"] is False
    assert second["items"][0]["duration_minutes"] == 45
    assert (await env.client.get(BASE + "/purposes?page=100")).json()["items"] == []


@pytest.mark.parametrize("path", ["purposes", "unavailability"])
@pytest.mark.parametrize("page,page_size", [(3, 1), (100, 20), (2**64, 20)])
async def test_out_of_range_pages_return_empty_even_with_oversized_offsets(
    booking_env, path, page, page_size
):
    env, _, start = booking_env
    async with env.factory.begin() as db:
        db.add_all(
            [
                StaffUnavailability(
                    staff_id=staff_id,
                    starts_at=start + timedelta(hours=index),
                    ends_at=start + timedelta(hours=index, minutes=30),
                )
                for index, staff_id in enumerate([env.staff_id, env.staff_id, env.other_staff_id])
            ]
        )
    await login(env)
    response = await env.client.get(
        BASE + f"/{path}", params={"page": page, "page_size": page_size}
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "page": page, "page_size": page_size, "total": 2}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Request-ID"]


async def test_preference_noop_toggle_creation_audit_and_history_preservation(booking_env):
    env, date, start = booking_env
    appointment_id = await appointment(env, start)
    new_id = uuid4()
    async with env.factory.begin() as db:
        db.add(Purpose(id=new_id, code="NEW", name="New consultation", duration_minutes=45))
        original = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        before = (original.created_at, original.updated_at)
        global_purpose = await db.get(Purpose, env.purpose_id)
        global_snapshot = (
            global_purpose.name,
            global_purpose.duration_minutes,
            global_purpose.updated_at,
        )
    await login(env)
    route = BASE + f"/purposes/{env.purpose_id}"
    assert (
        await env.client.put(route, json={"is_accepting": True}, headers=headers(env))
    ).status_code == 200
    assert (await counts(env))["audit_events"] == 0
    async with env.factory() as db:
        unchanged = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assert (unchanged.created_at, unchanged.updated_at) == before
    changed = await env.client.put(route, json={"is_accepting": False}, headers=headers(env))
    assert changed.status_code == 200
    async with env.factory() as db:
        event = await db.scalar(select(AuditEvent))
        assert event.actor_id == env.staff_id and event.action == "STAFF_PURPOSE_SET"
        assert event.request_id == changed.headers["X-Request-ID"]
        assert event.event_metadata == {
            "staff_id": str(env.staff_id),
            "purpose_id": str(env.purpose_id),
            "is_accepting": False,
        }
        retained = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assert retained is not None and not retained.is_accepting
        row = await db.get(Appointment, appointment_id)
        assert row.status == "UPCOMING" and row.other_reason == "Private consultation content"
        purpose = await db.get(Purpose, env.purpose_id)
        assert (purpose.name, purpose.duration_minutes, purpose.updated_at) == global_snapshot
    await login(env, staff=False)
    history = await env.client.get(f"/api/v1/appointments/{appointment_id}")
    assert history.status_code == 200 and history.json()["status"] == "UPCOMING"
    catalog = await env.client.get("/api/v1/staff", params={"purpose_id": str(env.purpose_id)})
    assert str(env.staff_id) not in {row["id"] for row in catalog.json()["items"]}
    unavailable = await env.client.get(
        "/api/v1/availability",
        params={
            "purpose_id": str(env.purpose_id),
            "staff_id": str(env.staff_id),
            "date": date.isoformat(),
        },
    )
    assert unavailable.status_code == 404
    await login(env)
    for accepting in (True, True):
        assert (
            await env.client.put(route, json={"is_accepting": accepting}, headers=headers(env))
        ).status_code == 200
    created = await env.client.put(
        BASE + f"/purposes/{new_id}", json={"is_accepting": False}, headers=headers(env)
    )
    assert created.status_code == 200 and not created.json()["is_accepting"]
    assert (await counts(env))["audit_events"] == 3
    assert (await counts(env))["notifications"] == 0


@pytest.mark.parametrize(
    "existing,accepting", [(True, False), (True, True), (False, False), (False, True)]
)
async def test_inactive_purpose_rules(booking_env, existing, accepting):
    env, _, start = booking_env
    async with env.factory.begin() as db:
        purpose = await db.get(Purpose, env.purpose_id)
        purpose.is_active = False
        if not existing:
            await db.delete(await db.get(StaffPurpose, (env.staff_id, env.purpose_id)))
    if existing and not accepting:
        result = await mutate(env, "purpose", start, accepting=False)
        assert result.is_active is False and result.is_accepting is False
    else:
        with pytest.raises(AppError) as error:
            await mutate(env, "purpose", start, accepting=accepting)
        assert error.value.status_code == 409 and error.value.code == "PURPOSE_INACTIVE"
        assert (await counts(env))["audit_events"] == 0
    with pytest.raises(AppError) as missing:
        await mutate(env, "purpose", start, purpose_id=uuid4())
    assert missing.value.code == "NOT_FOUND"


async def test_cross_day_utc_replay_precedes_conflict_and_keeps_receipt(booking_env):
    env, _, _ = booking_env
    await login(env)
    body = {"starts_at": "2020-01-01T23:07:13+07:00", "ends_at": "2020-02-03T10:17:42+02:00"}
    first = await env.client.post(BASE + "/unavailability", json=body, headers=headers(env))
    assert first.status_code == 201
    receipt = first.json()
    assert receipt["starts_at"] == "2020-01-01T16:07:13Z"
    assert receipt["ends_at"] == "2020-02-03T08:17:42Z"
    await appointment(
        env, datetime(2020, 1, 2, tzinfo=UTC)
    )  # legacy conflict after original commit
    retry = await env.client.post(
        BASE + "/unavailability",
        json={"starts_at": receipt["starts_at"], "ends_at": receipt["ends_at"]},
        headers=headers(env),
    )
    assert retry.status_code == 200 and retry.json() == receipt
    assert (await counts(env))["staff_unavailability"] == 1
    assert (await counts(env))["audit_events"] == 1
    assert (await counts(env))["notifications"] == 0
    async with env.factory() as db:
        audit = await db.scalar(select(AuditEvent))
        assert set(audit.event_metadata) == {"staff_id", "starts_at", "ends_at"}


@pytest.mark.parametrize(
    "status", ["UPCOMING", "CHANGE_PENDING", "CANCELLED", "COMPLETED", "NO_SHOW"]
)
async def test_period_conflicts_only_with_occupying_intervals_and_allows_adjacency(
    booking_env, status
):
    env, _, start = booking_env
    await appointment(env, start, status)
    if status in {"UPCOMING", "CHANGE_PENDING"}:
        with pytest.raises(AppError) as error:
            await mutate(env, "create", start + timedelta(minutes=15))
        assert error.value.code == "UNAVAILABILITY_CONFLICT"
        assert (await counts(env))["audit_events"] == 0
        await login(env)
        conflict = await env.client.post(
            BASE + "/unavailability",
            json={
                "starts_at": (start + timedelta(minutes=15)).isoformat(),
                "ends_at": (start + timedelta(minutes=45)).isoformat(),
            },
            headers=headers(env),
        )
        assert conflict.status_code == 409
        assert conflict.headers["X-Error-Class"] == "business"
        assert conflict.headers["Cache-Control"] == "private, no-store"
        assert conflict.json()["error"]["request_id"] == conflict.headers["X-Request-ID"]
    else:
        await mutate(env, "create", start + timedelta(minutes=15))
    await mutate(env, "create", start - timedelta(minutes=30))
    await mutate(env, "create", start + timedelta(minutes=30))


async def test_period_list_overlap_filters_order_pagination_and_owned_deletion(booking_env):
    env, _, start = booking_env
    identifiers = [UUID(int=i) for i in range(31, 36)]
    async with env.factory.begin() as db:
        db.add_all(
            [
                StaffUnavailability(
                    id=identifiers[0],
                    staff_id=env.staff_id,
                    starts_at=start,
                    ends_at=start + timedelta(hours=1),
                ),
                StaffUnavailability(
                    id=identifiers[1],
                    staff_id=env.staff_id,
                    starts_at=start,
                    ends_at=start + timedelta(hours=2),
                ),
                StaffUnavailability(
                    id=identifiers[2],
                    staff_id=env.staff_id,
                    starts_at=start - timedelta(hours=1),
                    ends_at=start,
                ),
                StaffUnavailability(
                    id=identifiers[3],
                    staff_id=env.staff_id,
                    starts_at=start + timedelta(hours=1),
                    ends_at=start + timedelta(hours=2),
                ),
                StaffUnavailability(
                    id=identifiers[4],
                    staff_id=env.other_staff_id,
                    starts_at=start,
                    ends_at=start + timedelta(hours=1),
                ),
            ]
        )
    await login(env)
    params = {
        "from": start.isoformat(),
        "to": (start + timedelta(hours=1)).isoformat(),
        "page_size": 1,
    }
    result = await env.client.get(BASE + "/unavailability", params=params)
    assert result.status_code == 200
    assert result.json()["total"] == 2
    assert result.json()["items"][0]["id"] == str(identifiers[0])
    params["page"] = 2
    assert (await env.client.get(BASE + "/unavailability", params=params)).json()["items"][0][
        "id"
    ] == str(identifiers[1])
    assert (
        await env.client.get(BASE + "/unavailability", params={"from": start.isoformat()})
    ).json()["total"] == 3
    assert (
        await env.client.get(BASE + "/unavailability", params={"to": start.isoformat()})
    ).json()["total"] == 1
    for identifier in [identifiers[4], uuid4()]:
        denied = await env.client.delete(
            BASE + f"/unavailability/{identifier}", headers=headers(env)
        )
        assert denied.status_code == 404 and denied.json()["error"]["code"] == "NOT_FOUND"
    deleted = await env.client.delete(
        BASE + f"/unavailability/{identifiers[0]}", headers=headers(env)
    )
    assert deleted.status_code == 204 and deleted.content == b""
    assert deleted.headers["Cache-Control"] == "private, no-store"
    assert (
        await env.client.delete(BASE + f"/unavailability/{identifiers[0]}", headers=headers(env))
    ).status_code == 404
    async with env.factory() as db:
        audit = await db.scalar(select(AuditEvent))
        assert audit.action == "STAFF_UNAVAILABILITY_DELETED" and audit.actor_id == env.staff_id
        assert audit.request_id == deleted.headers["X-Request-ID"]
        assert await db.get(StaffUnavailability, identifiers[4]) is not None
    assert (await counts(env))["audit_events"] == 1


async def test_overlapping_absence_union_and_delete_keep_remaining_availability_rules(booking_env):
    env, date, start = booking_env
    first, _ = await mutate(env, "create", start, end=start + timedelta(hours=1))
    second, _ = await mutate(
        env, "create", start + timedelta(minutes=30), end=start + timedelta(minutes=90)
    )
    await login(env, staff=False)
    params = {
        "purpose_id": str(env.purpose_id),
        "staff_id": str(env.staff_id),
        "date": date.isoformat(),
    }

    async def slots():
        response = await env.client.get("/api/v1/availability", params=params)
        assert response.status_code == 200
        return [row["starts_at"] for row in response.json()["slots"]]

    expected = (start + timedelta(minutes=90)).isoformat().replace("+00:00", "Z")
    assert await slots() == [expected]
    await mutate(env, "delete", start, identifier=first.id)
    # A slot ending exactly at the remaining period's start becomes available.
    assert await slots() == [start.isoformat().replace("+00:00", "Z"), expected]
    # An existing appointment continues to block even after the final absence is removed.
    await appointment(env, start)
    await mutate(env, "delete", start, identifier=second.id)
    available = await slots()
    assert start.isoformat().replace("+00:00", "Z") not in available
    assert expected in available
    assert all(
        datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(minutes=30)
        <= start + timedelta(hours=2)
        for value in available
    )


@pytest.mark.parametrize("configuration_first", [True, False])
@pytest.mark.parametrize("kind", ["create", "purpose"])
async def test_booking_serializes_with_configuration_on_the_same_staff_row(
    booking_env, auth_settings, monkeypatch, configuration_first, kind
):
    env, _, start = booking_env
    settings = auth_settings.model_copy(
        update={
            "request_timeout_seconds": 15,
            "db_query_timeout_seconds": 10,
            "db_lock_timeout_seconds": 5,
        }
    )

    async def configure(db):
        return await mutate(env, kind, start, db=db, settings=settings)

    async def book(db):
        return await _create(env, _payload(env, start), settings, db=db)

    operations = (configure, book) if configuration_first else (book, configure)
    results = await ordered_race(env, monkeypatch, env.staff_id, *operations)
    if configuration_first:
        assert not isinstance(results[0], AppError)
        assert isinstance(results[1], AppError)
        assert results[1].code == ("BOOKING_CONFLICT" if kind == "create" else "NOT_FOUND")
    elif kind == "create":
        assert not isinstance(results[0], AppError)
        assert isinstance(results[1], AppError) and results[1].code == "UNAVAILABILITY_CONFLICT"
    else:
        assert not any(isinstance(item, AppError) for item in results)
    async with env.factory() as db:
        overlaps = await db.scalar(
            select(func.count())
            .select_from(Appointment)
            .join(
                StaffUnavailability,
                (Appointment.staff_id == StaffUnavailability.staff_id)
                & (Appointment.starts_at < StaffUnavailability.ends_at)
                & (Appointment.ends_at > StaffUnavailability.starts_at),
            )
            .where(Appointment.status.in_(("UPCOMING", "CHANGE_PENDING")))
        )
        assert overlaps == 0
        rows = (await db.scalars(select(Appointment))).all()
        assert len(rows) == (0 if configuration_first else 1)
        assert all(row.status == "UPCOMING" for row in rows)
        if kind == "purpose":
            association = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
            assert not association.is_accepting


async def test_concurrent_exact_period_posts_create_one_record_and_one_audit(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    settings = auth_settings.model_copy(
        update={
            "request_timeout_seconds": 15,
            "db_query_timeout_seconds": 10,
            "db_lock_timeout_seconds": 5,
        }
    )

    async def create(db):
        return await mutate(env, "create", start, db=db, settings=settings)

    first, second = await ordered_race(env, monkeypatch, env.staff_id, create, create)
    assert not isinstance(first, AppError) and not isinstance(second, AppError)
    assert first[0] == second[0] and first[1] is False and second[1] is True
    assert (await counts(env))["staff_unavailability"] == 1
    assert (await counts(env))["audit_events"] == 1


@pytest.mark.parametrize("change", ["role", "active"])
async def test_mutable_staff_authority_is_rechecked_after_waiting_for_the_lock(
    booking_env, auth_settings, monkeypatch, change
):
    env, _, start = booking_env
    settings = auth_settings.model_copy(
        update={
            "request_timeout_seconds": 15,
            "db_query_timeout_seconds": 10,
            "db_lock_timeout_seconds": 5,
        }
    )

    async def change_account(db):
        async with db.begin():
            row = await users.lock_by_id(db, env.staff_id)
            assert row is not None
            if change == "role":
                row.role = "ADMIN"
            else:
                row.is_active = False

    result = await ordered_race(
        env,
        monkeypatch,
        env.staff_id,
        change_account,
        lambda db: mutate(env, "create", start, db=db, settings=settings),
    )
    assert isinstance(result[1], AppError) and result[1].status_code == 403
    assert (await counts(env))["staff_unavailability"] == 0
    assert (await counts(env))["audit_events"] == 0
