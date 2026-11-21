# Issue #53 — Reproducible load workload and consistency verification

**Base:** `origin/develop` at `724169a33b852a6cc6b255aff7cb92fffed89908`
**Branch:** `feature/issue-53-reproducible-booking-load`
**Worktree:** `D:\Production-Readiness-Lab-issue-53`

**Continuation:** Read [PR #63 agent guidance](../tasks/pr-63-agent-guidance.md) and [CI follow-up](../tasks/pr-63-ci-followup-plan.md) before further changes.

## Intent

Provide a repeatable, bounded way to exercise a declared lab release through its real gateway, seed fictional application data in an isolated database, verify business invariants after the run, and export a sanitized report. A short run can prove behavior in its observed window; it cannot claim a seven-day SLO.

## Chosen design

Implement a focused Python package in `tools/load/` using the existing `httpx`, SQLAlchemy/asyncmy, and stdlib dependencies. Divide it into configuration and safety validation, fictional fixture provisioning, an HTTP session workload, gateway/telemetry collection, invariant verification, and report serialization. Use the existing gateway classification values as the authority for SLI numerator/denominator membership. Unknown classifications, absent telemetry, insufficient samples, and generator saturation remain explicit insufficient evidence.

Load writes require an explicit opt-in, a declared candidate release, and an isolated schema with a verified ownership marker. Provision at least ten fictional staff and one thousand valid appointment rows with deterministic data. Only cleanup schemas whose safe prefix and persisted ownership marker match this run. Exercise authenticated sessions through the gateway using returned cookies and CSRF values; writes use unique idempotency keys and replay the same key only for the same payload.

The workload uses twenty concurrent sessions for ten minutes by default and a deterministic mix of reads, bookings, cancellations, and proposal creation. Reports contain finite route templates and aggregate outcomes/latencies, dataset and workload settings, target/source identity, timestamps, resource samples, generator capacity, telemetry availability, invariant results, and the SLI verdict. Exclude response bodies, credentials, cookies, CSRF/session identifiers, consultation descriptions, and personal fields.

## Approaches considered

- Reuse the ordinary demo seed: rejected because it does not provide the required 1,000-appointment dataset and may share persistent demo data.
- Put the workload in application runtime code or add a load-testing dependency: rejected because the runner is operational tooling and existing dependencies cover HTTP and database access.
- Use a separately owned, explicitly named load schema and a gateway client: chosen to keep writes isolated, make cleanup ownership checkable, and exercise production request contracts.

## Goals and boundaries

- Deliver fixture, runner, SLI math/classification, invariant checks, sanitized bounded report, and focused tests.
- Include an opt-in shorter real-MySQL/gateway acceptance path that fails when its prerequisites are missing.
- Preserve existing auth, CSRF, authorization, idempotency, deadline, transaction, and classification contracts.
- Do not touch shared schemas or ordinary demo rows; do not add runtime dependencies or claim the 10-minute run proves the seven-day SLO.
- Final release measurements belong to the declared candidate and deployment configuration; record actual source and artifact/config identity.

## Assumptions

- The operator supplies a candidate gateway URL, matching release identity, a passing deployment manifest for that exact release, and database administrator URL capable of creating/removing only the dedicated load schema.
- Fixture credentials are fictional, randomly generated outside the CLI, supplied through a private environment setting, and never included in logs/reports or derived from the public run ID.
- A run without a complete gateway classification window or required measurements returns an insufficient-evidence result, even when HTTP requests themselves completed.
- Cleanup is an explicit operation and only deletes a schema created by this run after verifying its ownership row and expected fixture objects.

## Implementation plan

### 1. Safe fixture and configuration

- **Acceptance:** strict preflight rejects missing opt-in, invalid target/release, unsafe schema names, and cleanup without the exact ownership marker; deterministic fixture produces at least 10 staff and 1,000 appointments with valid relationships and statuses.
- **Files:** `tools/load/config.py`, `tools/load/fixtures.py`, `tools/load/__init__.py`, `tests/load/test_config.py`, `tests/load/test_fixtures.py`.
- **Steps:** define immutable run config and schema ownership record; validate all destructive arguments before connecting; create/seed only an empty, run-owned schema; add focused tests for invalid and valid configurations/fixture distribution.
- **Checks:** focused `pytest tests/load/test_config.py tests/load/test_fixtures.py`; `ruff check` on changed Python files.
- **Commit:** `feat(load): provision isolated reproducible booking fixtures`

### 2. Authenticated workload and measurement

- **Acceptance:** default configuration is 20 sessions/600 seconds; requests use real gateway cookies and CSRF; booking/proposal writes carry correct idempotency and replay semantics; record throughput, per-route latency, classified status/error counts, and generator capacity without sensitive payloads. A failed replay remains visible to invariant checks; the clean checked-out migration source must match the declared candidate release.
- **Files:** `tools/load/runner.py`, `tools/load/metrics.py`, `tools/load/gateway.py`, `tools/load/report.py`, `tools/load/invariants.py`, `tools/load/cli.py`, `tools/load/fixtures.py`, `tests/load/test_metrics.py`, `tests/load/test_gateway.py`, `tests/load/test_runner.py`, `tests/load/test_report.py`, `tests/load/test_cli.py`, `tests/load/test_fixtures.py`, `pyproject.toml`.
- **Steps:** implement an async HTTP client per session; encode a seeded representative operation mix; parse allowlisted gateway records/telemetry into the existing success, excluded, and service-failure buckets; represent empty/missing signals and saturation as insufficient evidence; enforce report size and field allowlists; verify data/audit/notification invariants and source identity.
- **Checks:** `pytest tests/load`; `ruff check`, `black --check`, and `pyright` on load tooling and tests.
- **Commit:** `feat(load): run authenticated gateway workload and report SLI inputs`

### 3. Real integration and operations guide

- **Acceptance:** required real MySQL/gateway mode fails if prerequisites or source identity are missing; docs provide safe setup, the short and standard run commands, report interpretation, and cleanup; deployment artifact association remains explicitly incomplete until a runtime attestation exists; no seven-day SLO claim.
- **Files:** `docs/tasks/reproducible-booking-load.md`, `README.md`.
- **Steps:** document the implemented opt-in real integration path, separate candidate database setup, required telemetry, safety guardrails, evidence and limits.
- **Checks:** documentation/source diff review; Docker-backed real MySQL/gateway run when a disposable lab stack is available.
- **Commit:** `feat(load): verify booking invariants and document lab runs`

## Review focus

- Refuse all writes before network/database activity when opt-in or candidate identity is missing or malformed.
- Never drop an existing or unowned database; cleanup must verify schema prefix, run UUID, and fixture marker.
- Preserve the gateway's distinction among success, input/business exclusions, service failures, and unknown classifications.
- Keep all 4xx business/input conflicts out of success and service-failure counts according to the logged gateway classification.
- Missing/empty SLI data, too few in-scope samples, telemetry gaps, and generator saturation must not become zero-error success.
- No credentials, cookies, tokens, names, account/session payloads, consultation text, or raw URLs with query strings in logs/reports.
- Proposal/book/cancel operations must use valid authorization, CSRF, idempotency, and expected state/context contracts.

## Verification ledger

- Task 1: fixture and guard unit tests passed (`pytest tests/load/test_config.py tests/load/test_fixtures.py -q` → 14 passed); Ruff and Black checks passed. Real MySQL setup could not run because Docker Desktop's Linux engine is not available in this environment; the required integration path remains for Task 3.
- Task 2: load tests passed (`pytest tests/load -q` → 43 passed); Ruff, Black, and Pyright passed. A full lab gateway/MySQL run has not been executed because Docker Desktop's Linux engine is unavailable and no external lab endpoints/credentials are configured.
- Task 3: operations guide and README index are in place. Docker Desktop service start was attempted but Windows denied access to `com.docker.service`; the Linux engine remained unavailable. No external lab endpoints/credentials are configured, so real integration evidence remains pending.

## Group-lead review follow-up

### 4. Close CI and runtime correctness findings

- **Acceptance:** MySQL DSNs cannot redirect schema writes; schema creation connects without selecting a not-yet-created database; fixture batches persist; workload request hooks and IDs are captured correctly; proposal reads use the student-only availability contract; every acceptance workload proves at least one proposal write/replay; gateway evidence is joined by request ID and bounded Loki windows; missing timeout telemetry remains unavailable; audit/notification invariants check distinct records and exact recipients; cancellation cleanup removes a schema created by the interrupted invocation; backend CI runs load checks and real MySQL/API acceptance.
- **Files:** `.github/workflows/backend-models.yml`, `src/backend/app/observability/metrics.py`, `tools/load/`, `tests/load/`, `tests/backend/unit/observability/`.
- **Checks:** load tests, backend tests, Black, Ruff, Pyright, and the required MySQL test in CI. Local Docker Engine is unavailable, so only CI can provide the live MySQL/API result.
- **Commit:** `fix(load): address review findings and wire MySQL acceptance`

### Target artifact association

Deployment evidence records active container image IDs/digests, configuration identity, and normalized origin. Deployment invalidates the served attestation before transitions; the evidence operation verifies each healthy service's active container image ID before publishing a sanitized fingerprinted view through the read-only gateway mount. The load runner checks freshness, compares the live target response with local evidence, and confirms the fingerprint is unchanged after the workload. A full live gateway/Loki/Prometheus workload run remains external because this Windows environment has no running Docker Engine or configured telemetry target.
