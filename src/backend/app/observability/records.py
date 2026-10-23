"""Explicit telemetry record and attribute allowlists."""

import json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

EVENTS = frozenset(
    {
        "http.server.request",
        "db.statement",
        "db.pool",
        "worker.starting",
        "worker.ready",
        "worker.heartbeat",
        "worker.scan",
        "worker.candidate",
        "worker.stopping",
        "worker.stopped",
        "worker.fatal",
        "telemetry.export",
    }
)
SpanName = Literal[
    "http.server",
    "booking.create",
    "proposal.create",
    "proposal.accept",
    "proposal.decline",
    "proposal.expire",
    "db.query",
    "db.commit",
    "db.rollback",
    "worker.scan",
    "worker.candidate",
]
SPAN_NAMES: frozenset[str] = frozenset(
    {
        "http.server",
        "booking.create",
        "proposal.create",
        "proposal.accept",
        "proposal.decline",
        "proposal.expire",
        "db.query",
        "db.commit",
        "db.rollback",
        "worker.scan",
        "worker.candidate",
    }
)
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "OTHER"})
ERROR_CODES = frozenset(
    {
        "DATABASE_UNAVAILABLE",
        "DEADLINE_EXCEEDED",
        "COMMIT_UNKNOWN",
        "INCONSISTENT",
        "UNEXPECTED_FAILURE",
        "SCHEMA_INCOMPATIBLE",
        "CONFIGURATION_INVALID",
        "database_unavailable",
        "deadline_exceeded",
        "commit_unknown",
        "inconsistent",
        "unexpected_failure",
        "schema_incompatible",
        "configuration_invalid",
        "http_4xx",
        "http_5xx",
        "pool_timeout",
        "connection_error",
        "statement_error",
        "transaction_error",
    }
)
WORKER_PHASES = frozenset({"starting", "running", "stopping", "stopped"})
WORKER_OUTCOMES = frozenset({"EXPIRED", "NOOP", "FAILED"})
UNMATCHED_ROUTE = "__unmatched__"

_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_TRACE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_ATTRIBUTE_NAMES = frozenset(
    {
        "http.route",
        "http.request.method",
        "http.response.status_code",
        "error.type",
        "db.system.name",
        "db.operation.name",
        "worker.name",
        "worker.phase",
        "worker.outcome",
    }
)
_LOG_FIELDS = frozenset(
    {
        "request_id",
        "run_id",
        "trace_id",
        "route",
        "method",
        "status",
        "duration_seconds",
        "error_code",
        "worker_id",
        "current_run_id",
        "phase",
        "database_ok",
        "last_successful_scan",
        "last_scan_duration_seconds",
        "successful_scans",
        "failed_scans",
        "expired",
        "noops",
        "failed",
        "selected",
        "batch_expired",
        "batch_noops",
        "batch_failed",
        "reporting_failures",
        "backlog_count",
        "backlog_capped",
        "oldest_overdue_seconds",
        "attempts",
        "outcome",
    }
)


class InvalidTelemetryRecord(ValueError):
    """A record contains an invalid allowlisted value or cannot be encoded safely."""


class RouteAllowlist:
    """One-time, bounded inventory of registered route templates."""

    def __init__(self) -> None:
        self._routes = frozenset({UNMATCHED_ROUTE})
        self._registered = False
        self._frozen = False

    @property
    def routes(self) -> frozenset[str]:
        return self._routes

    @property
    def registered(self) -> bool:
        return self._registered

    def freeze(self) -> None:
        self._frozen = True

    def allows(self, route: str) -> bool:
        return route in self._routes

    def register(self, routes: Iterable[str]) -> None:
        if isinstance(routes, str):
            raise ValueError("Invalid route inventory")
        try:
            iterator = iter(routes)
        except TypeError as exc:
            raise ValueError("Invalid route inventory") from exc
        items = []
        for _ in range(129):
            try:
                items.append(next(iterator))
            except StopIteration:
                break
        if len(items) > 128:
            raise ValueError("Invalid route inventory")
        if any(not isinstance(route, str) for route in items):
            raise ValueError("Invalid route inventory")
        if len(items) > 128 or len(set(items)) != len(items):
            raise ValueError("Invalid route inventory")
        normalized: set[str] = set()
        for route in items:
            if not isinstance(route, str) or not route or len(route) > 128:
                raise ValueError("Invalid route template")
            try:
                route.encode("ascii")
            except UnicodeEncodeError as exc:
                raise ValueError("Invalid route template") from exc
            if any(ord(character) < 32 or ord(character) == 127 for character in route):
                raise ValueError("Invalid route template")
            if "?" in route or "#" in route or not route.startswith("/"):
                raise ValueError("Invalid route template")
            normalized.add(route)
        new_routes = frozenset(normalized | {UNMATCHED_ROUTE})
        if self._registered:
            if new_routes != self._routes:
                raise ValueError("Route inventory already registered")
            return
        if self._frozen:
            raise ValueError("Route inventory must be registered before telemetry starts")
        self._routes = new_routes
        self._registered = True


def _identifier(value: object, *, max_chars: int = 128) -> str:
    if isinstance(value, UUID):
        normalized = value.hex
    elif isinstance(value, str) and _ID_RE.fullmatch(value) is not None:
        normalized = value
    else:
        raise InvalidTelemetryRecord("Invalid identifier")
    if len(normalized) > max_chars:
        raise InvalidTelemetryRecord("Invalid identifier")
    return normalized


