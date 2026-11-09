# Current execution evidence and limits - revised BT3 VT-071

Original baseline: aa8c5b0be057754f9b3409c8699537d4aeb887b9 (develop), checked2026-11-01.
Original submission 4cfd6c6 remains in history; original red/green/load logs preserved.
Historical execution through U22 was delegated to Codex. The separately dated
02/11 student self-check below records their own terminal execution; earlier logs
are not retroactively reassigned.

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
M5 commit604c0e7 adds six setup lines to the test; its cardinality assertions stay
unchanged. Because mutant commits are sequential, use git diff81552d3 <mutant>
to inspect one mutation against clean experiment source, rather than only its parent.
The official assignment baseline remains aa8c5b0.
Original and all new mutant commits are not ancestors of submission. booking.py
blob still equals baseline; final tests contain no schema-dropping mutation.

## Load: historical FAIL and current local PASS must both remain visible

ASGI app + real MySQL8.4.13 TCP; Windows11/Python3.12.14,16logical CPUs, NullPool,
READ COMMITTED, request/query deadlines3/1s. 80clients share one real STUDENT session;
100owned+1foreign row. Session read is SELECT only, no per-request UPDATE.
Historical runs:58/400 and246/400 failures; p95all1.611..2.053s; failed responses
1.418..1.947s across archived samples. A mixed percentile does not measure successful capacity. Original
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

The60fb77a review run had560/560 success, PASS for its recorded fixture. Historical failures are not
explained or proven fixed. Empty real failure chains in that review run meant no causal CPU/pool/deadline
conclusion. Test-only server wrappers record auth/export stage, type chain, integer
DB code, timing/requestID, never exception messages/SQL/cookies/tokens. Two injected
probes verify sanitization/code1054/TimeoutError capture; they are not historical
failure evidence. Before-cardinality and final review load runs are both retained.
Not proved: gateway/TLS/browser,80distinct sessions, max1000rows under load, real
spreadsheet import/save/reopen, production reliability, stakeholder permission policy.

### Same-harness confirmation after a fresh MySQL process

The 560/560 PASS above describes the60fb77a review run, not a causal improvement.
Subsequent confirmation used the identical harness after a new MySQL process start
(preflight uptime12s), plus a fresh fixture schema. All earlier invocations also
created fresh schemas. Migration/seed/login/serial warmup still precede the burst.

Observed276/560 HTTP503; failures per burst68,3,0,44,1,80,80. Every real failure
is logged at export with ServiceUnavailable/TimeoutError/CancelledError chain,
no integer DB error code. The unchanged1s read_transaction deadline expired;
CPU vs connection vs query vs rollback timing is not isolated by these wrappers.
Both cold and warm gates FAIL; warm5 p95all2.527s, p95success null. This rules out
a claim that only the first burst is vulnerable, but does not prove startup alone
caused the failures. Same review harness permits a clearer replication; comparison
against the older, different harness is not a controlled causal improvement study.

Evidence: review-restart-{server,preflight,load,performance,metadata} files. Result:
1failed/2probe-pass in19.40s. Earlier review-performance.json restored byte-for-byte.
Performance verdict remains open FAIL; no code/deadline was modified to force pass.

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
| NFR/R6 |LOAD|integration/test_appointment_export_load_review.py|historicalFAIL,reviewPASS,restartFAIL;causalityunproved|

Exact new source base81552d3 and all mutant commit hashes are in review-mutants.json;
final evidence commit contains restored source/tests and this record. PDF cites that
actual immutable final commit rather than trying to embed a self-hash here.

## AI disclosure and ownership

Actual plan prompt is preserved verbatim in plan-review.md. Actual post-test review
and the multiplicity correction are in post-test-review.md/ai-usage-log.md. Before02/11 the student said they had not run commands; those historical logs
remain attributed to Codex. The later student self-check supersedes that current
status; reflection is now AI-assisted editing of their observed execution. First-page commitment is retained; responsibility to
understand/review AI-assisted work is distinct from claiming manual command execution.
No force push/merge. Human/team review and stakeholder validation remain outstanding.

## Production-default pool diagnostic follow-up

New opt-in test_appointment_export_load_pool.py exercises the default app factory
(5+5/0.5s) and NullPool under the same diagnostic protocol, connect/query/request1/1/3s.
Final302/560production503 are verified SQLAlchemy pool timeouts at auth; sampled
checked-out count reaches10. NullPool final0/560, but attempt2 had83/560 cancellation
failures at rollback after buffered SELECT/mapping. Aborted_clients rose83. Nominal
250ms process snapshots exclude observer in final protocol; no active owned query
persisted after its bursts. Transient queries and host/core contention remain open.
All three comparisons and two instrumentation corrections are retained, not just
the final pass. See README and evidence/review-pool-summary.json for limits and
raw source indices. Production code and all deadlines remain unchanged.

## Review__4_ documentation follow-up

