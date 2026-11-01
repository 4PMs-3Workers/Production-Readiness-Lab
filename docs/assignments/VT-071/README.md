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

Latest:0/560 errors; first p95=1.516s; warm p95=1.316..1.527s. Latest local run PASS.
Historical runs remain FAIL (58/400 and246/400), with503 latency roughly1.7-2.0s.
A percentile mixing failures and successes is not successful capacity. Root cause
of historical503s has not been reproduced or proved fixed.

Test-only server wrappers capture auth/export stage, exception class chain, integer
DB code, timing/requestID; never exception messages/SQL/cookies/tokens. Two injected
probes verify sanitization/code capture, not old503 causality. Latest real requests
all pass, so server failure chains are empty. Session read has no UPDATE per request.
CPU/pool/deadline/cold-start explanations remain hypotheses. Gateway/TLS/browser,
80distinct sessions, max1000rows under load and real spreadsheet clients unverified.

## Evidence and ownership

Read spec.md, plan-review.md (verbatim plan prompt), test-design.md, post-test-review.md,
execution-notes.md, review-revision.md and ai-usage-log.md. Source and logs are runnable.
The student explicitly said they have not personally run commands; execution was
delegated to Codex. Reflection remains an AI-assisted draft, not a manual-execution
claim. self-check-guide.md provides rerun and oral-defense preparation. No force
push or merge performed.
