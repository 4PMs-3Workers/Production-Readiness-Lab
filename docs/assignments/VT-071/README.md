# BT3 - Nguyễn Tố Uyên - 23001952 - VT-071

Official APP-03 / Production Readiness Lab / SE2026-T18.
Baseline develop: `aa8c5b0be057754f9b3409c8699537d4aeb887b9` (2026-11-01).
Submission: `codex/bt3-23001952-vt071`. No merge. Original red/green chain
`687d9e0 -> a68d424 -> 4cfd6c6` and original logs are preserved.

## Contract

GET /api/v1/appointments/export[?status=CANCELLED]: owned STUDENT history, UTF-8 CSV,
fixed public columns, UTC, private/no-store. STAFF/ADMIN denied. No owner/page query.
One SELECT LIMIT1001; >1000 returns409 instead of partial CSV. Formula-like prefixes
(including full-width variants) and leading tab/CR/LF are prefixed with apostrophe.
No real spreadsheet-client safety claim. No UI/queue/migration/new runtime dependency.
STUDENT-only/cap/columns remain proposals pending team review. Booking replay protects
source cardinality; no invented GET idempotency guard.

## Setup and current checks

Fresh checkout, Python>=3.11, dedicated local MySQL8.4. Never use production: fixtures
create/migrate a unique prl_models_test_<uuid> schema and drop only that owned schema.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt -e . coverage==7.17.0
$env:MYSQL_TEST_URL = '<dedicated local MySQL 8.4 admin URL>'
$env:PYTHONUTF8 = '1'
python -m pytest tests/backend/integration/test_appointment_export.py tests/backend/unit/test_appointment_export.py tests/backend/unit/test_openapi_export.py -q --require-mysql
python -m coverage run --source=app -m pytest tests/backend/integration/test_appointment_export.py tests/backend/unit/test_appointment_export.py tests/backend/unit/test_openapi_export.py -q --require-mysql
python -m coverage json -o docs/assignments/VT-071/evidence/review-coverage-full.json
python tools/scripts/vt071_diff_coverage.py
python tools/scripts/vt071_reverse_tests.py
python tools/scripts/export_openapi.py
```

37 export instances +4 OpenAPI =41 pass. Original red5fail/9pass (route absent); CSV
revision red7fail/10pass then green. Explicit greenlet/thread CTracer config resolves
same-TC10 measurement omission. Export service34/34 lines,8/8 arcs. Diff coverage from
official baseline: API6/6, repo7/7, schema3/3, service34/34 =50/50 changed executable
lines;10/10 arcs starting on changed lines. 100% does not prove all behaviors/states.

## Mutations: actual public commits, separate branches, do not merge

Original replay-only: a500f24b0aea8ac249251dfd30f534ffaa53a867 on
codex/bt3-23001952-vt071-mutation. Killed503 !=200; did not prove duplicated rows.

Direct/cardinality mutants: codex/bt3-23001952-vt071-mutants-verified. Exact commit
SHAs, tests and outcomes: evidence/review-mutants.json. Branch HEAD restores source.
Owner omitted -> exact IDs fail; overflow raise omitted ->200 !=409; both role
guards bypassed ->200 !=403. Primary role dependency alone survives because query
guard still requires STUDENT: no deny behavior changed in the executed cases.
Replay + unique index omitted only inside fixture-owned UUID test schema -> TC18
reaches CSV and fails "Expected one source row, got 2 CSV rows". Production schema
unchanged. Original and new mutant commits are not ancestors of submission.

M5 (`604c0e7`) adds six setup lines inside the test: imports, verification of the
fixture-owned schema name, and `ALTER TABLE ... DROP INDEX`. The existing TC18
assertions are unchanged; this setup-only edit is part of the mutant commit.

The mutant commits are sequential: each restores the preceding bug before applying
the next. Compare each mutant to clean source `81552d3`, not only to its parent:

```powershell
$clean = '81552d3c85075f6ebb1583178b52264163450d35'
git diff $clean 72e05f70a281c022983ca075525ea71ed4aaeba9 -- src/backend/app
git diff $clean 604c0e7e6743b0305503348e5f87d408881a4170 -- src/backend/app/services/booking.py tests/backend/integration/test_appointment_export.py
```

This clean experiment reference differs from the official assignment baseline
`aa8c5b0`; the assignment baseline is unchanged.

Reproduce on clean checkout with dedicated MYSQL_TEST_URL:

```powershell
python tools/scripts/vt071_review_mutants.py
```

Helper creates a local experimental branch, commits each mutant, captures logs and
restores source in finally; no automatic push/merge. Default local experiment branch
must not already exist. First runner attempt had a selector/classifier bug; archive
is separate and is not counted as a killed mutant.

## Cold/warm load and sanitized server diagnostics

```powershell
$env:BT3_RUN_LOAD = '1'
python -m pytest tests/backend/integration/test_appointment_export_load_review.py -q -s --require-mysql
Remove-Item Env:BT3_RUN_LOAD
```

80 clients share one real session;100 owned rows+1foreign canary; full ASGI app and
real MySQL TCP; NullPool/READ COMMITTED; deadlines request3s/query1s unchanged. One
serial request -> first80-task burst -> full80 warmup -> five warm bursts. Cold here
means first full burst, not restarted MySQL/empty OS cache. Every successful CSV
must have exactly100 rows AND expected ID set.

Review run at60fb77a:0/560 errors; first p95=1.516s; warm p95=1.316..1.527s (local PASS).
Historical runs remain FAIL (58/400 and246/400), with503 latency 1.418..1.947s across archived samples.
A percentile mixing failures and successes is not successful capacity. Root cause
of historical503s was not proved fixed; the confirmation below reproduces503.

Test-only server wrappers capture auth/export stage, exception class chain, integer
DB code, timing/requestID; never exception messages/SQL/cookies/tokens. Two injected
probes verify sanitization/code capture, not old503 causality. Latest real requests
all passed in that run, so its server failure chains are empty. Session read has no UPDATE per request.
Underlying CPU/pool/cold-start explanations remain unproved. Gateway/TLS/browser,
80distinct sessions, max1000rows under load and real spreadsheet clients unverified.

## Same-harness confirmation after MySQL startup: FAIL

Started a new MySQL8.4.13 process, verified preflight uptime12s, then ran the identical
review harness (Git blob recorded in evidence/review-restart-metadata.json). The
fixture creates a fresh migrated schema on every invocation, including earlier runs;
that by itself is not a new condition. Migration/seed/login/one serial request still
precede the first80-task burst, so this is not an empty-cache experiment.

Result:276/560 HTTP503. Errors per80: cold68, warmup3, warm1=0, warm2=44, warm3=1,
warm4=80, warm5=80. Last p95all=2.527s; successful-only p95 is null when all80fail.
Real failing chains occur in export: ServiceUnavailable -> TimeoutError ->
CancelledError; DB code is null. This confirms export read-transaction deadline
expiry under the unchanged1s budget, not its deeper CPU/connection/query cause.
Warm errors refute an explanation limited to the first burst. Harness differences
prevent direct causal comparison with the original two failing runs; restarting
alone has not been established as the cause by this one confirmation.

The acceptance test fails as intended (1failed,2diagnostic probes passed/19.40s).
Separate logs: review-restart-server.txt, review-restart-preflight.txt,
review-restart-load.txt and review-restart-performance.json. Earlier JSON was restored
byte-for-byte. No production code, timeout or harness changed; no broad functional
rerun was needed for this documentation/evidence clarification. Performance remains
an open FAIL gate; do not interpret the preceding local PASS as a demonstrated fix.


## Deployment-default pool comparison and sub-stage profiling

```powershell
$env:BT3_RUN_LOAD = '1'
python -m pytest tests/backend/integration/test_appointment_export_load_pool.py -q -s --require-mysql
Remove-Item Env:BT3_RUN_LOAD
```

New diagnostic harness uses a second app on the fixture-owned migrated UUID schema.
Production mode calls create_app(settings) without replacing engine_factory: actual
AsyncAdaptedQueuePool size5 + overflow5, queue timeout0.5s, connect1s, read1s, request3s.
Null mode changes only the factory to NullPool and keeps the same connect1s budget.
Earlier fixture used connect5s; this new protocol cannot be called the unchanged old
harness. Profiling explicitly checks out a connection inside read_transaction;
test-only wrappers time checkout, execute+buffered wire fetch, Result.all extraction,
mapping, rollback and CSV formatting. No production code/deadline/dependency changed.

Three sequential comparisons on one new MySQL process, with fresh schema per case:

| Attempt/order | NullPool errors/560 | Production errors/560 | Note |
|---|---:|---:|---|
| 1: null -> production | 0 | 340 | Auth503; exact leaf type not yet logged |
| 2: production -> null | 83 | 295 | Auth pool timeout verified; null rollback cancellations |
| 3: production -> null | 0 | 302 | Observer excluded from process list; final protocol |

Final production errors per80:49,46,45,35,41,45,41. All302 are HTTP503 at auth,
with leaf sqlalchemy.exc.TimeoutError (isinstance checked), before export runs.
The sampled pool reaches10 checked-out connections; Threads_connected peaks11
including the observer. This establishes queue timeout for those requests under
the configured0.5s limit; it does not prove a server-side SQL bottleneck. Successful
request p95 spans0.625..0.881s, which cannot compensate for302 failed requests.

In attempt2, all83 NullPool failures have ServiceUnavailable -> TimeoutError ->
CancelledError in export, and the intercepted cancellation is specifically at
transaction.rollback after execute, Result.all and mapping completed; format did
not start. Aborted_clients increases0->83 over that NullPool run. This locates the
cancellation await, not a slow SELECT or orphan-query root cause. Per-phase p95
event-loop lag reaches0.821s. Wall timers include scheduling and rollback's1s
transaction deadline; per-stage p95 values must not be added together.

Monitor samples nominally every250ms plus before/after bursts: global running/
connected threads, connections, aborted clients, uptime, sanitized process ID/
command/time/state and Windows all-core CPU mean. Query text, user, host, tokens and
error messages are omitted. Attempts1/2 include the monitor's own query in the
process list; attempt3 explicitly excludes CONNECTION_ID(). After each final burst,
only five idle pool connections remain for production, none for null; no active
owned-schema query persists in these snapshots. Transient orphans can be missed by
delayed sampling. No growing-query mechanism is established or ruled out.

Final all-core CPU means span11.3..29.4% (production),17.1..18.6% (null); this is not
per-core or mysqld CPU profiling. Same-host ASGI transport, shared session, scheduling
and sequential order remain limitations. No production fix, throughput improvement
or stable80-request acceptance is claimed. Pool capacity/backpressure and query
deadline are distinct observed failure paths; the performance gate remains FAIL.

Raw JSON/logs for all attempts, final source SHA256, order and metric limitations:
evidence/review-pool-summary.json, review-pool*-performance.json, review-pool*-load.txt.
Final load:1failed/1passed in23.90s; initial1failed/1passed23.55s; second2failed24.22s.
Load is opt-in. Functional recheck:41passed/2skipped in26.96s, recorded in
review-pool-functional.txt.

## PR55 CI follow-up

The models job stopped at seven Pyright errors: six datetime values supplied where
UtcTimestamp wrappers are declared, plus an ambiguous **dict passed to create_app.
Fixtures now construct UtcTimestamp explicitly, and the load app uses an explicit
engine_factory keyword only for null mode. No production behavior changed.

Three diagnostic modules carry the registered load marker. Functional CI selects
`-m "not load" --require-mysql`; the existing zero-skipped assertion remains intact.
All six opt-in instances are deselected, rather than weakening the required MySQL
gate. A local selection check reports24passed/6deselected, zero skips (7.16s).

Both security jobs found new GHSA-g3hv-75wg-m249 in dev-only minimatch10.2.6. The
upstream patched release is11.0.1; root npm override + regenerated lock select that
release without an advisory exception. npm12.2.0 clean install and audit report0
vulnerabilities. Frontend format/lint/typecheck/build pass;36files/349tests pass.
Pyright0errors and Black check308files / Ruff pass. Raw local CI evidence and the
original GitHub failure excerpts use the review-ci-* prefix.

Load numbers above were measured at c664a5d; the later marker/type spelling changes
do not represent a new performance measurement. review-pool-summary.json records
that measured source SHA256. No functional/CI PASS overrides the open load FAIL.

## Evidence and ownership

Read spec.md, plan-review.md (verbatim plan prompt), test-design.md, post-test-review.md,
execution-notes.md, review-revision.md and ai-usage-log.md. Source and logs are runnable.
The student explicitly said they have not personally run commands; execution was
delegated to Codex. Reflection remains an AI-assisted draft, not a manual-execution
claim. self-check-guide.md provides rerun and oral-defense preparation. No force
push or merge performed.
