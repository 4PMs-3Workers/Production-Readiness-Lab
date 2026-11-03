"""Read-only Prometheus exporter for bounded backup monitoring state."""

import os
import re
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from app.backup.monitoring import BackupMonitorState, read_state

PORT = 9108
MAX_THRESHOLD_SECONDS = 365 * 24 * 60 * 60
LABEL = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


@dataclass(frozen=True)
class MonitorConfig:
    environment: str
    store: str
    rpo_seconds: int
    schedule_interval_seconds: int
    job_runtime_seconds: int
    job_grace_seconds: int
    stale_seconds: int


def _seconds(values: Mapping[str, str], key: str, *, allow_zero: bool = False) -> int:
    value = values.get(key, "")
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise ValueError()
    seconds = int(value)
    if seconds > MAX_THRESHOLD_SECONDS or (seconds < 0 if allow_zero else seconds <= 0):
        raise ValueError()
    return seconds


def parse_config(values: Mapping[str, str]) -> MonitorConfig | None:
    try:
        environment, store = values.get("BACKUP_MONITOR_ENVIRONMENT", ""), values.get(
            "BACKUP_MONITOR_STORE", ""
        )
        if not LABEL.fullmatch(environment) or not LABEL.fullmatch(store):
            raise ValueError()
        result = MonitorConfig(
            environment=environment,
            store=store,
            rpo_seconds=_seconds(values, "BACKUP_MONITOR_RPO_SECONDS"),
            schedule_interval_seconds=_seconds(values, "BACKUP_MONITOR_SCHEDULE_INTERVAL_SECONDS"),
            job_runtime_seconds=_seconds(values, "BACKUP_MONITOR_JOB_RUNTIME_SECONDS"),
            job_grace_seconds=_seconds(values, "BACKUP_MONITOR_JOB_GRACE_SECONDS", allow_zero=True),
            stale_seconds=_seconds(values, "BACKUP_MONITOR_STALE_SECONDS"),
        )
        if (
            result.job_runtime_seconds + result.job_grace_seconds
            >= result.schedule_interval_seconds
            or result.rpo_seconds
            < result.schedule_interval_seconds
            + result.job_runtime_seconds
            + result.job_grace_seconds
        ):
            raise ValueError()
        return result
    except (TypeError, ValueError, OverflowError):
        return None


