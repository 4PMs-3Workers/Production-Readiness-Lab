from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
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
    alerts = {
        rule["alert"]: rule
        for group in rules["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }
    assert "TelemetryCollectorUnavailable" not in alerts
    target_down = alerts["TelemetryTargetDown"]["expr"]
    assert "collector" in target_down
    assert "prometheus-self" in target_down


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


def test_ops_probe_failure_summary_classifies_without_exposing_output():
    stdout = "TASK6_PROBE:admin_password=synthetic-secret"
    stderr = "Traceback (most recent call last): ConnectionRefusedError: user=admin"

    summary = telemetry_stack.safe_ops_probe_failure_summary(17, stdout, stderr)

    assert summary == {
        "exit_code": 17,
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "probe_result_marker_present": True,
        "failure_categories": ["connection_refused", "python_traceback"],
    }
    serialized = json.dumps(summary)
    assert "synthetic-secret" not in serialized
    assert "admin" not in serialized


def test_ops_probe_result_summary_keeps_http_shape_and_discards_response_values():
    payload = {
        "health": 200,
        "unauth_org": 401,
        "admin_org": 200,
        "datasource_status": 200,
        "datasource_uids": ["prl-loki", "synthetic-admin-secret", {"uid": "hidden"}],
        "datasource_unknown_uid_count": 2,
        "datasource_malformed_item_count": 1,
        "stage_observations": {
            "health": {"http_status": 200, "json_kind": "dict"},
            "datasources": {
                "http_status": 200,
                "json_kind": "dict",
                "body": {"admin_password": "synthetic-secret"},
            },
        },
        "last_stage": "datasources",
        "tls_exception_category": None,
        "failure_categories": ["connection_refused", "synthetic-secret"],
    }

    summary = telemetry_stack.safe_ops_probe_result_summary(payload)

    assert summary["stage_observations"] == {
        "health": {"http_status": 200, "json_kind": "dict"},
        "datasources": {"http_status": 200, "json_kind": "dict"},
    }
    assert summary["last_stage"] == "datasources"
    assert summary["datasource_uids"] == ["prl-loki"]
    assert summary["datasource_unknown_uid_count"] == 2
    assert summary["datasource_malformed_item_count"] == 1
    assert summary["failure_categories"] == ["connection_refused"]
    serialized = json.dumps(summary)
    assert "synthetic-secret" not in serialized
    assert "admin_password" not in serialized


def test_run_probe_records_only_allowlisted_stage_details(capsys):
    payload = {
        "health": 200,
        "unauth_org": 401,
        "admin_org": 200,
        "datasource_status": 200,
        "datasource_uids": ["prl-loki", "prl-prometheus", "prl-tempo"],
        "stage_observations": {
            "datasources": {"http_status": 200, "json_kind": "list", "list_count": 3},
        },
        "last_stage": "datasources",
        "tls_exception_category": None,
        "failure_categories": [],
        "admin_password": "synthetic-secret",
    }
    runtime: Any = SimpleNamespace(
        compose=lambda *_args: SimpleNamespace(
            returncode=0,
            stdout="TASK6_PROBE:" + json.dumps(payload),
            stderr="",
        ),
        last_operations_probe={"available": False},
    )

    result = telemetry_stack.FullStackRuntime.run_probe(runtime, "profile", "service")
    output = capsys.readouterr().out

    assert result == payload
    assert runtime.last_operations_probe["stage_observations"] == {
        "datasources": {"http_status": 200, "json_kind": "list", "list_count": 3}
    }
    assert "synthetic-secret" not in output
    assert "TASK6_OPS_PROBE_SUMMARY:" in output


def test_ops_probe_result_summary_rejects_unknown_stage_and_json_shapes():
    summary = telemetry_stack.safe_ops_probe_result_summary(
        {
            "stage_observations": {
                "unknown-secret-stage": {"http_status": 200, "json_kind": "dict"},
                "datasources": {"http_status": True, "json_kind": ["admin"]},
            },
            "last_stage": ["secret"],
            "tls_exception_category": ["admin"],
            "failure_categories": [{"secret": "value"}],
        }
    )

    assert summary["last_stage"] is None
    assert summary["tls_exception_category"] is None
    assert summary["stage_observations"] == {"datasources": {}}
    assert summary["failure_categories"] == []


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
    assert "request_id" in cast(list[str], loki["structured_field_keys"])
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


@pytest.mark.parametrize(
    ("service", "event"),
    [("api", "http.server.request"), ("worker", "worker.heartbeat")],
)
def test_native_loki_log_summary_accepts_plain_body_and_structured_release_without_values(
    service, event
):
    release = "e" * 40
    trace_id = "f" * 32
    body = {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {
                        "service_name": service,
                        "deployment_environment_name": "demo",
                    },
                    "values": [
                        [
                            "1792906956309689000",
                            event,
                            {
                                "event_name": event,
                                "service_version": release,
                                "trace_id": trace_id,
                            },
                        ]
                    ],
                }
            ]
        },
    }

    summary = telemetry_stack.summarize_loki_native_application_log(
        200,
        body,
        expected_service=service,
        expected_event=event,
        expected_release=release,
    )

    serialized = json.dumps(summary, sort_keys=True)
    assert summary["failure_category"] is None
    assert summary["service_name"] == service
    assert summary["deployment_environment_name"] == "demo"
    assert summary["event_match"] is True
    assert summary["release_match"] is True
    assert summary["entry_count"] == 1
    assert release not in serialized
    assert trace_id not in serialized


