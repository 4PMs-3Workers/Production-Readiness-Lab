"""Parse worker progress records and require monotonic progress across observations."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import NamedTuple

WORKER_ID = re.compile(r"[0-9a-f]{32}\Z")
_PROGRESS_EVENTS = {"heartbeat", "scan", "candidate", "ready"}
_TERMINAL_EVENTS = {"stopping", "stopped", "fatal"}
_KNOWN_EVENTS = _PROGRESS_EVENTS | _TERMINAL_EVENTS | {"starting"}


class WorkerProgress(NamedTuple):
    worker_id: str
    successful_scans: int


def _aware_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None


def _progress_record(record: dict[str, object]) -> WorkerProgress | None:
    worker_id = record.get("worker_id")
    scans = record.get("successful_scans")
    if (
        not isinstance(worker_id, str)
        or not WORKER_ID.fullmatch(worker_id)
        or type(scans) is not int
        or scans <= 0
        or record.get("phase") != "running"
        or record.get("database_ok") is not True
        or not _aware_timestamp(record.get("last_successful_scan"))
    ):
        return None
    return WorkerProgress(worker_id, scans)


def latest_worker_progress(output: str, *, require_heartbeat: bool) -> WorkerProgress | None:
    """Read the newest healthy progress event without crossing log boundaries.

    Event timestamps are validated as aware database values, but they do not determine
    freshness. Freshness is established by observing a larger scan counter from one worker.
    """
    worker_id: str | None = None
    for line in reversed(output.splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict):
            return None
        event = record.get("event")
        if not isinstance(event, str) or event not in _KNOWN_EVENTS:
            return None

        current_id = record.get("worker_id")
        if not isinstance(current_id, str) or not WORKER_ID.fullmatch(current_id):
            return None
        if worker_id is None:
            worker_id = current_id
        elif current_id != worker_id:
            return None

        if event in _TERMINAL_EVENTS or event == "starting":
            return None
        progress = _progress_record(record)
        if progress is None:
            return None
        if not require_heartbeat or event == "heartbeat":
            return progress
    return None


class WorkerProgressTracker:
    """Require a later scan from the same running worker process."""

    def __init__(self, *, excluded_worker_id: str | None = None) -> None:
        self.excluded_worker_id = excluded_worker_id
        self._worker_id: str | None = None
        self._successful_scans: int | None = None

    def _reset(self) -> None:
        self._worker_id = None
        self._successful_scans = None

    def observe(self, progress: WorkerProgress | None, *, running: bool) -> bool:
        if not running or progress is None or progress.worker_id == self.excluded_worker_id:
            self._reset()
            return False
        if progress.worker_id != self._worker_id:
            self._worker_id = progress.worker_id
            self._successful_scans = progress.successful_scans
            return False
        if (
            self._successful_scans is not None
            and progress.successful_scans > self._successful_scans
        ):
            self._successful_scans = progress.successful_scans
            return True
        return False
