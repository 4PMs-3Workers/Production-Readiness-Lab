# Observability alert runbook

Acceptance provenance, five dashboard captures and tested operating limits are in [the Issue #35 handoff](observability-foundation.md) and [sanitized runtime evidence](../evidence/observability-foundation/runtime-acceptance-run3.json). Treat empty windows as unknown and use the firing/recovery timelines only within the local synthetic test scope.

This runbook uses the five provisioned Grafana dashboards and the safe telemetry fields emitted by the application, worker, gateway, and Collector. Use normalized routes, finite error codes, database pool gauges, worker slot, and trace links to find the failing component. Do not copy SQL statements, request bodies, consultation text, credentials, cookies, or headers into dashboards or incident notes.

## API HTTP error rate

Open **API errors** and filter by the finite normalized route. Compare the 5xx/aborted rate with request volume, then inspect API log and trace details. Check **DB pool** for pool pressure or finite database failure reasons. If the API target is up but its stdout queue-capacity family is absent, treat that as an instrumentation gap rather than an idle service.

## Database pool or lock timeout

Open **DB / pool**. Check checked-out, idle, and overflow connections for both API and worker scrape paths. Inspect finite database failure reasons and correlated traces. Confirm MySQL readiness and pool recovery before retrying an operation; the telemetry path must not replay or roll back a committed business action.

## Worker heartbeat missing

Open **Worker progress / backlog** and select the configured worker slot. Confirm the Collector application-OTLP target is up, then compare the real heartbeat timestamp with the current process start timestamp. A missing or pre-restart heartbeat is unknown for the new process. Restart only through the managed worker lifecycle after checking its current phase and database health.

## Worker heartbeat stale

A heartbeat from the current worker process that is more than 45 seconds old fires without an additional hold. Check **Worker progress / backlog**, the Collector application-OTLP target, worker phase, and database health. A missing or pre-restart heartbeat follows the separate 30-second missing path.

## Worker progress stale

A fresh heartbeat with no current-process progress means the worker is alive but has not completed a successful scan or candidate transition. Check the current worker phase, database status, and progress timestamps. Startup time grants the first-progress window; it is not a progress event.

## Worker backlog snapshot stale

Open **Worker progress / backlog**. A snapshot older than 90 seconds is unknown and is excluded from backlog aggregation. Check the latest successful scan and database status before interpreting backlog size.

## Worker backlog overdue

The alert requires a fresh positive backlog and oldest-overdue age above 60 seconds. Compare the maximum fresh backlog across worker slots with the oldest overdue observation. Do not add replica backlog values together.

## Gateway fast-success or pipeline heartbeat

Open **Gateway SLI**. Fast-success is successful in-scope gateway requests completed within 500 ms divided by all successful in-scope requests. Availability uses successful in-scope requests divided by successful plus service-failure requests. Health checks and operations-vhost requests are excluded. Both ratios show no sample (unknown) when their request denominator is empty or Loki has not observed a genuine `gateway.healthcheck` event within the last minute. An expired heartbeat does not turn a ratio into 0% or 100%; this gate also suppresses the fast-success alert while pipeline freshness is unknown.

If the denominator remains positive while a ratio is unknown, check `GatewayPipelineHeartbeatMissing`, the gateway filelog handoff, Collector target, Loki target, and telemetry dropped/failed counters in **Telemetry loss**. Confirm new genuine `/health/gateway` events reach Loki before trusting SLI panels or the fast-success alert again.

## Telemetry target or loss

Open **Telemetry loss** and compare all configured scrape targets, API telemetry-family presence, queue size/capacity, and dropped/export-failed counters. Resolve a target outage first, then confirm fresh API and worker metrics/logs arrive. A target that is up with a missing required metric family is still a telemetry gap. Prometheus self-health can only alert while the Prometheus evaluator and Alertmanager path are running; a same-host Docker or host failure requires future off-host monitoring.


## Deployment boundary and setup

This runbook covers the repository's local/demo Compose topology. The application, Collector, Prometheus, Loki, Tempo, Grafana, Alertmanager, and the local alert receiver run on the same Docker host. Alertmanager currently delivers only to the internal receiver. The repository does not provision an off-host probe or an external on-call notification destination. A healthy target-down alert therefore does not prove that the host or alert path is observable when that host is unavailable. The short acceptance run is not a production deployment, a three-minute host-outage commitment, or a seven-day SLO campaign.

Follow the [Compose deployment guide](containerized-demo.md) for setup and lifecycle commands. Configure HTTPS and operations access with external paths for `PRL_TLS_CERT_FILE`, `PRL_TLS_KEY_FILE`, `PRL_HTTPS_ORIGIN`, `PRL_OPS_HOST`, `PRL_OPS_ORIGIN`, `PRL_OPS_CIDRS`, `PRL_OPS_TLS_CERT_FILE`, and `PRL_OPS_TLS_KEY_FILE`. Use a trusted certificate whose SAN matches each public hostname; keep private keys outside the checkout and restrict access to the Nginx container identity. Grafana is available on the distinct operations hostname, behind the configured CIDR allowlist, with anonymous access and sign-up disabled. Do not publish telemetry ports directly.

Set `PRL_GRAFANA_ADMIN_USER_FILE` and `PRL_GRAFANA_ADMIN_PASSWORD_FILE` to absolute paths outside the repository. Each UTF-8 file contains one line with no BOM, CR, NUL, or embedded newline; one optional terminal LF is accepted. Keep spaces as part of the value. The deployment CLI validates file shape without printing content. These files bootstrap only a new Grafana data volume; editing them does not rotate an account in an existing volume. Use Grafana's supported account procedure, update the external file, recreate the service with the same mode, and verify login. Never put credentials in shell history, a rendered Compose config, a screenshot, or a report. `docker compose config --quiet` validates configuration without printing resolved secrets.

A certificate file change does not reload a running Nginx process. Recreate only the owned service through the managed demo CLI with the same overlay and manifest mode, then verify the public certificate and operations origin. Preserve named volumes; do not use `down -v` as a routine restart. For exact commands and the guarded cleanup behavior, use the deployment guide above.

## Retention and resource limits

The checked-in configuration sets Loki and Tempo retention to 168 hours and Prometheus TSDB retention to 14 days. These are configured local volume retention values; the acceptance tests do not prove a full retention period, backup, or restore. The 64 MiB gateway log spool is tmpfs and volatile. Collector filelog offsets use a named volume and survive the tested graceful checkpoint/restart path, but offset checkpoint and downstream delivery are not an atomic exactly-once transaction. Do not promise durable spool or exactly-once delivery.

The run 3 envelope records configured memory caps totaling 5,888 MiB across 13 Compose services; these are limits, not measured utilization. Some integration tests start a temporary operations probe capped at 128 MiB in the test-only Compose configuration; it is outside the run 3 envelope. The 64 MiB spool is already included in the gateway service budget and must not be counted twice. Plan for an 8 vCPU/16 GiB host with at least 50 GiB free disk and synchronized clocks; the acceptance run did not measure those host conditions. Retention of 168 hours/14 days is not a volume quota, and Compose does not cap named-volume size. Budget OS/Docker overhead and monitor actual disk usage; this local smoke is not a production load test.

## Alert rules and first checks

The configured alerts are local receivers, not an external paging service:

| Alert | Trigger | First check |
|---|---|---|
| `TelemetryTargetDown` | Any of the nine required scrape jobs stays down for 30 seconds. | Check Prometheus target state and the named service health. This evaluator must still be running. |
| `ApiTelemetryFamilyMissing` | API is up but its required stdout queue family is absent for 30 seconds. | Treat the missing family as an instrumentation gap, not zero queue use. |
| `TelemetryLossDetected` | A dropped/failed application item or a Collector receiver refusal/failure/export enqueue/send failure increased during five minutes. | Compare the finite signal and receiver/exporter labels with Collector logs and the `Telemetry loss` dashboard. |
| `ApiHttpErrorRateWarning` / `ApiHttpErrorRateCritical` | The 5xx/aborted rate exceeds 0.5% / 5% across at least 20 API requests in five minutes, held for two / one minute. | Filter by normalized route and release; separate input/business responses from service failures. |
| `DatabasePoolOrLockTimeout` | A pool or lock timeout occurred in the last five minutes, held for 30 seconds. | Check API and worker pool gauges, MySQL readiness, and the finite failure reason. |
| `WorkerHeartbeatMissing` | An expected current worker slot has no current-process heartbeat for 30 seconds. | Check worker container/process identity and database reachability. |
| `WorkerHeartbeatStale` | A current-process heartbeat is over 45 seconds old. | Check process liveness, OTLP target, and DB connection status. |
| `WorkerProgressStale` / `WorkerBacklogSnapshotStale` | A current-process heartbeat has no progress or successful-scan snapshot newer than 90 seconds after startup; each alert is held for 30 seconds. | Check the worker's latest successful scan and progress timestamp; old-process values do not establish current progress. |
| `WorkerBacklogOverdue` | Fresh positive backlog has oldest overdue age above 60 seconds for one minute. | Compare the maximum fresh backlog across slots; do not sum replicas. |
| `GatewayFastSuccessBelowSLO` | Fewer than 95% of at least 20 successful in-scope requests finish within 500 ms over five minutes, held for two minutes, while a genuine gateway healthcheck was observed within one minute. | Check gateway SLI, API/service failures, request-volume sufficiency, and `GatewayPipelineHeartbeatMissing`. |
| `GatewayPipelineHeartbeatMissing` | The genuine gateway healthcheck event is absent from Loki for 60 seconds, held for 30 seconds. | Check gateway file output, Collector target and Loki target before interpreting the SLI. |

There is one `TelemetryTargetDown` rule for the Collector and all other configured jobs; the former duplicate collector-only warning was removed. Prometheus can alert that its own scrape target is down only while the Prometheus evaluator, Alertmanager, and receiver path remain available. The local receiver records alert summaries; it does not deliver an external page.

## Useful queries

Use these bounded queries as a starting point and set the dashboard time range to the incident window:

```promql
up{job=~"api|collector|application-otel|local-alert-receiver|prometheus-self|loki-self|tempo-self|grafana-self|alertmanager-self"}
```

```promql
sum by (route) (increase(prl_http_requests_total{status=~"5..|aborted"}[5m]))
```

```promql
increase(prl_telemetry_dropped_total[5m])
increase(prl_telemetry_failed_total[5m])
```

```promql
prl_worker_heartbeat_timestamp_seconds
prl_worker_started_timestamp_seconds
prl_worker_last_progress_timestamp_seconds
prl_worker_last_successful_scan_timestamp_seconds
prl:worker:backlog_max_fresh
prl:worker:backlog_oldest_overdue_max_fresh
```

For gateway source events in Loki, start with `{service_name="gateway",deployment_environment_name="demo"}`. Filter genuine healthchecks with `| json event="event" | __error__ = "" | event="gateway.healthcheck"`; business requests use `event="gateway.request"`. Compare the embedded producer UTC timestamp with the ingestion timestamp after a Collector restart. A recently ingested old source event is not fresh recovery evidence. The five dashboards are **API errors**, **DB / pool**, **Gateway SLI**, **Telemetry loss**, and **Worker progress / backlog**.

## Redaction and exporter failures

Correlate an incident with a normalized route, finite error code, release, request ID, trace ID, and timestamp. Keep user, appointment, request, and trace identifiers out of metric labels. Do not paste SQL statements, database URLs, request bodies, consultation text, cookies, authorization headers, passwords, raw alert payloads, or private environment-file paths into dashboards, tickets, screenshots, or incident notes. Exporter diagnostics intentionally classify failures without exposing response bodies or connection details; preserve that boundary when collecting evidence.

A missing metric family or absent counter is unknown, not zero. Check both application exporter counters (`prl_telemetry_dropped_total`, `prl_telemetry_failed_total`) and Collector receiver/exporter families before concluding the pipeline is healthy. An exporter failure must not replay or roll back a committed business transaction. Repair the target, verify a newly produced API/worker event reaches its sink, then confirm the corresponding counter/queue state recovers. When Collector or the backend is down, expect a documented gap instead of fabricated healthy values.

## Recovery and business verification

1. Check service, image release, health, and target state. Fix the failing component before restarting the full stack; preserve the MySQL and telemetry volumes.
2. After a DB interruption, wait for readiness and verify the current worker process performs a new successful scan and emits new progress. A cached scan, heartbeat from the old worker ID, or the same in-memory backlog is not proof that the new owner recovered.
3. Let the worker expire due proposals through its normal service. Verify the proposal and appointment transitions, audit event, and notification for the controlled IDs. Do not add an expiry endpoint or change the worker deadline to speed an incident test.
4. If a client lost the response after a possible commit, retry with the same idempotency key and confirm the authoritative row/audit/notification counts. Do not retry as a new operation with a new key until commit state is resolved.
5. Recheck fresh telemetry and dashboard queries. A recent Loki receipt timestamp alone is insufficient; verify producer event time and the correct query window. Treat missing periods as unknown.

The Task 8 business/outage acceptance repeats authenticated student and staff flows (real cookie and CSRF handling) in healthy, Collector-stopped, Loki-stopped, Tempo-stopped, and Prometheus-stopped states, restoring each component before the next state. Grafana and Alertmanager are checked at their UI/receiver boundaries rather than adding more business-write outage states. The evidence report records actual timings and traces; this matrix does not establish a host-outage alert path, a three-minute detection target, or a seven-day SLO.
