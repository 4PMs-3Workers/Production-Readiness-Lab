# Execution evidence and limits

## Verified chain

- Official repository metadata: default develop, push permission available, baseline
  aa8c5b0be057754f9b3409c8699537d4aeb887b9; inspected 2026-11-01.
- Red 687d9e0: 5 failed / 9 passed, exit 1. Four/five successful-export scenarios
  reached valid MySQL fixture/login but got 422 from the UUID detail route. The final
  red log makes all five failures an HTTP behavior mismatch before fault monkeypatching.
- First green: 25 pass / 18.70s. After review: 27 pass / 24.14s.
- Mutation temporary local commit a500f24: source replay return removed. TC13 fails
  (503 != 200), 1 failed / 3.66s. No duplicate insertion was observed or claimed.
- Restored suite: 27 pass / 30.95s under coverage. Final suite including timezone
  inputs and OpenAPI regression: 33 pass / 43.10s. Reverse order: 29 pass / 24.84s.
- Temporary mutation is not an ancestor of the final submission branch. booking.py
  equals baseline; patch text/log retained without executable bug in final source.

## Coverage (coverage.py 7.17.0, branch enabled)

New export service: 33/34 executable statements = 97.06% line coverage; 7/8 branch
arcs = 87.5%. Combined coverage.py figure = 95.24%, which is not line coverage.
Modified existing files also include legacy paths outside this slice: API 53/62
statements, repository 46/69, schemas 59/62. Do not use those denominators as a
percentage of acceptance criteria proved.

The coverage tool leaves the overflow raise at service line 56/true arc unmarked,
although TC10 observed EXPORT_TOO_LARGE at 1001 rows and all assertions passed.
This measurement discrepancy is disclosed and remains uninvestigated; do not erase
it or report 100%. Raw executed/missing lines/arcs are in coverage-final.json.

Risk coverage: AC01 public CSV/UTC/quoting/formula strings (unit + real API); AC02
real STUDENT/no session/STAFF/ADMIN plus harness; AC03 exact owner IDs, forbidden
scope query, statuses/duplicates; AC04 empty/bound/real SQL failure/recovery and
deterministic timeout; AC05 real create/cancel/replay/export and killed mutation.
Not proved: real spreadsheet clients, revoked-session race, gateway/TLS/browser,
large/multiple-tenant datasets, distributed production load or permission approval.

## Flaky risk checks

| Risk | Cause / evidence | Control and result | Limit |
|---|---|---|---|
| Order/shared state | IDs reused across tests could collide or pollute ownership oracle | unique migrated schema per test; normal 29 export tests and exact reverse order 29 pass | no exhaustive permutation; schema cleanup exercised, not all crash scenarios |
| Time/timezone | local clock/offset could change CSV and midnight booking dates | fixed timezone-aware fixtures; +07/-05 equivalent instants both yield exact UTC strings; DB clock drives booking fixture | real midnight rollover and expiry races not simulated |
| Concurrency/network | scheduler/fresh MySQL connections under 80 tasks can hit deadlines | Barrier(80), no sleeps/random waits, 5 rounds; capture all failures rather than retrying them away | both runs show reliability instability; precise root cause not established |

## Performance/risk result: FAIL

Windows 11, CPython 3.12.14, MySQL Community 8.4.13, full FastAPI app via ASGITransport,
real MySQL TCP, NullPool, READ COMMITTED. One warmup, 5 rounds x80 tasks, independent
clients sharing one real student session; 100 owned rows plus one foreign canary.
No mocked DB. Default query timeout 1s, request 3s; no budget inflation to force pass.
p95 uses nearest rank: sorted[ceil(.95*80)-1]. Raw samples retained.

| Round | Initial p95 all (s) | Initial errors | Diagnostic p95 all (s) | Diagnostic 503 | Diagnostic p95 success (s) |
|---|---:|---:|---:|---:|---:|
| 1 | 1.611 | 58 | 1.719 | 80 | n/a |
| 2 | 1.946 | 0 | 1.930 | 80 | n/a |
| 3 | 1.798 | 0 | 1.820 | 22 | 1.775 |
| 4 | 1.807 | 0 | 1.899 | 64 | 1.537 |
| 5 | 1.817 | 0 | 2.053 | 0 | 2.053 |

Initial failures = 58/400; diagnostic failures = 246/400. p95 all below 2.5s is
insufficient with errors. Successful-only values are null when all requests fail.
First harness lacked failure status in JSON; raw HTTP log retained it. Improved
harness records status/error code and separates successful latencies. Both runs kept.
The status is observed; CPU/pool/deadline root cause is still a hypothesis. Next test:
deployment-like pooled engine + gateway and instrumentation, 80 distinct sessions,
1000 rows, account/status diversity and repeated cold/warm bursts.

## Traceability

I = tests/backend/integration/test_appointment_export.py;
U = tests/backend/unit/test_appointment_export.py;
O = tests/backend/unit/test_openapi_export.py;
L = tests/backend/integration/test_appointment_export_load.py.

| AC/risk | Cases | File / callable | Commit | Observed result |
|---|---|---|---|---|
| AC01/R3 | 01,11,15,17 | I real_login_scope...; U csv_text.../timestamp...; O deterministic... | a68d424 + final evidence commit | PASS |
| AC02/R1,R2 | 02,03,04,14 | I denies... / without_real_session... / real_session_role... | a68d424 | PASS |
| AC03/R1,R2 | 05,06,07,08 | I real_login_scope... / rejects_scope... / status_filter... | 687d9e0 -> a68d424 | RED -> PASS |
| AC04/R2,R4 | 09,10,12,16 | I empty/overflow/mock_hides...; U timeout... | a68d424 | PASS, coverage gap disclosed |
| AC05/R5 | 13 | I booking_replay_after_cancellation_exports_one_source_row | a68d424; temporary a500f24 | PASS -> mutation FAIL -> restored PASS |
| NFR/R6 | LOAD | L export_80_tasks_five_rounds | final evidence commit | FAIL, 503 under 80 tasks |

The phrase "final evidence commit" is resolved by the immutable commit containing
this file in GitHub; the PDF records its actual SHA after publication (no self-hash).
See README for runnable commands and ai-usage-log.md for prompts/review decisions.

## Setup/regression history

setup-error.txt is missing telemetry dependency, not red evidence. The first broad
regression (regression.txt) had 1003 pass/8 fail: one expected-path set needed the new
route; seven child-process failures used the old venv or could not import app.
An isolated editable virtualenv fixes the environment boundary; no backup/deploy
production code changed. The rerun is stored as regression-final.txt: 1013 passed
in 84.36s, exit 0, without erasing the earlier failures. Performance remains a
separate failing acceptance gate.

AI plan/diff review are AI reviews, not the required future group approval. Proposed
export permission/cap/CSV need stakeholder validation. No merge performed.
