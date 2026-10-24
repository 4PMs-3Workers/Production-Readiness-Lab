"""Immutable, bounded OTLP metric DTOs for one worker observation snapshot."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import TypeAlias

from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Gauge,
    Histogram,
    HistogramDataPoint,
    Metric,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.util.types import Attributes
from prometheus_client.core import Metric as PrometheusMetric

from app.observability.metrics import HISTOGRAM_BUCKETS, TelemetryMetrics

MAX_METRIC_POINT_BYTES = 8192
MAX_METRIC_POINTS_PER_BATCH = 32
MAX_METRIC_POINTS_PER_SNAPSHOT = 256
MAX_METRIC_SNAPSHOT_BYTES = 65536
_SCOPE = InstrumentationScope("app.observability.worker", "0.1.0")
MetricAttributeValue: TypeAlias = str | bool | int | float
_RUNTIME_FAMILIES = frozenset(
    {
        "prl_db_statement_duration_seconds",
        "prl_db_pool_checked_out",
        "prl_db_pool_idle",
        "prl_db_pool_overflow",
        "prl_db_failures",
        "prl_service_errors",
        "prl_telemetry_exported",
        "prl_telemetry_failed",
        "prl_telemetry_dropped",
        "prl_telemetry_queue_capacity",
        "prl_telemetry_queue_size",
    }
)
_RUNTIME_COUNTER_NAMES = {
    "prl_db_failures": "prl_db_failures_total",
    "prl_service_errors": "prl_service_errors_total",
    "prl_telemetry_exported": "prl_telemetry_exported_total",
    "prl_telemetry_failed": "prl_telemetry_failed_total",
    "prl_telemetry_dropped": "prl_telemetry_dropped_total",
}


class MetricSnapshotError(ValueError):
    """A worker metric snapshot exceeded a finite encoding or value bound."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in {"invalid", "oversize"} else "invalid"
        super().__init__(self.reason)


@dataclass(frozen=True)
class WorkerMetricSnapshot:
    """Latest process-local counters and genuine event timestamps."""

    start_time_unix_nano: int
    started_timestamp_seconds: float
    heartbeat_timestamp_seconds: float | None
    last_successful_scan_timestamp_seconds: float | None
    last_progress_timestamp_seconds: float | None
    successful_scans: int
    failed_scans: int
    expired: int
    noops: int
    failed: int
    reporting_failures: int
    selected: int
    database_ok: bool
    phase: str
    scan_duration_seconds: float | None
    backlog_count: int | None
    backlog_capped: bool | None
    oldest_overdue_seconds: float | None
    scan_failure_counts: tuple[tuple[str, int], ...]
    candidate_failure_counts: tuple[tuple[str, int], ...]
    candidate_duration_count: int
    candidate_duration_sum: float
    candidate_duration_min: float | None
    candidate_duration_max: float | None
    candidate_duration_buckets: tuple[int, ...]


def _safe_attributes(values: Mapping[str, MetricAttributeValue]) -> Attributes:
    return MappingProxyType(dict(values))


def _number_metric(
    name: str,
    value: int | float,
    *,
    description: str,
    unit: str,
    attributes: Mapping[str, MetricAttributeValue],
    start_time_unix_nano: int,
    time_unix_nano: int,
    cumulative: bool = False,
) -> Metric:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise MetricSnapshotError("invalid")
    point = NumberDataPoint(
        attributes=_safe_attributes(attributes),
        start_time_unix_nano=start_time_unix_nano,
        time_unix_nano=time_unix_nano,
        value=value,
    )
    data = Sum((point,), AggregationTemporality.CUMULATIVE, True) if cumulative else Gauge((point,))
    return Metric(name, description, unit, data)


