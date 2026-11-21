"""Bounded aggregation for the existing gateway classification contract."""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

_KNOWN_CLASSIFICATIONS = frozenset({"success", "service_failure", "excluded", "out_of_scope"})
_AVAILABILITY_TARGET = 0.995
_FAST_SUCCESS_TARGET = 0.95
_FAST_SUCCESS_SECONDS = 0.5


@dataclass(frozen=True)
class GatewayEvent:
    route: str
    method: str
    status: int
    duration_seconds: float
    classification: str


@dataclass(frozen=True)
class RatioResult:
    ratio: float | None
    numerator: int
    denominator: int
    target: float
    meets_target: bool | None
    status: Literal["measured", "insufficient_evidence"]


@dataclass(frozen=True)
class SliSummary:
    window_seconds: float
    counts: dict[str, int]
    availability: RatioResult
    fast_success: RatioResult
    per_route: dict[str, dict[str, object]]
    request_rate_per_second: float
    generator_saturated: bool
    telemetry_complete: bool
    slo_period_claimed: bool = False


def summarize_gateway_events(
    events: list[GatewayEvent],
    *,
    duration_seconds: float,
    minimum_samples: int = 20,
    telemetry_complete: bool = True,
    generator_saturated: bool = False,
) -> SliSummary:
    """Calculate SLI ratios from logged classifications, never raw HTTP status alone."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be a positive finite number")
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    count = Counter(
        event.classification if event.classification in _KNOWN_CLASSIFICATIONS else "unknown"
        for event in events
    )
    for classification in _KNOWN_CLASSIFICATIONS | {"unknown"}:
        count.setdefault(classification, 0)
    complete = telemetry_complete and count["unknown"] == 0
    success = count["success"]
    service_failure = count["service_failure"]
    availability_denominator = success + service_failure
    availability = _ratio(
        success,
        availability_denominator,
        _AVAILABILITY_TARGET,
        minimum_samples,
        complete and not generator_saturated,
    )
    fast_success_count = sum(
        event.classification == "success" and event.duration_seconds <= _FAST_SUCCESS_SECONDS
        for event in events
    )
    fast_success = _ratio(
        fast_success_count,
        success,
        _FAST_SUCCESS_TARGET,
        minimum_samples,
        complete and not generator_saturated,
    )
    routes: dict[str, list[GatewayEvent]] = defaultdict(list)
    for event in events:
        routes[event.route].append(event)
    per_route = {
        route: _route_summary(route_events) for route, route_events in sorted(routes.items())
    }
    return SliSummary(
        window_seconds=duration_seconds,
        counts={
            key: count[key]
            for key in ("success", "service_failure", "excluded", "unknown", "out_of_scope")
        },
        availability=availability,
        fast_success=fast_success,
        per_route=per_route,
        request_rate_per_second=len(events) / duration_seconds,
        generator_saturated=generator_saturated,
        telemetry_complete=complete,
    )


def _ratio(
    numerator: int,
    denominator: int,
    target: float,
    minimum_samples: int,
    evidence_usable: bool,
) -> RatioResult:
    if not evidence_usable or denominator < minimum_samples:
        return RatioResult(None, numerator, denominator, target, None, "insufficient_evidence")
    ratio = numerator / denominator
    return RatioResult(ratio, numerator, denominator, target, ratio >= target, "measured")


def _route_summary(events: list[GatewayEvent]) -> dict[str, object]:
    latencies = sorted(event.duration_seconds for event in events)
    counts = Counter(
        event.classification if event.classification in _KNOWN_CLASSIFICATIONS else "unknown"
        for event in events
    )
    status_classes = Counter(f"{event.status // 100}xx" for event in events)
    return {
        "requests": len(events),
        "classifications": {key: counts[key] for key in sorted(counts)},
        "status_classes": dict(sorted(status_classes.items())),
        "latency_seconds": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": latencies[-1],
        },
    }


def _percentile(values: list[float], quantile: float) -> float:
    return values[max(0, math.ceil(quantile * len(values)) - 1)]
