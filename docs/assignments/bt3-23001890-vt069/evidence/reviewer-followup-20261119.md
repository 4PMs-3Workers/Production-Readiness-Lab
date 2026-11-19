# PR #66 reviewer follow-up — 2026-11-19

This supplement records implementation of the review comments on PR #66. Earlier
CI and PDF artifacts remain historical snapshots; this file reports only checks
run for this follow-up source state.

## Findings addressed

| Review finding | Change and regression |
|---|---|
| Successful state proofs rejected a reviewed predecessor schema | `StateStore.load()` now checks the verification pin against the cached manifest selected as stable. The state validator accepts only current/reviewed predecessor pins. `test_state_store_reads_a_successful_predecessor_proof` writes and reloads an unchanged predecessor proof. |
| Transition started from an older commit instead of PR base `12a74f7308550ccc294a783edde79db267a526d1` | Replaced the stale file hash pairs with exact base-to-head identities for the backend/frontend Dockerfiles, main Compose file, grants, and image lock. The unit regression compares each `previous_configuration()` hash with `git show` from the actual PR base. The Linux configuration rehearsal now archives that same base. |
| Resume failed after migration stamped the candidate schema | Resume of the same immutable target accepts a complete candidate catalog only after observing the candidate revision; a partial catalog still fails closed. Tests cover retry after grant failure and resuming after automatic fallback was refused during read-only preflight. |
| A refused explicit rollback left a recovery operation on a healthy host | Rollback now resolves the exact target and completes image/plan/vendor/schema checks before recording a rollback operation. A refusal is recorded without setting recovery pending; a subsequent compatible deploy is tested. |
| Integration coverage omitted quota four and lowering below the current count | The MySQL contract test raises quota to four, creates the fourth appointment, lowers quota to two, verifies existing `UPCOMING` and `CHANGE_PENDING` appointments remain, and confirms a new booking is denied. |
| Predecessor Compose render injected the current schema pin | Rendering now uses the immutable manifest's accepted schema revision. A predecessor-render regression asserts the older pin. |
| Historical v1/v2 deployment evidence became unreadable | The evidence reader accepts only the current or explicitly reviewed predecessor schema; new evidence creation still writes the current pin. Tests preserve historical records of both versions. |
| Student error text and admin recovery comparison hid the configured quota | The booking error uses configuration-based wording. `ConfigurationSummary` now displays the quota; the frontend regression shows distinct submitted/current values when quota is the only difference. |
| A malformed JSON response could be counted twice in latency | The synthetic harness appends latency once in `finally`. A test injects one malformed response among 60 concurrent requests and asserts exactly 60 samples and one failure. |

## Verification on this workstation

- Backend targeted regression command: `.venv\Scripts\python.exe -m pytest tests/backend/unit/test_release_manifest.py tests/backend/unit/test_rollout_state.py tests/backend/unit/test_rollout_controller.py tests/backend/unit/test_backup_evidence.py tests/load/test_vt069_quota_load.py -q` — **100 passed**, exit 0.
- Frontend command: `node node_modules/vitest/vitest.mjs run --config src/frontend/vitest.config.ts tests/frontend/components/admin-booking-configuration.test.tsx tests/frontend/unit/api-services.test.ts` — **57 passed**, 2 files, exit 0.
- MySQL integration discovery: `.venv\Scripts\python.exe -m pytest tests/backend/integration/test_booking_quota_configuration.py --collect-only -q` — **3 collected**. The MySQL tests were not executed on this Windows host.
- Ruff check and format check on changed Python files passed; `git diff --check` passed.
- The Linux/Docker end-to-end configuration transition rehearsal is not claimed as a local result; its updated base commit is exercised by the PR workflow when CI runs.

## Limits

The MySQL contract test and Linux/Docker rollout rehearsal require CI's Linux service environment. Local unit results establish controller and evidence behavior with deterministic fixtures; they do not establish a production deployment or successful rollback across the schema migration.
