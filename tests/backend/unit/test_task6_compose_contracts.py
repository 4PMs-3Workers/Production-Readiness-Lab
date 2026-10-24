from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from tests.containers import (
    test_compose_acceptance as compose_acceptance,
)
from tests.containers import (
    test_telemetry_stack as telemetry_stack,
)
from tests.containers.test_compose_acceptance import ComposeAcceptance
from tests.containers.test_telemetry_stack import make_certificate

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "tools/deploy"
INFRA = ROOT / "tools/infra"


def test_observability_overlay_publishes_only_existing_gateway_tls_listener():
    document = yaml.safe_load((DEPLOY / "compose.observability.yaml").read_text())
    services = document["services"]
    published = {
        name: service["ports"] for name, service in services.items() if service.get("ports")
    }
    assert published == {}
    # The base/HTTPS stack owns the only published listener; this opt-in overlay adds none.
    base = yaml.safe_load((DEPLOY / "compose.https.yaml").read_text())
    assert base["services"]["gateway"]["ports"]
    assert services["gateway"]["networks"]["ops_grafana"] == {}
    assert services["grafana"]["networks"] == ["telemetry", "ops_grafana"]
    assert services["local_alert_receiver"]["networks"] == ["telemetry"]
    assert "ports" not in services["local_alert_receiver"]
    assert document["networks"]["telemetry"]["internal"] is True
    assert document["networks"]["ops_grafana"]["internal"] is True


