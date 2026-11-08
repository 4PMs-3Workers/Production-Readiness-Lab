# Agent requirements for PR #63

Scope: `feature/issue-53-reproducible-booking-load`, PR #63, Issue #53. Read this file, [the CI follow-up plan](pr-63-ci-followup-plan.md), and [the load operations guide](reproducible-booking-load.md) before continuing this branch.

## Establish current source

- Worktree: `D:/Production-Readiness-Lab-issue-53`; PR base: `develop`. Fetch origin, verify the PR head/base and local changes, and preserve pushed history and other worktrees. Push only this feature branch; do not merge or deploy.
- This worktree has no CodeGraph index. The main checkout has an older index that omits new load/backup files. Compare commits and source before referring to that graph; inspect changed/unindexed source directly and do not create an index.
- Check both push and pull-request workflow runs on the final SHA. PR jobs can execute a synthetic merge. The connector's commit-workflows wrapper filters to PR events; use the Actions runs endpoint with `head_sha` and pagination to inspect all events.

## Failure mechanisms and required protections

| Boundary | Evidence and rule |
| --- | --- |
| Nested bind mounts | Runs at `609aedd` failed with `mkdirat ... /etc/nginx/prl/deploy: read-only file system`. The parent `/etc/nginx/prl` was already a read-only directory bind. Keep evidence at the sibling `/etc/nginx/deploy-evidence`; update Nginx, both Compose overlays and the mount-relationship regression together. YAML parsing and literal assertions do not verify Docker startup. |
| SQLAlchemy URLs | The load MySQL test failed with 1045 while 2,349 other tests passed. `str(URL)` replaced its password with `***`. Pass URL objects to engines. Use `render_as_string(hide_password=False)` only for connection values that require strings; keep logs/reports redacted. Retain reserved-password-character regression and real create/verify/drop acceptance. Do not weaken authentication. |
| Failure cleanup | Ignored diagnostic directories are absent on fresh runners: create them before writing/copying. Always run the UUID-owned project's `compose down` in a `finally`, even if diagnostics fail. Leaked test networks can exhaust Docker address pools. Never prune global resources. |
| Evidence isolation | Acceptance must mount its own pre-created scratch directory through `PRL_DEPLOY_EVIDENCE_DIR`. Do not modify an active operator deployment's attestation. Preserve active image inspection, freshness, lifecycle invalidation and pre/post workload fingerprint checks. |
| External downloads | A transient redirect/read timeout aborted age installation. Keep at most three attempts, 1/2-second backoff and one 120-second cooperative deadline; discard partial response data. Retry transient network errors and HTTP 408/429/5xx only. Preserve pinned SHA256, size and extraction guards; never bypass integrity or acceptance checks. Read each fresh failure before treating it as an external outage. |

## Evidence required before completion

- Use the worktree's `.venv/Scripts/python.exe` and `PYTHONUTF8=1` on Windows. The existing Unicode seed password requires UTF-8; Linux CI uses it by default.
- After Compose/Nginx/test-harness changes, run affected contracts and the full backend unit suite. Search workflow/configuration consumers. Obtain real container acceptance from CI, including attestation HTTP response, business flows, telemetry and lifecycle. Retain zero-skip gates.
- For load changes, run `tests/load`, Black/Ruff and Pyright. Backend CI must include `tests/load` and require its MySQL/API acceptance with `--require-mysql`. Unit success cannot replace that test.
- For installer changes, run `tests/backend/unit/test_install_age.py` and affected off-host tests. Retain deadline, permanent-error, digest, size and partial-response regressions.
- Use repository-pinned formatter/type-checker versions. Locate/install missing tools or report exactly which check could not run. Run `git diff --check` and review the complete change before pushing.
- Never fix CI by relaxing business assertions, production budgets, health gates, schema ownership, telemetry completeness or zero-skip requirements.
- Container acceptance can take about 39 minutes plus cold setup; its job budget is 60 minutes. Inspect named progress/durations at bounded intervals. In-progress, cancelled, skipped and older-head results do not establish success.
- Verify every applicable push/PR workflow on the final pushed SHA. Correct new source failures and retry transient external failures in a bounded way. Report persistent external blockers with exact failed-step evidence. Update PR #63 with measured results and keep the live-load/seven-day-SLO limitations explicit.
