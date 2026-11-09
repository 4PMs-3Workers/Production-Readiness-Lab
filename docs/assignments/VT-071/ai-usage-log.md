# AI Usage Log - actual interactions

Initial work:2026-11-01. Attribution updated:2026-11-09, Asia/Bangkok.

## Execution and review attribution by date

- 01/11: Codex performed the initial planning, patch/test work and experiments.
- 02/11: the student personally reran41functional/OpenAPI tests, TC18, coverage,
  mutation and the production-pool load; separate captured evidence is preserved.
- 03/11: the student confirmed reading the four-file production diff, completing
  oral-preparation questions and supplied seven personal review statements.
- 09/11: Codex reproduced and repaired the release-transition hash mismatch on
  an isolated integration of develop92d9157 with submission6184652; related
  Windows unit checks passed98cases. Full Linux checks are run through PR55 CI.
- Team approval/merge is not recorded. Student review and self-execution above
  are distinct from the earlier Codex phase. See U23-U31 and the student ledger.

## AI tools and contribution scope

Codex with one coding-agent subtask: repository/lecture analysis, implementation
plan, tests/patch, post-test review, experiments and PDF authoring. Shell/Git,
GitHub connector, Python/MySQL and ReportLab/Poppler support those activities.

Claude (Anthropic, review feedback relayed by the student in chat): supplied
external review inputs on coverage/greenlet, mock/DB contracts, mutation/M5,
NullPool/production-pool interpretation, PR scope and report consistency. Codex
checked proposed changes against repository files and attributable execution logs
before using them. The model version and external tool access are not independently
established here. Some supplied reviews self-report repository/sandbox checks;
they remain external-review claims and are not counted as Codex/student test runs.
This disclosure does not assert that Claude never accessed a repository or ran tests.

Original prompt sources: [complete user prompt](prompts/original-request.txt),
[verbatim planning prompt](prompts/plan-prompt.txt), and
[provenance/checksums](prompts/manifest.json). Tool/timeline attribution is recorded in [ai-usage-manifest.json](ai-usage-manifest.json).
The table below is a condensed
interaction history, not a verbatim transcript of each prompt. The PDF now prints
original excerpts and the full planning prompt, with complete source files embedded.

| Step | Actual prompt/input | Output used | Review/change/rejection | Evidence |
|---|---|---|---|---|
| U1 | User's full BT3 VT-071 prompt and two local lectures | requirements/rubric inventory | distinguish requested contract from existing code | spec.md; environment.json lecture hashes |
| U2 | Inspect official repo/default branch, local source, README, conventions | develop baseline, FastAPI/MySQL/auth boundaries | use separate worktree instead of user's existing feature branch | baseline SHA; Git history |
| U3 | Coding-agent task: read only aa8c5b0, propose small export slice, AC/files/tests/fallback; no code or fabricated plan errors | initial plan in plan-review.md | P1 reject page loop under READ COMMITTED; P2 require formula handling; P3 separate export service | plan-review.md; actual DB/repository/model code |
| U4 | Root task: derive decision rules/owner fixtures and executable API tests before implementation | test_appointment_export.py, decision table | setup failure excluded from red; route 422 failures accepted | red.txt, setup-error.txt; commit 687d9e0 |
| U5 | Implement reviewed status-only bounded CSV slice, reuse auth/transaction/public mapping; no guard for GET | 4 production files, deterministic OpenAPI | inspect Scope/Design/Correctness/Evidence before test; no runtime dependency added | commit a68d424; green.txt |
| U6 | Post-test agent prompt: read actual diff/new service/unit files and 25-pass log; separate evidence-supported findings from hypotheses; do not edit/manufacture claims | owner/bound/fault findings; impossible zero-duration fixture; missing real role sessions; CPU hypothesis | accept C1-C4; fix time fixture, add actual session-role tests; do not accept CPU bottleneck as proven | post-test-review.md; green-reviewed.txt |
| U7 | Remove existing booking replay return in temporary branch; run source workflow; restore | one-line deliberate bug | test caught 503 != 200; DB uniqueness still protects duplication; do not claim GET mutation | a500f24 local; mutation.txt/patch; restored coverage |
| U8 | Measure line/branch, reverse test order, explicit timezone inputs | coverage and 29 reversed tests | report measurement gap honestly, not 100% claim | coverage-final.json; reverse-order.txt |
| U9 | Five rounds x80 barrier-started tasks against full ASGI+real DB | raw latencies, status counts, scope checks | first report lacked per-failure status; preserve initial, improve harness and rerun; both FAIL | performance-initial.json, performance.json, load logs |
| U10 | Run broad regression and inspect failures | existing OpenAPI route-set regression exposed new path | add route contract assertions; switch to proper isolated editable virtualenv for child processes | regression.txt, regression-final.txt; test_openapi_export.py |
| U11 | Build A4 report <=8 main pages plus AI/evidence appendices from actual outputs | final PDF | render every page, check Unicode/tables/links/page count; no invented results | delivered PDF; local QA |

