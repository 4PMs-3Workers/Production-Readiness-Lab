"""Bounded asynchronous OTLP export adapters using the OpenTelemetry SDK APIs."""

import asyncio
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.context import Context
from opentelemetry.sdk._logs import LogRecordProcessor, ReadableLogRecord, ReadWriteLogRecord
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Status

from app.core.publishing import DROP_REASONS
from app.observability.records import (
    ERROR_CODES,
    EVENTS,
    METHODS,
    SPAN_NAMES,
    UNMATCHED_ROUTE,
    WORKER_OUTCOMES,
    WORKER_PHASES,
    InvalidTelemetryRecord,
    RouteAllowlist,
    encode_item,
    sanitize_span_attributes,
)

_SERVICE_RESOURCE_KEYS = ("service.name", "deployment.environment.name", "service.version")
_SAFE_INSTRUMENTATION_SCOPE = InstrumentationScope("app.observability", "0.1.0")


@dataclass
class _LaneItem:
    value: object
    encoded: bytes
    deadline: float
    dropped: bool = False


class ExportLane:
    """One daemon exporter lane with capacity, age, batch, and byte bounds."""

    def __init__(
        self,
        signal: str,
        exporter: Callable[[Sequence[object]], object] | object,
        *,
        capacity: int,
        max_age: float,
        max_batch: int,
        max_item_bytes: int,
        on_drop: Callable[[str], object] | None = None,
        on_exported: Callable[[int], object] | None = None,
        on_failed: Callable[[int], object] | None = None,
        on_queue_change: Callable[[int], object] | None = None,
    ) -> None:
        if capacity < 1 or max_batch < 1 or max_item_bytes < 1:
            raise ValueError("Invalid exporter bounds")
        if not math.isfinite(max_age) or max_age <= 0 or max_age > 1:
            raise ValueError("Invalid exporter bounds")
        self.signal = signal
        self._exporter = exporter
        self._capacity, self._max_age = capacity, max_age
        self._max_batch, self._max_item_bytes = max_batch, max_item_bytes
        self._on_drop = on_drop
        self._on_exported = on_exported
        self._on_failed = on_failed
        self._on_queue_change = on_queue_change
        self._condition = threading.Condition()
        self._queue: deque[_LaneItem] = deque()
        self._active: list[_LaneItem] = []
        self._outstanding = 0
        self._accepted_count = 0
        self._exported_count = 0
        self._drop_counts: dict[str, int] = {reason: 0 for reason in DROP_REASONS}
        self._closing = False
        self._abort = False
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"telemetry-{signal}-exporter", daemon=True
        )
        self._thread.start()

    def _callback(self, callback: Callable[..., object] | None, *args: object) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            pass

    def _drop_item(self, item: _LaneItem, reason: str) -> None:
        if item.dropped:
            return
        item.dropped = True
        safe_reason = reason if reason in self._drop_counts else "invalid"
        self._drop_counts[safe_reason] += 1
        self._callback(self._on_drop, safe_reason)

    def _queue_changed(self) -> None:
        self._callback(self._on_queue_change, self._outstanding)

    def _expire(self) -> None:
        now = time.monotonic()
        for item in self._active:
            if item.deadline <= now:
                self._drop_item(item, "expired")
        retained: deque[_LaneItem] = deque()
        while self._queue:
            item = self._queue.popleft()
            if item.deadline <= now:
                self._drop_item(item, "expired")
                self._outstanding -= 1
            else:
                retained.append(item)
        self._queue = retained
        self._queue_changed()

    def _reject(self, reason: str) -> bool:
        safe_reason = reason if reason in self._drop_counts else "invalid"
        self._drop_counts[safe_reason] += 1
        self._callback(self._on_drop, safe_reason)
        return False

    def note_drop(self, reason: str) -> None:
        with self._condition:
            self._reject(reason)

    def submit(self, value: object, *, encoded: bytes | None = None) -> bool:
        try:
            payload = encode_item(value) if encoded is None else encoded
            if not isinstance(payload, bytes):
                raise InvalidTelemetryRecord("Invalid encoded item")
        except Exception:
            with self._condition:
                return self._reject("invalid")
        with self._condition:
            self._expire()
            if len(payload) > self._max_item_bytes:
                return self._reject("oversize")
            if self._closing:
                return self._reject("shutdown")
            if self._outstanding >= self._capacity:
                return self._reject("queue_full")
            self._queue.append(_LaneItem(value, payload, time.monotonic() + self._max_age))
            self._outstanding += 1
            self._accepted_count += 1
            self._queue_changed()
            self._condition.notify()
            return True

    def _call_exporter(self, batch: Sequence[object]) -> object:
        exporter = self._exporter
        if callable(exporter):
            return exporter(batch)
        return cast(Any, exporter).export(batch)

    def _is_success(self, result: object) -> bool:
        if result is True:
            return True
        return getattr(result, "name", None) == "SUCCESS"

    def _shutdown_exporter(self) -> None:
        shutdown = getattr(self._exporter, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._expire()
                    while not self._queue and not self._closing:
                        self._condition.wait()
                        self._expire()
                    if self._abort or (self._closing and not self._queue):
                        break
                    batch_items: list[_LaneItem] = []
                    while self._queue and len(batch_items) < self._max_batch:
                        item = self._queue.popleft()
                        if item.deadline <= time.monotonic():
                            self._drop_item(item, "expired")
                            self._outstanding -= 1
                        else:
                            batch_items.append(item)
                    self._active = batch_items
                    self._queue_changed()
                if not batch_items:
                    continue
                try:
                    succeeded = self._is_success(
                        self._call_exporter([item.value for item in batch_items])
                    )
                except Exception:
                    succeeded = False
                with self._condition:
                    exported_now = 0
                    failed_now = 0
                    for item in batch_items:
                        if not item.dropped:
                            if succeeded:
                                self._exported_count += 1
                                exported_now += 1
                            else:
                                self._drop_item(item, "export_failed")
                                failed_now += 1
                        self._outstanding -= 1
                    self._active = []
                    self._queue_changed()
                    if exported_now:
                        self._callback(self._on_exported, exported_now)
                    if failed_now:
                        self._callback(self._on_failed, failed_now)
                    self._condition.notify_all()
        except Exception:
            # Exporter diagnostics are deliberately contained and never logged with payloads.
            with self._condition:
                for item in self._active:
                    self._drop_item(item, "export_failed")
                self._active = []
                while self._queue:
                    item = self._queue.popleft()
                    self._drop_item(item, "shutdown")
                    self._outstanding -= 1
                self._queue_changed()
        finally:
            self._shutdown_exporter()
            with self._condition:
                active_items = self._active
                for item in active_items:
                    self._drop_item(item, "shutdown")
                self._outstanding = max(0, self._outstanding - len(active_items))
                self._active = []
                while self._queue:
                    item = self._queue.popleft()
                    self._drop_item(item, "shutdown")
                    self._outstanding -= 1
                self._queue_changed()
                self._done.set()

    @property
    def accepted_count(self) -> int:
        with self._condition:
            return self._accepted_count

    @property
    def exported_count(self) -> int:
        with self._condition:
            return self._exported_count

    @property
    def dropped(self) -> dict[str, int]:
        with self._condition:
            self._expire()
            return self._drop_counts.copy()

    @property
    def queue_size(self) -> int:
        with self._condition:
            self._expire()
            return self._outstanding

    @property
    def is_idle(self) -> bool:
        return self.queue_size == 0

    def request_close(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    async def close(self, deadline: float) -> bool:
        self.request_close()
        loop = asyncio.get_running_loop()
        while not self._done.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.005, remaining))
        if not self._done.is_set():
            with self._condition:
                self._abort = True
                for item in self._active:
                    self._drop_item(item, "shutdown")
                while self._queue:
                    item = self._queue.popleft()
                    self._drop_item(item, "shutdown")
                    self._outstanding -= 1
                self._queue_changed()
                self._condition.notify_all()
        return self._done.is_set()


