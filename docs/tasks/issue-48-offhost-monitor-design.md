# Issue #48: Independent Off-host HTTPS Monitor

## Intent

Provide a small Linux-runnable monitor that detects application gateway outages even when the application's same-host telemetry and alerting stack is unavailable. The monitor must keep its scheduling, evaluation, state, and notification delivery outside that stack.

## Goals

- Probe a bounded set of explicitly configured HTTPS checks with certificate validation, no redirects, response-size limits, and an overall attempt deadline.
- Apply deterministic failure and recovery thresholds, persist transitions atomically, and resume pending alerts after restart.
- Deliver firing/resolved webhooks with bounded request and retry attempts, stable event IDs, and deduplication.
- Record timestamps, counters, and finite health states without retaining response bodies, credentials, request identifiers, or arbitrary URL details.
- Provide a dependency-free Python 3.11 package, Linux systemd unit, sample TOML, local test receiver, and operational runbook.

## Non-goals and boundary

- The monitor does not claim survival of physical-host loss when deployed on the same machine; production must place it on an independent host/network.
- A real notification destination, DNS name, certificate trust chain, and external host are deployment inputs. The checked-in sample uses a loopback receiver for local verification only.
- This issue does not alter application authentication, session/CSRF, APIs, database, or business data.

## Design choice

Use a standalone Python package under `tools/infra/offhost` with standard-library runtime dependencies. TOML configuration gives operators explicit bounded values without adding a parser dependency. An asyncio-based HTTPS client wraps DNS, connect, TLS, and body reads in one deadline; TLS verification remains enabled and redirects are rejected. Each attempt records only the configured finite check name, outcome class, status code, and timestamps.

Evaluation uses consecutive-failure and consecutive-success thresholds. State is a versioned JSON document replaced atomically in a restrictive state directory. It stores only bounded transition metadata and a bounded pending-notification queue; a stable SHA-256 event ID provides receiver-side deduplication across retries and restarts. Webhook attempts have bounded timeout and per-cycle attempts; unresolved deliveries remain pending while the monitor reports delivery degradation.

The command has `run`, `status`, and `receiver` modes. `run` owns its schedule and cannot be triggered by Prometheus/Alertmanager. `receiver` is a loopback-only in-memory fixture for local testing, never an external paging destination. A systemd service restarts the monitor process and keeps configuration and state in explicit external paths.

## Alternatives considered

- Reusing Prometheus/Alertmanager would retain the same-host failure dependency identified by the issue.
- A shell/curl probe would add runtime tool/version assumptions and make deterministic state, bounded persistence, and safe structured diagnostics harder to test.
- A third-party Python HTTP client would add deployment dependencies without improving this small monitor's required surface; the standard library is sufficient when the complete attempt is deadline-bounded.

## Assumptions and verification

- The operator supplies HTTPS checks and explicit deployment notification settings; the default receiver is local and test-only.
- The configured fault-to-firing bound includes the worst-case wait for the first probe, each serial monitor cycle, and the final probe timeout. With the sample values, the conservative bound is `3 * max(30, 10 + 30) + 10 = 130` seconds, below the three-minute target. If notification backpressure prevents a safe evaluation, probe health is degraded and the bound does not apply until probing resumes.
- Unit tests use a fake clock and injected transport. TLS/network integration tests use local HTTPS servers and generated certificates. A subprocess drill checks the loopback receiver and firing/resolved behavior. Physical-host-loss proof remains an environment-level drill.
