"""Per-process, safe telemetry providers with owner-bounded asynchronous export."""

import asyncio
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, cast

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import NonRecordingSpan, Span, get_current_span
from opentelemetry.util.types import Attributes

from app.core.config import Settings
from app.core.publishing import Publisher
from app.observability.exporting import (
    BoundedLogRecordProcessor,
    BoundedSpanProcessor,
    ExportLane,
)
from app.observability.metrics import TelemetryMetrics
from app.observability.records import (
    SPAN_NAMES,
    RouteAllowlist,
    SpanName,
    encode_item,
    sanitize_record,
)

Service = Literal["api", "worker"]


@dataclass
class _SpanBudget:
    count: int = 0


class TelemetryRuntime:
    """Owns isolated providers, metrics, JSON output, and optional OTLP lanes."""

    def __init__(
        self,
        settings: Settings,
        service: Service,
        *,
        stdout_sink=None,
        log_exporter=None,
        span_exporter=None,
    ) -> None:
        if service not in {"api", "worker"}:
            raise ValueError("Invalid service")
        self.settings = settings
        self.service: Service = service
        self.environment = settings.environment
        self.release = settings.service_release
        self._route_lock = threading.Lock()
        self.routes = RouteAllowlist()
        self.metrics = TelemetryMetrics(self.routes, on_http_request=self._freeze_routes)
        self._max_item_bytes = settings.telemetry_max_item_bytes
        self._max_span_attributes = settings.telemetry_max_span_attributes
        self._max_attribute_chars = settings.telemetry_max_attribute_chars
        self._max_spans_per_request = settings.telemetry_max_spans_per_request
        self._span_budget: ContextVar[_SpanBudget | None] = ContextVar(
            f"telemetry_span_budget_{id(self)}", default=None
        )
        self._closed = False
        self._log_lane: ExportLane | None = None
        self._trace_lane: ExportLane | None = None
        service_resource = Resource(
            {
                "service.name": service,
                "deployment.environment.name": settings.environment,
                "service.version": settings.service_release,
            }
        )
        self.trace_provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=service_resource,
            shutdown_on_exit=False,
        )
        self.log_provider = LoggerProvider(resource=service_resource, shutdown_on_exit=False)
        self._tracer = self.trace_provider.get_tracer("app.observability", "0.1.0")
        self._logger = self.log_provider.get_logger("app.observability", "0.1.0")
        log_capacity = settings.telemetry_log_queue_capacity
        self.metrics.configure_queue(service, "stdout", log_capacity)
        self._publisher = Publisher(
            stdout_sink,
            capacity=log_capacity,
            timeout=settings.telemetry_item_max_age_seconds,
            max_record_bytes=settings.telemetry_max_item_bytes,
            include_active_in_capacity=True,
            on_drop=self._record_stdout_drop,
            on_queue_change=lambda size: self.metrics.set_queue_size(service, "stdout", size),
            on_success=lambda amount: self.metrics.record_exported(service, "stdout", amount),
        )

        enabled = settings.telemetry_enabled
        if enabled or log_exporter is not None:
            otlp_logs = log_exporter or OTLPLogExporter(
                endpoint=f"{settings.telemetry_otlp_endpoint}/v1/logs",
                timeout=settings.telemetry_network_timeout_seconds,
            )
            self._log_lane = self._new_lane(
                "logs", otlp_logs, settings.telemetry_log_queue_capacity
            )
            self.log_processor = BoundedLogRecordProcessor(
                self._log_lane,
                service=service,
                environment=settings.environment,
                release=settings.service_release,
                max_attribute_chars=self._max_attribute_chars,
                routes=self.routes,
            )
            self.log_provider.add_log_record_processor(self.log_processor)
        else:
            self.log_processor = None

        if enabled or span_exporter is not None:
            otlp_traces = span_exporter or OTLPSpanExporter(
                endpoint=f"{settings.telemetry_otlp_endpoint}/v1/traces",
                timeout=settings.telemetry_network_timeout_seconds,
            )
            self._trace_lane = self._new_lane(
                "traces", otlp_traces, settings.telemetry_trace_queue_capacity
            )
            self.span_processor = BoundedSpanProcessor(
                self._trace_lane,
                service=service,
                environment=settings.environment,
                release=settings.service_release,
                max_attributes=settings.telemetry_max_span_attributes,
                max_attribute_chars=self._max_attribute_chars,
                routes=self.routes,
            )
            self.trace_provider.add_span_processor(self.span_processor)
        else:
            self.span_processor = None

    def _record_stdout_drop(self, reason: str) -> None:
        self.metrics.record_drop(self.service, "stdout", reason)
        if reason == "export_failed":
            self.metrics.record_export_failed(self.service, "stdout")

    def _new_lane(self, signal: str, exporter: object, capacity: int) -> ExportLane:
        self.metrics.configure_queue(self.service, signal, capacity)
        return ExportLane(
            signal,
            exporter,
            capacity=capacity,
            max_age=self.settings.telemetry_item_max_age_seconds,
            max_batch=self.settings.telemetry_max_batch_size,
            max_item_bytes=self._max_item_bytes,
            on_drop=lambda reason: self.metrics.record_drop(self.service, signal, reason),
            on_exported=lambda amount: self.metrics.record_exported(self.service, signal, amount),
            on_failed=lambda amount: self.metrics.record_export_failed(
                self.service, signal, amount
            ),
            on_queue_change=lambda size: self.metrics.set_queue_size(self.service, signal, size),
        )

    def register_routes(self, routes: Iterable[str]) -> None:
        """Register the finite set of router templates once before request handling."""
        with self._route_lock:
            self.routes.register(routes)

    def _freeze_routes(self) -> None:
        with self._route_lock:
            self.routes.freeze()

    @property
    def accepted_count(self) -> int:
        """JSON records admitted by the local diagnostic lane."""
        return self._publisher.accepted_count

    @property
    def dropped(self) -> dict[str, int]:
        counts = self._publisher.dropped
        for lane in (self._log_lane, self._trace_lane):
            if lane is None:
                continue
            for reason, count in lane.dropped.items():
                counts[reason] += count
        return counts

    def emit(self, record: Mapping[str, object]) -> bool:
        """Sanitize and enqueue a JSON record; no serialization or export exception escapes."""
        self._freeze_routes()
        try:
            safe = sanitize_record(
                record,
                service=self.service,
                environment=self.environment,
                release=self.release,
                routes=self.routes,
                max_attribute_chars=self._max_attribute_chars,
            )
            encoded = encode_item(safe)
            if len(encoded) > self._max_item_bytes:
                self.metrics.record_drop(self.service, "stdout", "oversize")
                if self._log_lane is not None:
                    self._log_lane.note_drop("oversize")
                return False
        except Exception:
            self.metrics.record_drop(self.service, "stdout", "invalid")
            if self._log_lane is not None:
                self._log_lane.note_drop("invalid")
            return False
        line = encoded.decode("utf-8")
        accepted = self._publisher.submit(line)
        if self._log_lane is not None:
            try:
                attributes = {
                    key: value
                    for key, value in safe.items()
                    if key not in {"timestamp", "service", "environment", "release", "event"}
                }
                event = cast(str, safe["event"])
                self._logger.emit(
                    body=event,
                    event_name=event,
                    attributes=cast(Attributes, attributes),
                )
            except Exception:
                self._log_lane.note_drop("invalid")
        return accepted

    @contextmanager
    def span(self, name: SpanName) -> Iterator[Span]:
        """Start one finite, exception-safe span inside this runtime's provider."""
        if name not in SPAN_NAMES:
            raise ValueError("Invalid span name")
        self._freeze_routes()
        budget = self._span_budget.get()
        token = None
        if budget is None:
            budget = _SpanBudget()
            token = self._span_budget.set(budget)
        budget.count += 1
        try:
            if budget.count > self._max_spans_per_request:
                yield cast(Span, NonRecordingSpan(get_current_span().get_span_context()))
                return
            with self._tracer.start_as_current_span(
                name, record_exception=False, set_status_on_exception=False
            ) as span:
                yield span
        finally:
            if token is not None:
                self._span_budget.reset(token)

    async def close(self, deadline: float) -> None:
        """Shut down providers and lanes within the caller's absolute monotonic deadline."""
        if self._closed:
            return
        self._closed = True
        try:
            self.trace_provider.shutdown()
        except Exception:
            if self._trace_lane is not None:
                self._trace_lane.request_close()
        try:
            self.log_provider.shutdown()
        except Exception:
            if self._log_lane is not None:
                self._log_lane.request_close()
        closers = [self._publisher.close(deadline)]
        if self._log_lane is not None:
            closers.append(self._log_lane.close(deadline))
        if self._trace_lane is not None:
            closers.append(self._trace_lane.close(deadline))
        try:
            await asyncio.gather(*closers, return_exceptions=True)
        except Exception:
            # Cleanup must never alter a completed business operation.
            return


def create_telemetry(settings: Settings, *, service: Service) -> TelemetryRuntime:
    """Create an isolated runtime for one API or worker process."""
    return TelemetryRuntime(settings, service)