Main plan prompt (subtask): "Read only baseline aa8c5b0, propose a small executable export
slice, 3-5 AC, intended files and assumptions; protect owner scope and mock/DB contract;
mandatory mutation must use an actual guard, not an invented GET guard. Do not code or
manufacture two review mistakes." Original response proposed CSV/student-only, reuse
history paging initially, resolve snapshot/row cap/formulas, real DB workflow, mutation
at booking._resolve_replay and no browser UI. Its content is preserved in plan-review.md
as a condensed record rather than presented as a verbatim transcript.

Main post-test review prompt: "Read actual diff plus untracked export service/unit tests
and green.txt (25 passed, 18.70s, exit 0). Review Scope/Design/Correctness/Evidence;
separate supported findings from hypotheses and residual risks. Do not edit files or
make up a weak claim to satisfy the rubric." The agent identified the real zero-duration
fixture, demonstrated owner/cap evidence, and an unmeasured 80x1000-row CPU risk.

Two initially plausible but untrusted outputs: page-loop snapshot assumption and the
formatter's zero-duration fixture. Third limitation: role-context stub pass did not
prove a MySQL-backed role session. Each was rejected, corrected or independently tested.
The student must still validate proposed policy and personally understand the diff.

## Revision after external review

U12: User supplied external read-only review, then said they had not personally run
commands and asked Codex execution. Codex clarified it would disclose delegated AI
execution and retain a reflection draft, not invent manual student execution.

U13: Same-TC10 default vs greenlet/thread experiment confirmed missing overflow line
was measurement. Applied official configuration and diff executable-line report.
No coverage metric edited manually.

U14: Bare tab/CR/LF and full-width CSV cases captured7fail/10pass red, then small
formatter patch and41pass green. Added TC18 cardinality beyond status assertion.

U15: Actual agent review accepted supported CSV/coverage/safe logging findings,
rejected historical503 root-cause claim as unproved, found lost row multiplicity in
set-ID load oracle. Fixed count assertion, reran and preserved earlier review run.

U16: Ran owner/overflow/role/source-plus-unique-test-schema mutants. First runner
misclassified plain pytest asserts and selected two decorated functions; archived
that harness error, fixed AST selection/classification, reran. Four killed, one
redundant-role survivor; public experimental commits. All source restored.

U17: Separated historical FAIL and new cold/warm PASS without causal claim; added
diagnostic sanitizer probes, risk legend, verbatim plan prompt, direct filenames,
self-check/oral guide and revised PDF. Human understanding/review remains unclaimed.

U18: External review requested explicit disclosure of M5 test-setup edits and how
to inspect sequential mutant commits. Verified604c0e7 vs clean81552d3: six setup
lines added; assertions unchanged. Added clean-reference Git diff commands.
Requested a same-harness load confirmation after a new local MySQL process start;
saved startup/uptime and separate data, preserving every earlier run. Delegated
execution and the personal reflection's unconfirmed status remain disclosed.

