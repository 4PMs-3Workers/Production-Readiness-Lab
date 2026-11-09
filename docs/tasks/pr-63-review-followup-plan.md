# PR #63 review follow-up plan

## Goal

Resolve every new actionable review finding on PR #63 and keep the exact fixes covered by regressions.

## Acceptance checks

- Release preflight includes Nginx's whole-second timestamp boundary; log collection filters expected request IDs before applying the aggregate workload-record budget.
- Prometheus range queries are anchored to the actual workload start/end and report incomplete scrape coverage as unavailable.
- Timed-out booking/proposal retries that receive a rejection remain ambiguous; cancellation inventories contain only confirmed effects or explicitly ambiguous outcomes.
- Read-only deployment status preserves a valid runtime attestation; published attestation paths remain traversable under `umask 077`.
- Age installer retries a response that ends before its declared `Content-Length`, without weakening SHA256 verification.
- Focused regression tests fail before their fix and pass after; applicable backend/load checks and final PR CI pass.

## Implementation parts

1. **Gateway and telemetry** — `tools/load/gateway.py`, `tools/load/cli.py`, `tests/load/test_gateway.py`, `tests/load/test_cli.py`. Test preflight bounds, large expected ID sets with unrelated logs, and delayed telemetry collection with exact window and coverage. Commit: `fix(load): preserve complete gateway and runtime evidence`.
2. **Workload write outcomes** — `tools/load/runner.py`, `tools/load/invariants.py`, `tests/load/test_runner.py`, `tests/load/test_invariants.py`. Test same-key retry rejections and definitive versus ambiguous cancellation effects. Commit: `fix(load): retain unresolved workload write outcomes`.
3. **Deployment attestation** — `tools/deploy/evidence.py`, `tools/deploy/demo.py`, relevant backend unit tests. Test restrictive umask readability and status preserving the published runtime attestation. Commit: `fix(deploy): preserve readable runtime attestations`.
4. **Pinned age downloader** — `tools/backup/install_age.py`, `tests/backend/unit/test_install_age.py`. Test a real HTTPResponse with early EOF and a declared body length, then verify retry and pinned digest. Commit: `fix(backup): retry truncated pinned downloads`.

Run each focused suite after its tests and implementation. Finish with load/backend checks, format/type/lint checks, review the complete diff, push the feature branch and inspect CI for its final SHA.

## Progress

- [x] Gateway bounds, request filtering, workload-anchored runtime queries, and scrape-coverage invalidation.
- [x] Ambiguous same-key retry and cancellation effect inventory handling.
- [x] Public attestation directory permissions, lifecycle behavior, Compose source, and generated-artifact ignore rule.
- [x] Content-Length truncation detection with bounded retry and pinned digest verification.
- [x] Agent guidance updated with all eight review edge cases.
- [x] Local verification: `tests/load` 64 passed/1 skipped; focused deployment and task-6 contract tests 58 passed/1 skipped; installer tests 28 passed; Black, Ruff, Pyright, Prettier, and `git diff --check` passed.
- [ ] Verify Linux-only restrictive-umask test and all applicable PR/push workflows on the final pushed SHA.

## Follow-up for the next PR review (2026-11-09)

### Acceptance checks

- Load provenance verifies the gateway attestation fingerprint from the validated deployment manifest without requiring a colocated public attestation file.
- Worker backlog PromQL uses the Collector-exported `_ratio` gauge, requires enough metric samples across the recorded workload window, and covers every configured expected worker slot.
- Gateway event collection accepts the measured 22,554-request standard workload while keeping a finite 50,000-record aggregate ceiling and the existing 5,000-row per-query ceiling.
- Successful and failed `status`/`config` checks write under `checks/` and preserve the active deployment manifest and public attestation.
- Focused tests reproduce all five review findings before implementation and pass after.

### Implementation parts

1. **Worker evidence and bounded workload budget** — `tools/load/gateway.py`, `tests/load/test_gateway.py`, agent guidance. Test exported metric name, stale/missing worker coverage, expected slot count, and the measured 22,554-ID workload. Commit: `fix(load): cover measured workload and worker evidence`.
2. **Public runtime attestation identity** — `tools/load/report.py`, `tests/load/test_report.py`. Test the documented split between private manifest and public attestation. Commit: `fix(load): verify gateway attestation from manifest`.
3. **Read-only deployment checks** — `tools/deploy/demo.py`, `tests/backend/unit/test_demo_operator.py`. Preserve active artifacts on successful and failed status/config commands. Commit: `fix(deploy): preserve evidence during read-only checks`.
4. **Review safeguards** — this plan and `pr-63-agent-guidance.md`. Record measured limits and data-source contracts. Commit: `docs: record PR 63 review safeguards`.

### Progress

- [x] Reproduced all five findings with focused tests; the direct regression assertions failed before the relevant implementation changes.
- [x] Run all load tests, affected backend suites, formatting, lint, and `git diff --check`.
- [ ] Run Pyright and final PR CI; local Pyright cannot start because both `pyright` and `npm` are absent.
- [ ] Review full diff, commit each completed part, push, and verify CI on the final SHA.

### Ruling

When no deployment manifest exists, keep the existing initial `status`/`config` command record at the primary path for compatibility. When any prior manifest exists, write the read-only result under `checks/` and preserve the prior manifest and attestation. This keeps existing empty-stack contracts while protecting active acceptance evidence.

### Verification record

- [x] `tests/load`: 65 passed, 1 skipped.
- [x] `tests/backend/unit`: 1,486 passed, 1 skipped on the sequential rerun. One prior subprocess timeout occurred while the load suite ran concurrently; the same test passed alone in 1.72 seconds.
- [x] Focused post-format regressions: 75 passed, 1 skipped.
- [x] Black check and Ruff check passed; `git diff --check` passed.
- [x] Implement and commit load coverage/budget, manifest-based attestation identity, and read-only evidence preservation.
- [ ] Pyright and final CI are pending; the local shell currently has no `pyright` or `npm` executable.
