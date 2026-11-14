# VT-070: best-effort no-show inbox notice

Baseline: `12a74f7308550ccc294a783edde79db267a526d1` (develop).
Test-first checkpoint: `557764716b40c38edc094bb5b0f829067bcd873f`.
Feature checkpoint: `04f2a58079bf191003f0e56f35e69ba0ee92c72f`.

For the first successful HTTP transition to NO_SHOW, publish one extra student
inbox notice after the main transaction commits and the response body is sent.
The existing outcome, audit, two generic notices, and response fields are unchanged.
The separate session gives the best-effort write its own 250 ms timeout; a database
error or timeout logs a fixed message without the error payload. Do not retry an
uncertain write. Replaying the same outcome does not schedule another extra notice.
No durable delivery or crash recovery is claimed. The lab does not merge or deploy.

## Reproduce

From the repository root in a Python 3.12 virtual environment:

```powershell
python -m pip install -r requirements.txt pytest==9.2.0 pytest-asyncio==1.5.0 httpx==0.28.1 coverage==7.17.3
python -m pytest -q tests/backend/test_vt070.py
python -m coverage run --branch --source=app -m pytest -q tests/backend/test_vt070.py
python -m coverage json
$env:PYTHONPATH = "$(Resolve-Path src/backend);$(Get-Location)"
python -m tools.scripts.vt070_load
```

The load runner exercises the real API, middleware, schema and service in-process.
It substitutes authorization and database boundaries. There is one warm-up wave,
then three 150-task waves per partition, with a check of responses and writes.
The p95 comparison to 4.0 s applies only to this lab, not production.

For real MySQL correctness, set `MYSQL_TEST_URL` to a **disposable MySQL 8.4**
instance with permission to create/drop test schemas. Never point it at demo or
production. The integration fixture applies Alembic to its own schema.

```powershell
python -m pytest -q --require-mysql tests/backend/integration/test_vt070_mysql.py tests/backend/integration/test_staff_appointment_management.py
```

To reproduce RED, use a separate checkout at the test-first checkpoint and run
the first pytest command. Expected: five failed assertions about missing notice
or inbox recognition, seven pass. To reproduce the mutation, use a temporary
checkout of the feature checkpoint and replace only the first-write
`StaffAppointmentOutcomeResponse(..., status=outcome.status, outcome_at=UtcTimestamp(now))`
mapping with `status="COMPLETED"`. Re-run the 13 cases. Expected: five fail;
TC01 reports COMPLETED vs NO_SHOW even with HTTP 200. Do not commit that change
on the submission branch.

AI disclosure: Codex supported planning, tests, implementation and review; test
oracles, before/after outcomes and limitations were checked against code and logs.

## Verified locally, 2026-11-13

| Stage | Result |
| --- | --- |
| RED | 5 failed, 7 passed; TC01 expected the new notice but saw an empty list |
| GREEN | 13 passed; 88 additional API/schema/inbox regressions passed |
| Order check | The 13 tests passed with collection order reversed |
| Mutation | 5 failed, 8 passed; TC01 got COMPLETED instead of NO_SHOW |
| MySQL 8.4.11 | 32 passed, including TC14/15 (real writes and inbox, fault injection) |
| 150-task ASGI harness | p95 normal 0.513632 s; 32768 B 0.566338 s; 32769 B rejection 0.155645 s |

## Draft PR #68 CI follow-up

The initial Backend models job stopped at Black before running tests. The
follow-up fixes formatting, import order and two test-double typing issues
without changing notification behavior or test assertions. Local verification:
Black and Ruff passed on the backend workflow's primary check paths, Pyright
reported zero errors, and 103 targeted tests passed, including both VT-070
tests against MySQL 8.4.11.

The Dependency security workflow blocked the baseline pin
`opentelemetry-api==1.45.1` under [GHSA-mf5v-w3w3-f6cg](https://github.com/advisories/GHSA-mf5v-w3w3-f6cg).
A separate security follow-up upgrades the OpenTelemetry package family to
`1.45.2` / `0.66b2`, keeping the lock and package metadata consistent.

## 150-task comparison over HTTP and MySQL

Run `python -m tools.scripts.vt070_mysql_http_load` with `src/backend` and
the repository root on `PYTHONPATH`. Set `DATABASE_URL` to a freshly migrated
MySQL 8.4 database named `vt070_lab` on loopback. The script refuses other
database names, non-loopback URLs and populated user tables. It inserts only
synthetic records; use a separate disposable DB for each run. To start, run
`python -m alembic upgrade head` from the application checkout.

For the baseline, run the same script by absolute path from a separate checkout
at `12a74f7` with **only that checkout's** `src/backend` on the app import path.
The patch run used `e26fecc` plus the measurement script. Auth and CSRF identities
are overridden in a loopback-only HTTP app, while API, middleware, SQL and
connection pool are real. Default pool: 5 connections, 5 overflow, checkout
timeout 0.5 s. Outside measurement, warm up with 150 normal requests. Measure
2 waves of 150 per class (300 distinct appointments). Include failed requests
in nearest-rank client p95; verify writes after background tasks drain.

| Checkout | Bytes | Expected status count | 503 | Transport error | p95 (s) | Outcomes / audit / generic / extra |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Baseline | 20 | 156 × 200 | 143 | 1 | 5.507513 | 156 / 156 / 312 / 0 |
| VT-070 | 20 | 123 × 200 | 175 | 2 | 5.570108 | 124 / 124 / 248 / 122 |
| Baseline | 32,768 | 139 × 200 | 160 | 1 | 4.503034 | 143 / 143 / 286 / 0 |
| VT-070 | 32,768 | 151 × 200 | 149 | 0 | 3.843860 | 151 / 151 / 302 / 150 |
| Baseline | 32,769 | 300 × 413 | 0 | 0 | 1.297748 | 0 / 0 / 0 / 0 |
| VT-070 | 32,769 | 300 × 413 | 0 | 0 | 1.469317 | 0 / 0 / 0 / 0 |

Both checkouts fail the valid-request acceptance gate in this loopback lab.
A p95 under 4 s does not compensate for 503 errors. The baseline shows the
failure pre-exists the patch, but one run per checkout cannot quantify the
patch's incremental impact or establish the cause of each 503. Counts are
aggregates; a missing 200 is not proof of a rolled-back outcome. The oversized
class passes on both. Metrics apply to local HTTP plus MySQL, not production.
