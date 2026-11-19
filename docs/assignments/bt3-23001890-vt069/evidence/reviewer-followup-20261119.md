# PR #66 reviewer follow-up — 2026-11-19

This supplement records implementation of the review comments on PR #66. Earlier
CI and PDF artifacts remain historical snapshots; this file reports only checks
run for this follow-up source state.

## Findings addressed

| Review finding | Change and regression |
|---|---|
| Successful state proofs rejected a reviewed predecessor schema | `StateStore.load()` now checks the verification pin against the cached manifest selected as stable. The state validator accepts only current/reviewed predecessor pins. `test_state_store_reads_a_successful_predecessor_proof` writes and reloads an unchanged predecessor proof. |
| Transition started from an older commit instead of PR base `12a74f7308550ccc294a783edde79db267a526d1` | Replaced the stale hash pairs with exact base-to-head identities for the two files that actually differ: main Compose and grants. The unit regression compares both Git object hashes against the PR base and HEAD. The Linux configuration rehearsal now archives that same base. |
| Resume failed after migration stamped the candidate schema | Resume of the same immutable target accepts a complete candidate catalog only after observing the candidate revision; a partial catalog still fails closed. Tests cover retry after grant failure and resuming after automatic fallback was refused during read-only preflight. |
| A refused explicit rollback left a recovery operation on a healthy host | Rollback now resolves the exact target and completes image/plan/vendor/schema checks before recording a rollback operation. A refusal is recorded without setting recovery pending; a subsequent compatible deploy is tested. |
| Integration coverage omitted quota four and lowering below the current count | The MySQL contract test raises quota to four, creates the fourth appointment, lowers quota to two, verifies existing `UPCOMING` and `CHANGE_PENDING` appointments remain, and confirms a new booking is denied. |
| Predecessor Compose render injected the current schema pin | Rendering now uses the immutable manifest's accepted schema revision. A predecessor-render regression asserts the older pin. |
| Historical v1/v2 deployment evidence became unreadable | The evidence reader accepts only the current or explicitly reviewed predecessor schema; new evidence creation still writes the current pin. Tests preserve historical records of both versions. |
| Student error text and admin recovery comparison hid the configured quota | The booking error uses configuration-based wording. `ConfigurationSummary` now displays the quota; the frontend regression shows distinct submitted/current values when quota is the only difference. |
| A malformed JSON response could be counted twice in latency | The synthetic harness appends latency once in `finally`. A test injects one malformed response among 60 concurrent requests and asserts exactly 60 samples and one failure. |

The first PR run on this source also exposed a newly published Next.js audit finding. The current npm audit reported two moderate advisories affecting `next` 16.4.1 and listed 16.4.6 as the fix. `next` and `@next/eslint-plugin-next` are now both pinned to 16.4.6. After that upgrade, npm audit reports zero vulnerabilities, so the two expired exceptions for `braces` and `minimatch` were removed instead of renewed. The local fail-closed dependency policy now approves with zero findings.

The first rollout workflow run after updating the baseline failed because the transition helper returned `None`. Review of Git object hashes showed the patch had mistaken Windows CRLF working-tree digests for changes in the Dockerfiles and image lock; those blobs are identical at the actual base and HEAD. Those entries were removed. Only `compose.yaml` and `grants.sql` remain in the reviewed transition.

## Verification on this workstation

- Backend targeted regression command: `.venv\Scripts\python.exe -m pytest tests/backend/unit/test_release_manifest.py tests/backend/unit/test_rollout_state.py tests/backend/unit/test_rollout_controller.py tests/backend/unit/test_backup_evidence.py tests/load/test_vt069_quota_load.py -q` — **100 passed**, exit 0.
- Frontend command: `node node_modules/vitest/vitest.mjs run --config src/frontend/vitest.config.ts tests/frontend/components/admin-booking-configuration.test.tsx tests/frontend/unit/api-services.test.ts` — **57 passed**, 2 files, exit 0.
- After the patched Next.js update, the full frontend suite passed **500 tests across 44 files** with 2 workers on Node 24.22.1; `next typegen`, TypeScript, and frontend lint also passed.
- Dependency audit on Node 24.22.1/npm 12.2.0: npm audit — **0 vulnerabilities**; Python pip-audit — **0 findings**; `check_dependency_advisories.py` — **approved, 0 findings**, exit 0.
- Pyright initially found an optional `operation` access in a new controller assertion; an explicit non-null assertion now guards it. Final Pyright result: **0 errors, 0 warnings, 0 informations**.
- MySQL integration discovery: `.venv\Scripts\python.exe -m pytest tests/backend/integration/test_booking_quota_configuration.py --collect-only -q` — **3 collected**. The MySQL tests were not executed on this Windows host.
- Ruff check and format check on changed Python files passed; `git diff --check` passed.
- The Linux/Docker end-to-end configuration transition rehearsal is not claimed as a local result; its updated base commit is exercised by the PR workflow when CI runs.

## Limits

The MySQL contract test and Linux/Docker rollout rehearsal require CI's Linux service environment. Local unit results establish controller and evidence behavior with deterministic fixtures; they do not establish a production deployment or successful rollback across the schema migration.


## CI follow-up after a8eb5f2

Actual job logs identified stale rollback assertions, an uncached successful state fixture, and a fourth booking at the same already occupied slot. The fixture now caches its exact manifest; the fourth booking uses a different available slot. Healthy preflight refusals preserve the successful operation; interrupted recovery still begins a rollback operation and preserves the recovery target. The Linux rehearsal checks refusal and then deploys a subsequent compatible candidate.

Local verification after these fixes: the entire rollout unit family plus release manifest and synthetic load regression passed **238 tests**. Ruff passed; Pyright reported **0 errors, 0 warnings, 0 informations**. For local hash-sensitive checks, the three unchanged tracked configuration files were temporarily represented as LF bytes (matching Git/Linux), then their original Windows CRLF bytes were restored without staging them. The transition now contains only two actual changed Git blobs. PR-specific agent instructions are in root `AGENTS.md`.

The previous backend CI also had one readiness elapsed-time boundary failure (0.700024 s against 0.7 s); no production timeout or test threshold was changed. A fresh run will establish whether this recurs.

The Linux rehearsal on `179fbb2` passed the actual-base upgrade and reached the availability probe, which returned 200 from both images. The old 503 expectation belonged to the earlier `724169a` predecessor. `git diff 12a74f7 -- src/backend/app/services/availability.py` is empty. The assertion now requires 200 and identical persisted slot outcomes; rollback compatibility still uses the predecessor manifest schema and exact catalog.

The full backend run at `179fbb2` finished with 2 failures / 2,519 passes: the shallow checkout lacked the baseline Git object, and the sync readiness latency test measured 0.702841 s against 0.7 s. Baseline Compose/grants bytes are now small fixtures with exact reviewed SHA256 checks; no runtime history lookup is required. Readiness measurement now uses async ASGI on one event loop with the same 0.2 s deadline and 0.7 s response bound, plus query and connection-cleanup assertions. The health module locally passed 12 tests (5 MySQL cases skipped); the revised deadline test passed five independent runs. Full rollout/manifest/load tests again passed 238 cases. Pyright: zero errors/warnings. No production timeout or assertion threshold was increased.