U18 outcome: same harness after startup failed276/560requests at export with
ServiceUnavailable/TimeoutError/CancelledError, including warm bursts. This narrows
the observed failure to read-transaction deadline expiry but does not isolate deeper
CPU/connection/query causes. Original PASS/FAIL records were retained; production
code, harness and deadlines are unchanged. Reflection draft was updated to reflect
this observation without claiming student manual execution.

U19: User supplied review__2_ highlighting NullPool vs deployment defaults and asked
for pool/CPU/MySQL metrics and sub-stage timing. Codex built a separate opt-in test
harness using default create_app (5+5/0.5s) and a matched NullPool variant, preserving
all historical harnesses. First comparison:0null/340production errors. Added exact
auth leaf module/type check, reversed order:295production/83null errors. Corrected
process snapshots to omit observer itself; final302production/0null errors. Both
pre-correction comparisons remain archived. Two later runs establish pool timeout
at auth; the83 null failures were canceled while awaiting rollback after execute/
row extraction/mapping. CPU/lag and server status are observations, not causal proof
of slow SQL, orphan accumulation or a production fix. Functional checks and PDF
render QA are recorded separately. Execution remains delegated; no student manual
execution, understanding or personal confirmation has been invented.

U20: User reported three failing CI checks on PR55 at c664a5d. Read actual job logs:
seven Pyright errors and minimatch10.2.6 advisory (same security finding on push/PR).
Wrapped fixture timestamps in UtcTimestamp and made engine_factory explicit; added
load marker with functional-CI exclusion while retaining --require-mysql/no-skip
policy. Applied upstream minimatch11.0.1 override and npm-generated lock, no waiver.
Local Pyright/Ruff/Black pass,24unit/OpenAPI pass and6load deselected; frontend
349tests and format/lint/typecheck/build pass, npm audit0. One unprivileged test
process stalled and was interrupted; elevated repeat produced the cited result.
All load evidence remains from c664a5d, not relabeled as post-CI reruns.

U21: User supplied review__4_ asking for reviewable PR scope, interpretation of the
first pool pair, next actions and a personal TC18/pool rerun. Codex added a README
review map with narrow diff commands, classified attempt1 as count-only evidence,
and documented backpressure/pool/timeout/transaction options and required gates.
Source + official FastAPI/SQLAlchemy documentation show request-session reuse with
sequential transactions, not evidence of two simultaneous connections/request.
Added student-run commands preserving the tracked report plus oral-defense answers.
No new experiment, production change, PR split or human execution is claimed; the
PDF retains measured c664a5d data and CI outcomes on e90325b. Reflection remains the
337-word draft pending the student's own experience/confirmation.

U22: During the review__4_ revision, user reported the completed container check
(37min):1failed/175passed, solely a stale generated gateway route map. Read the
actual GitHub log, reproduced that test1fail, ran the existing route generator,
and verified3targeted tests pass. Added an early generation check in the workflow,
kept all container tests/zero-skip gates and timeouts. This synchronizes an export
contract artifact; no new business behavior or pool setting is introduced.

U23: Student asked for complete command instructions. Codex inspected installed
Python/MySQL/tools and wrote a local step-by-step guide using a separate clone,
fresh venv, real DB tests, coverage, mutation and load. Instructions are not an
execution claim. Fifteen PowerShell code blocks were syntax-parsed only.

U24: Student actually ran the guide and supplied terminal output. Codex then read
local captured files, raw pool/coverage JSON and mutant logs, verified counts and
source restoration, compressed19rawfiles with checksum manifest, and prepared
AI-assisted factual reflection. No tests/load were rerun by Codex in this update.
Student41pass/TC18pass/coverage50of50/4kills1survivor and pool312of560FAIL are their
new results, not a relabeling of earlier Codex evidence. New mutation commits are
kept on a separately published experiment branch; submission source stays clean.
The earlier statement that the student has not run commands describes the earlier
stage only. Manual code authorship, full code review or group approval are not claimed.