def test_native_loki_log_summary_rejects_wrong_identity_event_and_release():
    release = "1" * 40

    def summarize(service, environment, event, version):
        return telemetry_stack.summarize_loki_native_application_log(
            200,
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "stream": {
                                "service_name": service,
                                "deployment_environment_name": environment,
                            },
                            "values": [["1", event, {"service_version": version}]],
                        }
                    ]
                },
            },
            expected_service="api",
            expected_event="http.server.request",
            expected_release=release,
        )

    assert (
        summarize("worker", "demo", "http.server.request", release)["failure_category"]
        == "resource_identity"
    )
    assert (
        summarize("api", "demo", "worker.heartbeat", release)["failure_category"] == "event_missing"
    )
    assert (
        summarize("api", "demo", "http.server.request", "2" * 40)["failure_category"]
        == "release_missing"
    )
    body_only_release = telemetry_stack.summarize_loki_native_application_log(
        200,
        {
            "status": "success",
            "data": {
                "result": [
                    {
                        "stream": {
                            "service_name": "api",
                            "deployment_environment_name": "demo",
                        },
                        "values": [
                            [
                                "1",
                                json.dumps({"event": "http.server.request", "release": release}),
                            ]
                        ],
                    }
                ]
            },
        },
        expected_service="api",
        expected_event="http.server.request",
        expected_release=release,
    )
    assert body_only_release["failure_category"] == "release_missing"


def test_api_readiness_summary_is_strict_and_does_not_copy_response_body():
    ready = telemetry_stack.summarize_api_readiness_observation(
        {
            "http_status": 200,
            "body": {"status": "ready"},
            "json_valid": True,
            "truncated": False,
            "error_category": None,
        }
    )
    failed = telemetry_stack.summarize_api_readiness_observation(
        {
            "http_status": 503,
            "body": {"status": "unavailable", "detail": "private diagnostic"},
            "json_valid": True,
            "truncated": False,
            "error_category": None,
        }
    )

    assert ready["passed"] is True
    assert ready["http_status"] == 200
    assert ready["body_status"] == "ready"
    assert failed["passed"] is False
    assert failed["http_status"] == 503
    assert failed["body_status"] == "other"
    assert "private diagnostic" not in json.dumps(failed)


def test_receiver_health_summary_keeps_only_finite_failure_metadata():
    summary = telemetry_stack.safe_receiver_health_summary(
        {
            "http_status": 503,
            "body": {
                "status": "failed",
                "unhealthy": ["grafana", "outside", "loki", "grafana"],
                "detail": "synthetic-secret",
            },
            "json_valid": True,
            "truncated": False,
            "error_category": None,
            "elapsed_ms": 123,
            "total_elapsed_ms": 123,
            "budget_exceeded": False,
        }
    )

    assert summary == {
        "error_category": None,
        "http_status": 503,
        "json_kind": "dict",
        "json_valid": True,
        "elapsed_ms": 123,
        "total_elapsed_ms": 123,
        "budget_exceeded": False,
        "status": "failed",
        "truncated": False,
        "unhealthy": ["grafana", "loki"],
        "unhealthy_shape_ok": True,
        "unknown_unhealthy_count": 1,
    }
    assert "synthetic-secret" not in json.dumps(summary)


