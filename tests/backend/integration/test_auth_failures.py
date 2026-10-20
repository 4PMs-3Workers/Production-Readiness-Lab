import asyncio

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.util.concurrency import await_only
from test_auth_endpoints import sign_in

from app.models import Session, User


async def test_commit_failure_rolls_back_session_and_never_sets_cookie(auth_env):
    client, factory, _, _ = auth_env

    def fail_on_session_commit(db):
        if any(isinstance(item, Session) for item in db.identity_map.values()):
            raise SQLAlchemyError("synthetic-sensitive-commit-failure")

    event.listen(SyncSession, "before_commit", fail_on_session_commit)
    try:
        response = await sign_in(client)
    finally:
        event.remove(SyncSession, "before_commit", fail_on_session_commit)
    assert response.status_code == 503
    assert "Set-Cookie" not in response.headers
    assert "synthetic-sensitive" not in response.text
    async with factory() as db:
        assert (await db.scalars(select(Session))).all() == []


async def test_account_lock_timeout_is_service_error_and_cleans_transaction(auth_env):
    client, factory, user_id, app = auth_env
    app.state.settings.db_lock_timeout_seconds = 0.05
    async with factory.begin() as blocker:
        from app.repositories import users

        await users.lock_by_id(blocker, user_id)
        response = await sign_in(client)
        assert response.status_code == 503
        assert response.headers["X-Error-Class"] == "service"
        assert "Set-Cookie" not in response.headers
    assert (await sign_in(client)).status_code == 200


async def test_email_throttle_identity_failure_is_service_error_without_raw_fallback(
    auth_env, monkeypatch
):
    from app.repositories import users

    client, _, _, app = auth_env
    throttle_checks = []

    async def fail_identity(db, email):
        raise SQLAlchemyError("synthetic-throttle-identity-secret")

    def record_identity(identity):
        throttle_checks.append(identity)

    monkeypatch.setattr(users, "email_throttle_identity", fail_identity, raising=False)
    monkeypatch.setattr(app.state.login_throttle, "check_email", record_identity)

    response = await sign_in(client)

    assert response.status_code == 503
    assert response.headers["X-Error-Class"] == "service"
    assert "synthetic-throttle-identity-secret" not in response.text
    assert "Set-Cookie" not in response.headers
    assert throttle_checks == []


async def test_cancel_after_flush_rolls_back_and_releases_account_lock(auth_env, monkeypatch):
    from app.repositories import sessions

    client, factory, user_id, _ = auth_env
    flushed = asyncio.Event()
    original = sessions.add_session

    async def pause_after_insert(db, row):
        await original(db, row)
        flushed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(sessions, "add_session", pause_after_insert)
    pending = asyncio.create_task(sign_in(client))
    try:
        await asyncio.wait_for(flushed.wait(), 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        monkeypatch.setattr(sessions, "add_session", original)
    assert "prl_session" not in client.cookies
    async with asyncio.timeout(1):
        async with factory.begin() as fresh:
            assert (await fresh.scalars(select(Session))).all() == []
            assert await fresh.get(User, user_id, with_for_update=True) is not None
    assert (await sign_in(client)).status_code == 200


async def test_timeout_awaiting_commit_dispatch_rolls_back_and_releases_lock(auth_env, monkeypatch):
    client, factory, user_id, app = auth_env
    engine = factory.kw["bind"]
    targets = set()
    entered_commit = asyncio.Event()
    original = engine.sync_engine.dialect.do_commit
    original_query_timeout = app.state.settings.db_query_timeout_seconds
    app.state.settings.db_query_timeout_seconds = 0.2

    def mark_write_connection(db):
        if any(isinstance(item, Session) for item in db.identity_map.values()):
            targets.add(id(db.connection().connection.dbapi_connection))

    def paused_commit(dbapi_connection):
        raw = getattr(dbapi_connection, "dbapi_connection", dbapi_connection)
        if id(raw) in targets:
            entered_commit.set()
            # Suspend the real dialect commit path before sending COMMIT.
            # This proves known-uncommitted cleanup, not ambiguous network outcomes.
            await_only(asyncio.Event().wait())
        original(dbapi_connection)

    event.listen(SyncSession, "before_commit", mark_write_connection)
    monkeypatch.setattr(engine.sync_engine.dialect, "do_commit", paused_commit)
    try:
        response = await sign_in(client)
    finally:
        event.remove(SyncSession, "before_commit", mark_write_connection)
        monkeypatch.setattr(engine.sync_engine.dialect, "do_commit", original)
        app.state.settings.db_query_timeout_seconds = original_query_timeout
    assert entered_commit.is_set()
    assert response.status_code == 503
    assert response.headers["X-Error-Class"] == "service"
    assert "Set-Cookie" not in response.headers
    async with asyncio.timeout(1):
        async with factory.begin() as fresh:
            assert (await fresh.scalars(select(Session))).all() == []
            assert await fresh.get(User, user_id, with_for_update=True) is not None
    assert (await sign_in(client)).status_code == 200
