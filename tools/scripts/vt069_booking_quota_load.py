"""Synthetic 60-task ASGI load probe for the booking quota decision only."""

import asyncio
import json
from math import ceil
from time import perf_counter, perf_counter_ns

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.domain.booking import booking_quota_reached

CONCURRENCY = 60
REQUEST_TIMEOUT_SECONDS = 2.0


def create_harness() -> FastAPI:
    app = FastAPI()
    barrier = asyncio.Barrier(CONCURRENCY)
    in_flight = 0
    peak_in_flight = 0

    @app.get("/warmup")
    async def warmup() -> dict[str, bool]:
        return {"ready": True}

    @app.post("/booking-quota")
    async def check_quota(payload: dict[str, int]) -> JSONResponse:
        nonlocal in_flight, peak_in_flight
        in_flight += 1
        peak_in_flight = max(peak_in_flight, in_flight)
        try:
            await barrier.wait()
            reached = booking_quota_reached(
                payload["active_count"], payload["max_active_appointments"]
            )
            return JSONResponse(
                status_code=409 if reached else 201,
                content={"outcome": "BOOKING_QUOTA_EXCEEDED" if reached else "BOOKING_ALLOWED"},
            )
        finally:
            in_flight -= 1

    app.state.get_peak_in_flight = lambda: peak_in_flight
    return app


async def run() -> dict[str, int | float | bool]:
    app = create_harness()
    payloads = [
        {
            "student": index,
            "active_count": index % 5,
            "max_active_appointments": index % 4 + 1,
        }
        for index in range(CONCURRENCY)
    ]
    latencies: list[float] = []
    correct = 0
    successes = 0
    business_rejections = 0
    timeouts = 0
    other_failures = 0

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://quota-harness.invalid"
    ) as client:
        for _ in range(5):
            response = await client.get("/warmup")
            if response.status_code != 200:
                raise RuntimeError("ASGI warm-up failed")

        async def submit(payload: dict[str, int]) -> None:
            nonlocal correct, successes, business_rejections, timeouts, other_failures
            started_ns = perf_counter_ns()
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    response = await client.post(
                        "/booking-quota",
                        json=payload,
                        headers={"X-Synthetic-Student": str(payload["student"])},
                    )
                elapsed = (perf_counter_ns() - started_ns) / 1_000_000_000
                latencies.append(elapsed)
                expected_allowed = payload["active_count"] < payload["max_active_appointments"]
                expected_status = 201 if expected_allowed else 409
                expected_outcome = (
                    "BOOKING_ALLOWED" if expected_allowed else "BOOKING_QUOTA_EXCEEDED"
                )
                actual_correct = response.status_code == expected_status and response.json() == {
                    "outcome": expected_outcome
                }
                correct += int(actual_correct)
                successes += int(response.status_code == 201)
                business_rejections += int(response.status_code == 409)
                other_failures += int(not actual_correct)
            except TimeoutError:
                latencies.append((perf_counter_ns() - started_ns) / 1_000_000_000)
                timeouts += 1
            except Exception:
                latencies.append((perf_counter_ns() - started_ns) / 1_000_000_000)
                other_failures += 1

        started = perf_counter()
        await asyncio.gather(*(submit(payload) for payload in payloads))
        elapsed = perf_counter() - started

    ordered = sorted(latencies)
    p95 = ordered[ceil(0.95 * len(ordered)) - 1]
    peak = app.state.get_peak_in_flight()
    report: dict[str, int | float | bool] = {
        "workload": "synthetic in-process ASGI quota decision; no database or gateway",
        "concurrency_requested": CONCURRENCY,
        "concurrency_observed": peak,
        "warmup_requests": 5,
        "measured_requests": len(latencies),
        "duration_seconds": round(elapsed, 6),
        "throughput_requests_per_second": round(CONCURRENCY / elapsed, 2),
        "successes": successes,
        "business_rejections": business_rejections,
        "timeouts": timeouts,
        "other_failures": other_failures,
        "business_correct_responses": correct,
        "business_correctness": correct == CONCURRENCY,
        "p95_seconds_all_samples": round(p95, 6),
        "p95_target_seconds": REQUEST_TIMEOUT_SECONDS,
        "p95_target_met_by_harness": p95 <= REQUEST_TIMEOUT_SECONDS,
    }
    return report


def main() -> None:
    report = asyncio.run(run())
    print(json.dumps(report, sort_keys=True))
    if not report["business_correctness"] or report["concurrency_observed"] != CONCURRENCY:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
