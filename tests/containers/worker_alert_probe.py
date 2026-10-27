"""Bounded helpers for proving real worker-alert transitions in Compose."""

from __future__ import annotations

import math
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from tests.containers.test_compose_acceptance import AcceptanceFailure

ROW_LOCK_READY = "PRL_ROW_LOCK_READY"
ROW_LOCK_RELEASED = "PRL_ROW_LOCK_RELEASED"
ROW_LOCK_ROLLED_BACK = "PRL_ROW_LOCK_ROLLED_BACK"
MYSQL_EXEC_SCRIPT = (
    'MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql --protocol=socket -uroot '
    "--connect-timeout=3 --batch --unbuffered --raw --skip-column-names prl"
)


@dataclass(frozen=True)
class MonotonicDeadline:
    """One shared monotonic budget for a stimulus, polling, and recovery phase."""

    expires_at: float
    started_at: float

    @classmethod
    def after(cls, seconds: float) -> MonotonicDeadline:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Deadline duration must be finite and positive")
        started_at = time.monotonic()
        return cls(expires_at=started_at + seconds, started_at=started_at)

    @classmethod
    def from_utc_timestamp(cls, value: str, seconds: float) -> MonotonicDeadline:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Deadline duration must be finite and positive")
        normalized = normalize_alertmanager_timestamp(value)
        event_at = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        elapsed = max(0.0, (datetime.now(UTC) - event_at).total_seconds())
        remaining = seconds - elapsed
        if remaining <= 0:
            raise ValueError("The event deadline has already expired")
        now = time.monotonic()
        return cls(expires_at=now + remaining, started_at=now - elapsed)

    @property
    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    @property
    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)


