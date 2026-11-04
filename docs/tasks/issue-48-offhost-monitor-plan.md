# Independent Off-host HTTPS Monitor Implementation Plan

> **For agentic workers:** Execute this plan task-by-task. Keep each completed task in a coherent commit.

**Goal:** Implement the independent bounded HTTPS probe and notification path required by Issue #48.

**Architecture:** A dependency-free Python 3.11 package runs a self-scheduled asyncio probe, deterministic threshold evaluator, atomic bounded state store, and bounded webhook delivery. A systemd unit runs it separately from the application Compose stack.

**Tech Stack:** Python 3.11 standard library, pytest, TOML, JSON, systemd.

**Spec:** `docs/tasks/issue-48-offhost-monitor-design.md`

## Global Constraints

- TLS certificate validation is mandatory; redirects are rejected.
- DNS/connect/TLS/read and webhook attempts have a finite total deadline.
- Response bodies and persisted state have strict byte/count limits.
- Diagnostics and labels are finite and contain no secrets or application payloads.
- The monitor does not depend on the application's Prometheus/Alertmanager stack.
- State updates are atomic, bounded, and restart-safe.
- The default receiver binds to loopback and is test-only.

## Review Focus

- A stalled resolver or slow body must not exceed the total attempt deadline; test injected timeout and local slow-response paths.
- A redirect or untrusted/expired certificate must fail closed; test redirect and TLS verification failures.
- Process restart between transition persistence and webhook acknowledgement must not lose or duplicate a logical event; test stable event IDs and pending delivery recovery.
- Repeated large/malformed responses and corrupt/oversized state must remain bounded; test response caps and fail-closed state validation.
- An invalid or secret-bearing configuration must not leak values into errors/status; test sanitized errors and bounded config validation.

---

### Task 1: Probe configuration, HTTPS transport, and evaluator

**Files:** Create `tools/infra/offhost/offhost_probe/{__init__.py,config.py,probe.py,evaluator.py}`, package `pyproject.toml`, and `tests/backend/unit/test_offhost_monitor.py`.

**Interfaces:** `load_config(path) -> MonitorConfig`; `HttpsTransport.probe(check, policy) -> ProbeResult`; `evaluate(results, states, failure_threshold, recovery_threshold, now, attempted_at) -> (transitions, states)`.

- [x] Write fake-clock tests for valid/invalid config, failure threshold, intermittent failure reset, empty checks, success recovery threshold, and transition timestamps.
- [x] Run those tests to confirm the missing interfaces fail.
- [x] Implement bounded finite configuration, injectable transport, and deterministic evaluator.
- [x] Run focused tests, Black, Ruff, and Pyright for the package/test files.
- [x] Commit as `feat(monitor): add bounded HTTPS probe and evaluator`.

### Task 2: Bounded persistence and notification lifecycle

**Files:** Create `tools/infra/offhost/offhost_probe/state.py`, `notify.py`, and extend `tests/backend/unit/test_offhost_monitor.py`.

**Interfaces:** `StateStore.load() -> MonitorState`; `StateStore.save(state) -> None`; `WebhookNotifier.deliver(event) -> DeliveryResult`.

- [x] Test atomic restart recovery, corrupt/oversized state rejection, stable event IDs, firing/resolved ordering, deduplication, bounded retry, and notification outage health.
- [x] Run the new tests to confirm they fail before implementation.
- [x] Implement versioned atomic JSON persistence with strict byte/queue bounds and webhook delivery with finite payload, timeout, and retry limits.
- [x] Run focused tests and applicable formatting/lint/type checks.
- [x] Commit as `feat(monitor): persist transitions and retry alert delivery`.

### Task 3: Runnable Linux package, CLI, integration tests, and runbook

**Files:** Create CLI/runtime/loopback fixture modules, sample TOML, systemd unit, Linux install/run documentation, and `tests/backend/integration/test_offhost_monitor.py`; update `docs/tasks/observability-runbook.md` and `docs/architecture/system-design.md`.

**Interfaces:** `offhost-probe run|status|receiver`; the run command owns scheduling and records attempt/evaluation/delivery/resolution timestamps and monitor health.

- [x] Write local TLS/network integration cases for unreachable upstream, expired/untrusted certificate, upstream error, redirect, response limit, and subprocess firing/resolved delivery.
- [x] Run integration tests; required local prerequisites are explicit and the tests fail on missing runtime/config instead of skipping.
- [x] Implement CLI, runtime lifecycle, loopback-only test receiver, TOML example, systemd service, and explicit installation/run commands.
- [x] Run focused unit/integration checks, package quality checks, and backend unit regression suite; the stopped application path fires within three minutes and the test receiver records firing/resolved transitions.
- [x] Review all diffs against acceptance criteria and run final review.
- [x] Commit as `feat(monitor): package independent Linux monitor and runbook`.

### Review follow-up

- [x] Add system trust when an optional deployment CA bundle is configured.
- [x] Report stale probe state as degraded and reconcile removed checks without losing firing/resolved delivery.
- [x] Keep notification saturation bounded; defer new checks without restarting the service, then resume after retired state drains.
- [x] Re-run backend unit tests, monitor TLS/network integration tests, Black, Ruff, Pyright, and diff checks.
