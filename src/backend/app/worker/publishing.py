"""Bounded, best-effort output isolated from the asyncio and interpreter shutdown paths."""

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


@dataclass
class Record:
    value: str
    deadline: float
    failed: bool = False


class Publisher:
    def __init__(
        self,
        sink: Callable[[str], object] | None = None,
        *,
        capacity: int = QUEUE_CAPACITY,
        timeout: float = PUBLISH_TIMEOUT_SECONDS,
    ) -> None:
        if capacity < 1 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid publisher bounds")
        self._sink = sink
        try:
            self._stdout = sys.stdout.fileno() if sink is None else None
        except (AttributeError, OSError, ValueError):
            self._stdout = None
        self._capacity, self._timeout = capacity, timeout
        self._condition = threading.Condition()
        self._queue: deque[Record] = deque()
        self._active: Record | None = None
        self._failures = 0
        self._closing = self._abort = False
        self._done = threading.Event()
        # Never use the asyncio default executor: its shutdown joins blocked threads.
        self._thread = threading.Thread(target=self._run, name="expiry-publisher", daemon=True)
        self._thread.start()

    def _fail(self, record: Record) -> None:
        if not record.failed:
            record.failed = True
            self._failures += 1

    def _expire(self) -> None:
        now = time.monotonic()
        if self._active is not None and self._active.deadline <= now:
            self._fail(self._active)
        while self._queue and self._queue[0].deadline <= now:
            self._fail(self._queue.popleft())

    @property
    def failures(self) -> int:
        with self._condition:
            self._expire()
            return self._failures

    def submit(self, value: str) -> bool:
        with self._condition:
            self._expire()
            if (
                self._closing
                or len(self._queue) >= self._capacity
                or len(value) > MAX_RECORD_BYTES
                or len(value.encode("utf-8")) > MAX_RECORD_BYTES
            ):
                self._failures += 1
                return False
            self._queue.append(Record(value, time.monotonic() + self._timeout))
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
                try:
                    if self._sink is not None:
                        self._sink(record.value)
                    else:
                        assert descriptor is not None
                        # Raw duplicated fd: no sys.stdout/StreamHandler lock is held by
                        # a blocked write, so Python's exit-time flush cannot join/block it.
                        remaining = (record.value + "\n").encode("utf-8")
                        while remaining:
                            written = os.write(descriptor, remaining)
                            if written <= 0:
                                raise OSError("Output write failed")
                            remaining = remaining[written:]
                except Exception:
                    with self._condition:
                        self._fail(record)
                finally:
                    with self._condition:
                        self._expire()
                        self._active = None
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
                    self._fail(self._active)
                while self._queue:
                    self._fail(self._queue.popleft())
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
                        self._fail(self._active)
                    while self._queue:
                        self._fail(self._queue.popleft())
                    self._condition.notify()
        return self._done.is_set()
