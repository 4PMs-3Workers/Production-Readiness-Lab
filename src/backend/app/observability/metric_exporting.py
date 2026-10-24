"""One owned daemon lane for periodically exporting the latest worker metric state."""

import asyncio
import math
import threading
import time
from collections.abc import Callable, Sequence

from opentelemetry.sdk.metrics.export import MetricExportResult, MetricsData


class LatestMetricExportLane:
    """Export immutable latest snapshots on one bounded, coalescing daemon lane."""

    def __init__(
        self,
        snapshot_factory: Callable[[], Sequence[MetricsData]],
        exporter: object,
        *,
        interval_seconds: float = 5.0,
        max_age_seconds: float = 1.0,
        network_timeout_seconds: float = 0.5,
        on_exported: Callable[[int], object] | None = None,
        on_failed: Callable[[int], object] | None = None,
        on_drop: Callable[[str], object] | None = None,
        on_queue_change: Callable[[int], object] | None = None,
    ) -> None:
        if (
            not math.isfinite(interval_seconds)
            or interval_seconds <= 0
            or not math.isfinite(max_age_seconds)
            or not 0 < max_age_seconds <= 1
            or not math.isfinite(network_timeout_seconds)
            or not 0 < network_timeout_seconds <= 0.5
        ):
            raise ValueError("Invalid metric export bounds")
        self._snapshot_factory = snapshot_factory
        self._exporter = exporter
        self._interval = interval_seconds
        self._max_age = max_age_seconds
        self._network_timeout = network_timeout_seconds
        self._on_exported = on_exported
        self._on_failed = on_failed
        self._on_drop = on_drop
        self._on_queue_change = on_queue_change
        self._condition = threading.Condition()
        self._queue_revision = 0
        self._pending = False
        self._closing = False
        self._abort = False
        self._final_export_started = False
        self._done = threading.Event()
        self._exported_count = 0
        self._failed_count = 0
        self._drop_counts = {
            "queue_full": 0,
            "oversize": 0,
            "expired": 0,
            "export_failed": 0,
            "shutdown": 0,
            "invalid": 0,
        }
        self._thread = threading.Thread(
            target=self._run,
            name="telemetry-worker-metrics-exporter",
            daemon=True,
        )
        self._thread.start()

    def _callback(self, callback: Callable[..., object] | None, *args: object) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            pass

    def _drop(self, reason: str) -> None:
        safe_reason = reason if reason in self._drop_counts else "invalid"
        with self._condition:
            self._drop_counts[safe_reason] += 1
        self._callback(self._on_drop, safe_reason)

    def _notify_queue_change(self) -> None:
        """Publish queue occupancy without holding the lane state lock."""
        if self._on_queue_change is None:
            return
        while True:
            with self._condition:
                revision = self._queue_revision
                size = int(self._pending)
            self._callback(self._on_queue_change, size)
            with self._condition:
                if revision == self._queue_revision:
                    return

    def note_snapshot(self) -> bool:
        """Mark a newer immutable state as waiting for the next timer tick."""
        with self._condition:
            if self._closing:
                accepted = False
                notify = False
            else:
                accepted = True
                notify = not self._pending
                if not self._pending:
                    self._pending = True
                    self._queue_revision += 1
        if not accepted:
            self._drop("shutdown")
            return False
        if notify:
            self._notify_queue_change()
        return True

    def _export(self) -> bool:
        created = time.monotonic()
        deadline = created + self._max_age
        try:
            batches = tuple(self._snapshot_factory())
            if not batches:
                raise ValueError("Empty metric snapshot")
        except Exception as error:
            reason = getattr(error, "reason", "invalid")
            self._drop(reason if reason in {"oversize", "invalid"} else "invalid")
            return False

        for batch in batches:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._drop("expired")
                return False
            timeout_millis = min(self._network_timeout, remaining) * 1000
            try:
                export = getattr(self._exporter, "export", None)
                if callable(export):
                    result = export(batch, timeout_millis=timeout_millis)
                elif callable(self._exporter):
                    result = self._exporter(batch)
                else:
                    raise TypeError("Invalid metric exporter")
                succeeded = result is True or result == MetricExportResult.SUCCESS
            except Exception:
                succeeded = False
            if not succeeded:
                with self._condition:
                    self._failed_count += 1
                self._drop("export_failed")
                self._callback(self._on_failed, 1)
                return False
        with self._condition:
            self._exported_count += 1
        self._callback(self._on_exported, 1)
        return True

    def _run(self) -> None:
        next_tick = time.monotonic() + self._interval
        try:
            while True:
                with self._condition:
                    while not self._closing and not self._abort:
                        remaining = next_tick - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
                    if self._abort:
                        break
                    if self._closing:
                        if self._final_export_started:
                            break
                        self._final_export_started = True
                    else:
                        next_tick = time.monotonic() + self._interval
                    if self._pending:
                        self._pending = False
                        self._queue_revision += 1
                self._notify_queue_change()
                self._export()
                with self._condition:
                    if self._closing or self._abort:
                        break
        except Exception:
            self._drop("invalid")
        finally:
            shutdown = getattr(self._exporter, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass
            with self._condition:
                if self._pending:
                    self._pending = False
                    self._queue_revision += 1
            self._notify_queue_change()
            self._done.set()

    @property
    def exported_count(self) -> int:
        with self._condition:
            return self._exported_count

    @property
    def failed_count(self) -> int:
        with self._condition:
            return self._failed_count

    @property
    def dropped(self) -> dict[str, int]:
        with self._condition:
            return self._drop_counts.copy()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def request_close(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    async def close(self, deadline: float) -> bool:
        """Attempt one final current snapshot inside the caller's shared deadline."""
        loop = asyncio.get_running_loop()
        deadline = min(deadline, loop.time() + 1.0)
        self.request_close()
        while not self._done.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                with self._condition:
                    self._abort = True
                    self._condition.notify_all()
                break
            await asyncio.sleep(min(0.005, remaining))
        return self._done.is_set()
