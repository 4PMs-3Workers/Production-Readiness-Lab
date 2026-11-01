# BT3 - Nguyễn Tố Uyên - 23001952 - VT-071

Official project: APP-03 / Production Readiness Lab / SE2026-T18.
Baseline `develop`: `aa8c5b0be057754f9b3409c8699537d4aeb887b9` (checked 2026-11-01).
Submission branch: `codex/bt3-23001952-vt071`. No merge performed.

## Scope and proposed contract

`GET /api/v1/appointments/export[?status=CANCELLED]` exports the authenticated
STUDENT's history as UTF-8 CSV. STAFF/ADMIN are denied consistently with existing
student history; this permission policy, CSV column set and 1000-row cap are new
proposals pending team review. Unknown/duplicate query keys are rejected. One SQL
statement fetches at most 1001 rows; overflow returns 409, not partial CSV.
No description, internal key/hash, email or foreign owner's data is exported.
No browser UI, export jobs, new runtime dependency or export idempotency guard added.

## Setup (fresh checkout, Python >=3.11, MySQL 8.4)

```powershell
git checkout codex/bt3-23001952-vt071
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt -e . coverage==7.17.0
$env:MYSQL_TEST_URL = '<dedicated local MySQL 8.4 admin URL>'
$env:PYTHONUTF8 = '1'
```

Use only a dedicated test database server/account: fixtures create a unique
`prl_models_test_<uuid>` schema, run Alembic, and drop only that owned schema.
Do not set MYSQL_TEST_URL to production. The URL is never committed. For this run,
a portable official MySQL 8.4.13 ZIP was used in a local temporary directory,
loopback port 3371, without installing a Windows service. Docker could not start.

## Run functional, coverage and order checks

```powershell
python -m pytest tests/backend/integration/test_appointment_export.py tests/backend/unit/test_appointment_export.py -q --require-mysql
python -m pytest tests/backend/unit/test_openapi_export.py -q
python -m coverage run --branch --source=app -m pytest tests/backend/integration/test_appointment_export.py tests/backend/unit/test_appointment_export.py tests/backend/unit/test_openapi_export.py -q --require-mysql
python -m coverage report --include='*/services/appointment_export.py'
python tools/scripts/vt071_reverse_tests.py
python tools/scripts/export_openapi.py
```

Expected final functional counts: 29 export tests, plus 4 OpenAPI tests = 33 pass.
Red commit `687d9e0` had 5 failed/9 passed due to missing export route (422 vs 200).
First green `a68d424` incorporated the patch and reviewed test refinements.
See evidence/green.txt (25 pass), green-reviewed.txt (27 pass), final-coverage-run.txt
(33 pass) and reverse-order.txt (29 pass). Setup errors are separately disclosed.

## Reproduce the mandatory mutation in a temporary branch

```powershell
git switch -c codex/bt3-vt071-mutation-reproduce
git apply docs/assignments/VT-071/evidence/mutation.patch.txt
python -m pytest tests/backend/integration/test_appointment_export.py::test_booking_replay_after_cancellation_exports_one_source_row -q --require-mysql
# Expected FAIL: replay returns 503 instead of 200.
git restore --source=HEAD -- src/backend/app/services/booking.py
git switch codex/bt3-23001952-vt071
```

Actual temporary local mutation commit: `a500f24b0aea8ac249251dfd30f534ffaa53a867`.
It bypassed existing `booking._resolve_replay`; that commit is not on the submission
branch and is not published. This tests source-data retry integrity, not a GET guard.
The database unique constraint still prevents duplicate inserts; killed mutation
shows the retry/receipt contract was broken, not that duplication was observed.

## Reproduce the 80-task performance/risk experiment

```powershell
$env:BT3_RUN_LOAD = '1'
python -m pytest tests/backend/integration/test_appointment_export_load.py -q -s --require-mysql
Remove-Item Env:BT3_RUN_LOAD
```

This is an opt-in acceptance gate and may FAIL. Five rounds of 80 barrier-started
tasks use independent HTTP clients sharing one real STUDENT session, 100 owned rows
and one foreign canary. Requests exercise the full ASGI application and real MySQL
TCP with fixture NullPool, not gateway/TLS/browser networking. Default deadlines
remain request=3s, DB query=1s. Raw samples and both experiment runs are preserved.

Initial run: p95 1.611..1.946s; 58/400 failures (503 in first burst). Diagnostic rerun:
p95 all responses 1.719..2.053s; 246/400 failures. Successful-only p95 is also saved;
when all 80 fail it is null. Overall gate FAIL: a low p95 that includes quick 503s is
not successful performance. No production capacity or 80-distinct-user claim.
Timeout/pool/CPU causality remains unproven; instrument pooled deployments next.

## Evidence and AI disclosure

Read spec.md, plan-review.md, test-design.md, post-test-review.md, ai-usage-log.md,
execution-notes.md and evidence/. All reported outputs are actual execution logs.
AI authored/reviewed the changes; student/team human approval is not claimed.
The PDF reflection is an AI-assisted draft for the student to review and own.
No force push or merge. Performance acceptance and group review remain open gates.