def _metric_points(metrics: Iterable[Metric]) -> list[Metric]:
    """Split multi-point metrics into one-point public DTOs for strict batching."""
    values: list[Metric] = []
    for metric in metrics:
        if isinstance(metric.data, Sum):
            points = metric.data.data_points
            for point in points:
                values.append(
                    Metric(
                        metric.name,
                        metric.description,
                        metric.unit,
                        Sum(
                            (point,), metric.data.aggregation_temporality, metric.data.is_monotonic
                        ),
                    )
                )
        elif isinstance(metric.data, Gauge):
            for point in metric.data.data_points:
                values.append(Metric(metric.name, metric.description, metric.unit, Gauge((point,))))
        elif isinstance(metric.data, Histogram):
            for point in metric.data.data_points:
                values.append(
                    Metric(
                        metric.name,
                        metric.description,
                        metric.unit,
                        Histogram((point,), metric.data.aggregation_temporality),
                    )
                )
        else:
            raise MetricSnapshotError("invalid")
    return values


def _runtime_metrics(
    telemetry_metrics: TelemetryMetrics, *, slot: int, start_ns: int, time_ns: int
) -> list[Metric]:
    families: dict[str, PrometheusMetric] = {}
    try:
        for family in telemetry_metrics.registry.collect():
            if family.name in _RUNTIME_FAMILIES:
                families[family.name] = family
    except Exception:
        raise MetricSnapshotError("invalid") from None

    metrics: list[Metric] = []
    min_max = telemetry_metrics.db_statement_min_max
    for name, family in families.items():
        samples = family.samples
        if family.type == "gauge":
            for sample in samples:
                labels = dict(sample.labels)
                if "service" in labels and labels["service"] != "worker":
                    continue
                attrs = {**labels, "worker.slot": slot}
                metric_name = sample.name
                metrics.append(
                    _number_metric(
                        metric_name,
                        sample.value,
                        description=family.documentation,
                        unit="1",
                        attributes=attrs,
                        start_time_unix_nano=start_ns,
                        time_unix_nano=time_ns,
                    )
                )
        elif family.type == "counter":
            for sample in samples:
                expected_name = _RUNTIME_COUNTER_NAMES.get(name)
                if sample.name != expected_name:
                    continue
                labels = dict(sample.labels)
                if labels.get("service", "worker") != "worker":
                    continue
                attrs = {**labels, "worker.slot": slot}
                value = int(sample.value) if float(sample.value).is_integer() else sample.value
                metrics.append(
                    _number_metric(
                        sample.name,
                        value,
                        description=family.documentation,
                        unit="1",
                        attributes=attrs,
                        start_time_unix_nano=start_ns,
                        time_unix_nano=time_ns,
                        cumulative=True,
                    )
                )
        elif family.type == "histogram" and name == "prl_db_statement_duration_seconds":
            groups: dict[tuple[tuple[str, str], ...], dict[str, float]] = defaultdict(dict)
            for sample in samples:
                labels = dict(sample.labels)
                operation = labels.pop("le", None)
                key = tuple(sorted(labels.items()))
                if "service" in labels and labels["service"] != "worker":
                    continue
                group = groups[key]
                if operation is not None:
                    group[operation] = sample.value
                elif sample.name.endswith("_count"):
                    group["count"] = sample.value
                elif sample.name.endswith("_sum"):
                    group["sum"] = sample.value
            for labels, values in groups.items():
                if not values:
                    continue
                finite_bounds = sorted(
                    (float(bound), bound)
                    for bound in values
                    if bound not in {"count", "sum", "+Inf"}
                )
                explicit_bounds = tuple(bound for bound, _ in finite_bounds)
                cumulative_counts = [int(values.get(label, 0)) for _, label in finite_bounds]
                total = int(values.get("+Inf", values.get("count", 0)))
                prior = 0
                bucket_counts = []
                for count in cumulative_counts:
                    bucket_counts.append(max(0, count - prior))
                    prior = count
                bucket_counts.append(max(0, total - prior))
                count = int(values.get("count", total))
                sample_sum = float(values.get("sum", 0.0))
                label_values = dict(labels)
                operation = label_values.get("operation", "OTHER")
                minimum, maximum = min_max.get(operation, (0.0, max(0.0, sample_sum)))
                point = HistogramDataPoint(
                    attributes=_safe_attributes({**label_values, "worker.slot": slot}),
                    start_time_unix_nano=start_ns,
                    time_unix_nano=time_ns,
                    count=count,
                    sum=sample_sum,
                    bucket_counts=tuple(bucket_counts),
                    explicit_bounds=explicit_bounds,
                    min=minimum,
                    max=maximum,
                )
                metrics.append(
                    Metric(
                        name,
                        family.documentation,
                        "s",
                        Histogram((point,), AggregationTemporality.CUMULATIVE),
                    )
                )
    return metrics


