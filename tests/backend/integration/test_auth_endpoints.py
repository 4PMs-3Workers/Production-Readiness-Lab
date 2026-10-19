"""Real HTTP/session/transaction checks against an Alembic-created MySQL schema."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Session, User

ORIGIN = {"Origin": "http://localhost:3000"}
CREDENTIALS = {"email": "student@example.invalid", "password": "  Good-password!  "}


async def sign_in(client):
    return await client.post("/api/v1/auth/login", json=CREDENTIALS, headers=ORIGIN)


async def test_login_me_csrf_logout_and_cookie_replay(auth_env):
    client, factory, user_id, _ = auth_env
    response = await sign_in(client)
    assert response.status_code == 200
    assert set(response.json()) == {"id", "email", "name", "role"}
    token = client.cookies["prl_session"]
    csrf = client.cookies["prl_csrf"]
    async with factory() as db:
        row = (await db.scalars(select(Session))).one()
        assert row.user_id == user_id
        assert row.token_hash == hashlib.sha256(token.encode("ascii")).digest()
        assert row.expires_at - row.created_at == timedelta(seconds=86400)
    me = await client.get("/api/v1/auth/me")
    assert me.json() == response.json()
    bootstrap = await client.get("/api/v1/auth/csrf")
    assert set(bootstrap.json()) == {"csrf_token", "user_id"}
    assert bootstrap.json()["user_id"] == str(user_id)
    assert bootstrap.json()["csrf_token"] == client.cookies["prl_csrf"]
    # Previously issued signed tokens remain valid for this session.
    client.cookies.set("prl_csrf", csrf, domain="localhost.local", path="/")
    result = await client.post("/api/v1/auth/logout", headers=ORIGIN | {"X-CSRF-Token": csrf})
    assert result.status_code == 204 and result.content == b""
    assert "prl_session" not in client.cookies
    replay = await client.get("/api/v1/auth/me", headers={"Cookie": "prl_session=" + token})
    assert replay.status_code == 401
    again = await client.post("/api/v1/auth/logout", headers=ORIGIN)
    assert again.status_code == 204
    for item in (response, me, bootstrap, result, replay, again):
        assert item.headers["Cache-Control"] == "private, no-store"
        assert "X-Request-ID" in item.headers


async def test_csrf_owner_matches_token_session_after_switching_accounts(auth_env):
    client, factory, user_id, _ = auth_env
    async with factory.begin() as db:
        student = await db.get(User, user_id)
        assert student is not None
        other = User(
            email="other@example.invalid",
            name="Other Student",
            role="STUDENT",
            password_hash=student.password_hash,
        )
        db.add(other)
    await sign_in(client)
    first = (await client.get("/api/v1/auth/csrf")).json()
    assert first["user_id"] == str(user_id)
    switched = await client.post(
        "/api/v1/auth/login",
        json=CREDENTIALS | {"email": "other@example.invalid"},
        headers=ORIGIN,
    )
    assert switched.status_code == 200
    second = (await client.get("/api/v1/auth/csrf")).json()
    assert second["user_id"] == str(other.id)
    # Even a matching cookie/header pair from A cannot authorize B's session.
    client.cookies.set("prl_csrf", first["csrf_token"], domain="localhost.local", path="/")
    rejected = await client.post(
        "/api/v1/auth/logout", headers=ORIGIN | {"X-CSRF-Token": first["csrf_token"]}
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "INVALID_CSRF"
    assert (await client.get("/api/v1/auth/me")).json()["id"] == second["user_id"]


@pytest.mark.parametrize("kind", ["unknown", "wrong", "inactive"])
async def test_login_failures_share_one_public_error(auth_env, kind):
    client, factory, user_id, _ = auth_env
    data = dict(CREDENTIALS)
    if kind == "unknown":
        data["email"] = "unknown@example.invalid"
    elif kind == "wrong":
        data["password"] = CREDENTIALS["password"].strip()
    else:
        async with factory.begin() as db:
            user = await db.get(User, user_id)
            user.is_active = False
    response = await client.post("/api/v1/auth/login", json=data, headers=ORIGIN)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert response.json()["error"]["message"] == "Invalid email or password"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "Set-Cookie" not in response.headers
    async with factory() as db:
        assert (await db.scalars(select(Session))).all() == []


async def test_collation_equivalent_emails_share_login_budget(auth_env, auth_settings, monkeypatch):
    from app.core.login_throttle import LoginThrottle
    from app.repositories import users

    client, factory, user_id, app = auth_env
    app.state.login_throttle = LoginThrottle(auth_settings)
    original_verify = app.state.passwords.verify
    verifications = 0

    async def counted_verify(password, encoded_hash):
        nonlocal verifications
        verifications += 1
        return await original_verify(password, encoded_hash)

    monkeypatch.setattr(app.state.passwords, "verify", counted_verify)
    emails = ["student@example.invalid"] + [
        "student" + "\u200b" * count + "@example.invalid" for count in range(1, 6)
    ]
    for index, email in enumerate(emails):
        async with factory() as db:
            account = await users.find_by_email(db, email)
            assert account is not None and account.id == user_id
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "wrong-password"},
            headers=ORIGIN,
        )
        assert response.status_code == (429 if index == 5 else 401)
        assert response.json()["error"]["code"] == (
            "LOGIN_THROTTLED" if index == 5 else "INVALID_CREDENTIALS"
        )
        if index == 5:
            assert int(response.headers["Retry-After"]) > 0
    assert verifications == 5


@pytest.mark.parametrize(
    "state", ["missing", "malformed", "unknown", "expired", "revoked", "inactive"]
)
async def test_invalid_sessions_are_rejected(auth_env, state):
    client, factory, user_id, _ = auth_env
    if state not in {"missing", "malformed", "unknown"}:
        assert (await sign_in(client)).status_code == 200
        async with factory.begin() as db:
            row = (await db.scalars(select(Session))).one()
            if state == "expired":
                row.created_at = datetime.now(UTC) - timedelta(days=2)
                row.expires_at = datetime.now(UTC) - timedelta(days=1)
            elif state == "revoked":
                row.revoked_at = datetime.now(UTC)
            else:
                (await db.get(User, user_id)).is_active = False
    elif state != "missing":
        client.cookies.set("prl_session", "bad" if state == "malformed" else "a" * 42 + "A")
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/csrf")).status_code == 401
    assert (await client.post("/api/v1/auth/logout", headers=ORIGIN)).status_code == 204


@pytest.mark.parametrize("headers", [{}, {"Origin": "null"}, {"Origin": "http://localhost:3001"}])
async def test_login_and_logout_require_origin(auth_env, headers):
    client, _, _, _ = auth_env
    assert (
        await client.post("/api/v1/auth/login", json=CREDENTIALS, headers=headers)
    ).status_code == 403
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 403


@pytest.mark.parametrize("mode", ["missing", "mismatch", "tampered", "cross_session"])
async def test_logout_requires_valid_session_bound_csrf(auth_env, mode):
    client, _, _, _ = auth_env
    assert (await sign_in(client)).status_code == 200
    csrf = client.cookies["prl_csrf"]
    headers = ORIGIN | {"X-CSRF-Token": csrf}
    if mode == "missing":
        del headers["X-CSRF-Token"]
    elif mode == "mismatch":
        headers["X-CSRF-Token"] = csrf + "x"
    elif mode == "tampered":
        csrf = "v2" + csrf[2:]
        client.cookies.clear()
        # Restore session via another login, then tamper both submit channels.
        await sign_in(client)
        client.cookies.set("prl_csrf", csrf, domain="localhost.local", path="/")
        headers["X-CSRF-Token"] = csrf
    else:
        await sign_in(client)
        client.cookies.set("prl_csrf", csrf, domain="localhost.local", path="/")
    response = await client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 403
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_rbac_uses_current_database_role_without_admin_bypass(auth_env):
    from app.dependencies.auth import require_roles

    client, factory, user_id, app = auth_env

    @app.get("/test-student", dependencies=[Depends(require_roles("STUDENT"))])
    async def student_only():
        return {"ok": True}

    @app.get("/test-staff", dependencies=[Depends(require_roles("STAFF"))])
    async def staff_only():
        return {"ok": True}

    @app.get("/test-admin", dependencies=[Depends(require_roles("ADMIN"))])
    async def admin_only():
        return {"ok": True}

    await sign_in(client)

    for role in ("STUDENT", "STAFF", "ADMIN"):
        async with factory.begin() as db:
            (await db.get(User, user_id)).role = role
        for allowed in ("STUDENT", "STAFF", "ADMIN"):
            response = await client.get("/test-" + allowed.lower())
            assert response.status_code == (200 if role == allowed else 403)


async def test_repeated_login_keeps_other_sessions_valid(auth_env):
    client, _, _, _ = auth_env
    await sign_in(client)
    first = client.cookies["prl_session"]
    await sign_in(client)
    second = client.cookies["prl_session"]
    assert first != second
    for token in (first, second):
        response = await client.get("/api/v1/auth/me", headers={"Cookie": "prl_session=" + token})
        assert response.status_code == 200


async def test_unknown_account_performs_dummy_verification(auth_env, monkeypatch):
    client, _, _, app = auth_env
    calls = []
    original = app.state.passwords.verify_dummy

    async def spy(password):
        calls.append(True)
        await original(password)

    monkeypatch.setattr(app.state.passwords, "verify_dummy", spy)
    response = await client.post(
        "/api/v1/auth/login",
        json=CREDENTIALS | {"email": "missing@example.invalid"},
        headers=ORIGIN,
    )
    assert response.status_code == 401 and calls == [True]


@pytest.mark.parametrize("route", ["me", "csrf", "logout"])
async def test_database_failure_during_session_lookup_is_not_401_or_204(
    auth_env, monkeypatch, route
):
    from app.repositories import sessions

    client, _, _, _ = auth_env
    await sign_in(client)

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("test-session-query-secret")

    monkeypatch.setattr(sessions, "find_with_user", fail)
    response = await client.request(
        "POST" if route == "logout" else "GET", "/api/v1/auth/" + route, headers=ORIGIN
    )
    assert response.status_code == 503
    assert response.headers["X-Error-Class"] == "service"
    assert "test-session-query-secret" not in response.text
    assert "Set-Cookie" not in response.headers


async def test_database_failure_is_service_error_not_credentials(auth_env, monkeypatch, caplog):
    from app.repositories import users

    client, _, _, _ = auth_env

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("synthetic-secret-password-sql")

    monkeypatch.setattr(users, "find_by_email", fail)
    result = await sign_in(client)
    assert result.status_code == 503
    assert result.headers["X-Error-Class"] == "service"
    assert "synthetic-secret" not in result.text + caplog.text
    assert "Set-Cookie" not in result.headers


@pytest.mark.parametrize("commit", [False, True])
async def test_deactivation_and_revocation_share_caller_transaction(auth_env, commit):
    from app.services.auth import revoke_user_sessions

    client, factory, user_id, _ = auth_env
    await sign_in(client)
    async with factory() as db:
        transaction = await db.begin()
        user = await db.get(User, user_id, with_for_update=True)
        user.is_active = False
        await revoke_user_sessions(db, user_id)
        await (transaction.commit() if commit else transaction.rollback())
    async with factory() as db:
        user = await db.get(User, user_id)
        row = (await db.scalars(select(Session))).one()
        assert user.is_active is (not commit)
        assert (row.revoked_at is not None) is commit
    assert (await client.get("/api/v1/auth/me")).status_code == (401 if commit else 200)