def test_receiver_health_summary_preserves_transport_failure_category_only():
    summary = telemetry_stack.safe_receiver_health_summary(
        {
            "http_status": None,
            "body": None,
            "json_valid": False,
            "truncated": False,
            "error_category": "TransportError",
            "elapsed_ms": 40000,
        }
    )

    assert summary == {
        "error_category": "TransportError",
        "http_status": None,
        "json_kind": "unavailable",
        "json_valid": False,
        "elapsed_ms": 30000,
        "total_elapsed_ms": None,
        "budget_exceeded": None,
        "status": None,
        "truncated": False,
        "unhealthy": [],
        "unhealthy_shape_ok": False,
        "unknown_unhealthy_count": 0,
    }


def test_receiver_health_summary_bounds_malformed_fields_and_unknown_categories():
    summary = telemetry_stack.safe_receiver_health_summary(
        {
            "http_status": True,
            "body": {"status": "unexpected", "unhealthy": "grafana"},
            "json_valid": True,
            "truncated": 1,
            "error_category": "synthetic-secret",
            "elapsed_ms": -1,
            "total_elapsed_ms": -1,
            "budget_exceeded": "synthetic-secret",
        }
    )

    assert summary == {
        "error_category": "Other",
        "http_status": None,
        "json_kind": "unavailable",
        "json_valid": True,
        "elapsed_ms": None,
        "total_elapsed_ms": None,
        "budget_exceeded": None,
        "status": None,
        "truncated": False,
        "unhealthy": [],
        "unhealthy_shape_ok": False,
        "unknown_unhealthy_count": 0,
    }
    assert "synthetic-secret" not in json.dumps(summary)


def test_receiver_health_pass_predicate_requires_valid_pass_and_empty_targets():
    healthy = {
        "error_category": None,
        "http_status": 200,
        "json_kind": "dict",
        "json_valid": True,
        "elapsed_ms": 10,
        "total_elapsed_ms": 10,
        "budget_exceeded": False,
        "status": "passed",
        "truncated": False,
        "unhealthy": [],
        "unhealthy_shape_ok": True,
        "unknown_unhealthy_count": 0,
    }
    assert telemetry_stack.receiver_health_is_passed(healthy) is True

    for changed in (
        {"http_status": 503},
        {"json_valid": False},
        {"status": "failed"},
        {"truncated": True},
        {"unhealthy": ["loki"]},
        {"unhealthy_shape_ok": False},
        {"unknown_unhealthy_count": 1},
        {"error_category": "TransportError"},
        {"total_elapsed_ms": 60001},
        {"budget_exceeded": True},
        {"budget_exceeded": None},
    ):
        assert telemetry_stack.receiver_health_is_passed(healthy | changed) is False
    without_budget = healthy.copy()
    without_budget.pop("budget_exceeded")
    assert telemetry_stack.receiver_health_is_passed(without_budget) is False


def test_receiver_component_summary_keeps_only_finite_backend_statuses():
    summary = telemetry_stack.safe_receiver_component_summary(
        {
            "poll_attempts": 3,
            "poll_elapsed_ms": 1000,
            "poll_budget_exceeded": False,
            "poll_unhealthy": ["grafana", "outside"],
            "receiver_health": {
                "http_status": 503,
                "body": {
                    "status": "failed",
                    "unhealthy": ["grafana", "outside"],
                    "detail": "private-payload",
                },
                "json_valid": True,
                "truncated": False,
                "error_category": None,
                "elapsed_ms": 24,
                "total_elapsed_ms": 24,
                "budget_exceeded": False,
            },
            "targets": {
                "grafana": {
                    "initial_status": 302,
                    "redirect": True,
                    "redirect_to_ops_host": True,
                    "final_status": None,
                    "final_host_is_ops": False,
                    "follow_error_category": "dns",
                    "elapsed_ms": 500,
                },
                "outside": {"url": "secret"},
            },
        }
    )

    assert summary == {
        "poll_attempts": 3,
        "poll_elapsed_ms": 1000,
        "poll_budget_exceeded": False,
        "poll_healthy": False,
        "poll_unhealthy": ["grafana"],
        "unknown_poll_failure_count": 1,
        "receiver_health": {
            "error_category": None,
            "http_status": 503,
            "json_kind": "dict",
            "json_valid": True,
            "elapsed_ms": 24,
            "total_elapsed_ms": 24,
            "budget_exceeded": False,
            "status": "failed",
            "truncated": False,
            "unhealthy": ["grafana"],
            "unhealthy_shape_ok": True,
            "unknown_unhealthy_count": 1,
        },
        "targets": {
            "grafana": {
                "initial_status": 302,
                "redirect": True,
                "redirect_to_ops_host": True,
                "final_status": None,
                "final_host_is_ops": False,
                "follow_error_category": "dns",
                "elapsed_ms": 500,
            }
        },
        "target_count": 1,
        "unknown_target_count": 1,
    }
    assert "private-payload" not in json.dumps(summary)
    assert "outside" not in json.dumps(summary)


