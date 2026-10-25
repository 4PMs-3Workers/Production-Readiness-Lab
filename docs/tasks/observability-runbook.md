# Observability alert runbook

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

Open **Gateway SLI**. Fast-success is successful in-scope gateway requests completed within 500 ms divided by all successful in-scope requests. Availability uses successful in-scope requests divided by successful plus service-failure requests. Health checks and operations-vhost requests are excluded. A missing ratio with no eligible samples is insufficient data; a missing healthcheck heartbeat with a down target or telemetry loss indicates a pipeline gap.

For a pipeline gap, check the gateway filelog handoff, Collector target, Loki target, and telemetry dropped/failed counters in **Telemetry loss**. Confirm new genuine `/health/gateway` events reach Loki before trusting SLI panels again.

## Telemetry target or loss

Open **Telemetry loss** and compare all configured scrape targets, API telemetry-family presence, queue size/capacity, and dropped/export-failed counters. Resolve a target outage first, then confirm fresh API and worker metrics/logs arrive. A target that is up with a missing required metric family is still a telemetry gap. Prometheus self-health can only alert while the Prometheus evaluator and Alertmanager path are running; a same-host Docker or host failure requires future off-host monitoring.
