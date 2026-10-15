"""Canonical opaque tokens; persistence accepts only a full SHA-256 digest."""

import base64
import hashlib
import re
import secrets


def new_session_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def valid_session_token(token: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        return False
    decoded = base64.urlsafe_b64decode(token + "=")
    return base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") == token


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()
