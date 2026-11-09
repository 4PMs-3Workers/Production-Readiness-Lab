"""MySQL atomicity and retry contracts for policy and purpose mutations."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction
from tests.backend.integration import test_admin_booking_configuration as http_tests
from tests.backend.integration.test_admin_booking_configuration import (
    CONFIGURATION,
    PURPOSE,
    PURPOSES,
    policy_body,
    purpose_body,
    window,
    write_headers,
)

from app.models import AuditEvent, ServiceHour
from app.repositories import booking_configuration

pytestmark = pytest.mark.mysql
admin_policy_env = http_tests.admin_policy_env


async def audit_actions(factory):
    async with factory() as db:
        return list(await db.scalars(select(AuditEvent.action).order_by(AuditEvent.created_at)))


async def test_policy_version_covers_window_only_changes_and_preserves_unchanged_rows(
    admin_policy_env,
):
    client, factory, _, _ = admin_policy_env
    headers = write_headers(client)
    baseline = (await client.get(CONFIGURATION)).json()
    retained = window(1, "08:00:00", "12:00:00")
    first = await client.put(
        CONFIGURATION, json=policy_body(baseline["updated_at"], windows=[retained]), headers=headers
    )
    assert first.status_code == 200
    current = first.json()
    assert current["updated_at"] > baseline["updated_at"]
    async with factory() as db:
        row = await db.scalar(select(ServiceHour))
        assert row is not None
        original = row.id, row.created_at, row.updated_at

    stale = await client.put(
        CONFIGURATION,
        json=policy_body(baseline["updated_at"], start_step_minutes=25),
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "BOOKING_CONFIGURATION_VERSION_CONFLICT"
    desired = [window(3, "09:00:00", "10:00:00"), retained]
    body = policy_body(current["updated_at"], timezone="UTC", windows=desired)
    updated = await client.put(CONFIGURATION, json=body, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["windows"] == list(reversed(desired))
    replay = await client.put(
        CONFIGURATION, json={**body, "windows": list(reversed(desired))}, headers=headers
    )
    assert replay.status_code == 200 and replay.json() == updated.json()
    async with factory() as db:
        row = await db.scalar(select(ServiceHour).where(ServiceHour.weekday == 1))
        assert row is not None and (row.id, row.created_at, row.updated_at) == original
        event = await db.scalar(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(1))
        assert event is not None
        assert event.event_metadata == {
            "changed_fields": ["timezone", "windows"],
            "windows_added": 1,
            "windows_removed": 0,
        }
    assert await audit_actions(factory) == ["BOOKING_CONFIGURATION_UPDATED"] * 2


async def test_configuration_rolls_back_after_policy_and_window_work(admin_policy_env, monkeypatch):
    client, factory, _, _ = admin_policy_env
    headers = write_headers(client)
    baseline = (await client.get(CONFIGURATION)).json()
    installed = await client.put(
        CONFIGURATION,
        json=policy_body(baseline["updated_at"], windows=[window(1, "08:00:00", "11:00:00")]),
        headers=headers,
    )
    assert installed.status_code == 200
    before = installed.json()
    original = booking_configuration.replace_week

    async def fail_after_flush(db, current, desired):
        await original(db, current, desired)
        await db.flush()
        raise SQLAlchemyError("fictional private driver failure")

    monkeypatch.setattr(booking_configuration, "replace_week", fail_after_flush)
    failed = await client.put(
        CONFIGURATION, json=policy_body(before["updated_at"], timezone="UTC"), headers=headers
    )
    assert failed.status_code == 503
    assert "private driver" not in failed.text
    assert (await client.get(CONFIGURATION)).json() == before
    assert await audit_actions(factory) == ["BOOKING_CONFIGURATION_UPDATED"]


@pytest.mark.parametrize(
    "operation,success,action",
    [
        ("create", 201, "PURPOSE_CREATED"),
        ("activate", 200, "PURPOSE_UPDATED"),
        ("schedule", 200, "BOOKING_CONFIGURATION_UPDATED"),
    ],
)
async def test_retry_after_commit_loses_no_state_or_audit(
    admin_policy_env, monkeypatch, operation, success, action
):
    client, factory, _, _ = admin_policy_env
    headers = write_headers(client)
    if operation == "activate":
        created = await client.post(PURPOSES, json=PURPOSE, headers=headers)
        assert created.status_code == 201
        item = created.json()
        method, path = "PUT", f"{PURPOSES}/{item['id']}"
        body = purpose_body(item, is_active=False)
    elif operation == "schedule":
        current = (await client.get(CONFIGURATION)).json()
        method, path = "PUT", CONFIGURATION
        body = policy_body(current["updated_at"], windows=[window(3, "13:00:00", "16:00:00")])
    else:
        method, path, body = "POST", PURPOSES, PURPOSE

    original = AsyncSessionTransaction.commit
    committed = 0

    async def commit_then_disconnect(transaction):
        nonlocal committed
        await original(transaction)
        committed += 1
        raise SQLAlchemyError("fictional private disconnect after COMMIT")

    with monkeypatch.context() as injected:
        injected.setattr(AsyncSessionTransaction, "commit", commit_then_disconnect)
        lost = await client.request(method, path, json=body, headers=headers)
    assert lost.status_code == 503 and "private disconnect" not in lost.text
    assert committed == 1
    replay = await client.request(method, path, json=body, headers=headers)
    assert replay.status_code == success, replay.json()
    assert (await audit_actions(factory)).count(action) == 1

    if operation == "create":
        conflict = await client.post(
            PURPOSES, json={**body, "duration_minutes": 35}, headers=headers
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "PURPOSE_CODE_CONFLICT"
        read = await client.get(f"{PURPOSES}/{replay.json()['id']}")
    elif operation == "activate":
        conflict = await client.put(path, json={**body, "is_active": True}, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "PURPOSE_VERSION_CONFLICT"
        read = await client.get(path)
    else:
        read = await client.get(path)
    assert read.json() == replay.json()