U25: User reported ambiguous branch labels and final wording. Codex normalized
the standalone mutant navigation metadata to the published student experiment
branch, retained the original local name explicitly, and labeled the student
checkout as local-only. Raw ZIP bytes and manifest hashes remain unchanged.
Documented302vs312 as repeats on the same Windows/MySQL environment, with one
shared session within each run but fresh login/UUID schema across fixtures.
Prepared an AI-authored four-file production-diff reading guide and blank checklist;
no student code-review completion or subjective reflection has been invented.
Reworded the PDF footer neutrally. Reflection stays327words; PR split remains a
team-integration follow-up. No runtime/test/config change, new test or load rerun.


U26: On03/11/2026 Codex prepared a separate22-question oral-preparation guide and
verified all63unique PDF link targets (79annotations), including45file paths in
remote pinned Git trees. These local study/audit files did not change the report,
submission source, earlier measurements or student completion status.

U27: On03/11/2026 the student directly confirmed completion of the four-file
production diff reading and oral-preparation answers. Codex updated the checklist, execution ledger,
summary metadata, reflection and PDF to record that confirmation. The reflection
adds only the reported reading/answering activity; no individual answers, feelings,
assessment score, new test execution or team approval are invented. Earlier
“not reported” entries describe the status before this confirmation. Raw ZIP and
its original manifest are unchanged; production code/tests/config remain unchanged.


U28: User supplied review__9_ recommending personal review notes, more reflection
headroom and formal wording in public documents. Codex replaced the informal chat
quotation with a factual attribution summary, preserving the confirmation date
and scope. Shortened reflection347to330words without changing its observations.
Rechecked the four linked GitHub Actions runs: all completed/success at15526cb.
Prepared a blank personal-review template outside the submission and requested
the student's own notes; no individual answers or acceptance decision invented.
No runtime/test/config change, new test/load run, team approval or merge.


U29: The student supplied seven personal review statements on03/11/2026: roles
of the four files, learning the1001sentinel, remaining uncertainty about80-way
load/503, and agreement with the approach conditional on further performance
work before accepting merge. Codex preserved their wording in a separate review
note, linked it from the report/checklist/ledger and recorded the decision in
summary metadata. Notes are student-authored; formatting and technical context
are AI-assisted. No new test, subjective statement or merge approval invented.


U30: User asked why the appendix did not display the original prompt as in BT1/BT2.
Codex inspected both prior appendices and the original BT3 attachment. Archived
the complete user prompt byte-for-byte and extracted the already-preserved full
planning prompt without reconstructing missing transcripts. Reorganized appendixA
into a prompt/source page and a condensed usage/decision page; embedded full user
prompt, planning prompt and AI Usage Log in the PDF, with hashes for source checks.
Main content remains8pages; appendices now4pages, total12. Source, tests, original
measurement logs, reflection330words and student-authored review notes unchanged.


U31: On09/11/2026 the user identified a stale blanket denial in the embedded
usage-log header and missing Claude attribution. Codex replaced the header with
dated01/02/03-November responsibilities and declared Claude/Anthropic review
feedback supplied by the student, without inventing model/access/execution
metadata. Added a canonical-LF AI-log checksum/tool manifest and refreshed the
embedded log. AppendixA now ties kept/changed/rejected outputs to named evidence
and summarizes supported versus insufficient review findings. Moved original-red
and student-pool references into B1/B3. Original prompts, raw evidence,330-word
reflection and personal review notes remain unchanged. No test/load rerun or merge.


U32: The student asked to fix the two remaining PR55 failures. Codex reproduced
the release-predecessor unit failure on synthetic merge808bc790 (develop92d9157
plus submission6184652). The seven generated export-route additions changed the
gateway hash; the grants hash and predecessor hashes were unchanged. Updated
the exact current route-map hash without weakening configuration drift checks.
Expanded the existing opt-in/drift test across both reviewed files and three
supported profiles. Local related tests:98passed/13.92s; Black/Ruff and generator
check pass. Work began on isolated codex/bt3-release-compat-20261109; PR55 is
updated by fast-forward only, retaining all cited commits and not merging into
develop. See ci-integration-20261109.md. Linux backend/rollback verdicts come from
the new CI run, not these Windows unit tests; historical load data is unchanged.
