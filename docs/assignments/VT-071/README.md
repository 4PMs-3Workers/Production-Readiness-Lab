# BT3 - Nguyễn Tố Uyên - 23001952 - VT-071

Official APP-03 / Production Readiness Lab / SE2026-T18.
Baseline develop: `aa8c5b0be057754f9b3409c8699537d4aeb887b9` (2026-11-01).
Submission: `codex/bt3-23001952-vt071`. No merge. Original red/green chain
`687d9e0 -> a68d424 -> 4cfd6c6` and original logs are preserved.

## Review map: feature, validation, CI/dependency and evidence

PR55 is the combined assignment submission. The table below identifies the review
surfaces; it does not constitute separate PRs or a reduction of the existing raw diff.

| Surface | Paths / commits | Review focus |
|---|---|---|
| Export feature | Four files under app: api/appointments.py, repositories/appointments.py, schemas/appointments.py, services/appointment_export.py; docs/api/openapi.yaml; generated tools/deploy/nginx/route-map.conf | Route/auth/owner SQL/cap/CSV contract and finite gateway labels; booking.py remains baseline |
| Functional tests | tests/backend/integration/test_appointment_export.py; unit/test_appointment_export.py; unit/test_openapi_export.py | AC oracles, real DB failure, TC13 vs TC18; UtcTimestamp fixture typing fixed in e90325b |
| Validation tools/config | Three test_appointment_export_load*.py modules; tools/scripts/vt071_*.py; pyproject.toml | Opt-in load marker and greenlet/thread coverage tracking; these are test settings, not a production pool change |
| CI/dependency repair | e90325b: .github/workflows/backend-models.yml, package.json, package-lock.json, related marker/type fixes; containerized-demo.yml early generated-route check | Required functional zero-skip gate; development-only minimatch11.0.1 fix; fail early on a stale artifact before Docker acceptance |
| Report/evidence | docs/assignments/VT-071, especially evidence/ | Logs, all measured attempts, mutant metadata and analysis; not application code |

Read the smaller diffs independently, from the official baseline:

```powershell
$base = 'aa8c5b0be057754f9b3409c8699537d4aeb887b9'
git diff $base HEAD -- src/backend/app docs/api/openapi.yaml tools/deploy/nginx/route-map.conf tests/backend/integration/test_appointment_export.py tests/backend/unit/test_appointment_export.py tests/backend/unit/test_openapi_export.py
git diff $base HEAD -- .github/workflows/backend-models.yml .github/workflows/containerized-demo.yml package.json package-lock.json pyproject.toml
git diff --name-only $base HEAD -- docs/assignments/VT-071/evidence
git show --stat e90325b
```

Raw evidence is retained in Git for the assignment's immutable links; readers can
start with execution-notes.md and evidence/review-pool-summary.json rather than the
per-request JSON. For team integration, the proposed packaging is a feature PR,
a CI/dependency PR and a versioned evidence artifact with a checksum manifest.
That packaging remains a follow-up before team merge; this map keeps the combined
submission reviewable. PR55 has not been merged.

For the student's own review, [the four-file reading guide](student-diff-review-guide.md)
uses the fixed diff `aa8c5b0..15526cb` (118 additions,2 deletions under src/).
The guide was AI-prepared. On03/11/2026 the student confirmed reading the
four-file production diff and completing the oral-preparation questions. The src/
diff to4695035 is identical to the tested15526cb source.
[Student-authored review notes](student-diff-review-notes.md) record seven observations
and the decision: agree with the implementation approach, but investigate/remediate
performance further before accepting merge.

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

Attempt1's two counts (null0, production340) are observations, not verified error
classification. The auth stage and the name TimeoutError cannot distinguish a
SQLAlchemy queue timeout from a built-in transaction deadline. Attempt1 lacks the
leaf module/isinstance evidence added in attempt2; it is excluded from mechanism
claims and from causal claims that one configuration improves the other. The
295/302 classified pool failures in attempts2/3 support the pool-timeout finding.

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

## Pool follow-up: choices and evidence required before a change

Decision now: NFR-03 remains FAIL; no production setting is selected from these
diagnostic runs. First measure checkout wait and connection hold time separately
for auth/export, plus event-loop lag, MySQL CPU and query duration. Use a repeated
concurrency ramp5/10/20/40/80 with independent sessions and randomized configuration
order, on the same data/host protocol; retain unsuccessful and successful requests.

| Candidate, not implemented | Tradeoff and required evidence |
|---|---|
| Bound admitted concurrency / backpressure | Compare queue/rejection rate, completed throughput and p95; account for rejected work, not only requests admitted to DB |
| Increase bounded pool capacity | Check total across workers/replicas plus other DB clients against max_connections/headroom; show DB CPU/lock/latency and error rate remain acceptable |
| Increase pool wait timeout | Queue wait consumes the existing1s transaction /3s request budgets; it may move failure to another deadline. Measure end-to-end latency and cleanup, not just disappearance of pool exceptions |
| Reduce checkout count / transaction round trips | Trace checkout/checkin per request before changing transaction boundaries; verify auth/role/owner behavior and error/cleanup paths, with no shared session across concurrent requests |