def normalize_alertmanager_timestamp(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError("Invalid Alertmanager timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid Alertmanager timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Invalid Alertmanager timestamp")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def wait_until_deadline(
    deadline: MonotonicDeadline,
    probe: Callable[[float], object],
    *,
    failure: str,
    interval_seconds: float = 0.5,
) -> object:
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("The polling interval must be finite and positive")
    last_error: str | None = None
    while deadline.remaining > 0:
        try:
            value = probe(deadline.expires_at)
        except (
            AcceptanceFailure,
            OSError,
            ValueError,
            TimeoutError,
            subprocess.SubprocessError,
        ) as exc:
            last_error = type(exc).__name__
        else:
            if value is not None and value is not False:
                return value
        remaining = deadline.remaining
        if remaining > 0:
            time.sleep(min(interval_seconds, remaining))
    suffix = f" (last poll error: {last_error})" if last_error else ""
    raise AcceptanceFailure(f"{failure}{suffix}")


def capture_receiver_snapshot_before_recovery(
    receiver_snapshot: Callable[[], list[dict[str, str]]],
    recovery: Callable[[], None],
) -> list[dict[str, str]]:
    """Capture the receiver baseline before running a recovery that can deliver resolution."""
    snapshot = receiver_snapshot()
    recovery()
    return snapshot


def active_worker_alert_identity(
    alerts: object,
    alertname: str,
    *,
    severity: str,
    worker_slot: str | None,
) -> dict[str, object] | None:
    """Return one sanitized active Alertmanager identity matching the rule labels."""
    if not isinstance(alerts, list):
        raise AcceptanceFailure("Alertmanager returned an invalid active alert list")
    matches: list[dict[str, object]] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels")
        if not isinstance(labels, dict) or labels.get("alertname") != alertname:
            continue
        status = alert.get("status")
        if not isinstance(status, dict) or status.get("state") != "active":
            continue
        if (
            labels.get("service_name") != "worker"
            or labels.get("deployment_environment_name") != "demo"
            or labels.get("severity") != severity
        ):
            raise AcceptanceFailure("The worker alert labels did not match the expected rule")
        if worker_slot is None:
            if "worker_slot" in labels:
                raise AcceptanceFailure("The backlog overdue alert unexpectedly had a worker slot")
        elif labels.get("worker_slot") != worker_slot:
            continue
        fingerprint = alert.get("fingerprint")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{16,64}", fingerprint):
            raise AcceptanceFailure("Alertmanager returned an invalid worker alert fingerprint")
        try:
            starts_at = normalize_alertmanager_timestamp(alert.get("startsAt"))
        except ValueError:
            raise AcceptanceFailure(
                "Alertmanager returned an invalid worker alert start time"
            ) from None
        safe_labels = {
            key: labels[key]
            for key in (
                "alertname",
                "severity",
                "service_name",
                "deployment_environment_name",
                "worker_slot",
            )
            if isinstance(labels.get(key), str)
        }
        matches.append(
            {
                "fingerprint": fingerprint,
                "starts_at": starts_at,
                "labels": safe_labels,
            }
        )
    if len(matches) > 1:
        raise AcceptanceFailure(
            "Alertmanager returned multiple active identities for one worker rule"
        )
    return matches[0] if matches else None


def pending_due_row_lock_sql(proposal_id: str) -> tuple[str, str]:
    """Build a read-only, UUID-scoped row-lock script and its exact row marker."""
    try:
        canonical_id = str(UUID(proposal_id))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("The proposal id must be a UUID") from None
    compact_id = canonical_id.replace("-", "")
    sql = (
        "SELECT CONNECTION_ID();\n"
        "SET SESSION wait_timeout = 300;\n"
        "SET SESSION innodb_lock_wait_timeout = 5;\n"
        "START TRANSACTION;\n"
        "SELECT LOWER(HEX(id)) FROM change_proposals "
        f"WHERE id = UNHEX(REPLACE('{canonical_id}', '-', '')) "
        "AND status = 'PENDING' AND expires_at <= UTC_TIMESTAMP(6) FOR UPDATE;\n"
        f"SELECT '{ROW_LOCK_READY}';\n"
    )
    return sql, compact_id


def run_bounded_mysql_statement(
    compose_base: Sequence[str],
    compose_env: Mapping[str, str],
    statement: str,
    *,
    timeout_seconds: float,
) -> str:
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30:
        raise ValueError("The MySQL statement timeout is outside its bounded range")
    command = [
        *compose_base,
        "exec",
        "-T",
        "mysql",
        "bash",
        "-c",
        MYSQL_EXEC_SCRIPT,
    ]
    try:
        result = subprocess.run(
            command,
            input=statement,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=dict(compose_env),
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceFailure("The bounded MySQL statement exceeded its deadline") from None
    if result.returncode:
        raise AcceptanceFailure("The bounded MySQL statement did not complete successfully")
    return result.stdout.strip()


class OwnedMySQLRowLock:
    """An owned docker-exec MySQL client holding one proposal row lock."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        *,
        compose_base: Sequence[str],
        compose_env: Mapping[str, str],
        proposal_id: str,
        connection_id: int,
        expected_row_hex: str,
        output: queue.Queue[str | None],
        reader: threading.Thread,
    ) -> None:
        self._process = process
        self._compose_base = tuple(compose_base)
        self._compose_env = dict(compose_env)
        self._proposal_id = str(UUID(proposal_id))
        self._connection_id = connection_id
        self._expected_row_hex = expected_row_hex
        self._output = output
        self._reader = reader
        self._closed = False

    def __enter__(self) -> OwnedMySQLRowLock:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.rollback_and_close()

    @property
    def is_open(self) -> bool:
        return not self._closed and self._process.poll() is None

    @classmethod
    def acquire(
        cls,
        compose_base: Sequence[str],
        compose_env: Mapping[str, str],
        inspect_mysql: Callable[[], dict[str, object]],
        *,
        compose_project: str,
        mysql_container_id: str,
        proposal_id: str,
        timeout_seconds: float = 15,
    ) -> OwnedMySQLRowLock:
        if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30:
            raise ValueError("The row-lock startup timeout is outside its bounded range")
        if not re.fullmatch(r"[0-9a-f]{64}", mysql_container_id):
            raise AcceptanceFailure("The MySQL container identity was invalid")
        record = inspect_mysql()
        labels = (record.get("Config") or {}).get("Labels") or {}
        state = record.get("State") or {}
        if (
            record.get("Id") != mysql_container_id
            or labels.get("com.docker.compose.project") != compose_project
            or state.get("Status") != "running"
        ):
            raise AcceptanceFailure(
                "The MySQL row-lock target failed its container ownership check"
            )

        statement, expected_row_hex = pending_due_row_lock_sql(proposal_id)
        command = [
            *compose_base,
            "exec",
            "-T",
            "mysql",
            "bash",
            "-c",
            MYSQL_EXEC_SCRIPT,
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=dict(compose_env),
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            process.wait(timeout=5)
            raise AcceptanceFailure("The owned MySQL client did not expose bounded pipes")
        output: queue.Queue[str | None] = queue.Queue()

        def read_lines() -> None:
            try:
                for line in process.stdout:
                    output.put(line.rstrip("\r\n"))
            finally:
                output.put(None)

        reader = threading.Thread(target=read_lines, name="prl-mysql-row-lock-reader", daemon=True)
        reader.start()
        holder = cls(
            process,
            compose_base=compose_base,
            compose_env=compose_env,
            proposal_id=proposal_id,
            connection_id=0,
            expected_row_hex=expected_row_hex,
            output=output,
            reader=reader,
        )
        try:
            setup, selection = statement.split("\n", 1)
            process.stdin.write(setup + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + timeout_seconds
            observed_connection_id: int | None = None
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    line = output.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    if process.poll() is not None:
                        raise AcceptanceFailure(
                            "The owned MySQL row-lock client exited before ready"
                        ) from None
                    continue
                if line is None:
                    raise AcceptanceFailure("The owned MySQL row-lock client closed before ready")
                if not re.fullmatch(r"[1-9][0-9]{0,9}", line):
                    raise AcceptanceFailure(
                        "The owned MySQL row-lock client returned an invalid connection id"
                    )
                observed_connection_id = int(line)
                holder._connection_id = observed_connection_id
                break
            if observed_connection_id is None:
                raise AcceptanceFailure("The owned MySQL connection id did not arrive in time")

            process.stdin.write(selection)
            process.stdin.flush()
            observed_row = False
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    line = output.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    if process.poll() is not None:
                        raise AcceptanceFailure(
                            "The owned MySQL row-lock client exited before ready"
                        ) from None
                    continue
                if line is None:
                    raise AcceptanceFailure("The owned MySQL row-lock client closed before ready")
                if line == ROW_LOCK_READY:
                    if not observed_row:
                        raise AcceptanceFailure(
                            "The MySQL row-lock client returned an incomplete ready marker"
                        )
                    return holder
                if line == expected_row_hex:
                    observed_row = True
                    continue
                raise AcceptanceFailure("The MySQL row-lock client returned an unexpected marker")
            raise AcceptanceFailure("The owned MySQL row lock did not become ready in time")
        except BaseException:
            holder.rollback_and_close()
            raise

    def _send_sql(self, statements: str) -> None:
        if self._process.stdin is None or self._process.stdin.closed:
            raise AcceptanceFailure("The owned MySQL client input was already closed")
        self._process.stdin.write(statements)
        self._process.stdin.flush()

    def _wait_for_marker(self, marker: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                line = self._output.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if self._process.poll() is not None:
                    return False
                continue
            if line is None:
                return False
            if line == marker:
                return True
        return False

    def commit_and_release(self, *, timeout_seconds: float = 10) -> None:
        if self._closed:
            raise AcceptanceFailure("The owned MySQL row lock was already closed")
        try:
            self._send_sql(f"COMMIT;\nSELECT '{ROW_LOCK_RELEASED}';\n")
            if self._process.stdin is not None:
                self._process.stdin.close()
            if not self._wait_for_marker(ROW_LOCK_RELEASED, timeout_seconds):
                raise AcceptanceFailure("The MySQL row-lock client did not acknowledge release")
            self._process.wait(timeout=timeout_seconds)
            if self._process.returncode != 0:
                raise AcceptanceFailure("The MySQL row-lock client did not exit cleanly")
            self._closed = True
        except BaseException:
            self._terminate_and_verify_release()
            raise

    def rollback_and_close(self, *, timeout_seconds: float = 5) -> None:
        if self._closed:
            return
        acknowledged = False
        try:
            if self._process.poll() is None and self._process.stdin is not None:
                self._send_sql(f"ROLLBACK;\nSELECT '{ROW_LOCK_ROLLED_BACK}';\n")
                self._process.stdin.close()
                acknowledged = self._wait_for_marker(ROW_LOCK_ROLLED_BACK, timeout_seconds)
            if acknowledged:
                self._process.wait(timeout=timeout_seconds)
                if self._process.returncode == 0:
                    self._closed = True
                    return
        except (OSError, subprocess.SubprocessError, AcceptanceFailure):
            pass
        self._terminate_and_verify_release()

    def _terminate_and_verify_release(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._connection_id > 0:
            verify_script = (
                "SET SESSION innodb_lock_wait_timeout = 2;\n"
                "SELECT COUNT(*) FROM performance_schema.data_locks AS locks "
                "JOIN performance_schema.threads AS threads USING (THREAD_ID) "
                f"WHERE threads.PROCESSLIST_ID = {self._connection_id};\n"
                "SELECT 'PRL_ROW_LOCK_CONNECTION_CLOSED';\n"
            )
            command = [
                *self._compose_base,
                "exec",
                "-T",
                "mysql",
                "bash",
                "-c",
                MYSQL_EXEC_SCRIPT,
            ]
            result = subprocess.run(
                command,
                input=verify_script,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=10,
                check=False,
                env=self._compose_env,
            )
            lines = [line.strip() for line in result.stdout.splitlines()]
            if result.returncode != 0 or lines != ["0", "PRL_ROW_LOCK_CONNECTION_CLOSED"]:
                raise AcceptanceFailure(
                    "The owned MySQL client connection closure did not release its row lock"
                )
        self._closed = True


def acquire_pending_proposal_row_lock(
    compose_base: Sequence[str],
    compose_env: Mapping[str, str],
    inspect_mysql: Callable[[], dict[str, object]],
    *,
    compose_project: str,
    mysql_container_id: str,
    proposal_id: str,
    timeout_seconds: float = 15,
) -> OwnedMySQLRowLock:
    return OwnedMySQLRowLock.acquire(
        compose_base,
        compose_env,
        inspect_mysql,
        compose_project=compose_project,
        mysql_container_id=mysql_container_id,
        proposal_id=proposal_id,
        timeout_seconds=timeout_seconds,
    )