def test_inspect_service_includes_stopped_compose_container(monkeypatch: pytest.MonkeyPatch):
    project = "prl-task6-" + "b" * 32
    container_id = "a" * 64
    record = {
        "Config": {"Labels": {"com.docker.compose.project": project}},
        "State": {"Status": "exited"},
    }
    compose_calls: list[tuple[str, ...]] = []
    inspect_calls: list[list[str]] = []

    def compose(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        compose_calls.append(args)
        return subprocess.CompletedProcess(list(args), 0, container_id + "\n", "")

    def inspect(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        inspect_calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")

    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.stack = cast(Any, SimpleNamespace(project=project, compose=compose))
    monkeypatch.setattr(telemetry_stack, "acceptance_invoke", inspect)

    observed = runtime.inspect_service("worker")

    assert compose_calls == [("ps", "-a", "-q", "worker")]
    assert inspect_calls == [["docker", "inspect", container_id]]
    assert observed == record


def test_inspect_service_rejects_stopped_container_from_another_compose_project(
    monkeypatch: pytest.MonkeyPatch,
):
    project = "prl-task6-" + "b" * 32
    container_id = "a" * 64
    record = {"Config": {"Labels": {"com.docker.compose.project": "another-project"}}}

    def compose(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 0, container_id + "\n", "")

    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.stack = cast(Any, SimpleNamespace(project=project, compose=compose))
    monkeypatch.setattr(
        telemetry_stack,
        "acceptance_invoke",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, json.dumps([record]), ""),
    )

    with pytest.raises(telemetry_stack.AcceptanceFailure, match="exact owner check"):
        runtime.inspect_service("worker")


def test_inspect_service_rejects_multiple_stopped_container_ids(monkeypatch: pytest.MonkeyPatch):
    project = "prl-task6-" + "b" * 32
    identifiers = "a" * 64 + "\n" + "c" * 64 + "\n"

    def compose(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 0, identifiers, "")

    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.stack = cast(Any, SimpleNamespace(project=project, compose=compose))
    monkeypatch.setattr(
        telemetry_stack,
        "acceptance_invoke",
        lambda *_args, **_kwargs: pytest.fail("inspect must not run for an ambiguous identity"),
    )

    with pytest.raises(telemetry_stack.AcceptanceFailure, match="identity is invalid"):
        runtime.inspect_service("worker")


def test_worker_restart_retention_requires_same_series_and_unchanged_db_points():
    labels = {
        "service_name": "worker",
        "deployment_environment_name": "demo",
        "worker_slot": "1",
    }
    metric_names = {
        "started": "prl_worker_started_timestamp_seconds",
        "last_successful_scan": "prl_worker_last_successful_scan_timestamp_seconds",
        "last_progress": "prl_worker_last_progress_timestamp_seconds",
    }

    def metric_point(
        role: str,
        value: object,
        metric_labels: dict[str, str] | None = None,
    ) -> dict[str, object]:
        return {
            "name": metric_names[role],
            "value": value,
            "labels": labels if metric_labels is None else metric_labels,
        }

    previous_state = {
        "started": {
            "name": "prl_worker_started_timestamp_seconds",
            "value": 90.0,
            "labels": labels,
        },
        "last_successful_scan": {
            "name": "prl_worker_last_successful_scan_timestamp_seconds",
            "value": 110.0,
            "labels": labels,
        },
        "last_progress": {
            "name": "prl_worker_last_progress_timestamp_seconds",
            "value": 111.0,
            "labels": labels,
        },
    }
    current_state = {
        "started": {
            "name": "prl_worker_started_timestamp_seconds",
            "value": 100.0,
            "labels": labels,
        },
        "last_successful_scan": {
            "name": "prl_worker_last_successful_scan_timestamp_seconds",
            "value": 110.0,
            "labels": labels,
        },
        "last_progress": {
            "name": "prl_worker_last_progress_timestamp_seconds",
            "value": 111.0,
            "labels": labels,
        },
    }
    summary = telemetry_stack.FullStackRuntime.worker_restart_observation_summary(
        previous_state, current_state
    )

    assert summary == {
        "previous_started_timestamp_seconds": 90.0,
        "started_timestamp_seconds": 100.0,
        "started_advanced": True,
        "started_series_matches": True,
        "started_unchanged": False,
        "previous_last_successful_scan_timestamp_seconds": 110.0,
        "last_successful_scan_timestamp_seconds": 110.0,
        "last_successful_scan_series_matches": True,
        "last_successful_scan_retained": True,
        "previous_last_progress_timestamp_seconds": 111.0,
        "last_progress_timestamp_seconds": 111.0,
        "last_progress_series_matches": True,
        "last_progress_retained": True,
        "series_identity_matches": True,
    }
    assert telemetry_stack.FullStackRuntime.worker_restart_retained_prior_points(summary) is True

    for changed in (
        {"started": metric_point("started", 90.0)},
        {"started": metric_point("started", float("nan"))},
        {"started": metric_point("started", float("inf"))},
        {"started": metric_point("started", True)},
        {"last_successful_scan": metric_point("last_successful_scan", None)},
        {"last_successful_scan": metric_point("last_successful_scan", float("nan"))},
        {"last_successful_scan": metric_point("last_successful_scan", float("inf"))},
        {"last_successful_scan": metric_point("last_successful_scan", 109.0)},
        {"last_successful_scan": metric_point("last_successful_scan", 111.0)},
        {
            "last_successful_scan": {
                **metric_point(
                    "last_successful_scan",
                    110.0,
                    {**labels, "instance": "replacement:9464"},
                ),
            }
        },
        {"last_progress": metric_point("last_progress", None)},
        {"last_progress": metric_point("last_progress", float("nan"))},
        {"last_progress": metric_point("last_progress", float("inf"))},
        {"last_progress": metric_point("last_progress", 110.0)},
        {"last_progress": metric_point("last_progress", 112.0)},
        {
            "last_progress": {
                **metric_point("last_progress", 111.0, {**labels, "worker_slot": "2"}),
            }
        },
    ):
        assert (
            telemetry_stack.FullStackRuntime.worker_restart_retained_prior_points(
                telemetry_stack.FullStackRuntime.worker_restart_observation_summary(
                    previous_state, current_state | changed
                )
            )
            is False
        )

    missing = current_state.copy()
    missing.pop("last_progress")
    assert (
        telemetry_stack.FullStackRuntime.worker_restart_retained_prior_points(
            telemetry_stack.FullStackRuntime.worker_restart_observation_summary(
                previous_state, missing
            )
        )
        is False
    )
    older_than_observed = current_state | {"last_progress": metric_point("last_progress", 70.0)}
    assert (
        telemetry_stack.FullStackRuntime.worker_restart_retained_prior_points(
            telemetry_stack.FullStackRuntime.worker_restart_observation_summary(
                previous_state, older_than_observed
            )
        )
        is False
    )
    for changed_previous in (
        {"started": metric_point("started", float("nan"))},
        {"started": metric_point("started", True)},
        {"last_successful_scan": metric_point("last_successful_scan", None)},
        {"last_progress": metric_point("last_progress", float("inf"))},
    ):
        assert (
            telemetry_stack.FullStackRuntime.worker_restart_retained_prior_points(
                telemetry_stack.FullStackRuntime.worker_restart_observation_summary(
                    previous_state | changed_previous, current_state
                )
            )
            is False
        )


def test_worker_started_value_matches_container_generation():
    predicate = getattr(
        telemetry_stack.FullStackRuntime,
        "worker_started_value_matches_container_generation",
        None,
    )
    assert callable(predicate), "the worker start-generation predicate is missing"

    container_started_at = 1_800_000_000.000
    assert predicate(1_799_999_964.808, container_started_at) is False
    assert predicate(1_800_000_001.684, container_started_at) is True
    assert predicate(1_799_999_999.000, container_started_at) is True
    assert predicate(1_800_000_010.000, container_started_at) is True
    assert predicate(1_799_999_998.999, container_started_at) is False
    assert predicate(1_800_000_010.001, container_started_at) is False

    for invalid_metric_value in (None, True, float("nan"), float("inf"), "1800000001.684"):
        assert predicate(invalid_metric_value, container_started_at) is False
    for invalid_container_started_at in (None, True, float("nan"), float("inf"), "1800000000"):
        assert predicate(1_800_000_001.684, invalid_container_started_at) is False


def test_worker_metric_state_records_ambiguous_role_counts_without_labels():
    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    identity = {
        "service_name": "worker",
        "deployment_environment_name": "demo",
        "worker_slot": "1",
    }
    responses = iter(
        (
            {"status": "success", "data": ["prl_worker_started_timestamp_seconds"]},
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {**identity, "instance": "worker-a"},
                            "value": [1_793_010_965.0, "1.0"],
                        },
                        {
                            "metric": {**identity, "instance": "worker-b"},
                            "value": [1_793_010_965.0, "1.0"],
                        },
                    ]
                },
            },
        )
    )
    runtime.api_json = cast(Any, lambda _url: next(responses))

    with pytest.raises(telemetry_stack.AcceptanceFailure, match="multiple worker series"):
        runtime.worker_metric_state()

    assert runtime.last_worker_metric_query_diagnostic["candidate_counts"] == {"started": 2}
    assert runtime.last_worker_metric_query_diagnostic["ambiguous_roles"] == ["started"]
    assert "worker-a" not in str(runtime.last_worker_metric_query_diagnostic)
    assert "worker-b" not in str(runtime.last_worker_metric_query_diagnostic)


