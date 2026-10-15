from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.passwords import PasswordService


def test_throttle_limits_expire_and_state_is_bounded(auth_settings):
    from app.core.login_throttle import LoginThrottle

    now = [0.0]
    limiter = LoginThrottle(auth_settings, clock=lambda: now[0])
    for _ in range(30):
        limiter.check_ip("127.0.0.1")
    with pytest.raises(AppError) as caught:
        limiter.check_ip("127.0.0.1")
    assert caught.value.headers is not None
    assert caught.value.headers["Retry-After"] == "60"
    for _ in range(5):
        limiter.check_email("a@example.invalid")
    with pytest.raises(AppError):
        limiter.check_email(" A@Example.invalid ")
    now[0] = 60.0
    limiter.check_ip("127.0.0.1")
    limiter.check_email("a@example.invalid")
    small = LoginThrottle(
        auth_settings.model_copy(update={"login_state_max_entries": 2}), clock=lambda: now[0]
    )
    small.check_ip("1")
    small.check_ip("2")
    with pytest.raises(AppError):
        small.check_ip("3")
    now[0] = 120.0
    small.check_ip("3")


async def test_equivalent_lookup_emails_share_one_login_budget(auth_settings, monkeypatch):
    from app.core.login_throttle import LoginThrottle
    from app.repositories import users
    from app.schemas.auth import LoginRequest
    from app.services import auth

    class Session:
        @asynccontextmanager
        async def begin(self):
            yield

    class Passwords:
        def __init__(self):
            self.verifications = 0

        async def verify(self, password, encoded_hash):
            self.verifications += 1
            return False

    account = SimpleNamespace(
        id=uuid4(), email="student@example.invalid", password_hash="stored-hash"
    )

    async def find_by_email(db, email):
        return account

    monkeypatch.setattr(users, "find_by_email", find_by_email)
    passwords = Passwords()
    throttle = LoginThrottle(auth_settings)
    emails = ["student@example.invalid"] + [
        "student" + "\u200b" * count + "@example.invalid" for count in range(1, 6)
    ]
    for index, email in enumerate(emails):
        with pytest.raises(AppError) as caught:
            await auth.login(
                cast(AsyncSession, Session()),
                LoginRequest.model_validate({"email": email, "password": "wrong-password"}),
                settings=auth_settings,
                passwords=cast(PasswordService, passwords),
                throttle=throttle,
            )
        assert caught.value.code == ("LOGIN_THROTTLED" if index == 5 else "INVALID_CREDENTIALS")
        if index == 5:
            assert caught.value.headers is not None
            assert int(caught.value.headers["Retry-After"]) > 0
    assert passwords.verifications == 5