def test_ops_gateway_and_grafana_security_and_file_credentials_are_explicit():
    document = yaml.safe_load((DEPLOY / "compose.observability.yaml").read_text())
    gateway = document["services"]["gateway"]
    grafana = document["services"]["grafana"]
    assert gateway["environment"]["PRL_OPS_HOST"].endswith("}")
    assert gateway["environment"]["PRL_OPS_CIDRS"].endswith("}")
    assert any(
        volume.get("target") == "/etc/nginx/tls/ops.crt"
        for volume in gateway["volumes"]
        if isinstance(volume, dict)
    )
    env = grafana["environment"]
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert env["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert env["GF_SECURITY_COOKIE_SECURE"] == "true"
    assert env["GF_SECURITY_ADMIN_USER__FILE"] == "/run/secrets/grafana-admin-user"
    assert env["GF_SECURITY_ADMIN_PASSWORD__FILE"] == "/run/secrets/grafana-admin-password"
    assert env["GF_SERVER_ROOT_URL"].endswith("}/")
    assert env["GF_LIVE_ALLOWED_ORIGINS"].endswith("}")


def test_observability_runtime_aligns_worker_environment_with_api_and_gateway():
    overlay = yaml.safe_load((DEPLOY / "compose.observability.yaml").read_text())
    worker = overlay["services"]["worker"]["environment"]
    assert worker["ENVIRONMENT"] == "demo"
    assert worker["COOKIE_SECURE"] == "true"


def test_acceptance_worker_progress_uses_only_direct_stdout_not_mixed_stderr(
    monkeypatch: pytest.MonkeyPatch,
):
    record = {
        "event": "heartbeat",
        "worker_id": "a" * 32,
        "phase": "running",
        "database_ok": True,
        "successful_scans": 2,
        "last_successful_scan": "2026-10-24T00:00:00Z",
    }
    stdout = json.dumps(record)
    stderr = "SDK exporter warning without JSON\n"
    calls: list[list[str]] = []

    def direct_logs(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout, stderr)

    stack = object.__new__(ComposeAcceptance)
    stack.compose = lambda *args, **kwargs: subprocess.CompletedProcess(list(args), 0, "b" * 64, "")
    monkeypatch.setattr(compose_acceptance, "invoke", direct_logs)

    output = stack.worker_log_output()

    assert calls == [["docker", "logs", "--tail", "400", "b" * 64]]
    assert output == stdout
    assert compose_acceptance.latest_worker_progress(stdout, require_heartbeat=False) is not None
    assert (
        compose_acceptance.latest_worker_progress(stdout + "\n" + stderr, require_heartbeat=False)
        is None
    )
    unknown = json.dumps(dict(record, event="unrecognized_sdk_event"))
    assert compose_acceptance.latest_worker_progress(unknown, require_heartbeat=False) is None


def test_retention_checkpoint_and_memory_contracts_are_visible_in_source_config():
    overlay = yaml.safe_load((DEPLOY / "compose.observability.yaml").read_text())
    assert overlay["services"]["loki"]["command"] == ["--config.file=/etc/loki/loki.yaml"]
    resources = {
        "collector": 512,
        "prometheus": 512,
        "loki": 768,
        "tempo": 512,
        "grafana": 512,
        "alertmanager": 128,
        "local_alert_receiver": 128,
        "gateway_log_rotate": 128,
    }
    assert {
        name: int(overlay["services"][name]["mem_limit"].removesuffix("m")) for name in resources
    } == resources
    collector = yaml.safe_load((INFRA / "otel-collector.yaml").read_text())
    assert collector["processors"]["memory_limiter"]["limit_mib"] == 384
    assert collector["processors"]["batch"]["send_batch_max_size"] == 64
    assert collector["exporters"]["otlp/tempo"]["sending_queue"]["queue_size"] == 128
    assert collector["exporters"]["otlphttp/loki"]["retry_on_failure"]["max_elapsed_time"] == "30s"
    filelog = collector["receivers"]["filelog/gateway"]
    assert filelog["start_at"] == "beginning"
    assert filelog["storage"] == "file_storage"
    assert "polls_to_archive" not in filelog
    prom = yaml.safe_load((INFRA / "prometheus.yml").read_text())
    assert prom["scrape_configs"][0]["static_configs"][0]["targets"] == ["api:8000"]
    assert any("retention.time=14d" in arg for arg in overlay["services"]["prometheus"]["command"])
    loki = yaml.safe_load((INFRA / "loki.yaml").read_text())
    assert loki["limits_config"]["retention_period"] == "168h"
    attributes = loki["limits_config"]["otlp_config"]["resource_attributes"]
    assert attributes["ignore_defaults"] is True
    assert attributes["attributes_config"] == [
        {
            "action": "index_label",
            "attributes": ["service.name", "deployment.environment.name"],
        }
    ]
    tempo = yaml.safe_load((INFRA / "tempo.yaml").read_text())
    assert tempo["backend_worker"]["compaction"]["block_retention"] == "168h"
    assert "backend_scheduler" not in tempo


def test_prometheus_scrapes_backend_and_collector_self_health_endpoints():
    prom = yaml.safe_load((INFRA / "prometheus.yml").read_text())
    jobs = {job["job_name"]: job for job in prom["scrape_configs"]}
    assert jobs["api"]["static_configs"][0]["targets"] == ["api:8000"]
    assert jobs["collector"]["static_configs"][0]["targets"] == ["collector:8888"]
    assert jobs["application-otel"]["static_configs"][0]["targets"] == ["collector:9464"]
    assert jobs["local-alert-receiver"]["static_configs"][0]["targets"] == [
        "local_alert_receiver:8088"
    ]
    expected_backend_targets = {
        "prometheus-self": "prometheus:9090",
        "loki-self": "loki:3100",
        "tempo-self": "tempo:3200",
        "grafana-self": "grafana:3000",
        "alertmanager-self": "alertmanager:9093",
    }
    for job_name, target in expected_backend_targets.items():
        assert jobs[job_name]["static_configs"][0]["targets"] == [target]
    collector = yaml.safe_load((INFRA / "otel-collector.yaml").read_text())
    readers = collector["service"]["telemetry"]["metrics"]["readers"]
    prometheus = readers[0]["pull"]["exporter"]["prometheus"]
    assert prometheus["host"] == "0.0.0.0"
    assert prometheus["port"] == 8888
    assert collector["service"]["telemetry"]["resource"] == {
        "service.version": None,
        "service.instance.id": None,
    }
    rules = yaml.safe_load((INFRA / "prometheus-rules.yml").read_text())
    assert 'up{job="collector"} == 0' == rules["groups"][0]["rules"][0]["expr"]


def test_filelog_promotes_validated_gateway_metadata_to_loki_resource_labels():
    collector = yaml.safe_load((INFRA / "otel-collector.yaml").read_text())
    filelog = collector["receivers"]["filelog/gateway"]
    moves = {
        (operator.get("from"), operator.get("to"))
        for operator in filelog["operators"]
        if operator.get("type") == "move"
    }
    assert ("attributes.service", 'resource["service.name"]') in moves
    assert (
        "attributes.environment",
        'resource["deployment.environment.name"]',
    ) in moves
    assert collector["service"]["pipelines"]["logs"]["receivers"] == [
        "otlp",
        "filelog/gateway",
    ]


def test_worker_resource_identity_projects_only_allowlisted_resource_dimensions():
    collector = yaml.safe_load((INFRA / "otel-collector.yaml").read_text())
    processor = collector["processors"]["transform/worker_identity"]
    projection = processor["metric_statements"]
    assert projection == [
        {
            "context": "datapoint",
            "statements": [
                'set(datapoint.attributes["service.name"], resource.attributes["service.name"]) '
                'where resource.attributes["service.name"] == "worker"',
                'set(datapoint.attributes["deployment.environment.name"], '
                'resource.attributes["deployment.environment.name"]) '
                'where resource.attributes["service.name"] == "worker" and '
                'resource.attributes["deployment.environment.name"] != nil',
            ],
        }
    ]
    assert processor["error_mode"] == "ignore"
    assert "transform/worker_identity" in collector["service"]["pipelines"]["metrics"]["processors"]
    assert (
        "transform/worker_identity" not in collector["service"]["pipelines"]["logs"]["processors"]
    )
    assert (
        "transform/worker_identity" not in collector["service"]["pipelines"]["traces"]["processors"]
    )
    assert (
        collector["exporters"]["prometheus"]["resource_to_telemetry_conversion"]["enabled"] is False
    )


def test_mysql_and_gateway_lock_metadata_does_not_change_legacy_base_tag_contract():
    lock = json.loads((INFRA / "images.lock.json").read_text(encoding="utf-8-sig"))
    base = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    observability = yaml.safe_load((DEPLOY / "compose.observability.yaml").read_text())
    exact_mysql = (
        "mysql:8.4.11@sha256:" "24989179f58856db763f418010c1169fecc944e3ec1ea7e921572d83eb070f70"
    )
    assert base["services"]["mysql"]["image"] == "mysql:8.4.11"
    assert lock["images"]["mysql"] == exact_mysql
    assert observability["services"]["mysql"]["image"] == exact_mysql
    assert lock["images"]["gateway"] == observability["services"]["gateway_log_rotate"]["image"]


def test_gateway_operations_record_has_only_finite_out_of_scope_fields():
    nginx = (DEPLOY / "nginx/nginx.conf").read_text()
    entrypoint = (DEPLOY / "nginx/gateway-entrypoint.sh").read_text()
    assert '"event":"gateway.operations"' in nginx
    assert '"route":"__unmatched__"' in nginx
    assert '"classification":"out_of_scope"' in nginx
    assert "include /tmp/prl-ops-server.conf;" in nginx
    assert "allow %s;" in entrypoint
    assert "deny all;" in entrypoint
    assert "X-Forwarded-For $remote_addr;" in entrypoint
    assert 'proxy_set_header Forwarded "";' in entrypoint
    assert "set $prl_ops_upstream http://grafana:3000;" in entrypoint
    assert "proxy_pass $prl_ops_upstream;" in entrypoint
    operations_format = nginx[
        nginx.index("log_format prl_operations") : nginx.index("access_log /dev/stdout prl_gateway")
    ]
    assert "request_id" not in operations_format


def test_compose_acceptance_writes_requested_tls_origin_into_runtime_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(ComposeAcceptance, "_source_revision", lambda self: "a" * 40)
    monkeypatch.setattr(ComposeAcceptance, "_free_port", staticmethod(lambda: 23456))
    monkeypatch.setattr(ComposeAcceptance, "_ensure_unique_ownership", lambda self: None)
    monkeypatch.setattr(ComposeAcceptance, "_ensure_local_docker", lambda self: None)
    monkeypatch.setattr(
        ComposeAcceptance, "_project_snapshot", staticmethod(lambda project: ((), ()))
    )
    monkeypatch.setattr(ComposeAcceptance, "_ensure_gateway_subnet_available", lambda self: None)

    stack = ComposeAcceptance(tmp_path, origin_scheme="https", origin_host="app.example.invalid")
    runtime_env = Path(stack.env["PRL_RUNTIME_ENV_FILE"]).read_text(encoding="utf-8")

    assert "APPLICATION_ORIGIN=https://app.example.invalid:23456\n" in runtime_env


def test_tls_certificates_for_app_and_ops_hosts_keep_distinct_files(tmp_path: Path):
    app_cert, _app_key = make_certificate(tmp_path, "app.example.invalid")
    app_certificate = app_cert.read_bytes()
    ops_cert, _ops_key = make_certificate(tmp_path, "ops.example.invalid")

    assert app_cert != ops_cert
    assert app_cert.read_bytes() == app_certificate


def test_collector_image_creates_storage_as_nonroot_from_owned_context():
    dockerfile = (INFRA / "collector.Dockerfile").read_text(encoding="utf-8")
    lock = json.loads((INFRA / "images.lock.json").read_text(encoding="utf-8-sig"))
    storage_marker = INFRA / "collector-file-storage/.keep"

    assert dockerfile.splitlines()[0] == f"FROM {lock['images']['collector']}"
    assert "USER 0" not in dockerfile
    assert (
        "COPY --chown=10001:10001 tools/infra/collector-file-storage/ "
        "/var/lib/otelcol/file_storage/"
    ) in dockerfile
    assert storage_marker.is_file()
    assert storage_marker.read_text(encoding="utf-8").strip()


def test_signal_diagnostic_summaries_preserve_sink_status_without_exposing_ids():
    request_id = "a" * 32
    trace_id = "b" * 32
    release = "c" * 40
    loki = telemetry_stack.summarize_loki_signal(
        200,
        {
            "status": "success",
            "data": {
                "result": [
                    {
                        "stream": {
                            "service_name": "gateway",
                            "deployment_environment_name": "demo",
                            "request_id": request_id,
                            "trace_id": trace_id,
                            "release": release,
                            "event": "gateway.request",
                        },
                        "values": [
                            [
                                "1",
                                json.dumps({"request_id": request_id, "event": "gateway.request"}),
                                {"trace_id": trace_id, "release": release},
                            ]
                        ],
                    }
                ]
            },
        },
        request_id=request_id,
        trace_id=trace_id,
        expected_release=release,
    )
    tempo = telemetry_stack.summarize_tempo_signal(
        200,
        {
            "batches": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "api"}},
                            {
                                "key": "deployment.environment.name",
                                "value": {"stringValue": "demo"},
                            },
                            {"key": "service.version", "value": {"stringValue": release}},
                        ]
                    },
                    "scopeSpans": [{"spans": [{"traceId": trace_id}]}],
                }
            ]
        },
        trace_id,
        expected_release=release,
    )

    serialized = json.dumps({"loki": loki, "tempo": tempo}, sort_keys=True)
    assert request_id not in serialized
    assert trace_id not in serialized
    assert release not in serialized
    assert loki["http_status"] == 200
    assert loki["stream_count"] == 1
    assert loki["entry_count"] == 1
    assert loki["query_result_resource_identity"] == [
        {"service_name": "gateway", "deployment_environment_name": "demo"}
    ]
    assert loki["request_id_match"] is True
    assert loki["trace_id_match"] is True
    assert loki["release_match"] is True
    assert loki["failure_category"] is None
    assert "request_id_indexed" not in loki
    assert "request_id" in loki["structured_field_keys"]
    assert tempo["http_status"] == 200
    assert tempo["trace_id_hex_match"] is True
    assert tempo["trace_id_field_count"] == 1
    assert tempo["resource_services"] == ["api"]
    assert tempo["resource_environments"] == ["demo"]
    assert tempo["resource_release_match"] is True
    encoded_trace = base64.b64encode(bytes.fromhex(trace_id)).decode("ascii")
    tempo_base64 = telemetry_stack.summarize_tempo_signal(
        200, {"batches": [{"scopeSpans": [{"spans": [{"traceId": encoded_trace}]}]}]}, trace_id
    )
    tempo_decoy = telemetry_stack.summarize_tempo_signal(
        200,
        {
            "batches": [
                {
                    "spans": [
                        {
                            "attributes": [{"key": "traceId", "value": {"stringValue": trace_id}}],
                            "links": [{"traceId": trace_id}],
                        }
                    ]
                }
            ]
        },
        trace_id,
    )
    tempo_malformed = telemetry_stack.summarize_tempo_signal(200, {"batches": []}, trace_id)
    assert tempo_base64["trace_id_base64_match"] is True
    assert tempo_decoy["trace_id_hex_match"] is False
    assert tempo_decoy["failure_category"] == "trace_id_field_missing"
    assert tempo_malformed["failure_category"] == "trace_id_field_missing"