def test_worker_metric_state_records_prometheus_error_status_without_error_text():
    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.api_json = cast(
        Any,
        lambda _url: {"status": "error", "error": "private query details"},
    )

    with pytest.raises(telemetry_stack.AcceptanceFailure, match="unsuccessful worker metric-name"):
        runtime.worker_metric_state()

    assert runtime.last_worker_metric_query_diagnostic == {
        "query_stage": "metric_names",
        "api_status": "error",
    }
    assert "private query details" not in str(runtime.last_worker_metric_query_diagnostic)


def test_worker_metric_state_caps_serial_requests_to_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.api_id = "api-id"
    clock = {"now": 100.0}
    request_timeouts = []
    metric_names = [
        "prl_worker_started_timestamp_seconds",
        "prl_worker_heartbeat_timestamp_seconds",
        "prl_worker_last_successful_scan_timestamp_seconds",
        "prl_worker_last_progress_timestamp_seconds",
        "prl_worker_backlog_count",
        "prl_worker_successful_scans_total",
    ]
    identity = {
        "service_name": "worker",
        "deployment_environment_name": "demo",
        "worker_slot": "1",
        "instance": "worker:9464",
    }

    def monotonic():
        return clock["now"]

    def invoke(args, *, timeout=15):
        request_timeouts.append(timeout)
        clock["now"] += 1.0
        url = args[-3]
        if "/label/__name__/values" in url:
            response = {"status": "success", "data": metric_names}
        else:
            metric_name = parse_qs(urlsplit(url).query)["query"][0]
            response = {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": identity,
                            "value": ["1800000001.0", "1.0"],
                        }
                    ]
                },
            }
            assert metric_name in metric_names
        return subprocess.CompletedProcess(args, 0, json.dumps(response), "")

    monkeypatch.setattr(telemetry_stack, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(telemetry_stack, "acceptance_invoke", invoke)

    try:
        _selected, state = runtime.worker_metric_state(deadline=108.0)
    except TypeError as error:
        pytest.fail(f"worker metric state must accept a shared request deadline: {error}")

    assert len(state) == 6
    assert request_timeouts == [8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0]


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


def test_query_loki_request_can_recheck_an_original_event_after_long_acceptance(
    monkeypatch: pytest.MonkeyPatch,
):
    request_id = "d" * 32
    trace_id = "e" * 32
    release = "a" * 40
    event_timestamp_ns = 1_793_010_000_000_000_000
    now_ns = event_timestamp_ns + 900_000_000_000
    start_ns = event_timestamp_ns - 1_000_000_000
    seen_urls: list[str] = []
    runtime = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.stack = cast(Any, SimpleNamespace(expected_sha=release))

    def observe(url: str) -> dict[str, object]:
        seen_urls.append(url)
        if urlsplit(url).path == "/loki/api/v1/query_range":
            body = {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "stream": {
                                "service_name": "gateway",
                                "deployment_environment_name": "demo",
                            },
                            "values": [
                                [
                                    str(event_timestamp_ns),
                                    json.dumps(
                                        {
                                            "event": "gateway.request",
                                            "request_id": request_id,
                                            "trace_id": trace_id,
                                            "release": release,
                                        }
                                    ),
                                    {},
                                ]
                            ],
                        }
                    ]
                },
            }
        else:
            body = {
                "status": "success",
                "data": [
                    {
                        "service_name": "gateway",
                        "deployment_environment_name": "demo",
                    }
                ],
            }
        return {
            "http_status": 200,
            "body": body,
            "json_valid": True,
            "truncated": False,
            "error_category": None,
        }

    runtime.api_json_observation = cast(Any, observe)
    monkeypatch.setattr(telemetry_stack.time, "time_ns", lambda: now_ns)

    result = runtime.query_loki_request(request_id, trace_id, start_ns=start_ns)

    request_query = next(
        parse_qs(urlsplit(url).query)
        for url in seen_urls
        if urlsplit(url).path == "/loki/api/v1/query_range"
    )
    assert request_query["start"] == [str(start_ns)]
    assert request_query["end"] == [str(now_ns + 60_000_000_000)]
    assert result["request_id_match"] is True
    assert result["request_event_timestamp_unix_ns"] == event_timestamp_ns
    assert request_id not in json.dumps(result)
    assert trace_id not in json.dumps(result)


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


