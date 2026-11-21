from app.observability.metrics import FAILURE_REASONS, TelemetryMetrics


def test_database_failure_counters_are_exported_before_first_failure():
    metrics = TelemetryMetrics()

    rendered = metrics.render()

    for reason in FAILURE_REASONS:
        assert f'prl_db_failures_total{{reason="{reason}"}} 0.0' in rendered