def _number(value: object, *, nonnegative: bool = True) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidTelemetryRecord("Invalid numeric value")
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise InvalidTelemetryRecord("Invalid numeric value")
    return value


def _timestamp(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidTelemetryRecord("Invalid timestamp")
        parsed = value.astimezone(UTC)
        return parsed.isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and len(value) <= 32:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidTelemetryRecord("Invalid timestamp") from None
        if parsed.tzinfo is None:
            raise InvalidTelemetryRecord("Invalid timestamp")
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise InvalidTelemetryRecord("Invalid timestamp")


def sanitize_record(
    record: Mapping[str, object],
    *,
    service: str,
    environment: str,
    release: str,
    routes: RouteAllowlist,
    max_attribute_chars: int = 128,
) -> dict[str, object]:
    """Build a JSON-ready record from finite, explicitly permitted scalar fields."""
    event = record.get("event")
    if not isinstance(event, str) or event not in EVENTS:
        raise InvalidTelemetryRecord("Invalid event")
    cleaned: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "service": service,
        "environment": environment,
        "release": release,
        "event": event,
    }
    for key in _LOG_FIELDS:
        if key not in record:
            continue
        value = record[key]
        if key in {"request_id", "run_id", "worker_id", "current_run_id"}:
            cleaned[key] = _identifier(value, max_chars=max_attribute_chars)
        elif key == "trace_id":
            if not isinstance(value, str) or _TRACE_ID_RE.fullmatch(value) is None:
                raise InvalidTelemetryRecord("Invalid trace identifier")
            cleaned[key] = value
        elif key == "route":
            cleaned[key] = (
                value
                if isinstance(value, str)
                and len(value) <= max_attribute_chars
                and routes.allows(value)
                else UNMATCHED_ROUTE
            )
        elif key == "method":
            cleaned[key] = value if isinstance(value, str) and value in METHODS else "OTHER"
        elif key == "status":
            if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
                raise InvalidTelemetryRecord("Invalid status")
            cleaned[key] = value
        elif key in {"duration_seconds", "last_scan_duration_seconds", "oldest_overdue_seconds"}:
            cleaned[key] = _number(value)
        elif key == "error_code":
            if not isinstance(value, str) or value not in ERROR_CODES:
                raise InvalidTelemetryRecord("Invalid error code")
            cleaned[key] = value
        elif key == "phase":
            if not isinstance(value, str) or value not in WORKER_PHASES:
                raise InvalidTelemetryRecord("Invalid worker phase")
            cleaned[key] = value
        elif key == "outcome":
            if not isinstance(value, str) or value not in WORKER_OUTCOMES:
                raise InvalidTelemetryRecord("Invalid worker outcome")
            cleaned[key] = value
        elif key in {"database_ok", "backlog_capped"}:
            if not isinstance(value, bool):
                raise InvalidTelemetryRecord("Invalid worker flag")
            cleaned[key] = value
        elif key == "last_successful_scan":
            cleaned[key] = _timestamp(value)
        elif key in {
            "successful_scans",
            "failed_scans",
            "expired",
            "noops",
            "failed",
            "selected",
            "batch_expired",
            "batch_noops",
            "batch_failed",
            "reporting_failures",
            "backlog_count",
            "attempts",
        }:
            numeric = _number(value)
            if not isinstance(numeric, int) or numeric > 10_000_000:
                raise InvalidTelemetryRecord("Invalid worker count")
            cleaned[key] = numeric
    try:
        encoded = json.dumps(cleaned, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidTelemetryRecord("Invalid telemetry encoding") from exc
    return cleaned


def sanitize_span_attributes(
    attributes: Mapping[str, object],
    *,
    routes: RouteAllowlist,
    limit: int = 16,
    max_attribute_chars: int = 128,
) -> dict[str, object]:
    """Keep only known span keys with bounded scalar values."""
    safe: dict[str, object] = {}
    for index, (key, value) in enumerate(attributes.items()):
        if index >= 128 or len(safe) >= limit:
            break
        if not isinstance(key, str) or key not in _ATTRIBUTE_NAMES:
            continue
        if key == "http.route":
            safe[key] = (
                value
                if isinstance(value, str)
                and len(value) <= max_attribute_chars
                and routes.allows(value)
                else UNMATCHED_ROUTE
            )
        elif key == "http.request.method":
            safe[key] = value if isinstance(value, str) and value in METHODS else "OTHER"
        elif key == "http.response.status_code":
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                safe[key] = value
        elif key == "error.type":
            if isinstance(value, str) and value in ERROR_CODES:
                safe[key] = value
        elif key == "db.system.name":
            if isinstance(value, str) and value == "mysql":
                safe[key] = value
        elif key == "db.operation.name":
            if isinstance(value, str) and value in {
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "BEGIN",
                "COMMIT",
                "ROLLBACK",
            }:
                safe[key] = value
        elif key == "worker.name":
            if value == "expiry":
                safe[key] = value
        elif key == "worker.phase":
            if isinstance(value, str) and value in WORKER_PHASES:
                safe[key] = value
        elif key == "worker.outcome":
            if isinstance(value, str) and value in WORKER_OUTCOMES:
                safe[key] = value
    return safe


def encode_item(value: object) -> bytes:
    """Encode sanitized data deterministically, rejecting NaN and Infinity."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidTelemetryRecord("Invalid telemetry encoding") from exc