class BackupMetricsExporter:
    def __init__(self, values: Mapping[str, str], state_path: Path):
        self.config = parse_config(values)
        self.state_path = state_path
        self.last_successful_state_read_timestamp: float | None = None

    @staticmethod
    def _gauge(registry, name, help_text, labels=()):
        return Gauge(name, help_text, labels, registry=registry)

    def render(self, *, now: float | None = None) -> str:
        registry = CollectorRegistry()
        config_valid = self.config is not None
        self._gauge(
            registry,
            "prl_backup_monitor_config_valid",
            "Whether all required backup monitoring thresholds are valid.",
        ).set(1 if config_valid else 0)
        state = read_state(self.state_path, max_bytes=65_536)
        if state is not None:
            self.last_successful_state_read_timestamp = time.time() if now is None else now
        config = self.config
        labels = ("environment", "store")
        label_values = (config.environment, config.store) if config is not None else ()
        state_valid = self._gauge(
            registry,
            "prl_backup_monitor_state_valid",
            "Whether the versioned backup monitoring snapshot was read and validated.",
            labels if config is not None else (),
        )
        if config is not None:
            state_valid.labels(*label_values).set(1 if state is not None else 0)
        else:
            state_valid.set(1 if state is not None else 0)

        if self.last_successful_state_read_timestamp is not None:
            last_read = self._gauge(
                registry,
                "prl_backup_monitor_last_successful_state_read_timestamp_seconds",
                "Unix timestamp of the last valid state read by this exporter process.",
                labels if config is not None else (),
            )
            if config is not None:
                last_read.labels(*label_values).set(self.last_successful_state_read_timestamp)
            else:
                last_read.set(self.last_successful_state_read_timestamp)

        if config is not None:
            for metric, value in (
                ("prl_backup_monitor_rpo_seconds", config.rpo_seconds),
                ("prl_backup_monitor_schedule_interval_seconds", config.schedule_interval_seconds),
                ("prl_backup_monitor_job_runtime_seconds", config.job_runtime_seconds),
                ("prl_backup_monitor_job_grace_seconds", config.job_grace_seconds),
                ("prl_backup_monitor_stale_seconds", config.stale_seconds),
            ):
                self._gauge(
                    registry, metric, "Configured backup monitoring threshold in seconds.", labels
                ).labels(*label_values).set(value)

        if state is not None and config is not None:
            self._export_state(registry, state, config)
        return generate_latest(registry).decode("utf-8")

    def _export_state(self, registry, state: BackupMonitorState, config: MonitorConfig) -> None:
        label_values = (config.environment, config.store)
        labels = ("environment", "store")
        gauges = {}

        def set_value(name, help_text, value, extra_labels=(), extra_values=()):
            label_names = (*labels, *extra_labels)
            gauge = gauges.get(name)
            if gauge is None:
                gauge = self._gauge(registry, name, help_text, label_names)
                gauges[name] = gauge
            gauge.labels(*label_values, *extra_values).set(value)

        if state.monitor_initialized_at is not None:
            set_value(
                "prl_backup_monitor_initialized_timestamp_seconds",
                "Unix timestamp when the backup monitor state was initialized.",
                state.monitor_initialized_at.timestamp(),
            )

        if state.last_verified_backup_at is not None:
            set_value(
                "prl_backup_last_verified_timestamp_seconds",
                "Unix timestamp of the latest integrity-verified backup artifact.",
                state.last_verified_backup_at.timestamp(),
            )
        if state.artifact_integrity_verified is not None:
            set_value(
                "prl_backup_artifact_integrity_verified",
                "Whether a valid backup artifact was integrity verified.",
                int(state.artifact_integrity_verified),
            )
        set_value(
            "prl_backup_application_rehearsal_verified",
            "Whether an application recovery rehearsal was verified.",
            int(state.application_rehearsal_verified),
        )
        if state.last_job_started_at is not None:
            set_value(
                "prl_backup_job_last_started_timestamp_seconds",
                "Unix timestamp when the latest backup operation started.",
                state.last_job_started_at.timestamp(),
            )
        if state.last_scheduled_job_started_at is not None:
            set_value(
                "prl_backup_scheduled_job_last_started_timestamp_seconds",
                "Unix timestamp when the latest scheduled backup operation started.",
                state.last_scheduled_job_started_at.timestamp(),
            )
        if state.last_scheduled_job_finished_at is not None:
            set_value(
                "prl_backup_scheduled_job_last_finished_timestamp_seconds",
                "Unix timestamp when the latest scheduled backup operation finished.",
                state.last_scheduled_job_finished_at.timestamp(),
            )
        if state.last_job_finished_at is not None:
            set_value(
                "prl_backup_job_last_finished_timestamp_seconds",
                "Unix timestamp when the latest backup operation finished.",
                state.last_job_finished_at.timestamp(),
            )
        if state.last_job_result is not None and state.last_job_operation is not None:
            set_value(
                "prl_backup_job_last_success",
                "Whether the latest backup operation completed successfully.",
                int(state.last_job_result == "success"),
                ("operation",),
                (state.last_job_operation,),
            )
        set_value(
            "prl_backup_scheduled_job_failure_pending",
            "Whether a scheduled backup failure remains unresolved.",
            int(state.scheduled_job_failure_pending),
        )
        if state.last_job_duration_seconds is not None:
            set_value(
                "prl_backup_job_last_duration_seconds",
                "Duration of the latest backup operation in seconds.",
                state.last_job_duration_seconds,
            )
        if state.last_job_reason is not None:
            set_value(
                "prl_backup_job_last_reason",
                "One-hot finite reason for the latest failed backup operation.",
                1,
                ("reason",),
                (state.last_job_reason.value,),
            )
        if state.last_job_reason is None and state.last_job_result == "success":
            set_value(
                "prl_backup_job_last_reason",
                "One-hot finite reason for the latest failed backup operation.",
                1,
                ("reason",),
                ("none",),
            )
        remote_statuses = ("not_configured", "unknown", "pending", "success", "failure")
        remote = state.remote_copy.status if state.remote_copy is not None else "unknown"
        for status in remote_statuses:
            set_value(
                "prl_backup_remote_copy_state",
                "One-hot finite remote-copy verification state.",
                int(status == remote),
                ("state",),
                (status,),
            )
        try:
            capacity = shutil.disk_usage(self.state_path.parent)
        except OSError:
            capacity = None
        if capacity is not None:
            set_value(
                "prl_backup_store_total_bytes",
                "Total bytes of the backup store filesystem.",
                capacity.total,
            )
            set_value(
                "prl_backup_store_free_bytes",
                "Free bytes on the backup store filesystem.",
                capacity.free,
            )
        if state.minimum_free_bytes is not None:
            set_value(
                "prl_backup_minimum_free_bytes",
                "Minimum free bytes required by the backup producer.",
                state.minimum_free_bytes,
            )


def serve() -> None:
    exporter = BackupMetricsExporter(
        os.environ,
        Path(os.environ.get("BACKUP_MONITOR_STATE_PATH", "/backups/.prl-monitoring-state.json")),
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = exporter.render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    serve()
