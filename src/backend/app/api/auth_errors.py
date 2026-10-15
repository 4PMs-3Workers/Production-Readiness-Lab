"""Stable auth errors layered on the shared HTTP contract."""

from typing import Any

from app.api.errors import AppError
from app.schemas.common import ErrorResponse

ERRORS = {
    "INVALID_CREDENTIALS": (401, "Invalid email or password"),
    "INVALID_SESSION": (401, "Invalid session"),
    "FORBIDDEN_ROLE": (403, "Role not permitted"),
    "INVALID_CSRF": (403, "Invalid CSRF token"),
    "INVALID_ORIGIN": (403, "Invalid request origin"),
    "LOGIN_THROTTLED": (429, "Too many login attempts"),
    "PAYLOAD_TOO_LARGE": (413, "Request payload too large"),
    "UNSUPPORTED_MEDIA_TYPE": (415, "JSON input required"),
}


def auth_error(code: str, *, retry_after: int | None = None) -> AppError:
    status, message = ERRORS[code]
    headers = {"Cache-Control": "private, no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(max(1, retry_after))
    return AppError(status, code, message, "input", headers)


def auth_responses(success: int, *statuses: int) -> dict[int | str, dict[str, Any]]:
    headers = {name: {"schema": {"type": "string"}} for name in ("X-Request-ID", "Cache-Control")}
    result: dict[int | str, dict[str, Any]] = {success: {"headers": headers}}
    for status in statuses:
        error_headers = headers | {"X-Error-Class": {"schema": {"type": "string"}}}
        if status == 429:
            error_headers["Retry-After"] = {"schema": {"type": "string"}}
        result[status] = {
            "model": ErrorResponse,
            "headers": error_headers,
            "description": "Sanitized authentication error",
        }
    return result
