from datetime import UTC, datetime, timedelta
from typing import TypedDict

from tests.backend.unit.test_backup_metrics_exporter import valid_environment, write_sample_state
from tests.backend.unit.test_backup_storage import fixture_artifact
from tools.backup.metrics_exporter import BackupMetricsExporter

from app.backup.monitoring import STATE_FILENAME, read_state, record_remote_copy


class StoreOptions(TypedDict):
    environment: str
    store: str
    configuration_sha256: str


def test_local_remote_and_rehearsal_results_remain_distinct_across_failures(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    path = tmp_path / STATE_FILENAME
    write_sample_state(path)
    before = read_state(path)
    started = datetime.now(UTC) - timedelta(seconds=10)
    options = StoreOptions(
        environment="demo",
        store="backup-storage",
        configuration_sha256="a" * 64,
    )
    record_remote_copy(config, **options, started_at=started, status="pending")
    pending = read_state(path)
    assert pending is not None and pending.remote_copy is not None
    assert pending.remote_copy.status == "pending" and pending.remote_copy.last_verified_at is None
    verified, point = started + timedelta(seconds=1), started - timedelta(hours=1)
    record_remote_copy(
        config,
        **options,
        started_at=started,
        status="success",
        verified_at=verified,
        recovery_point_at=point,
    )
    record_remote_copy(
        config, **options, started_at=started + timedelta(seconds=2), status="failure"
    )
    after = read_state(path)
    assert before is not None and after is not None and after.remote_copy is not None
    assert after.remote_copy.status == "failure" and after.remote_copy.last_verified_at == verified
    assert after.remote_copy.recovery_point_at == point
    assert after.last_verified_backup_at == before.last_verified_backup_at
    assert after.last_job_result == before.last_job_result
    assert after.application_rehearsal_verified is False
    env = dict(valid_environment(), BACKUP_MONITOR_REMOTE_CONFIGURATION_SHA256="a" * 64)
    metrics = BackupMetricsExporter(env, path).render()
    assert (
        'prl_backup_remote_copy_state{environment="demo",state="failure",'
        'store="backup-storage"} 1.0' in metrics
    )
    assert "prl_backup_remote_recovery_point_timestamp_seconds" in metrics
    assert "a" * 64 not in metrics
    env["BACKUP_MONITOR_REMOTE_CONFIGURATION_SHA256"] = "b" * 64
    stale = BackupMetricsExporter(env, path).render()
    assert (
        'prl_backup_remote_copy_binding_valid{environment="demo",store="backup-storage"} 0.0'
        in stale
    )
    assert (
        'prl_backup_remote_copy_state{environment="demo",state="unknown",'
        'store="backup-storage"} 1.0' in stale
    )
    assert "prl_backup_remote_recovery_point_timestamp_seconds" not in stale


def test_new_store_binding_and_late_job_cannot_reuse_or_overwrite_success(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    path = tmp_path / STATE_FILENAME
    write_sample_state(path)
    start = datetime.now(UTC) - timedelta(seconds=5)
    options = StoreOptions(
        environment="demo", store="backup-storage", configuration_sha256="a" * 64
    )
    record_remote_copy(
        config,
        **options,
        started_at=start,
        status="success",
        verified_at=start,
        recovery_point_at=start,
    )
    record_remote_copy(config, **options, started_at=start + timedelta(seconds=1), status="pending")
    record_remote_copy(config, **options, started_at=start, status="failure")
    state = read_state(path)
    assert (
        state is not None
        and state.remote_copy is not None
        and state.remote_copy.status == "pending"
    )
    options.update(configuration_sha256="b" * 64, store="different-store")
    record_remote_copy(config, **options, started_at=start + timedelta(seconds=2), status="pending")
    state = read_state(path)
    assert (
        state is not None
        and state.remote_copy is not None
        and state.remote_copy.last_verified_at is None
    )
    metrics = BackupMetricsExporter(valid_environment(), path).render()
    assert (
        'prl_backup_remote_copy_state{environment="demo",state="unknown",'
        'store="backup-storage"} 1.0' in metrics
    )
    assert "prl_backup_remote_copy_last_verified_timestamp_seconds" not in metrics


def test_late_success_from_previous_configuration_cannot_replace_new_pending_state(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    path = tmp_path / STATE_FILENAME
    write_sample_state(path)
    start = datetime.now(UTC) - timedelta(seconds=5)
    options = StoreOptions(
        environment="demo", store="backup-storage", configuration_sha256="a" * 64
    )
    record_remote_copy(config, **options, started_at=start, status="pending")
    options["configuration_sha256"] = "b" * 64
    record_remote_copy(
        config,
        **options,
        started_at=start + timedelta(seconds=1),
        status="pending",
    )
    before = read_state(path)
    assert before is not None and before.remote_copy is not None
    assert before.remote_copy.configuration_sha256 == "b" * 64
    options["configuration_sha256"] = "a" * 64
    record_remote_copy(
        config,
        **options,
        started_at=start,
        status="success",
        verified_at=start + timedelta(seconds=2),
        recovery_point_at=start,
    )
    assert read_state(path) == before
