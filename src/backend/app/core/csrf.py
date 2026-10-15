"""Signed double-submit tokens bound to the database session ID."""

import base64
import hashlib
import hmac
from uuid import UUID

from app.api.auth_errors import auth_error
from app.core.session_tokens import new_session_token, valid_session_token


def _signature(session_id: UUID, nonce: str, secret: str) -> str:
    message = b"prl-csrf-v1" + session_id.bytes + base64.urlsafe_b64decode(nonce + "=")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_csrf_token(session_id: UUID, secret: str) -> str:
    nonce = new_session_token()
    return "v1." + nonce + "." + _signature(session_id, nonce, secret)


def verify_csrf_token(
    *, cookie: str | None, header: str | None, session_id: UUID, secret: str
) -> None:
    if (
        not cookie
        or not header
        or len(cookie) != 90
        or not cookie.isascii()
        or not header.isascii()
    ):
        raise auth_error("INVALID_CSRF")
    parts = cookie.split(".")
    if (
        len(parts) != 3
        or parts[0] != "v1"
        or not valid_session_token(parts[1])
        or not valid_session_token(parts[2])
        or not hmac.compare_digest(cookie, header)
        or not hmac.compare_digest(parts[2], _signature(session_id, parts[1], secret))
    ):
        raise auth_error("INVALID_CSRF")
