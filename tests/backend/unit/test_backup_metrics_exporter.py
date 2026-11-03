from datetime import UTC, datetime, timedelta

from tools.backup.metrics_exporter import BackupMetricsExporter

from app.backup.config import Reason
from app.backup.monitoring import BackupMonitorState, write_state


def valid_environment():
    return {
        "BACKUP_MONITOR_ENVIRONMENT": "demo",
        "BACKUP_MONITOR_STORE": "backup-storage",
        "BACKUP_MONITOR_RPO_SECONDS": "172800",
        "BACKUP_MONITOR_SCHEDULE_INTERVAL_SECONDS": "86400",
        "BACKUP_MONITOR_JOB_RUNTIME_SECONDS": "1300",
        "BACKUP_MONITOR_JOB_GRACE_SECONDS": "300",
        "BACKUP_MONITOR_STALE_SECONDS": "120",
    }


def write_sample_state(path, *, now=None):
    now = now or datetime.now(UTC)
    write_state(
        path,
        BackupMonitorState(
            schema_version=1,
            updated_at=now,
            last_verified_backup_at=now - timedelta(hours=1),
            artifact_integrity_verified=True,
            application_rehearsal_verified=False,
            last_job_operation="scheduled",
            last_job_started_at=now - timedelta(seconds=30),
            last_job_finished_at=now - timedelta(seconds=1),
            last_job_result="failure",
            last_job_reason=Reason.STORAGE,
            last_job_duration_seconds=29.0,
            scheduled_job_failure_pending=True,
            minimum_free_bytes=1024,
            remote_copy=None,
        ),
    )


def test_exporter_emits_backup_and_job_gauges_from_state(tmp_path):
    state_path = tmp_path / ".prl-monitoring-state.json"
    write_sample_state(state_path)
    exporter = BackupMetricsExporter(valid_environment(), state_path)

    metrics = exporter.render()

    assert "prl_backup_monitor_config_valid 1.0" in metrics
    assert (
        'prl_backup_artifact_integrity_verified{environment="demo",store="backup-storage"} 1.0'
        in metrics
    )
    assert (
        'prl_backup_job_last_success{environment="demo",operation="scheduled",'
        'store="backup-storage"} 0.0'
    ) in metrics
    assert (
        'prl_backup_scheduled_job_failure_pending{environment="demo",store="backup-storage"} 1.0'
        in metrics
    )
    assert "prl_backup_last_verified_timestamp_seconds" in metrics
    assert "prl_backup_store_total_bytes" in metrics
    assert "prl_backup_store_free_bytes" in metrics


def test_invalid_or_missing_thresholds_are_unknown_and_config_invalid(tmp_path):
    state_path = tmp_path / ".prl-monitoring-state.json"
    write_sample_state(state_path)
    config = valid_environment()
    del config["BACKUP_MONITOR_RPO_SECONDS"]
    config["BACKUP_MONITOR_JOB_RUNTIME_SECONDS"] = "86400"
    exporter = BackupMetricsExporter(config, state_path)

    metrics = exporter.render()

    assert "prl_backup_monitor_config_valid 0.0" in metrics
    assert "prl_backup_artifact_integrity_verified{" not in metrics
    assert "prl_backup_last_verified_timestamp_seconds{" not in metrics


def test_rpo_shorter_than_schedule_and_enforced_runtime_is_invalid(tmp_path):
    state_path = tmp_path / ".prl-monitoring-state.json"
    write_sample_state(state_path)
    config = valid_environment()
    config["BACKUP_MONITOR_RPO_SECONDS"] = "86400"
    config["BACKUP_MONITOR_SCHEDULE_INTERVAL_SECONDS"] = "86400"

    metrics = BackupMetricsExporter(config, state_path).render()

    assert "prl_backup_monitor_config_valid 0.0" in metrics
    assert "prl_backup_last_verified_timestamp_seconds{" not in metrics


def test_metrics_never_label_backup_id_uuid_path_or_error_text(tmp_path):
    state_path = tmp_path / ".prl-monitoring-state.json"
    write_sample_state(state_path)
    exporter = BackupMetricsExporter(valid_environment(), state_path)

    metrics = exporter.render()

    assert "backup_id" not in metrics
    assert "00000000-0000-0000-0000-000000000001" not in metrics
    assert str(state_path) not in metrics
    assert 'reason="STORAGE_UNAVAILABLE_OR_CAPACITY_EXCEEDED"' in metrics
    assert "secret-url" not in metrics and "raw traceback" not in metrics


def test_missing_remote_copy_is_unknown_not_healthy(tmp_path):
    state_path = tmp_path / ".prl-monitoring-state.json"
    write_sample_state(state_path)
    exporter = BackupMetricsExporter(valid_environment(), state_path)

    metrics = exporter.render()

    assert (
        'prl_backup_remote_copy_state{environment="demo",state="unknown",'
        'store="backup-storage"} 1.0'
    ) in metrics
    assert (
        'prl_backup_remote_copy_state{environment="demo",state="success",'
        'store="backup-storage"} 0.0'
    ) in metrics


def test_corrupt_state_does_not_emit_success(tmp_path):
    state_path = tmp_path / ".prl-monitoring-state.json"
    state_path.write_text('{"schema_version":1,"artifact_integrity_verified":true}')
    exporter = BackupMetricsExporter(valid_environment(), state_path)

    metrics = exporter.render()

    assert "prl_backup_artifact_integrity_verified{" not in metrics
    assert "prl_backup_job_last_success{" not in metrics
    assert "prl_backup_monitor_last_successful_state_read_timestamp_seconds" not in metrics


def test_exporter_reads_state_read_only_without_database_environment(tmp_path):
    state_path = tmp_path / ".prl-monitoring-state.json"
    write_sample_state(state_path)
    exporter = BackupMetricsExporter(valid_environment(), state_path)

    metrics = exporter.render()

    assert (
        'prl_backup_monitor_state_valid{environment="demo",store="backup-storage"} 1.0' in metrics
    )
    assert "DATABASE_URL" not in metrics
    assert "prl_backup_monitor_last_successful_state_read_timestamp_seconds" in metrics
