"""Bounded best-effort publishing for process-owned diagnostic lanes."""

import asyncio
import math
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

QUEUE_CAPACITY = 64
MAX_RECORD_BYTES = 8192
PUBLISH_TIMEOUT_SECONDS = 0.25
CLEANUP_TIMEOUT_SECONDS = 0.1
DROP_REASONS = ("queue_full", "oversize", "expired", "export_failed", "shutdown", "invalid")


@dataclass
class Record:
    value: str
    deadline: float
    failed: bool = False


class Publisher:
    """Serialize records through one daemon writer without blocking the caller."""

    def __init__(
        self,
        sink: Callable[[str], object] | None = None,
        *,
        capacity: int = QUEUE_CAPACITY,
        timeout: float = PUBLISH_TIMEOUT_SECONDS,
        max_record_bytes: int = MAX_RECORD_BYTES,
        include_active_in_capacity: bool = False,
        on_drop: Callable[[str], object] | None = None,
        on_success: Callable[[int], object] | None = None,
        on_queue_change: Callable[[int], object] | None = None,
    ) -> None:
        if (
            capacity < 1
            or not math.isfinite(timeout)
            or timeout <= 0
            or max_record_bytes < 1
            or max_record_bytes > MAX_RECORD_BYTES
        ):
            raise ValueError("Invalid publisher bounds")
        self._sink = sink
        try:
            self._stdout = sys.stdout.fileno() if sink is None else None
        except (AttributeError, OSError, ValueError):
            self._stdout = None
        self._capacity, self._timeout = capacity, timeout
        self._max_record_bytes = max_record_bytes
        self._include_active_in_capacity = include_active_in_capacity
        self._on_drop = on_drop
        self._on_success = on_success
        self._on_queue_change = on_queue_change
        self._condition = threading.Condition()
        self._queue: deque[Record] = deque()
        self._active: Record | None = None
        self._failures = 0
        self._accepted_count = 0
        self._drop_counts: dict[str, int] = {reason: 0 for reason in DROP_REASONS}
        self._closing = self._abort = False
        self._done = threading.Event()
        # Never use the asyncio default executor: its shutdown joins blocked threads.
        self._thread = threading.Thread(target=self._run, name="diagnostic-publisher", daemon=True)
        self._thread.start()

    def _drop(self, reason: str) -> None:
        self._failures += 1
        self._drop_counts[reason] += 1
        if self._on_drop is not None:
            try:
                self._on_drop(reason)
            except Exception:
                pass

    def _queue_changed(self) -> None:
        if self._on_queue_change is not None:
            try:
                self._on_queue_change(self._occupancy())
            except Exception:
                pass

    def _occupancy(self) -> int:
        active = 1 if self._include_active_in_capacity and self._active is not None else 0
        return len(self._queue) + active

    def _fail(self, record: Record, reason: str) -> None:
        if not record.failed:
            record.failed = True
            self._drop(reason)

    def _expire(self) -> None:
        now = time.monotonic()
        if self._active is not None and self._active.deadline <= now:
            self._fail(self._active, "expired")
        while self._queue and self._queue[0].deadline <= now:
            self._fail(self._queue.popleft(), "expired")
        self._queue_changed()

    @property
    def failures(self) -> int:
        with self._condition:
            self._expire()
            return self._failures

    @property
    def accepted_count(self) -> int:
        with self._condition:
            return self._accepted_count

    @property
    def dropped(self) -> dict[str, int]:
        with self._condition:
            self._expire()
            return self._drop_counts.copy()

    @property
    def queue_size(self) -> int:
        with self._condition:
            self._expire()
            return len(self._queue)

    def submit(self, value: str) -> bool:
        try:
            encoded = value.encode("utf-8")
        except (AttributeError, UnicodeError):
            with self._condition:
                self._drop("invalid")
            return False
        with self._condition:
            self._expire()
            if self._closing:
                self._drop("shutdown")
                return False
            if len(encoded) > self._max_record_bytes:
                self._drop("oversize")
                return False
            if self._occupancy() >= self._capacity:
                self._drop("queue_full")
                return False
            self._queue.append(Record(value, time.monotonic() + self._timeout))
            self._accepted_count += 1
            self._queue_changed()
            self._condition.notify()
            return True

    def _run(self) -> None:
        descriptor: int | None = None
        try:
            if self._sink is None:
                if self._stdout is None:
                    raise OSError("Output descriptor unavailable")
                descriptor = os.dup(self._stdout)
            while True:
                with self._condition:
                    self._expire()
                    while not self._queue and not self._closing:
                        self._condition.wait()
                    if self._abort or not self._queue:
                        break
                    record = self._queue.popleft()
                    self._active = record
                    self._queue_changed()
                try:
                    if self._sink is not None:
                        self._sink(record.value)
                    else:
                        assert descriptor is not None
                        # A duplicated fd avoids retaining a Python stream lock during a block.
                        remaining = (record.value + "\n").encode("utf-8")
                        while remaining:
                            written = os.write(descriptor, remaining)
                            if written <= 0:
                                raise OSError("Output write failed")
                            remaining = remaining[written:]
                    with self._condition:
                        self._expire()
                        if not record.failed and self._on_success is not None:
                            try:
                                self._on_success(1)
                            except Exception:
                                pass
                except Exception:
                    with self._condition:
                        self._fail(record, "export_failed")
                finally:
                    with self._condition:
                        self._expire()
                        self._active = None
                        self._queue_changed()
        except Exception:
            # Never pass sink/descriptor exception payloads to logging or stderr.
            pass
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            with self._condition:
                self._closing = True
                if self._active is not None:
                    self._fail(self._active, "shutdown")
                    self._active = None
                while self._queue:
                    self._fail(self._queue.popleft(), "shutdown")
                self._queue_changed()
                self._done.set()

    async def close(self, deadline: float) -> bool:
        with self._condition:
            self._closing = True
            self._condition.notify()
        loop = asyncio.get_running_loop()
        try:
            while not self._done.is_set():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.005, remaining))
        finally:
            if not self._done.is_set():
                with self._condition:
                    self._abort = True
                    if self._active is not None:
                        self._fail(self._active, "shutdown")
                    while self._queue:
                        self._fail(self._queue.popleft(), "shutdown")
                    self._queue_changed()
                    self._condition.notify()
        return self._done.is_set()
