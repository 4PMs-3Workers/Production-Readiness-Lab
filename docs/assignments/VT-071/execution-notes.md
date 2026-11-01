# Current execution evidence and limits - revised BT3 VT-071

Original baseline: aa8c5b0be057754f9b3409c8699537d4aeb887b9 (develop), checked2026-11-01.
Original submission 4cfd6c6 remains in history; original red/green/load logs preserved.
All execution below was delegated to Codex, not manually performed by the student.

## Test-first and final verification

- Original687d9e0:5fail/9pass (24.47s, exit1): missing export resolved as UUID route422.
  9passes are3role harness+5invalid query+1real missing session, not existing export.
- Original green:25pass/18.70s, reviewed27pass/24.14s, original final33pass/43.10s.
- CSV revision red:7fail/10pass (bare tab/CR/LF/full-width prefixes); green41pass/36.32s.
- After new mutants restored:41pass/45.77s under correctly configured coverage.
- Latest broad regression:1037pass/120.60s, exit0 (review-regression.txt).
- Reverse order: see reverse-order.txt for current37cases; original29 preserved as
  reverse-order-original.txt. Static/diff checks in review-static.txt.
- Setup import failures and first mutation-runner selector/classifier error are
  separately archived; neither is treated as a valid killed mutant/red behavior.

## Coverage: same-case experiment, then diff coverage

On the same pre-revision TC10, default tracing omitted line56; greenlet/thread
recorded it. Source was identical; review-coverage-default/greenlet JSON and logs
are retained. pyproject.toml now uses concurrency greenlet/thread and core ctrace.
The new full run records export service34/34 statements and8/8 branch arcs.
Diff coverage uses added lines intersected with coverage executable statements,
relative to official baseline: API6/6, repository7/7, schemas3/3, service34/34,
total50/50 executable changed lines and10/10 arcs originating on changed lines.
Raw line lists and denominator algorithm: review-diff-coverage.json and
tools/scripts/vt071_diff_coverage.py. Legacy whole-file percentages are not used
as an acceptance-criterion score. 100% does not establish all combinations/states.

Official source: https://coverage.readthedocs.io/en/latest/config.html#run-concurrency
explains explicit concurrency tracking. OWASP CSV input hazards (including control
and full-width prefixes) informed added cases; client safety is still unverified:
https://community.owasp.org/attacks/CSV_Injection

## Mutations: actual code and test-schema experiments

Original replay-only a500f24: TC13 fails503 !=200; original DB unique protection
remains, so no duplicate CSV observation was claimed. It is now publicly reachable:
https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/a500f24b0aea8ac249251dfd30f534ffaa53a867

New five mutants have isolated commits on codex/bt3-23001952-vt071-mutants-verified.
The branch head restores source; no experiment branch was merged into submission.

| Mutant | Result | Assertion/evidence | Immutable commit |
|---|---|---|---|
| owner | KILLED | exact owner ID list differs/foreign row included | [e812499](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/e81249977e8b3a4171f5cbefcc7b65d5a926dd72) |
| overflow | KILLED | 200 !=409 at1001rows | [72e05f7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/72e05f70a281c022983ca075525ea71ed4aaeba9) |
| role_primary | SURVIVED | 403still returned: query dependency requires STUDENT | [5876f65](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/5876f656c6fbade7620ff0f3b5b9223c949d6002) |
| role_all | KILLED | STAFF/ADMIN200 !=403 | [9bdf7ed](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/9bdf7edb1d7e3f1ba82ad412df61f8d5ad96f8fa) |
| source_and_unique | KILLED | Expected one source row, got2CSV rows | [604c0e7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/604c0e7e6743b0305503348e5f87d408881a4170) |

The primary-role survivor did not change observed denial in those cases: redundant
protection is retained. No artificial test was added to kill behavior-equivalent
protection. Bypassing both guards is detected; do not report a simplistic mutation %.
Source+unique mutant drops only the named index in the fixture-owned UUID schema,
validated before DDL. It reaches TC18's CSV count oracle. This is counterfactual;
production schema is unchanged. TC13 still checks exact200/original receipt.
Original and all new mutant commits are not ancestors of submission. booking.py
blob still equals baseline; final tests contain no schema-dropping mutation.

