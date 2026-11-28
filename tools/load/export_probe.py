"""Bounded export-burst evidence; measurements never change runtime pool policy."""

import hashlib
import json
import math
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from sqlalchemy import event

PHASES = ("first_burst", "warmup80", "warm1", "warm2", "warm3", "warm4", "warm5")
MAX_REPORT_BYTES = 5 * 1024 * 1024


def burst_verdict(bursts):
    if [b.get("phase") for b in bursts] != list(PHASES):
        return False
    for burst in bursts:
        samples = burst.get("samples", [])
        phase = burst["phase"]
        if len(samples) != 80 or {s.get("sample") for s in samples} != {
            f"{phase}-{i}" for i in range(80)
        }:
            return False
        if any(
            s.get("status") != 200
            or s.get("scope_ok") is not True
            or not isinstance(s.get("latency_seconds"), (int, float))
            or isinstance(s.get("latency_seconds"), bool)
            or not math.isfinite(s["latency_seconds"])
            or s["latency_seconds"] < 0
            for s in samples
        ):
            return False
        values = sorted(s["latency_seconds"] for s in samples)
        if values[math.ceil(0.95 * len(values)) - 1] > 2.5:
            return False
    return True


def save_report(report, directory):
    destination = Path(directory) / uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    encoded = json.dumps(report, allow_nan=False, indent=2).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("Export report exceeds bounded evidence budget")
    (destination / "report.json").write_bytes(encoded)
    (destination / "report.sha256").write_text(
        hashlib.sha256(encoded).hexdigest(), encoding="ascii"
    )
    return destination / "report.json"


@contextmanager
def measure_pool(engine, sample, stage):
    """Checkout occurs after acquisition; duration includes connect/pre-ping/scheduling."""
    peak = {"checked_out": 0, "peak": 0, "invalidations": 0}

    def checkout(connection, record, proxy):
        peak["checked_out"] += 1
        peak["peak"] = max(peak["peak"], peak["checked_out"])
        current = sample.get()
        now = perf_counter()
        record.info["nfr03_hold"] = (current, stage.get(), now)
        if current is not None:
            label = stage.get()
            start = current.pop("_acquire_started", None)
            if start is not None:
                current[label + "_acquisition_seconds"] = now - start
            current[label + "_checkouts"] = current.get(label + "_checkouts", 0) + 1

    def checkin(connection, record):
        peak["checked_out"] = max(0, peak["checked_out"] - 1)
        retained = record.info.pop("nfr03_hold", None)
        if retained is not None:
            current, label, start = retained
            if current is not None:
                key = label + "_hold_seconds"
                current[key] = current.get(key, 0) + perf_counter() - start

    def invalidate(connection, record, error):
        peak["invalidations"] += 1

    handlers = [("checkout", checkout), ("checkin", checkin), ("invalidate", invalidate)]
    installed = []
    try:
        for name, handler in handlers:
            event.listen(engine.sync_engine.pool, name, handler)
            installed.append((name, handler))
        yield peak
    finally:
        for name, handler in reversed(installed):
            event.remove(engine.sync_engine.pool, name, handler)
