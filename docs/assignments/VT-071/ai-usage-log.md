# AI Usage Log - actual interactions

Date: 2026-11-01, Asia/Bangkok. Tool: Codex with shell, GitHub connector, Python,
ReportLab/Poppler and one coding-agent subtask reused for post-test review.
This is AI-assisted work. No human review/approval or manual student execution is claimed.

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
