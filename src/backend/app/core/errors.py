"""Shared sanitized error types without HTTP adapter or router initialization."""

from collections.abc import Mapping
from typing import Literal

ErrorClass = Literal["input", "business", "service"]


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        error_class: ErrorClass,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.error_class = error_class
        self.headers = headers


class BusinessConflict(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(409, code, message, "business")


class ServiceUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(
            503,
            "SERVICE_UNAVAILABLE",
            "Service temporarily unavailable",
            "service",
            {"Cache-Control": "no-store"},
        )
