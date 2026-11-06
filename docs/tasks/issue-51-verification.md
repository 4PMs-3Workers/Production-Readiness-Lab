# Issue #51 — Implementation verification

Date: 2026-11-06, Asia/Bangkok.

## Source and configuration

- Base: `develop` at `0d5d1cc070de83b97758b8c97fe578b7f981de21` (#49 / PR #60).
- Implementation and test source: `c023cd3d6bd1b592a90b40c7921a129dedd6d788`.
- Frontend source tree: `89791003c348ee2e688c0fcf8dcc3b22d6434f8b`.
- The subsequent documentation commit records results without changing runtime code.
- Local environment: Windows x64, Node 24.22.1, npm 12.2.0, Python 3.12.14,
  Next.js 16.4.1 / React 19.3.0, MySQL 8.4.13, Playwright Chromium build 1248.
- Real API configuration: `NEXT_PUBLIC_APP_MODE=api`,
  `API_UPSTREAM_URL=http://127.0.0.1:8101`; frontend origin
  `http://127.0.0.1:3101`. Browser timezone is `America/Los_Angeles`, as in the
  existing API Playwright config; service data uses its configured business time.
- API E2E consumed production Next build ID `6UDc-ycwwWdV0ACeJimR8`.
- Mock E2E consumed the separately built Next build ID `Ing4MsZRwjj-4_9P_FZYB`.
- MySQL ran on a task-owned local server; each harness run used a uniquely named,
  Alembic-initialized `prl_frontend_e2e_<uuid>` schema. Credentials, session data,
  account payloads and password hashes are excluded from this report.

Public input SHA-256 values:

| File                                    | SHA-256                                                            |
| --------------------------------------- | ------------------------------------------------------------------ |
| `package-lock.json`                     | `2b74d23e6e5a82f35784292d9ec3edf1786e2b9a151efa3c0752ebd90565aa88` |
| `src/frontend/next.config.ts`           | `d0eaa3b66d4c783a94180e67f539cbdcd0079a6f148de17454657db360135183` |
| `src/frontend/playwright.api.config.ts` | `7afe60a8267fb23ab1e6b2dfa5f214a5560ef733e19c08e4b200bf8b142446fa` |

## Results

| Check                                                                   | Result                                                                      |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Frontend formatting, lint, route type generation and TypeScript         | Passed                                                                      |
| Full frontend unit/component suite, 2 workers                           | 388 passed, 0 skipped                                                       |
| Production API build                                                    | Passed                                                                      |
| Real API/MySQL E2E                                                      | 108 distinct cases verified: 106 in full run + 2 targeted reruns; 0 skipped |
| Production mock build and mock E2E                                      | Build passed; 66 passed, 0 skipped (1.7 minutes)                            |
| Account API integration plus schema/repository/service unit regressions | 35 passed, 0 skipped                                                        |
| Committed-response probe unit tests                                     | 24 passed, 0 skipped                                                        |
| Black and Ruff on changed Python harness/probe tests                    | Passed                                                                      |
| Repository Pyright 1.1.407                                              | 0 errors, 0 warnings                                                        |
| Git whitespace check                                                    | Passed                                                                      |

The real API suite contains 108 cases across `api-desktop` and `api-mobile`,
including 34 ADMIN cases. Required DB/server/build/browser prerequisites remain
hard failures. The API CI job's timeout increases from 25 to 35 minutes to cover
the additional scenarios; its services, test discovery, serialized execution,
retry policy and existing regression checks remain in place.

The full local invocation completed in 22.3 minutes with 106 passing cases and
two failures using the previously loaded STUDENT return-path assertion. The
corrected assertion retains denial when STUDENT signs in through a STAFF return
path, while ADMIN lands in its own portal. A fresh invocation of that exact case
passed for both projects (2 passed in 26.7 seconds). The frontend runtime source
and production build were unchanged between these invocations.

The harness schemas were cleaned up after the real runs. The task-owned MySQL
server was stopped and its temporary data directory removed after verification.

## Commands

Run from the repository root with project dependencies installed. The local
session invoked npm through the available npm 12.2.0 CLI and Python through the
workspace virtual environment; the equivalent project commands are:

```text
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
node node_modules/vitest/vitest.mjs run --config src/frontend/vitest.config.ts --maxWorkers=2
```

Build API mode with the public configuration above, then provide the dedicated
`MYSQL_TEST_URL`, `PYTHONPATH=src/backend` and `FRONTEND_E2E_PYTHON`:

```text
npm --workspace src/frontend run build
node node_modules/@playwright/test/cli.js test --config src/frontend/playwright.api.config.ts --max-failures=3
node node_modules/@playwright/test/cli.js test --config src/frontend/playwright.api.config.ts --grep "staff cannot read another staff member appointment" --max-failures=1
```

Build separately in `NEXT_PUBLIC_APP_MODE=mock` for mock regressions:

```text
npm --workspace src/frontend run build
node node_modules/@playwright/test/cli.js test --config src/frontend/playwright.config.ts --workers=2
```

Backend and harness checks:

```text
python -m pytest --require-mysql tests/backend/integration/test_admin_account_endpoints.py tests/backend/unit/test_admin_account_schemas.py tests/backend/unit/test_admin_account_repository.py tests/backend/unit/test_admin_account_service.py -q
python -m pytest tests/backend/unit/test_frontend_admin_probe.py -q
python -m black --workers 1 --check tools/scripts/frontend_e2e_backend.py tests/backend/unit/test_frontend_admin_probe.py
python -m ruff check tools/scripts/frontend_e2e_backend.py tests/backend/unit/test_frontend_admin_probe.py
pyright
git diff --check
```

## Acceptance boundaries

Real browser tests verify account state after reload, duplicate email, exact
no-op, concurrent ADMIN edits, policy rejections, actual target/self session
revocation and committed writes with lost/held receipts. Fixture assertions
verify the account audit actions/counts and metadata allowlist without printing
secret values. Password reset uncertainty is retained even after a newer account
version is read; account APIs do not expose password verification or operation
receipts for lost requests.

This is automated lab acceptance. Deployment measurements and review/merge are
separate steps. #51 remains open until its reviewed PR is merged and accepted.
The account UI consumes #49's existing API and grants; #52 catalog/settings,
consultation-content browsing, SSO, email resets and account deletion are outside
this delivery.

## CI follow-up: COMMIT timeout fault placement

The initial push [backend run](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/42161481060)
failed `test_query_timeout_during_commit_invalidates_without_retrying_writes`
before reaching its COMMIT hook (`entered` was false). The same source passed
the PR backend run. The 250 ms attempt budget included all SQL preparation, so a
contended runner could cancel setup instead of the COMMIT fault being tested.

The test now tracks the production-requested query scope with bounded setup
headroom, then arms its original 250 ms budget at the intended pre-COMMIT or
post-server-COMMIT pause. It asserts that this query scope actually expires,
the pause does not complete, exactly one write commit is attempted, the
connection is invalidated, locks are released, and persisted bundle/replay
results match whether the server committed. A 750 ms setup variant protects
both commit outcomes against the original failure. Runtime timeout settings and
application code are unchanged.

Local checks on MySQL 8.4.13:

- Full `tests/backend/integration/test_booking_timeouts.py`: **14 passed**, no skips.
- Both commit outcomes with normal/slow setup, repeated twice: **4 passed + 4 passed**.
- Black, Ruff and targeted Pyright: passed, 0 errors/warnings.

```text
python -m pytest --require-mysql tests/backend/integration/test_booking_timeouts.py -q
python -m pytest --require-mysql tests/backend/integration/test_booking_timeouts.py -k test_query_timeout_during_commit_invalidates_without_retrying_writes -q
```
