"""Bridge real worker events into safe logs and cumulative OTLP metrics."""

import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from math import isfinite

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

from app.core.config import Settings
from app.observability.metric_exporting import LatestMetricExportLane
from app.observability.metrics import HISTOGRAM_BUCKETS
from app.observability.runtime import TelemetryRuntime
from app.observability.transport import SanitizingOTLPSession
from app.observability.worker_metrics import (
    WorkerMetricSnapshot,
    build_metric_batches,
)
from app.worker.models import CandidateResult, FailureReason, Outcome, WorkerState

WORKER_METRIC_EXPORT_INTERVAL_SECONDS = 5.0
_EVENTS = frozenset(
    {"starting", "ready", "heartbeat", "scan", "candidate", "stopping", "stopped", "fatal"}
)
_COUNTERS = (
    "successful_scans",
    "failed_scans",
    "expired",
    "noops",
    "failed",
    "reporting_failures",
)
_MAX_COUNTER = 2**63 - 1


def _timestamp(value: datetime | None) -> float | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    result = value.astimezone(UTC).timestamp()
    return result if isfinite(result) and result >= 0 else None


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_COUNTER


class WorkerObserver:
    """Observe existing Reporter events without changing worker business state."""

    def __init__(
        self,
        settings: Settings,
        telemetry: TelemetryRuntime,
        *,
        exporter: object | None = None,
        export_interval_seconds: float = WORKER_METRIC_EXPORT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.slot = settings.worker_replica_slot
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._start_time_unix_nano = time.time_ns()
        self._last_snapshot_unix_nano = self._start_time_unix_nano
        self._last_counters = {name: 0 for name in _COUNTERS}
        self._heartbeat_timestamp: float | None = None
        self._last_successful_scan_timestamp: float | None = None
        self._last_progress_timestamp: float | None = None
        self._scan_duration_seconds: float | None = None
        self._backlog_count: int | None = None
        self._backlog_capped: bool | None = None
        self._oldest_overdue_seconds: float | None = None
        self._phase = "starting"
        self._database_ok = False
        self._selected = 0
        self._scan_failure_counts: Counter[str] = Counter()
        self._candidate_failure_counts: Counter[str] = Counter()
        self._candidate_duration_buckets = [0] * (len(HISTOGRAM_BUCKETS) + 1)
        self._candidate_duration_count = 0
        self._candidate_duration_sum = 0.0
        self._candidate_duration_min: float | None = None
        self._candidate_duration_max: float | None = None
        self._closed = False
        self._lane: LatestMetricExportLane | None = None

        if exporter is None and settings.telemetry_enabled:
            try:
                exporter = OTLPMetricExporter(
                    endpoint=f"{settings.telemetry_otlp_endpoint}/v1/metrics",
                    timeout=settings.telemetry_network_timeout_seconds,
                    session=SanitizingOTLPSession(),
                )
            except Exception:
                self._record_drop("invalid")
        if exporter is not None:
            try:
                telemetry.metrics.configure_queue("worker", "metrics", 1)
                self._lane = LatestMetricExportLane(
                    self._build_latest_batches,
                    exporter,
                    interval_seconds=export_interval_seconds,
                    max_age_seconds=settings.telemetry_item_max_age_seconds,
                    network_timeout_seconds=settings.telemetry_network_timeout_seconds,
                    on_exported=lambda amount: telemetry.metrics.record_exported(
                        "worker", "metrics", amount
                    ),
                    on_failed=lambda amount: telemetry.metrics.record_export_failed(
                        "worker", "metrics", amount
                    ),
                    on_drop=lambda reason: telemetry.metrics.record_drop(
                        "worker", "metrics", reason
                    ),
                    on_queue_change=lambda size: telemetry.metrics.set_queue_size(
                        "worker", "metrics", size
                    ),
                )
                self._lane.note_snapshot()
            except Exception:
                self._lane = None
                self._record_drop("invalid")

    def _record_drop(self, reason: str) -> None:
        try:
            self.telemetry.metrics.record_drop("worker", "metrics", reason)
        except Exception:
            pass

    def _build_latest_batches(self):
        with self._lock:
            # Coarse wall clocks may return the same tick for the final snapshot.
            # Preserve ordering so an older cumulative point cannot win a tie.
            collected_at = max(time.time_ns(), self._last_snapshot_unix_nano + 1)
            self._last_snapshot_unix_nano = collected_at
            snapshot = WorkerMetricSnapshot(
                start_time_unix_nano=self._start_time_unix_nano,
                started_timestamp_seconds=self._start_time_unix_nano / 1_000_000_000,
                heartbeat_timestamp_seconds=self._heartbeat_timestamp,
                last_successful_scan_timestamp_seconds=self._last_successful_scan_timestamp,
                last_progress_timestamp_seconds=self._last_progress_timestamp,
                successful_scans=self._last_counters["successful_scans"],
                failed_scans=self._last_counters["failed_scans"],
                expired=self._last_counters["expired"],
                noops=self._last_counters["noops"],
                failed=self._last_counters["failed"],
                reporting_failures=self._last_counters["reporting_failures"],
                selected=self._selected,
                database_ok=self._database_ok,
                phase=self._phase,
                scan_duration_seconds=self._scan_duration_seconds,
                backlog_count=self._backlog_count,
                backlog_capped=self._backlog_capped,
                oldest_overdue_seconds=self._oldest_overdue_seconds,
                scan_failure_counts=tuple(sorted(self._scan_failure_counts.items())),
                candidate_failure_counts=tuple(sorted(self._candidate_failure_counts.items())),
                candidate_duration_count=self._candidate_duration_count,
                candidate_duration_sum=self._candidate_duration_sum,
                candidate_duration_min=self._candidate_duration_min,
                candidate_duration_max=self._candidate_duration_max,
                candidate_duration_buckets=tuple(self._candidate_duration_buckets),
            )
        return build_metric_batches(
            snapshot,
            service="worker",
            environment=self.settings.environment,
            slot=self.slot,
            telemetry_metrics=self.telemetry.metrics,
            time_unix_nano=collected_at,
            max_metric_point_bytes=self.settings.telemetry_max_item_bytes,
            max_metric_points_per_batch=self.settings.telemetry_max_batch_size,
        )

    def _metric_event_time(self) -> float | None:
        try:
            return _timestamp(self._clock())
        except Exception:
            return None

    def _candidate_duration(self, seconds: float) -> bool:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return False
        value = float(seconds)
        if not isfinite(value) or value < 0:
            return False
        bucket = next(
            (index for index, bound in enumerate(HISTOGRAM_BUCKETS) if value <= bound),
            len(HISTOGRAM_BUCKETS),
        )
        self._candidate_duration_buckets[bucket] += 1
        self._candidate_duration_count += 1
        self._candidate_duration_sum += value
        self._candidate_duration_min = (
            value
            if self._candidate_duration_min is None
            else min(self._candidate_duration_min, value)
        )
        self._candidate_duration_max = (
            value
            if self._candidate_duration_max is None
            else max(self._candidate_duration_max, value)
        )
        return True

    def _log_record(
        self,
        event: str,
        state: WorkerState,
        *,
        reason: FailureReason | None,
        result: CandidateResult | None,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "event": f"worker.{event}",
            "worker_id": state.worker_id,
            "phase": state.phase,
            "database_ok": state.database_ok,
            "successful_scans": state.successful_scans,
            "failed_scans": state.failed_scans,
            "expired": state.expired,
            "noops": state.noops,
            "failed": state.failed,
            "selected": state.selected,
            "batch_expired": state.batch_expired,
            "batch_noops": state.batch_noops,
            "batch_failed": state.batch_failed,
            "reporting_failures": state.reporting_failures,
            "backlog_capped": state.backlog_capped,
        }
        if state.current_run_id is not None:
            record["current_run_id"] = state.current_run_id
        if state.last_successful_scan is not None:
            record["last_successful_scan"] = state.last_successful_scan
        if state.last_scan_duration_seconds is not None:
            record["last_scan_duration_seconds"] = state.last_scan_duration_seconds
        if state.backlog_count is not None:
            record["backlog_count"] = state.backlog_count
        if state.oldest_overdue_seconds is not None:
            record["oldest_overdue_seconds"] = state.oldest_overdue_seconds
        selected_reason = result.reason if result is not None else reason
        if selected_reason is not None:
            record["error_code"] = selected_reason.value
        if result is not None:
            record["outcome"] = result.outcome.value
            record["attempts"] = result.attempts
            record["duration_seconds"] = result.duration_seconds
            record["request_id"] = result.request_id
        return record

    def observe(
        self,
        event: str,
        state: WorkerState,
        *,
        reason: FailureReason | None = None,
        result: CandidateResult | None = None,
    ) -> None:
        """Accept one existing worker event; instrumentation errors never escape."""
        if event not in _EVENTS:
            self._record_drop("invalid")
            return
        lane_to_notify: LatestMetricExportLane | None = None
        invalid_drops = 0
        event_time = self._metric_event_time()
        try:
            with self._lock:
                if self._closed:
                    return
                current = {name: getattr(state, name) for name in _COUNTERS}
                if not all(_count(value) for value in current.values()) or any(
                    current[name] < self._last_counters[name] for name in _COUNTERS
                ):
                    invalid_drops += 1
                    valid = False
                else:
                    valid = True

                scan_advanced = (
                    valid and current["successful_scans"] > self._last_counters["successful_scans"]
                )
                if scan_advanced and _timestamp(state.last_successful_scan) is None:
                    invalid_drops += 1
                    valid = False

                if valid:
                    previous = self._last_counters.copy()
                    candidate_advanced = (
                        event == "candidate"
                        and current["expired"] + current["noops"] + current["failed"]
                        > previous["expired"] + previous["noops"] + previous["failed"]
                    )
                    if event == "heartbeat" and event_time is not None:
                        self._heartbeat_timestamp = event_time
                    if scan_advanced:
                        self._last_successful_scan_timestamp = _timestamp(
                            state.last_successful_scan
                        )
                        self._last_progress_timestamp = self._last_successful_scan_timestamp
                        self._backlog_count = state.backlog_count
                        self._backlog_capped = (
                            state.backlog_capped if state.backlog_count is not None else None
                        )
                        self._oldest_overdue_seconds = state.oldest_overdue_seconds
                    if event == "scan":
                        value = state.last_scan_duration_seconds
                        if (
                            value is not None
                            and not isinstance(value, bool)
                            and isinstance(value, (int, float))
                            and isfinite(value)
                            and value >= 0
                        ):
                            self._scan_duration_seconds = float(value)
                    if (
                        event == "candidate"
                        and candidate_advanced
                        and isinstance(result, CandidateResult)
                    ):
                        if not self._candidate_duration(result.duration_seconds):
                            invalid_drops += 1
                        result_reason = result.reason
                        if result.outcome == Outcome.FAILED and isinstance(
                            result_reason, FailureReason
                        ):
                            self._candidate_failure_counts[result_reason.value] += 1
                        if (
                            result.outcome in {Outcome.EXPIRED, Outcome.NOOP}
                            and event_time is not None
                        ):
                            self._last_progress_timestamp = event_time
                    if event == "scan" and current["failed_scans"] > previous["failed_scans"]:
                        if isinstance(reason, FailureReason):
                            self._scan_failure_counts[reason.value] += 1
                    self._last_counters = current
                    self._phase = state.phase
                    self._database_ok = bool(state.database_ok)
                    self._selected = state.selected if _count(state.selected) else 0
                    lane_to_notify = self._lane
            for _ in range(invalid_drops):
                self._record_drop("invalid")
            if lane_to_notify is not None:
                lane_to_notify.note_snapshot()
        except Exception:
            # A failed observation is counted locally and cannot escape into worker behavior.
            self._record_drop("invalid")

        try:
            self.telemetry.emit(self._log_record(event, state, reason=reason, result=result))
        except Exception:
            pass

    async def close(self, deadline: float) -> None:
        """Stop the metric lane within the owner's already-capped deadline."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._lane is not None:
            try:
                await self._lane.close(deadline)
            except Exception:
                pass
