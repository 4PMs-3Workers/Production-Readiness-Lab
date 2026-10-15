"""Authentication owns transactions; account locks serialize issuance/revocation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_errors import auth_error
from app.api.errors import ServiceUnavailable
from app.core.config import Settings
from app.core.login_throttle import LoginThrottle
from app.core.passwords import PasswordService
from app.core.session_tokens import hash_session_token, new_session_token, valid_session_token
from app.domain.auth import AuthContext, LoginResult
from app.models import Session, User
from app.repositories import sessions, users
from app.schemas.auth import LoginRequest, Role


@asynccontextmanager
async def _transaction(db: AsyncSession, settings: Settings) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(settings.db_query_timeout_seconds):
            async with db.begin():
                yield
    except (SQLAlchemyError, TimeoutError):
        if db.in_transaction():
            await db.rollback()
        raise ServiceUnavailable() from None


def _context(row: Session, user: User) -> AuthContext:
    return AuthContext(
        row.id, user.id, user.email, user.name, cast(Role, user.role), row.expires_at
    )


async def _lock(db: AsyncSession, user_id: UUID, settings: Settings) -> User | None:
    async with asyncio.timeout(settings.db_lock_timeout_seconds):
        return await users.lock_by_id(db, user_id)


async def login(
    db: AsyncSession,
    credentials: LoginRequest,
    *,
    settings: Settings,
    passwords: PasswordService,
    throttle: LoginThrottle,
) -> LoginResult:
    async with _transaction(db, settings):
        user = await users.find_by_email(db, credentials.email)
        snapshot = (user.id, user.password_hash) if user is not None else None
        throttle_email = user.email if user is not None else credentials.email
    throttle.check_email(throttle_email)
    password = credentials.password.get_secret_value()
    if snapshot is None:
        await passwords.verify_dummy(password)
        raise auth_error("INVALID_CREDENTIALS")
    user_id, encoded_hash = snapshot
    if not await passwords.verify(password, encoded_hash):
        raise auth_error("INVALID_CREDENTIALS")
    async with _transaction(db, settings):
        user = await _lock(db, user_id, settings)
        if user is None or not user.is_active or user.password_hash != encoded_hash:
            raise auth_error("INVALID_CREDENTIALS")
        now = await users.utc_now(db)
        token = new_session_token()
        row = Session(
            user_id=user.id,
            token_hash=hash_session_token(token),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.session_lifetime_seconds),
        )
        await sessions.add_session(db, row)
        result = LoginResult(_context(row, user), token)
    return result


async def authenticate(
    db: AsyncSession, token: str | None, *, settings: Settings
) -> AuthContext | None:
    if token is None or not valid_session_token(token):
        return None
    async with _transaction(db, settings):
        pair = await sessions.find_with_user(db, hash_session_token(token))
        if pair is None:
            return None
        row, user = pair
        now = await users.utc_now(db)
        if row.revoked_at is not None or row.expires_at <= now or not user.is_active:
            return None
        return _context(row, user)


async def logout(db: AsyncSession, context: AuthContext | None, *, settings: Settings) -> None:
    if context is None:
        return
    async with _transaction(db, settings):
        if await _lock(db, context.user_id, settings) is not None:
            await sessions.revoke_one(
                db, context.session_id, context.user_id, await users.utc_now(db)
            )


async def revoke_user_sessions(db: AsyncSession, user_id: UUID) -> None:
    """Join a caller-owned transaction; caller applies its own operation deadline."""
    if not db.in_transaction():
        raise RuntimeError("Session revocation requires a caller-owned transaction")
    await users.lock_by_id(db, user_id)
    await sessions.revoke_all(db, user_id, await users.utc_now(db))
