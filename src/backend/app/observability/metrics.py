"""Process-owned Prometheus metrics with finite label vocabularies."""

from collections.abc import Callable
from math import isfinite

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from app.observability.records import METHODS, UNMATCHED_ROUTE, RouteAllowlist

SERVICES = frozenset({"api", "worker"})
SIGNALS = frozenset({"stdout", "logs", "traces", "metrics"})
DROP_REASONS = frozenset(
    {"queue_full", "oversize", "expired", "export_failed", "shutdown", "invalid"}
)
FAILURE_REASONS = frozenset(
    {"pool_timeout", "connection_error", "statement_error", "transaction_error", "unknown"}
)
HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5)


class TelemetryMetrics:
    """An isolated registry; creating one never changes Prometheus globals."""

    def __init__(
        self,
        routes: RouteAllowlist | None = None,
        *,
        on_http_request: Callable[[], object] | None = None,
    ) -> None:
        self._routes = routes or RouteAllowlist()
        self._on_http_request = on_http_request
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "prl_http_requests_total",
            "HTTP requests by normalized route, finite method, and status.",
            ("route", "method", "status"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "prl_http_request_duration_seconds",
            "HTTP request duration in seconds.",
            ("route", "method"),
            buckets=HISTOGRAM_BUCKETS,
            registry=self.registry,
        )
        self.service_errors = Counter(
            "prl_service_errors_total",
            "Service errors by service and finite reason.",
            ("service", "reason"),
            registry=self.registry,
        )
        self.db_statement_duration = Histogram(
            "prl_db_statement_duration_seconds",
            "Database statement duration in seconds.",
            ("operation",),
            buckets=HISTOGRAM_BUCKETS,
            registry=self.registry,
        )
        self.db_pool_checked_out = Gauge(
            "prl_db_pool_checked_out", "Checked out database connections.", registry=self.registry
        )
        self.db_pool_idle = Gauge(
            "prl_db_pool_idle", "Idle database connections.", registry=self.registry
        )
        self.db_pool_overflow = Gauge(
            "prl_db_pool_overflow", "Overflow database connections.", registry=self.registry
        )
        self.db_failures = Counter(
            "prl_db_failures_total",
            "Database failures by finite reason.",
            ("reason",),
            registry=self.registry,
        )
        self.telemetry_exported = Counter(
            "prl_telemetry_exported_total",
            "Telemetry items successfully exported.",
            ("service", "signal"),
            registry=self.registry,
        )
        self.telemetry_failed = Counter(
            "prl_telemetry_failed_total",
            "Telemetry items that failed during export.",
            ("service", "signal"),
            registry=self.registry,
        )
        self.telemetry_dropped = Counter(
            "prl_telemetry_dropped_total",
            "Telemetry items dropped before export by finite reason.",
            ("service", "signal", "reason"),
            registry=self.registry,
        )
        self.telemetry_queue_capacity = Gauge(
            "prl_telemetry_queue_capacity",
            "Telemetry queue capacity by owner and signal.",
            ("service", "signal"),
            registry=self.registry,
        )
        self.telemetry_queue_size = Gauge(
            "prl_telemetry_queue_size",
            "Current telemetry queue occupancy by owner and signal.",
            ("service", "signal"),
            registry=self.registry,
        )

    def record_http_request(
        self,
        route: str,
        method: str,
        status: int,
        duration_seconds: float,
        *,
        response_complete: bool = True,
    ) -> None:
        if self._on_http_request is not None:
            try:
                self._on_http_request()
            except Exception:
                pass
        safe_route = (
            route if isinstance(route, str) and self._routes.allows(route) else UNMATCHED_ROUTE
        )
        safe_method = method if isinstance(method, str) and method in METHODS else "OTHER"
        safe_status = status if isinstance(status, int) and not isinstance(status, bool) else 500
        if not 100 <= safe_status <= 599:
            safe_status = 500
        try:
            duration = float(duration_seconds)
        except (TypeError, ValueError, OverflowError):
            return
        if not isfinite(duration) or duration < 0:
            return
        labels = (safe_route, safe_method)
        status_label = str(safe_status) if response_complete else "aborted"
        self.http_requests.labels(*labels, status_label).inc()
        self.http_duration.labels(*labels).observe(duration)

    def record_service_error(self, service: str, reason: str) -> None:
        safe_service = service if isinstance(service, str) and service in SERVICES else "api"
        safe_reason = reason if isinstance(reason, str) and reason in FAILURE_REASONS else "unknown"
        self.service_errors.labels(safe_service, safe_reason).inc()

    def record_db_statement(self, operation: str, duration_seconds: float) -> None:
        safe_operation = (
            operation
            if isinstance(operation, str) and operation in {"SELECT", "INSERT", "UPDATE", "DELETE"}
            else "OTHER"
        )
        try:
            duration = float(duration_seconds)
        except (TypeError, ValueError, OverflowError):
            return
        if not isfinite(duration) or duration < 0:
            return
        self.db_statement_duration.labels(safe_operation).observe(duration)

    def record_db_failure(self, reason: str) -> None:
        safe_reason = reason if isinstance(reason, str) and reason in FAILURE_REASONS else "unknown"
        self.db_failures.labels(safe_reason).inc()

    def configure_queue(self, service: str, signal: str, capacity: int) -> None:
        labels = self._queue_labels(service, signal)
        self.telemetry_queue_capacity.labels(*labels).set(capacity)

    def set_queue_size(self, service: str, signal: str, size: int) -> None:
        self.telemetry_queue_size.labels(*self._queue_labels(service, signal)).set(size)

    def record_exported(self, service: str, signal: str, amount: int = 1) -> None:
        self.telemetry_exported.labels(*self._queue_labels(service, signal)).inc(amount)

    def record_export_failed(self, service: str, signal: str, amount: int = 1) -> None:
        self.telemetry_failed.labels(*self._queue_labels(service, signal)).inc(amount)

    def record_drop(self, service: str, signal: str, reason: str, amount: int = 1) -> None:
        safe_reason = reason if reason in DROP_REASONS else "invalid"
        self.telemetry_dropped.labels(*self._queue_labels(service, signal), safe_reason).inc(amount)

    @staticmethod
    def _queue_labels(service: str, signal: str) -> tuple[str, str]:
        return (
            service if service in SERVICES else "api",
            signal if signal in SIGNALS else "stdout",
        )

    def render(self) -> str:
        return generate_latest(self.registry).decode("utf-8")