## Load: historical FAIL and current local PASS must both remain visible

ASGI app + real MySQL8.4.13 TCP; Windows11/Python3.12.14,16logical CPUs, NullPool,
READ COMMITTED, request/query deadlines3/1s. 80clients share one real STUDENT session;
100owned+1foreign row. Session read is SELECT only, no per-request UPDATE.
Historical runs:58/400 and246/400 failures; p95all1.611..2.053s; failed responses
roughly1.7-2.0s. A mixed percentile does not measure successful capacity. Original
logs lacked exception class, so earlier503 root cause is not known.
Current: serial request -> first80burst ('cold') -> full80warmup -> five warm bursts.
Cold is not a database restart/empty cache. Record count100 AND exact ID set.

| Phase | p95success(s) | Errors/80 | Observed server failures |
|---|---:|---:|---|
| cold | 1.516 | 0 | {} |
| warmup80 | 1.307 | 0 | {} |
| warm1 | 1.428 | 0 | {} |
| warm2 | 1.316 | 0 | {} |
| warm3 | 1.415 | 0 | {} |
| warm4 | 1.527 | 0 | {} |
| warm5 | 1.379 | 0 | {} |

Latest local560/560 success, PASS for recorded fixture. Historical failures are not
explained or proven fixed. Empty real failure chains mean no causal CPU/pool/deadline
conclusion. Test-only server wrappers record auth/export stage, type chain, integer
DB code, timing/requestID, never exception messages/SQL/cookies/tokens. Two injected
probes verify sanitization/code1054/TimeoutError capture; they are not historical
failure evidence. Before-cardinality and final review load runs are both retained.
Not proved: gateway/TLS/browser,80distinct sessions, max1000rows under load, real
spreadsheet import/save/reopen, production reliability, stakeholder permission policy.

## Flaky risks and traceability

Order/shared-state: each test owns migrated UUID schema; original normal/reverse29
pass, new reverse37 is recorded. No exhaustive permutation/crash cleanup guarantee.
Time: fixed aware fixtures and equivalent+07/-05 instants produce exact UTC;
booking uses DB clock. Midnight/expiry races not simulated. Concurrency: Barrier80,
no random sleeps or retry suppression; all cold/warm samples, including old failures,
retained. New run's success does not remove observed environment sensitivity.

Files below are relative to tests/backend, with executable callable names in source.

| AC/risk | Cases | Test file | Commit chain/result |
|---|---|---|---|
| AC01/R3 |01,11,15,17|integration/test_appointment_export.py;unit/test_appointment_export.py;unit/test_openapi_export.py|687d9e0 ->a68d424 ->fd4e2ed;PASS|
| AC02/R1,R2 |02-04,14|integration/test_appointment_export.py|a68d424;PASS;primary-role survives,both-guards mutant killed|
| AC03/R1,R2 |05-08|integration/test_appointment_export.py|RED ->PASS;owner mutant killed|
| AC04/R2,R4 |09,10,12,16|integration/test_appointment_export.py;unit/test_appointment_export.py|PASS;overflow mutant killed;coverage omission resolved|
| AC05/R5 |13,18|integration/test_appointment_export.py|PASS;replay-only and replay+test-unique killed;restoredPASS|
| NFR/R6 |LOAD|integration/test_appointment_export_load_review.py|historicalFAIL,currentPASS,causalityunproved|

Exact new source base81552d3 and all mutant commit hashes are in review-mutants.json;
final evidence commit contains restored source/tests and this record. PDF cites that
actual immutable final commit rather than trying to embed a self-hash here.

## AI disclosure and ownership

Actual plan prompt is preserved verbatim in plan-review.md. Actual post-test review
and the multiplicity correction are in post-test-review.md/ai-usage-log.md. Student
said they have not run commands; execution is attributed to Codex. Reflection is332
words and remains a draft. First-page commitment is retained; responsibility to
understand/review AI-assisted work is distinct from claiming manual command execution.
No force push/merge. Human/team review and stakeholder validation remain outstanding.
