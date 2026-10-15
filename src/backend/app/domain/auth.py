"""Immutable values crossing authentication transaction boundaries."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.schemas.auth import Role


@dataclass(frozen=True)
class AuthContext:
    session_id: UUID
    user_id: UUID
    email: str
    name: str
    role: Role
    expires_at: datetime


@dataclass(frozen=True)
class LoginResult:
    context: AuthContext
    session_token: str = field(repr=False)
