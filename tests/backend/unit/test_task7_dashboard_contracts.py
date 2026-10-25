from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "tools/infra"
LOKI_RULES = INFRA / "loki-rules/fake/gateway-sli.yml"


def _loki_rules():
    assert LOKI_RULES.is_file(), "Task 7 gateway SLI ruler rules are not provisioned"
    return yaml.safe_load(LOKI_RULES.read_text(encoding="utf-8"))


def test_gateway_sli_ratio_rules_have_truthful_denominators_and_bounded_fields():
    document = _loki_rules()
    rules = {
        rule.get("record"): rule["expr"]
        for group in document["groups"]
        for rule in group["rules"]
        if "record" in rule
    }

    availability = rules["prl:gateway_availability:ratio5m"]
    fast_success = rules["prl:gateway_fast_success:ratio5m"]

    assert 'event="gateway.request"' in availability
    assert 'classification=~"success|service_failure"' in availability
    assert "or on()" in availability and "0 *" in availability
    assert "> 0" in availability
    assert "vector(0)" not in availability and "vector(1)" not in availability

    assert 'classification="success"' in fast_success
    assert "duration_seconds <= 0.500" in fast_success
    assert "or on()" in fast_success and "0 *" in fast_success
    assert "> 0" in fast_success
    assert "vector(0)" not in fast_success and "vector(1)" not in fast_success
    assert "request_id" not in availability + fast_success
    assert "trace_id" not in availability + fast_success
    assert "duration_seconds" not in availability
    assert "| keep event, classification, route" in fast_success