def test_loki_index_summary_uses_series_endpoint_and_keeps_ids_private():
    request_id = "d" * 32
    indexed_labels = {
        "service_name": "gateway",
        "deployment_environment_name": "demo",
    }
    finite = telemetry_stack.summarize_loki_index_series(
        200,
        {"status": "success", "data": [indexed_labels]},
        expected_request_id=request_id,
    )
    high_cardinality = telemetry_stack.summarize_loki_index_series(
        200,
        {"status": "success", "data": [dict(indexed_labels, request_id=request_id)]},
        expected_request_id=request_id,
    )
    empty = telemetry_stack.summarize_loki_index_series(
        200,
        {"status": "success", "data": []},
        expected_request_id=request_id,
    )

    serialized = json.dumps({"finite": finite, "high_cardinality": high_cardinality})
    assert request_id not in serialized
    assert finite["series_count"] == 1
    assert finite["index_schema_ok"] is True
    assert finite["request_id_indexed"] is False
    assert finite["failure_category"] is None
    assert high_cardinality["request_id_indexed"] is True
    assert high_cardinality["index_schema_ok"] is False
    assert high_cardinality["failure_category"] == "index_schema"
    assert empty["index_schema_ok"] is False
    assert empty["failure_category"] == "empty_series"


def test_loki_index_observation_calls_series_api_with_finite_gateway_selector():
    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    seen = {}

    def observe(url):
        seen["url"] = url
        return {
            "http_status": 200,
            "body": {
                "status": "success",
                "data": [
                    {
                        "service_name": "gateway",
                        "deployment_environment_name": "demo",
                    }
                ],
            },
            "json_valid": True,
            "truncated": False,
            "error_category": None,
        }

    runtime.api_json_observation = observe
    summary = runtime.observe_loki_index_series(expected_request_id="e" * 32)

    parsed = urlsplit(seen["url"])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "loki:3100"
    assert parsed.path == "/loki/api/v1/series"
    assert query["match[]"] == ['{service_name="gateway",deployment_environment_name="demo"}']
    assert set(query) == {"match[]", "start", "end"}
    assert int(query["start"][0]) < int(query["end"][0])
    assert summary["http_status"] == 200
    assert summary["index_schema_ok"] is True
    assert summary["failure_category"] is None


def test_pre_cleanup_snapshot_keeps_loki_query_index_and_tempo_independent():
    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.stack = SimpleNamespace(expected_sha="a" * 40)

    def unavailable(_service):
        raise telemetry_stack.AcceptanceFailure("fixture only")

    runtime.inspect_service = unavailable
    runtime._safe_collector_counter_snapshot = lambda: {"available": False}
    sinks = {
        "loki_query": {"failure_category": "no_event"},
        "loki_index_series": {"failure_category": None, "series_count": 1},
        "tempo": {"failure_category": "not_found"},
    }

    diagnostic = runtime.signal_boundary_diagnostic(
        route_status=None,
        request_id="",
        trace_id="",
        sink_observations=sinks,
    )

    assert diagnostic["route"]["http_status"] is None
    assert diagnostic["loki_query"] == sinks["loki_query"]
    assert diagnostic["loki_index_series"] == sinks["loki_index_series"]
    assert diagnostic["tempo"] == sinks["tempo"]
    assert all(
        service["inspection_available"] is False for service in diagnostic["services"].values()
    )
