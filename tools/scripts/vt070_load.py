"""Run 150 synchronized ASGI requests per wave with synthetic DB and identity."""

import asyncio
import json
from collections import Counter
from math import ceil
from time import perf_counter

from app.core.config import Settings
from tests.backend.test_vt070 import BODY, PATH, Harness, payload

N = 150
WAVES = 3


async def main():
    settings = Settings(
        DATABASE_URL="mysql+asyncmy://test:unused@127.0.0.1:1/test",
        APPLICATION_ORIGIN="http://test",
        COOKIE_SECRET="test-only-cookie-secret",
        CSRF_SECRET="test-only-csrf-secret",
        ACCEPTED_SCHEMA_REVISIONS="a1b2c3d4e5f6",
        COOKIE_SECURE=False,
        ENVIRONMENT="test",
    )
    h = Harness(settings, shared_state=False)
    scenarios = [
        ("normal", BODY, 200),
        ("max_valid", payload(32768), 200),
        ("over_limit", payload(32769), 413),
    ]

    async def wave(client, body):
        gate = asyncio.Event()
        ready = active = peak = 0

        async def request():
            nonlocal ready, active, peak
            ready += 1
            if ready == N:
                gate.set()
            await gate.wait()
            start = perf_counter()
            active += 1
            peak = max(peak, active)
            try:
                # Scheduler checkpoint lets every admitted task start before dispatch.
                await asyncio.sleep(0)
                async with asyncio.timeout(8):
                    response = await client.post(
                        PATH, content=body, headers={"Content-Type": "application/json"}
                    )
                if response.status_code == 200 and response.json()["status"] != "NO_SHOW":
                    return "bad_mapping", perf_counter() - start
                return str(response.status_code), perf_counter() - start
            except TimeoutError:
                return "timeout", perf_counter() - start
            finally:
                active -= 1

        return await asyncio.gather(*(request() for _ in range(N))), peak

    with h.bind():
        async with h.client() as client:
            failed = False
            for name, body, expected in scenarios:
                warmup, warmup_peak = await wave(client, body)
                failed |= warmup_peak != N or any(status != str(expected) for status, _ in warmup)
                before = h.primary_commits, h.delivery_attempts, len(h.deliveries)
                samples, peaks = [], []
                for _ in range(WAVES):
                    rows, peak = await wave(client, body)
                    samples.extend(rows)
                    peaks.append(peak)
                statuses = Counter(status for status, _ in samples)
                timings = sorted(seconds for _, seconds in samples)
                p95 = timings[ceil(0.95 * len(timings)) - 1]
                expected_writes = N * WAVES if expected == 200 else 0
                writes = tuple(a - b for a, b in zip(
                    (h.primary_commits, h.delivery_attempts, len(h.deliveries)), before, strict=True
                ))
                passed = (
                    statuses == {str(expected): N * WAVES}
                    and all(peak == N for peak in peaks)
                    and writes == (expected_writes,) * 3
                    and p95 <= 4.0
                )
                failed |= not passed
                print(json.dumps({
                    "scenario": name, "bytes": len(body), "samples": len(timings),
                    "peak_by_wave": peaks, "statuses": statuses,
                    "p95_s": round(p95, 6), "max_s": round(timings[-1], 6),
                    "primary_commits_delivery_attempts_inserted": writes,
                    "harness_gate_pass": passed,
                }))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