def test_gateway_alerts_have_required_windows_and_runbook_annotations():
    document = _loki_rules()
    alerts = {
        rule["alert"]: (group, rule)
        for group in document["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }
    expected_for = {
        "GatewayFastSuccessBelowSLO": "2m",
        "GatewayPipelineHeartbeatMissing": "30s",
    }
    assert expected_for.keys() <= alerts.keys()
    for name, expected in expected_for.items():
        group, rule = alerts[name]
        assert group["interval"] == "15s"
        assert rule["for"] == expected
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert rule["annotations"].get("summary")
        assert rule["annotations"].get("runbook_url", "").startswith("https://")


def test_every_alert_runbook_url_resolves_to_a_real_heading():
    runbook = ROOT / "docs/tasks/observability-runbook.md"
    headings = {
        re.sub(r"-+", "-", re.sub(r"[^a-z0-9 _-]", "", match.group(1).lower()))
        .strip()
        .replace(" ", "-")
        for match in re.finditer(
            r"^#{1,6}\s+(.+?)\s*#*\s*$", runbook.read_text(encoding="utf-8"), re.M
        )
    }
    documents = (
        yaml.safe_load((INFRA / "prometheus-rules.yml").read_text(encoding="utf-8")),
        _loki_rules(),
    )
    alerts = [
        rule
        for document in documents
        for group in document["groups"]
        for rule in group["rules"]
        if "alert" in rule
    ]
    assert alerts
    for rule in alerts:
        url = rule["annotations"].get("runbook_url", "")
        parsed = urlsplit(url)
        assert parsed.path.endswith("/docs/tasks/observability-runbook.md"), rule["alert"]
        assert parsed.fragment in headings, (rule["alert"], parsed.fragment, headings)


def test_five_grafana_dashboards_are_provisioned_with_nonhealthy_empty_state():
    provider_path = INFRA / "grafana-dashboard-providers.yml"
    assert provider_path.is_file(), "Grafana dashboard file provisioning is not configured"
    provider = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
    dashboard_paths = sorted((INFRA / "grafana/dashboards").glob("*.json"))
    dashboards = {
        json.loads(path.read_text(encoding="utf-8"))["uid"]: (
            path,
            json.loads(path.read_text(encoding="utf-8")),
        )
        for path in dashboard_paths
    }

    assert set(dashboards) == {
        "prl-gateway",
        "prl-api-errors",
        "prl-db-pool",
        "prl-worker-progress",
        "prl-telemetry-loss",
    }
    assert provider["providers"][0]["options"]["path"] == "/etc/grafana/dashboards"
    assert provider["providers"][0]["allowUiUpdates"] is False
    for _path, dashboard in dashboards.values():
        assert dashboard.get("annotations", {}).get("list")
        for panel in dashboard.get("panels", []):
            for target in panel.get("targets", []):
                expression = target.get("expr", "")
                for selector in re.findall(r"\{([^{}]*)\}", expression):
                    assert not any(label in selector for label in ("request_id=", "trace_id="))


def test_gateway_dashboard_runs_the_native_loki_sli_and_excludes_healthchecks():
    dashboard = json.loads((INFRA / "grafana/dashboards/gateway.json").read_text(encoding="utf-8"))
    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    for panel_id in (2, 3, 4):
        panel = panels[panel_id]
        assert panel["datasource"] == {"type": "loki", "uid": "prl-loki"}
        assert panel["targets"][0]["datasource"] == panel["datasource"]
    availability = panels[2]["targets"][0]["expr"]
    fast_success = panels[3]["targets"][0]["expr"]
    healthcheck = panels[4]["targets"][0]["expr"]
    assert 'event="gateway.request"' in availability
    assert 'classification=~"success|service_failure"' in availability
    assert "or on()" in availability and "> 0" in availability
    assert "duration_seconds <= 0.500" in fast_success
    assert "or on()" in fast_success and "> 0" in fast_success
    assert 'event="gateway.healthcheck"' in healthcheck
    assert all("prl:" not in query for query in (availability, fast_success, healthcheck))
    assert "insufficient data" in panels[2]["description"].lower()
    assert "unknown" in panels[3]["description"].lower()


def test_worker_dashboard_runtime_gauges_use_observed_worker_scope():
    for name in ("db-pool.json", "telemetry-loss.json"):
        dashboard = json.loads((INFRA / "grafana/dashboards" / name).read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                expression = target.get("expr", "")
                if 'job="application-otel"' in expression and 'service_name="worker"' in expression:
                    assert 'deployment_environment_name="demo"' in expression
    telemetry = json.loads(
        (INFRA / "grafana/dashboards/telemetry-loss.json").read_text(encoding="utf-8")
    )
    logs = next(panel for panel in telemetry["panels"] if panel["type"] == "logs")
    assert '|= ""' not in logs["targets"][0]["expr"]


def test_grafana_trace_drilldowns_use_structured_metadata_and_existing_tempo_link():
    datasource_config = yaml.safe_load(
        (INFRA / "grafana-datasources.yml").read_text(encoding="utf-8")
    )
    loki = next(item for item in datasource_config["datasources"] if item["uid"] == "prl-loki")
    derived = loki["jsonData"]["derivedFields"]
    assert derived == [
        {
            "name": "TraceID",
            "matcherType": "label",
            "matcherRegex": "^trace_id$",
            "datasourceUid": "prl-tempo",
            "url": "$${__value.raw}",
            "urlDisplayLabel": "View trace",
        }
    ]
    tempo = next(item for item in datasource_config["datasources"] if item["uid"] == "prl-tempo")
    assert tempo["jsonData"]["tracesToLogsV2"]["filterByTraceID"] is True
    assert tempo["jsonData"]["tracesToLogsV2"]["datasourceUid"] == "prl-loki"


def test_compose_mounts_and_operator_identity_cover_every_dashboard_and_ruler_input():
    compose = yaml.safe_load(
        (ROOT / "tools/deploy/compose.observability.yaml").read_text(encoding="utf-8")
    )
    loki_mounts = compose["services"]["loki"]["volumes"]
    grafana_mounts = compose["services"]["grafana"]["volumes"]
    assert "../infra/loki-rules:/etc/loki/rules:ro" in loki_mounts
    assert "../infra/grafana/dashboards:/etc/grafana/dashboards:ro" in grafana_mounts
    assert any("grafana-dashboard-providers.yml" in str(item) for item in grafana_mounts)

    tree = ast.parse((ROOT / "tools/deploy/demo.py").read_text(encoding="utf-8"))
    config_node = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "CONFIG_OBSERVABILITY"
            for target in node.targets
        )
    )
    configured = set(ast.literal_eval(config_node))
    required = {
        "tools/infra/grafana-datasources.yml",
        "tools/infra/grafana-dashboard-providers.yml",
        "tools/infra/loki-rules/fake/gateway-sli.yml",
        "tools/infra/prometheus-rules.yml",
        "tools/infra/prometheus-rule-tests.yml",
        "tools/infra/grafana/dashboards/gateway.json",
        "tools/infra/grafana/dashboards/api-errors.json",
        "tools/infra/grafana/dashboards/db-pool.json",
        "tools/infra/grafana/dashboards/worker-progress.json",
        "tools/infra/grafana/dashboards/telemetry-loss.json",
    }
    assert required <= configured


def test_prometheus_alert_rules_have_required_windows_and_all_annotations():
    rules_path = INFRA / "prometheus-rules.yml"
    document = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    alerts = {
        rule["alert"]: (group, rule)
        for group in document["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }
    expected_for = {
        "ApiHttpErrorRateWarning": "2m",
        "ApiHttpErrorRateCritical": "1m",
        "DatabasePoolOrLockTimeout": "30s",
        "TelemetryTargetDown": "30s",
        "ApiTelemetryFamilyMissing": "30s",
        "WorkerHeartbeatMissing": "30s",
        "WorkerProgressStale": "30s",
        "WorkerBacklogSnapshotStale": "30s",
        "WorkerBacklogOverdue": "1m",
    }

    assert expected_for.keys() <= alerts.keys()
    for name, expected in expected_for.items():
        group, rule = alerts[name]
        assert group["interval"] == "15s"
        assert rule["for"] == expected
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert rule["annotations"].get("summary")
        assert rule["annotations"].get("runbook_url", "").startswith("https://")
    heartbeat_stale = alerts["WorkerHeartbeatStale"][1]
    assert "for" not in heartbeat_stale
    assert "time() - prl:worker:heartbeat_current > 45" in heartbeat_stale["expr"]
    heartbeat_missing = alerts["WorkerHeartbeatMissing"][1]
    assert heartbeat_missing["for"] == "30s"
    assert "prl:worker:heartbeat_current" in heartbeat_missing["expr"]
    target_down_expr = alerts["TelemetryTargetDown"][1]["expr"]
    for job in (
        "api",
        "collector",
        "application-otel",
        "local-alert-receiver",
        "prometheus-self",
        "loki-self",
        "tempo-self",
        "grafana-self",
        "alertmanager-self",
    ):
        assert job in target_down_expr
    loss_group, loss = alerts["TelemetryLossDetected"]
    assert loss_group["interval"] == "15s"
    assert "for" not in loss
    assert "increase(prl_telemetry_dropped_total[5m]) > 0" in loss["expr"]
    assert "increase(prl_telemetry_failed_total[5m]) > 0" in loss["expr"]

    assert (
        'prl_telemetry_queue_capacity{service="api",signal="stdout"}'
        in alerts["ApiTelemetryFamilyMissing"][1]["expr"]
    )
    backlog_expr = next(
        rule["expr"]
        for group in document["groups"]
        for rule in group["rules"]
        if rule.get("record") == "prl:worker:backlog_max_fresh"
    )
    assert backlog_expr.lstrip().startswith("max by(service_name, deployment_environment_name) (")
    assert "time() - prl_worker_last_successful_scan_timestamp_seconds" in backlog_expr
    assert "<= 90" in backlog_expr
    oldest_expr = next(
        rule["expr"]
        for group in document["groups"]
        for rule in group["rules"]
        if rule.get("record") == "prl:worker:backlog_oldest_overdue_max_fresh"
    )
    assert "prl_worker_backlog_count_ratio > 0" in oldest_expr


def test_prometheus_worker_inventory_joins_actual_service_environment_and_slot_labels():
    config = yaml.safe_load((INFRA / "prometheus.yml").read_text(encoding="utf-8"))
    receiver = next(
        job for job in config["scrape_configs"] if job["job_name"] == "local-alert-receiver"
    )
    relabel_text = json.dumps(receiver["metric_relabel_configs"], sort_keys=True)
    assert "prl_expected_worker_slot" in relabel_text
    assert "service_name" in relabel_text and "deployment_environment_name" in relabel_text
    assert "worker_slot" in relabel_text and "[1-8]" in relabel_text


def test_prometheus_rule_tests_cover_thresholds_restart_and_backlog_freshness():
    path = INFRA / "prometheus-rule-tests.yml"
    assert path.is_file(), "Prometheus rule tests are not provisioned"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["evaluation_interval"] == "15s"
    assert len(document["tests"]) >= 8
    text = path.read_text(encoding="utf-8")
    assert "Same-slot restart" in text
    assert "Missing first progress" in text
    assert "Stale high backlog is excluded before max" in text
    assert "prl_worker_last_successful_scan_timestamp_seconds" in text
    assert "prl_worker_started_timestamp_seconds" in text
    assert "A fresh empty scan excludes a retained oldest-overdue value before max" in text


def test_same_slot_restart_fixture_keeps_cached_backlog_unknown_until_new_scan():
    document = yaml.safe_load((INFRA / "prometheus-rule-tests.yml").read_text(encoding="utf-8"))
    restart = next(
        test
        for test in document["tests"]
        if any(
            "prl_worker_started_timestamp_seconds" in series["series"]
            and series["values"] == "25+0x4"
            for series in test.get("input_series", [])
        )
    )
    metric_names = {series["series"].split("{", 1)[0] for series in restart["input_series"]}
    assert "prl_worker_backlog_count_ratio" in metric_names
    assert "prl_worker_oldest_overdue_seconds" in metric_names
    expr_tests = {
        (test["expr"], test["eval_time"]): test["exp_samples"]
        for test in restart.get("promql_expr_test", [])
    }
    for expression in (
        "prl:worker:backlog_max_fresh",
        "prl:worker:backlog_oldest_overdue_max_fresh",
    ):
        assert expr_tests[(expression, "30s")] == []
    assert expr_tests[("prl:worker:backlog_max_fresh", "45s")] == [
        {
            "labels": '{__name__="prl:worker:backlog_max_fresh",service_name="worker",deployment_environment_name="demo"}',
            "value": 4,
        }
    ]
    assert expr_tests[("prl:worker:backlog_oldest_overdue_max_fresh", "45s")] == [
        {
            "labels": '{__name__="prl:worker:backlog_oldest_overdue_max_fresh",service_name="worker",deployment_environment_name="demo"}',
            "value": 15,
        }
    ]


def test_fullstack_collector_outage_covers_pipeline_missing_and_genuine_recovery():
    source = (ROOT / "tests/containers/test_telemetry_stack.py").read_text(encoding="utf-8")
    start = source.index('failure_context["stage"] = "collector_stop_api_readiness"')
    end = source.index('failure_context["stage"] = "worker_signal_contract"', start)
    outage = source[start:end]
    for required in (
        "GatewayPipelineHeartbeatMissing",
        "absent_over_time",
        "api_readiness",
        "genuine_gateway_healthchecks_after_collector_recovery",
        "start_ns=collector_recovered_ns",
        "latest_event_unix_ns",
        "collector_outage_elapsed_seconds",
        "collector_pipeline_missing_firing",
        "collector_pipeline_missing_resolved",
    ):
        assert required in outage
    assert "push_gateway_json_logs" not in outage


def test_alertmanager_timing_and_resolved_delivery_are_explicit():
    config = yaml.safe_load((INFRA / "alertmanager.yml").read_text(encoding="utf-8"))
    route = config["route"]
    assert route["group_wait"] == "5s"
    assert route["group_interval"] == "15s"
    assert route["repeat_interval"] == "30m"
    assert all(
        item.get("send_resolved") is True
        for receiver in config["receivers"]
        for item in receiver.get("webhook_configs", [])
    )


def test_compose_probe_escapes_grafana_macro_interpolation():
    source = (ROOT / "tests/containers/test_telemetry_stack.py").read_text(encoding="utf-8")
    assert 'field.get("url") == "$" + "{__value.raw}"' in source


def test_telemetry_loss_covers_all_collector_signal_families():
    rules = yaml.safe_load((INFRA / "prometheus-rules.yml").read_text(encoding="utf-8"))
    alerts = {
        rule["alert"]: rule
        for group in rules["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }
    loss = alerts["TelemetryLossDetected"]
    assert "for" not in loss
    collector_metrics = (
        "otelcol_receiver_refused_log_records",
        "otelcol_receiver_refused_metric_points",
        "otelcol_receiver_refused_spans",
        "otelcol_receiver_failed_log_records",
        "otelcol_receiver_failed_metric_points",
        "otelcol_receiver_failed_spans",
        "otelcol_exporter_enqueue_failed_log_records",
        "otelcol_exporter_enqueue_failed_metric_points",
        "otelcol_exporter_enqueue_failed_spans",
        "otelcol_exporter_send_failed_log_records",
        "otelcol_exporter_send_failed_metric_points",
        "otelcol_exporter_send_failed_spans",
    )
    for metric in collector_metrics:
        assert metric in loss["expr"]
    assert loss["expr"].count('{job="collector"}[5m]') == len(collector_metrics)

    dashboard = json.loads(
        (INFRA / "grafana/dashboards/telemetry-loss.json").read_text(encoding="utf-8")
    )
    expressions = " ".join(
        target.get("expr", "")
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    for metric in collector_metrics:
        assert metric in expressions
    tests = (INFRA / "prometheus-rule-tests.yml").read_text(encoding="utf-8")
    assert "Collector refuses metric points" in tests
    assert "Collector enqueue failure for metric points" in tests
    assert "Collector export failure for metric points" in tests


def test_gateway_slo_threshold_uses_boolean_gating():
    document = _loki_rules()
    rule = next(
        rule
        for group in document["groups"]
        for rule in group["rules"]
        if rule.get("alert") == "GatewayFastSuccessBelowSLO"
    )
    assert "and on()" in rule["expr"]
    assert "\n          *\n" not in rule["expr"]


def test_gateway_query_runtime_covers_zero_fast_and_minimum_count_truth_table():
    source = (ROOT / "tests/containers/test_telemetry_stack.py").read_text(encoding="utf-8")
    for contract in (
        "all_slow_successful_requests",
        "all_slow_success_ratio",
        "all_slow_alert_predicate",
        "all_slow_alert_present = all_slow_alert_predicate is not None",
        "all_errors_alert_predicate is not None",
        "sub20_alert_suppressed = sub20_alert_predicate is None",
        "recovered_alert_suppressed = recovered_alert_predicate is None",
    ):
        assert contract in source


def test_fullstack_alert_recovery_fixture_uses_observed_sli_counts():
    source = (ROOT / "tests/containers/test_telemetry_stack.py").read_text(encoding="utf-8")
    start = source.index("def test_full_observability_stack_runtime_acceptance")
    firing = source.index("def gateway_rule_firing_received", start)
    resolved = source.index("def gateway_rule_resolved_received", firing)
    recovery = source[firing:resolved]
    assert "healthy_needed = math.floor" in recovery
    assert "success_count" in recovery and "fast_count" in recovery
    assert "recovery_query_validation" in recovery
    assert "range(500)" not in recovery


def test_collector_inventory_records_observed_finite_source_labels():
    source = (ROOT / "tests/containers/test_telemetry_stack.py").read_text(encoding="utf-8")
    start = source.index("def worker_dashboard_metric_inventory")
    end = source.index("def api_json_observation", start)
    inventory = source[start:end]
    assert "collector_loss_counter_sample_labels" in inventory
    assert 'sample.get("metric", {}).get("job") == "collector"' in inventory
    assert '"receiver" if metric_name.startswith("otelcol_receiver_") else "exporter"' in inventory
    assert '{"request_id", "run_id", "trace_id", "span_id"}' in inventory