Source inspection matters for the last option: optional_auth and the route use
get_db_session with default dependency caching, so the request reuses one session.
Auth finishes its transaction before export starts a separate read_transaction.
SQLAlchemy releases the connection at transaction end, then acquires for the next
transaction. The observed160 new NullPool connections per successful80-request
burst is consistent with two sequential acquisitions, not two connections held
simultaneously per request. This is a code/framework inference, not a new per-request
checkout-ID trace. Merging transactions might save checkout/round-trip overhead
while increasing hold time; retain role/session-revocation semantics and release
before CSV formatting. It is not a demonstrated fix.

For each candidate, vary one factor, repeat cold/warm bursts and compare all HTTP
errors, successful p95<=2.5s, scope/cardinality, auth/export wait/hold, cancellations
and post-burst connections. Count backpressure rejections in the acceptance result;
reducing admitted work alone does not satisfy the original80-request requirement.
Apply only after team review and a reproducible improvement without weakened auth
or recovery. TC18/TC13, role/scope and DB-fault recovery remain regression oracles.

Framework references: [SQLAlchemy pool-limit diagnosis](https://docs.sqlalchemy.org/en/20/errors.html#error-3o7r),
[session/transaction connection release](https://docs.sqlalchemy.org/en/20/orm/session_basics.html),
[FastAPI dependency reuse](https://fastapi.tiangolo.com/tutorial/dependencies/sub-dependencies/#using-the-same-dependency-multiple-times).
The application call path above was checked against the repository's pinned code.

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

### Container route-map follow-up

The e90325b container workflow finished1failed/175passed: the generated gateway
map was stale relative to the new export OpenAPI operation. Reproduced the exact
test locally (1failed), regenerated tools/deploy/nginx/route-map.conf with the
existing generator (seven added entries,31business operations), and reran the
original failing test plus URI-regex/gateway contracts:3passed in0.66s. Static
export precedes the parameterized appointment route in the generated finite map.
The workflow now checks generation before Docker acceptance for earlier feedback;
the original container test and zero-skip gate remain enabled. No timeout change.
See evidence/review-gateway-map-ci-failure.txt, review-gateway-map-red.txt and
review-gateway-map-green.txt. The new full container run is separate from the
175passing tests on e90325b; local checks do not claim a fresh full Docker pass.

## Student self-check on02/11/2026

The student supplied terminal output and a local recheck copy at source15526cb.
Verified captured functional41pass/45.39s and TC18 1pass/4.09s; coverage service
34/34lines,8/8arcs and diff50/50lines,10/10arcs. Their mutation helper produced
4kills/1survivor, with restored source and no mutant ancestor of the source branch.
New mutant commits are on a separate student-mutants-20261102 experiment branch.

Their production-pool run is a new FAIL:312/560HTTP503, all verified auth pool queue
timeouts. Errors per80:49,45,45,42,41,45,45; successful p95 range0.619..0.834s.
The prior Codex302/560 and all older runs remain separate. No setting/code changed;
this is a same-environment repeat, not independent-environment validation or a
before/after improvement experiment; NFR-03 remainsFAIL. Both runs use the same
Windows host and local MySQL. Each fixture has a new UUID schema and fresh login;
within a run,80clients share one authenticated session.

See [student execution ledger](evidence/student-20261102/README.md), summary/manifest,
and the compressed raw-evidence.zip (19files,~164KiB instead of~1.95MiB expanded).
The ledger maps local-only checkout/helper branch names to the published student
mutant branch; normalized JSON links to that public ref. Original metadata inside
the ZIP retains its local name and original checksum.
The ZIP preserves raw bytes; a small UTF-8 excerpt is provided for the UTF-16 terminal
logs. Codex collected/verified the student's data without rerunning their tests.
CI for source15526cb is verified successful in the source snapshot; evidence/doc
updates do not constitute a new code/CI execution. Earlier code/design/AI attribution
is retained; the old blanket statement that the student has not run commands is superseded.

## Evidence and ownership

Read spec.md, plan-review.md (verbatim plan prompt), test-design.md, post-test-review.md,
execution-notes.md, review-revision.md and ai-usage-log.md. Source and logs are runnable.
Earlier execution was delegated to Codex. On02/11/2026 the student personally ran
41tests, TC18, coverage, mutation and production-pool load; local artifacts were
cross-checked. Reflection now records that observed execution, with AI-assisted
editing. On03/11/2026 the student also confirmed reading the four-file production
diff and finishing the oral-preparation questions. The source of this confirmation
is recorded in student-diff-review-guide.md and summary.json. Team review and PR
separation remain steps before merge. self-check-guide.md supports further practice.
No force push or merge performed.


## Original prompts in the PDF appendix

[Prompt sources and provenance](prompts/README.md) preserve the complete initial
user request and the verbatim technical planning prompt. AppendixA prints original
excerpts/full planning prompt and separates them from the condensed usage table.
The PDF embeds the complete user prompt, planning prompt and AI Usage Log, like
the earlier assignment's Attachments workflow. Main content remains8pages;
four appendix pages bring the report to12pages. No source/test change or new run.


## AI tool disclosure and dated attribution

[AI Usage Log](ai-usage-log.md) now opens with the01/02/03-November timeline and
declares Codex plus Claude (Anthropic) review feedback relayed by the student.
[AI log manifest](ai-usage-manifest.json) records tools, attribution, byte size
and SHA256 for the embedded log. Original prompt checksums remain unchanged.
