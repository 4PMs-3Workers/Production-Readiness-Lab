from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SyncSession
from test_auth_endpoints import sign_in

from app.models import Session


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