def build_metric_batches(
    snapshot: WorkerMetricSnapshot,
    *,
    service: str,
    environment: str,
    slot: int,
    telemetry_metrics: TelemetryMetrics,
    time_unix_nano: int,
    max_metric_point_bytes: int = MAX_METRIC_POINT_BYTES,
    max_metric_points_per_batch: int = MAX_METRIC_POINTS_PER_BATCH,
) -> tuple[MetricsData, ...]:
    """Build validated <=32-point OTLP batches from one immutable latest snapshot."""
    if (
        service != "worker"
        or not 1 <= slot <= 8
        or not 1 <= max_metric_point_bytes <= MAX_METRIC_POINT_BYTES
        or not 1 <= max_metric_points_per_batch <= MAX_METRIC_POINTS_PER_BATCH
    ):
        raise MetricSnapshotError("invalid")
    if time_unix_nano <= 0 or snapshot.start_time_unix_nano <= 0:
        raise MetricSnapshotError("invalid")
    slot_attributes = {"worker.slot": slot}
    base = slot_attributes
    start_ns = snapshot.start_time_unix_nano
    metrics: list[Metric] = []

    def gauge(
        name: str,
        value: int | float | None,
        description: str,
        unit: str = "1",
        extra: Mapping[str, MetricAttributeValue] | None = None,
    ) -> None:
        if value is None:
            return
        attrs = {**base, **(extra or {})}
        metrics.append(
            _number_metric(
                name,
                value,
                description=description,
                unit=unit,
                attributes=attrs,
                start_time_unix_nano=start_ns,
                time_unix_nano=time_unix_nano,
            )
        )

    def counter(name: str, value: int, description: str) -> None:
        metrics.append(
            _number_metric(
                name,
                value,
                description=description,
                unit="1",
                attributes=base,
                start_time_unix_nano=start_ns,
                time_unix_nano=time_unix_nano,
                cumulative=True,
            )
        )

    gauge(
        "prl_worker_started_timestamp_seconds",
        snapshot.started_timestamp_seconds,
        "Worker process startup time.",
        "s",
    )
    gauge(
        "prl_worker_heartbeat_timestamp_seconds",
        snapshot.heartbeat_timestamp_seconds,
        "Timestamp of the most recent real worker heartbeat event.",
        "s",
    )
    gauge(
        "prl_worker_last_successful_scan_timestamp_seconds",
        snapshot.last_successful_scan_timestamp_seconds,
        "Database timestamp of the most recent successful discovery scan.",
        "s",
    )
    gauge(
        "prl_worker_last_progress_timestamp_seconds",
        snapshot.last_progress_timestamp_seconds,
        "Timestamp of the most recent successful scan or candidate transition.",
        "s",
    )
    gauge(
        "prl_worker_database_ok",
        int(snapshot.database_ok),
        "Whether the last worker operation was database healthy.",
    )
    gauge(
        "prl_worker_selected_candidates",
        snapshot.selected,
        "Candidates selected in the latest scan.",
    )
    gauge("prl_worker_phase", 1, "Current worker lifecycle phase.", extra={"phase": snapshot.phase})
    gauge(
        "prl_worker_last_scan_duration_seconds",
        snapshot.scan_duration_seconds,
        "Duration of the most recent completed scan.",
        "s",
    )
    gauge("prl_worker_backlog_count", snapshot.backlog_count, "Observed due backlog lower bound.")
    gauge(
        "prl_worker_backlog_capped",
        int(snapshot.backlog_capped) if snapshot.backlog_capped is not None else None,
        "Whether the observed backlog reached its configured cap.",
    )
    gauge(
        "prl_worker_oldest_overdue_seconds",
        snapshot.oldest_overdue_seconds,
        "Oldest overdue age from the latest successful discovery.",
        "s",
    )

    counters = (
        (
            "prl_worker_successful_scans_total",
            snapshot.successful_scans,
            "Successful worker discovery scans.",
        ),
        ("prl_worker_failed_scans_total", snapshot.failed_scans, "Failed worker discovery scans."),
        ("prl_worker_candidates_expired_total", snapshot.expired, "Candidates durably expired."),
        (
            "prl_worker_candidates_noop_total",
            snapshot.noops,
            "Candidate transitions that completed as no-ops.",
        ),
        (
            "prl_worker_candidates_failed_total",
            snapshot.failed,
            "Candidate processing failures, including uncertain commits.",
        ),
        (
            "prl_worker_reporting_failures_total",
            snapshot.reporting_failures,
            "Worker stdout reporting failures observed by Reporter.",
        ),
    )
    for name, value, description in counters:
        counter(name, value, description)

    def reason_counter(name: str, values: tuple[tuple[str, int], ...], description: str) -> None:
        points = tuple(
            NumberDataPoint(
                attributes=_safe_attributes({**base, "reason": reason}),
                start_time_unix_nano=start_ns,
                time_unix_nano=time_unix_nano,
                value=count,
            )
            for reason, count in values
            if count > 0
        )
        if points:
            metrics.append(
                Metric(name, description, "1", Sum(points, AggregationTemporality.CUMULATIVE, True))
            )

    reason_counter(
        "prl_worker_scan_failures_total",
        snapshot.scan_failure_counts,
        "Worker discovery failures by finite reason.",
    )
    reason_counter(
        "prl_worker_candidate_failures_by_reason_total",
        snapshot.candidate_failure_counts,
        "Worker candidate failures by finite reason.",
    )

    if snapshot.candidate_duration_count:
        if (
            snapshot.candidate_duration_min is None
            or snapshot.candidate_duration_max is None
            or len(snapshot.candidate_duration_buckets) != len(HISTOGRAM_BUCKETS) + 1
        ):
            raise MetricSnapshotError("invalid")
        point = HistogramDataPoint(
            attributes=_safe_attributes(base),
            start_time_unix_nano=start_ns,
            time_unix_nano=time_unix_nano,
            count=snapshot.candidate_duration_count,
            sum=snapshot.candidate_duration_sum,
            bucket_counts=snapshot.candidate_duration_buckets,
            explicit_bounds=HISTOGRAM_BUCKETS,
            min=snapshot.candidate_duration_min,
            max=snapshot.candidate_duration_max,
        )
        metrics.append(
            Metric(
                "prl_worker_candidate_duration_seconds",
                "Duration of each new real candidate event.",
                "s",
                Histogram((point,), AggregationTemporality.CUMULATIVE),
            )
        )

    metrics.extend(
        _runtime_metrics(
            telemetry_metrics,
            slot=slot,
            start_ns=start_ns,
            time_ns=time_unix_nano,
        )
    )
    points = _metric_points(metrics)
    if len(points) > MAX_METRIC_POINTS_PER_SNAPSHOT:
        raise MetricSnapshotError("oversize")

    batches: list[MetricsData] = []
    resource = Resource(
        {
            "service.name": service,
            "deployment.environment.name": environment,
        }
    )
    for offset in range(0, len(points), max_metric_points_per_batch):
        batch_points = points[offset : offset + max_metric_points_per_batch]
        if any(
            _encoded_size(resource, (metric,)) > max_metric_point_bytes for metric in batch_points
        ):
            raise MetricSnapshotError("oversize")
        scope_metrics = ScopeMetrics(_SCOPE, tuple(batch_points), "")
        resource_metrics = ResourceMetrics(resource, (scope_metrics,), "")
        data = MetricsData((resource_metrics,))
        batches.append(data)
    total_size = sum(encode_metrics(batch).ByteSize() for batch in batches)
    if total_size > MAX_METRIC_SNAPSHOT_BYTES:
        raise MetricSnapshotError("oversize")
    return tuple(batches)


def _encoded_size(resource: Resource, points: tuple[Metric, ...]) -> int:
    """Return the public OTLP protobuf size including resource/scope framing."""
    scope_metrics = ScopeMetrics(_SCOPE, points, "")
    resource_metrics = ResourceMetrics(resource, (scope_metrics,), "")
    return encode_metrics(MetricsData((resource_metrics,))).ByteSize()