@pytest.mark.parametrize(
    ("service", "event"),
    [("api", "http.server.request"), ("worker", "worker.heartbeat")],
)
def test_native_loki_log_observation_uses_finite_source_and_event(service, event):
    runtime: Any = object.__new__(telemetry_stack.FullStackRuntime)
    runtime.stack = SimpleNamespace(expected_sha="a" * 40)
    seen = {}

    def observe(url):
        seen["url"] = url
        return {
            "http_status": 200,
            "body": {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "stream": {
                                "service_name": service,
                                "deployment_environment_name": "demo",
                            },
                            "values": [["1", event, {"service_version": "a" * 40}]],
                        }
                    ]
                },
            },
            "json_valid": True,
            "truncated": False,
            "error_category": None,
        }

    runtime.api_json_observation = observe
    summary = runtime.observe_loki_native_application_log(service, event)

    parsed = urlsplit(seen["url"])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "loki:3100"
    assert parsed.path == "/loki/api/v1/query_range"
    assert query["query"] == [
        f'{{service_name="{service}",deployment_environment_name="demo"}} |= "{event}"'
    ]
    assert set(query) == {"query", "start", "end", "limit"}
    assert int(query["start"][0]) < int(query["end"][0])
    assert summary["failure_category"] is None


def test_pre_cleanup_snapshot_keeps_loki_query_index_and_tempo_independent():
    runtime: Any = object.__new__(telemetry_stack.FullStackRuntime)
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

    diagnostic = cast(
        dict[str, Any],
        runtime.signal_boundary_diagnostic(
            route_status=None,
            request_id="",
            trace_id="",
            sink_observations=sinks,
            receiver_health_summary=telemetry_stack.safe_receiver_health_summary(
                {
                    "http_status": 503,
                    "body": {
                        "status": "failed",
                        "unhealthy": ["grafana"],
                        "detail": "private",
                    },
                    "json_valid": True,
                    "truncated": False,
                    "error_category": None,
                    "elapsed_ms": 50,
                }
            ),
        ),
    )

    assert diagnostic["route"]["http_status"] is None
    assert diagnostic["loki_query"] == sinks["loki_query"]
    assert diagnostic["loki_index_series"] == sinks["loki_index_series"]
    assert diagnostic["tempo"] == sinks["tempo"]
    assert diagnostic["receiver_health"]["http_status"] == 503
    assert diagnostic["receiver_health"]["unhealthy"] == ["grafana"]
    assert "private" not in json.dumps(diagnostic)
    assert all(
        service["inspection_available"] is False for service in diagnostic["services"].values()
    )
