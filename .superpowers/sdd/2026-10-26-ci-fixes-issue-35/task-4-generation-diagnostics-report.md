# Task 4 Generation Diagnostics Report

## Scope

The diagnostic-only source change is limited to `tests/containers/test_telemetry_stack.py`. The capture points bracket due-expiry fixture setup: worker container metadata is read immediately before the fixture and immediately after it. If the existing 45-second stopped-metric wait fails, the failure snapshot adds a timeout worker inspection and the generation comparisons.

The snapshot reports a validated `StartedAt` epoch value, bounded restart count, allowlisted container status, exit code, and OOMKilled value. Comparisons report only container-identity booleans, `StartedAt` deltas, and restart-count deltas. Raw container IDs and worker UUIDs stay in local variables. On failure only, lifecycle records are read from at most the last 200 log lines per distinct observed container (3-second command timeout); the report includes counts for `starting`, `stopping`, `stopped`, and `fatal`, a valid-worker-ID count, and whether the latest record in each category matches the pre-fixture worker. It does not include log lines or IDs.

The failure snapshot also correlates the existing pre-snapshot started metric value and sample timestamp with the last stopped-metric poll's value and sample timestamp, and computes numeric deltas against the observed container `StartedAt` values. No acceptance threshold is inferred.

The 45-second wait, stability predicate, metric comparisons, assertions, Compose lifecycle, flags, cleanup, and production/workflow configuration are unchanged. Diagnostic inspection/log errors are represented by exception type or a fixed category only.

## CI evidence motivating the diagnostics

The push-triggered run [#40390989151](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/40390989151) and PR-triggered run [#40391000411](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/40391000411) for diagnostic commit `def9f89b00d61836bdd2814b9568a867908989c9` each finished with **143 passed, 1 failed, 0 skipped** at the unchanged stopped-metric settle wait.

Both sanitized snapshots showed completed successful Prometheus queries, one candidate per role, and matching series identity. The startup metric moved from `1793046619.006` to `1793046646.369` (a `+27.363` second delta); scan/progress values then advanced on that series before the wait expired. Worker and MySQL exited with code 0, restart count 0, and OOMKilled false. The push run showed a `+27.262` second startup-metric delta. This follow-up records evidence to distinguish a process restart during fixture setup from a producer/series alias; it does not change acceptance behavior.

## Verification

- `.venv/Scripts/python.exe -m ruff check tests/containers/test_telemetry_stack.py` — passed.
- `.venv/Scripts/python.exe -m py_compile tests/containers/test_telemetry_stack.py` — passed.
- `git diff --check` — passed (Git printed its CRLF-to-LF advisory for this file).
- `.venv/Scripts/python.exe -m ruff format --diff tests/containers/test_telemetry_stack.py` — only the four already-existing formatting hunks at HEAD remain; the new helper lines were manually aligned with Ruff output. No formatter was applied to unrelated code.
- The full container acceptance stack was not run for this diagnostics-only change.

## Commit

Scoped review: PASS. Commit message: `test(containers): capture worker generation diagnostics`.
