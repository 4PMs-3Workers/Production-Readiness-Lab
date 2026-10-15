import asyncio

from sqlalchemy import select
from test_auth_endpoints import sign_in

from app.models import Session, User
from app.repositories import users
from app.services.auth import revoke_user_sessions


async def test_deactivation_before_login_lock_blocks_issuance(auth_env, monkeypatch):
    client, factory, user_id, _ = auth_env
    ready, release = asyncio.Event(), asyncio.Event()
    original = users.lock_by_id

    async def delayed(db, target):
        current = asyncio.current_task()
        if current is not None and current.get_name() == "racing-login":
            ready.set()
            await release.wait()
        return await original(db, target)

    monkeypatch.setattr(users, "lock_by_id", delayed)
    task = asyncio.create_task(sign_in(client), name="racing-login")
    try:
        await asyncio.wait_for(ready.wait(), 5)
        async with factory.begin() as db:
            user = await original(db, user_id)
            assert user is not None
            user.is_active = False
            await revoke_user_sessions(db, user_id)
    finally:
        release.set()
    response = await task
    assert response.status_code == 401
    async with factory() as db:
        assert (await db.scalars(select(Session))).all() == []


async def test_login_before_deactivation_leaves_no_usable_session(auth_env, monkeypatch):
    client, factory, user_id, _ = auth_env
    locked, release, revoker_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = users.lock_by_id

    async def delayed(db, target):
        user = await original(db, target)
        current = asyncio.current_task()
        if current is not None and current.get_name() == "racing-login":
            locked.set()
            await release.wait()
        return user

    async def deactivate():
        async with factory.begin() as db:
            revoker_started.set()
            user = await original(db, user_id)
            assert user is not None
            user.is_active = False
            await revoke_user_sessions(db, user_id)

    monkeypatch.setattr(users, "lock_by_id", delayed)
    login_task = asyncio.create_task(sign_in(client), name="racing-login")
    try:
        await asyncio.wait_for(locked.wait(), 5)
        revoke_task = asyncio.create_task(deactivate())
        await revoker_started.wait()
        assert not revoke_task.done()
    finally:
        release.set()
    assert (await login_task).status_code == 200
    await revoke_task
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with factory() as db:
        assert (await db.get(User, user_id)).is_active is False
        assert all(row.revoked_at is not None for row in (await db.scalars(select(Session))))


async def test_changed_password_after_verify_blocks_issuance(auth_env, monkeypatch):
    client, factory, user_id, _ = auth_env
    ready, release = asyncio.Event(), asyncio.Event()
    original = users.lock_by_id

    async def delayed(db, target):
        ready.set()
        await release.wait()
        return await original(db, target)

    monkeypatch.setattr(users, "lock_by_id", delayed)
    task = asyncio.create_task(sign_in(client))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        async with factory.begin() as db:
            (await db.get(User, user_id)).password_hash = "changed-hash"
    finally:
        release.set()
    assert (await task).status_code == 401
