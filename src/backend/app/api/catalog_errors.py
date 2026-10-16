"""Stable sanitized errors and OpenAPI response metadata for catalog routes."""

from typing import Any

from app.api.errors import AppError
from app.schemas.common import ErrorResponse

ERRORS = {
    "NOT_FOUND": (404, "Catalog item not found"),
    "VALIDATION_ERROR": (422, "Invalid request"),
}


def catalog_error(code: str) -> AppError:
    status, message = ERRORS[code]
    return AppError(status, code, message, "input", {"Cache-Control": "private, no-store"})


def catalog_responses(success: int, *statuses: int) -> dict[int | str, dict[str, Any]]:
    request_id = {
        "schema": {"type": "string"},
        "description": "Identifier for this request",
    }
    cache_control = {
        "schema": {"type": "string"},
        "description": "Private responses are not stored",
    }
    response_headers = {"X-Request-ID": request_id, "Cache-Control": cache_control}
    error_headers = response_headers | {
        "X-Error-Class": {
            "schema": {"type": "string"},
            "description": "Classification of the sanitized error",
        }
    }
    result: dict[int | str, dict[str, Any]] = {success: {"headers": response_headers}}
    for status in statuses:
        result[status] = {
            "model": ErrorResponse,
            "headers": error_headers,
            "description": "Sanitized catalog error",
        }
    return result
