from typing import cast

from tools.load.metrics import GatewayEvent, summarize_gateway_events


def event(route, classification, duration):
    return GatewayEvent(
        route=route,
        method="GET",
        status=200 if classification == "success" else 409,
        duration_seconds=duration,
        classification=classification,
    )


def test_sli_uses_gateway_classification_and_published_denominator():
    result = summarize_gateway_events(
        [
            event("/api/v1/appointments", "success", 0.5),
            event("/api/v1/appointments", "success", 0.501),
            event("/api/v1/appointments", "service_failure", 0.7),
            event("/api/v1/appointments", "excluded", 0.01),
        ],
        duration_seconds=10,
        minimum_samples=1,
    )

    assert result.availability.ratio == 2 / 3
    assert result.fast_success.ratio == 0.5
    assert result.counts == {
        "success": 2,
        "service_failure": 1,
        "excluded": 1,
        "unknown": 0,
        "out_of_scope": 0,
    }
    route = cast(dict[str, object], result.per_route["/api/v1/appointments"])
    classifications = cast(dict[str, int], route["classifications"])
    assert classifications["service_failure"] == 1


def test_empty_window_unknown_classification_and_saturation_are_insufficient():
    empty = summarize_gateway_events([], duration_seconds=60)
    unknown = summarize_gateway_events(
        [event("/api/v1/appointments", "unrecognized", 0.2)], duration_seconds=60
    )
    saturated = summarize_gateway_events(
        [event("/api/v1/appointments", "success", 0.2)],
        duration_seconds=60,
        generator_saturated=True,
    )

    assert empty.availability.status == "insufficient_evidence"
    assert empty.fast_success.status == "insufficient_evidence"
    assert unknown.availability.status == "insufficient_evidence"
    assert saturated.fast_success.status == "insufficient_evidence"


def test_thresholds_are_inclusive_and_no_short_window_is_called_seven_day_slo():
    events = [event("/api/v1/appointments", "success", 0.5) for _ in range(200)] + [
        event("/api/v1/appointments", "service_failure", 0.2)
    ]
    result = summarize_gateway_events(events, duration_seconds=600, minimum_samples=20)

    assert result.availability.meets_target is True
    assert result.fast_success.meets_target is True
    assert result.window_seconds == 600
    assert result.slo_period_claimed is False


def test_missing_telemetry_never_becomes_a_zero_failure_success():
    result = summarize_gateway_events(
        [event("/api/v1/appointments", "success", 0.1)],
        duration_seconds=10,
        telemetry_complete=False,
        minimum_samples=1,
    )

    assert result.availability.ratio is None
    assert result.fast_success.status == "insufficient_evidence"