def _service_resource(service: str, environment: str, release: str) -> Resource:
    return Resource(
        {
            _SERVICE_RESOURCE_KEYS[0]: service,
            _SERVICE_RESOURCE_KEYS[1]: environment,
            _SERVICE_RESOURCE_KEYS[2]: release,
        }
    )


class BoundedSpanProcessor(SpanProcessor):
    """Sanitize ended spans synchronously, then enqueue only bounded SDK records."""

    def __init__(
        self,
        lane: ExportLane,
        *,
        service: str,
        environment: str,
        release: str,
        max_attributes: int,
        max_attribute_chars: int,
        routes: RouteAllowlist,
    ) -> None:
        self._lane = lane
        self._resource = _service_resource(service, environment, release)
        self._max_attributes = max_attributes
        self._max_attribute_chars = max_attribute_chars
        self._routes = routes

    def on_start(self, span, parent_context: Context | None = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        try:
            span_context = span.context
            if span.name not in SPAN_NAMES or span_context is None:
                self._lane.note_drop("invalid")
                return
            attributes = sanitize_span_attributes(
                span.attributes or {},
                routes=self._routes,
                limit=self._max_attributes,
                max_attribute_chars=self._max_attribute_chars,
            )
            scrubbed = ReadableSpan(
                name=span.name,
                context=span.context,
                parent=span.parent,
                resource=self._resource,
                attributes=cast(Any, attributes),
                events=(),
                links=(),
                kind=span.kind,
                instrumentation_scope=_SAFE_INSTRUMENTATION_SCOPE,
                status=Status(span.status.status_code),
                start_time=span.start_time,
                end_time=span.end_time,
            )
            payload = {
                "name": scrubbed.name,
                "trace_id": f"{span_context.trace_id:032x}",
                "span_id": f"{span_context.span_id:016x}",
                "attributes": attributes,
            }
            self._lane.submit(scrubbed, encoded=encode_item(payload))
        except Exception:
            self._lane.note_drop("invalid")

    def shutdown(self) -> None:
        self._lane.request_close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._lane.is_idle


class BoundedLogRecordProcessor(LogRecordProcessor):
    """Sanitize SDK log callbacks and enqueue immutable records to one daemon lane."""

    def __init__(
        self,
        lane: ExportLane,
        *,
        service: str,
        environment: str,
        release: str,
        max_attribute_chars: int,
        routes: RouteAllowlist,
    ) -> None:
        self._lane = lane
        self._resource = _service_resource(service, environment, release)
        self._max_attribute_chars = max_attribute_chars
        self._routes = routes

    def on_emit(self, log_record: ReadWriteLogRecord) -> None:
        try:
            source = log_record.log_record
            event = source.event_name or source.body
            if not isinstance(event, str) or event not in EVENTS:
                self._lane.note_drop("invalid")
                return
            source_attributes = source.attributes or {}
            safe_attributes = sanitize_log_attributes(
                source_attributes,
                routes=self._routes,
                max_attribute_chars=self._max_attribute_chars,
            )
            safe_record = LogRecord(
                timestamp=source.timestamp,
                observed_timestamp=source.observed_timestamp,
                context=source.context or Context(),
                severity_number=SeverityNumber.INFO,
                severity_text="INFO",
                body=event,
                attributes=cast(Any, safe_attributes),
                event_name=event,
            )
            readable = ReadableLogRecord(
                log_record=safe_record,
                resource=self._resource,
                instrumentation_scope=_SAFE_INSTRUMENTATION_SCOPE,
                limits=log_record.limits,
            )
            payload = {
                "event": event,
                "attributes": safe_attributes,
                "timestamp": source.timestamp,
            }
            self._lane.submit(readable, encoded=encode_item(payload))
        except Exception:
            self._lane.note_drop("invalid")

    def shutdown(self) -> None:
        self._lane.request_close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._lane.is_idle


def sanitize_log_attributes(
    attributes: Mapping[str, Any],
    *,
    routes: RouteAllowlist,
    max_attribute_chars: int = 128,
) -> dict[str, object]:
    """Keep only the runtime's approved scalar log fields for OTLP."""
    allowed = {
        "request_id",
        "run_id",
        "worker_id",
        "current_run_id",
        "trace_id",
        "route",
        "method",
        "status",
        "duration_seconds",
        "last_scan_duration_seconds",
        "oldest_overdue_seconds",
        "last_successful_scan",
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
        "attempts",
        "error_code",
        "response_complete",
        "phase",
        "database_ok",
        "outcome",
    }
    safe: dict[str, object] = {}
    for index, (key, value) in enumerate(attributes.items()):
        if index >= 128:
            break
        if not isinstance(key, str) or key not in allowed:
            continue
        if key in {"request_id", "run_id", "worker_id", "current_run_id"}:
            if (
                isinstance(value, str)
                and len(value) <= max_attribute_chars
                and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value)
            ):
                safe[key] = value
        elif key == "trace_id":
            if (
                isinstance(value, str)
                and len(value) == 32
                and all(character in "0123456789abcdef" for character in value)
            ):
                safe[key] = value
        elif key == "route":
            safe[key] = (
                value
                if isinstance(value, str)
                and len(value) <= max_attribute_chars
                and routes.allows(value)
                else UNMATCHED_ROUTE
            )
        elif key == "method":
            safe[key] = value if isinstance(value, str) and value in METHODS else "OTHER"
        elif key == "status":
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                safe[key] = value
        elif key in {"duration_seconds", "last_scan_duration_seconds", "oldest_overdue_seconds"}:
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            ):
                safe[key] = value
        elif key == "last_successful_scan":
            if isinstance(value, datetime):
                if value.tzinfo is not None and value.utcoffset() is not None:
                    safe[key] = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
            elif isinstance(value, str) and len(value) <= 32:
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if parsed.tzinfo is not None:
                    safe[key] = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
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
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000_000:
                safe[key] = value
        elif key == "error_code":
            if isinstance(value, str) and value in ERROR_CODES:
                safe[key] = value
        elif key == "phase":
            if isinstance(value, str) and value in WORKER_PHASES:
                safe[key] = value
        elif key == "outcome":
            if isinstance(value, str) and value in WORKER_OUTCOMES:
                safe[key] = value
        elif key in {"database_ok", "backlog_capped"} and isinstance(value, bool):
            safe[key] = value
        elif key == "response_complete" and isinstance(value, bool):
            safe[key] = value
    return safe