README maps feature, validation config, CI/dependency and raw-evidence paths for
review without rewriting history or splitting PR55. Attempt1 counts remain valid
observations but not classified mechanism evidence. Pool options are proposed
experiments with wait/hold and acceptance measurements, not production fixes.
The shared request-session / two sequential transactions explanation is from code
and official framework documentation; no new per-request connection-ID trace was
executed. Student guide adds explicit TC18/pool commands and preserves canonical
JSON on rerun. No tests or load measurements were rerun for documentation changes.
Prior CI remains bound to e90325b (1774pass/6deselected/zero skipped), not relabeled
as a run on the new documentation commit.

## Container CI follow-up after e90325b

Actual CI:1failed/175passed in2171.06s; failure was the deterministic generated-map
check, not a failing Docker scenario. Exact failing test reproduced locally before
generation; seven export entries added by tools/infra/generate_gateway_routes.py.
Generator --check verifies31business operations; original test + URI/gateway unit
contracts:3pass/0.66s. Workflow adds a fast generation check before Docker. Scope
map includes the generated artifact and workflow. Full CI rerun remains a separate
result; no local full Docker pass is claimed.

## Student self-check on02/11/2026 — new execution attribution

The student supplied captured logs from a separate copy at source15526cb. Codex
read the files and verified41pass/45.39s, TC18 1pass/4.09s, service34/34lines/8arcs,
diff50/50lines/10arcs,4killed/1survived mutants and source restoration. Coverage
run41pass/69.38s is from the student's pasted terminal; coverage JSON was checked.
New load312/560503 all at auth with SQLAlchemy pool TimeoutError; phase errors
49,45,45,42,41,45,45. No causal improvement or production fix is claimed.
Raw19files are compressed with a checksum manifest under evidence/student-20261102.
Student mutant commits remain on a separate restored experimental branch, never
merged. The original code/test/evidence chain is unchanged. Source15526cb CI now
verified successful; current documentation does not claim new test execution.


## Final metadata and wording follow-up

Published mutant ref and local-only checkout/helper refs are now explicit in the
student ledger and standalone JSON. The raw ZIP retains its original metadata and
all original bytes/checksums.302and312are same-environment repetitions, not an
independent-environment validation. Each load fixture logs in afresh and owns its
UUID schema;80clients share a session within each run. A source-pinned four-file
diff guide is prepared for the student's own review, which remains not reported.
Reflection is unchanged. This update changes documentation/navigation only;
no code/config/tests, raw measurement, test run, PR split or merge is claimed.


## Student diff review and oral preparation confirmed on03/11/2026

The student confirmed reading the production diff and finishing the oral questions
in this chat. The recorded scope is the four-file src/ diff aa8c5b0..4695035,
identical to the15526cb production source already exercised on02/11. The guide
checklist and summary metadata now record completion by student confirmation.
Reflection includes that reported activity, with AI editing still declared.
This supersedes the earlier pending-review status; it does not relabel historical
AI experiments or introduce new test/load output. Team review/PR separation remain
before merge. No production/test/config change or raw evidence modification.


## Review__9_ wording and link verification

The public confirmation now uses a formal attribution summary rather than an
informal verbatim chat quote. Student-confirmed scope/date are unchanged.
Reflection is shortened to330words, retaining all measured results and limits.
The four PDF-linked Actions runs were rechecked through the GitHub connector:
Backend models, Frontend, Containerized demo and MySQL recovery all completed
successfully at source15526cb. This does not assign those runs to the newer
documentation commit. Raw evidence/archive/manifest remain byte-identical.


## Personal review notes supplied on03/11/2026

student-diff-review-notes.md preserves seven student-authored statements about
the four production files,1001sentinel, unresolved80-concurrency503 and the
conditional pre-merge decision. The report now links these notes. The student
agrees with the approach but wants further performance checks/remediation before
accepting merge; this does not change NFR-03 or imply team approval/new execution.


## Original prompt appendix

The original user attachment is archived without byte changes under prompts/,
alongside the full technical planning prompt already preserved in plan-review.md.
The PDF prints exact original excerpts and embeds the complete sources plus the
current AI Usage Log. Prompt excerpts and condensed input/output records are
labeled separately. AppendixA spans2pages for legibility; the8main pages and
execution/data appendices remain, making12pages total. No experiment rerun.


## AI attribution and appendix consistency update on09/11/2026

The embedded log header now identifies initial Codex work on01/11, the student's
self-execution on02/11 and personal diff review/notes on03/11. Claude/Anthropic
external feedback supplied by the student is declared, with no unverified blanket
claim about its repo/test access. ai-usage-manifest.json and prompts/manifest.json
cover the log's canonical UTF-8/LF bytes and tool roles. Printed appendix wording,
review finding/evidence links and B1/B3 reference placement are revised.
Original prompt bytes, raw ZIP, code/tests/config and measured outcomes unchanged.
