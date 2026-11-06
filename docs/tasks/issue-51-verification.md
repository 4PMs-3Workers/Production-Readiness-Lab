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

## PR #61 review follow-up

The six findings on `d6fc40a` are addressed by the review-fix commits through
`72baca574ea4653bac5bbacc9a03d8b96f20aebc`. The following documentation commit
does not change runtime or test code. Earlier results above describe the initial
implementation and timeout follow-up, not this revised source.

| Finding               | Resulting behavior and regression coverage                                                                                                                                                                                                                                                                                                          |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Recovery              | Reads independently of visible filters; matches stable ID or exact normalized create email; shows submitted/current account and student-profile fields side by side. Empty, failed and wrong-account reads keep acknowledgement locked. Pages beyond the first require user action, as required by the plan. Password uncertainty remains explicit. |
| Sign-in compatibility | Native email syntax and UTF-16 truncation removed. Tests cover Unicode email, 513-emoji create credentials, email edits and a 1024-code-point reset password including surrounding spaces.                                                                                                                                                          |
| Replaced session      | Purpose-separated HMAC public marker on login, me and CSRF distinguishes sessions of the same ADMIN. Provider/gate clear prior drafts/confirmations; read and write-preflight fences reject stale generations. Same-session verification preserves drafts. Marker cannot authenticate requests.                                                     |
| CI evidence           | Both frontend suites upload only allowlisted JSON summaries. Test titles, step arguments, raw errors/stdout/stderr, attachments, HTML and raw test-results are excluded. An intentionally failing Playwright probe confirms failure exit/status are retained without copying its canary diagnostics into summary or console.                        |
| Enum receipts         | Non-string role/gender values are rejected; malformed 2xx receipts leave the dispatched write unknown.                                                                                                                                                                                                                                              |
| Pagination            | A refreshed out-of-range page is clamped using server total and page size, then refetched. Tests cover deactivating the sole row on page 2 and a genuinely empty result set.                                                                                                                                                                        |

The maximum-Unicode browser regression exposed an additional cause of the
credential incompatibility: the prior 4096-byte login body cap rejected a
schema-valid password. The default and Compose login cap are now 16384 bytes,
enough for 254 email plus 1024 password code points even with JSON surrogate
escapes. It remains below the 32768-byte business API cap. UTF-8 and escaped-JSON
boundary tests authenticate successfully, while oversized, chunked, prefixed and
slash-redirect requests still exercise the byte guard. No migration, database
grant or new route is introduced.

Local verification on the environment recorded above:

- Frontend format, lint, route generation, TypeScript and production API build: passed.
- Full frontend unit/component suite: **409 passed**, no skips.
- Auth endpoints, auth HTTP and HTTP body-limit regression suite: **95 passed**, no skips.
- Earlier auth/session/CSRF regression run: **52 passed**; unit auth/CSRF/OpenAPI run: **29 passed**.
- Black and Ruff on the backend: passed; repository Pyright: **0 errors/warnings**.
- Final API production build: `KCk4JOGRVdkF9FPmXiq4y`.
- Final-build desktop/mobile targeted API E2E: **14 passed**, no skips, covering
  Unicode credentials, shared-cookie session replacement, recovery despite
  excluding filters, page shrink and expiry.
- Browser acceptance and complete CI results are recorded on PR #61 for the final head.

The real API suite now contains **114 cases**, including **40 ADMIN cases**, across
desktop and mobile. CI's required no-skip behavior and existing timeout remain
unchanged. No fixture credentials, hashes, session tokens or account payloads
are included in this verification record.

## CI follow-up after review fixes

On `0c91c5e`, PR CI passed 2087 backend tests, 409 frontend unit/component tests,
66 mock E2E and all 114 real API E2E. The push frontend check instead stopped
during Chromium dependency installation: the hosted runner's Microsoft APT feed
returned HTTP 403, causing `apt-get` to exit 100 before browser tests started.
The installer now makes at most three attempts with 10 seconds between failures,
then returns the final nonzero exit code. Both frontend jobs and container
acceptance use the same helper. Browser tests and package-signature verification
are unchanged. Stubbed installer probes verify immediate success, recovery after
a transient failure, and permanent failure after exactly three attempts.

The push backend run also failed the existing finite-sweep test (2086 passed):
only one of two old candidates expired, but the test did not capture the
candidate's reason. A controlled 1.25-second transition delay reproduces this
symptom with `DEADLINE_EXCEEDED` under its one-second query budget. This is a
timing-sensitivity reproduction, not proof of the unrecorded CI candidate reason.
The fairness test now has bounded test-only query/request headroom, checks both
normal/delayed transitions, asserts exact candidate IDs and outcomes, and verifies
the newly due proposal stays PENDING until the next sweep. Production worker
budgets and transitions are unchanged; dedicated timeout/failure tests still run.

Local MySQL verification: batches plus worker failure tests **18 passed**; both
finite-sweep variants repeated twice **2 passed + 2 passed**. Black, Ruff,
workflow YAML parsing, shell syntax and installer behavior probes passed.

## Follow-up review of c263837

Four additional findings are addressed in one follow-up commit:

- Both gateway overlays inherit the shared 16384-byte login cap, matching the
  backend. Exact/slash Nginx boundary tests now accept 16384 bytes and reject 16385. Real Compose acceptance seeds a valid 1024-emoji ADMIN password and
  authenticates it through the gateway, then verifies `/auth/me` succeeds.
- Completed no-match create reviews have an explicit acknowledgement/new-intent
  action. Failed and partially read searches stay blocked. No password or write
  is replayed; known-account update/reset reviews retain exact-target checks.
- Replacement sessions of the same STUDENT retain unresolved booking body/key
  through `suspendForSessionLoss`, while aborting old requests. Different users
  and roles still clear the intent; ADMIN generation isolation remains intact.
- Safe E2E attempts now contain stable case IDs and allowlisted project IDs in
  stdout/JSON. No raw titles, inputs, errors/output or attachments are copied.

Local verification: full frontend suite **415 passed**; targeted real API browser
regressions **12 passed** across desktop/mobile. An actual Playwright probe ran
two failing cases in two projects with parallel workers and retries: four case
identities remained stable across eight attempts, sensitive diagnostics were
absent, and exit status remained failure. Python acceptance helper tests **2
passed**; Ruff and Pyright passed. Final-build reruns and CI results are recorded
on PR #61. Final production build `_6etO9ZToySeqQwWMjpR6` also passed all four
new desktop/mobile booking-session and no-match-create cases. Docker Linux
engine was unavailable locally after a startup
attempt; real Nginx/Compose acceptance is therefore required in CI, not reported
as locally passed.

During this follow-up, develop advanced to `5f44bb9` (PR #58), producing a
conflict in the booking COMMIT-timeout regression. The integration preserves
develop's indefinite COMMIT pause and independent 15-second watchdog, plus the
admin branch's tracked query scope armed at the COMMIT boundary. Setup delay is
injected only once inside the settings-lock setup. No runtime budgets change.
The follow-up commit includes the develop integration so no extra fix commit is
added; full timeout integration and frontend checks were rerun after resolution.

The two new replacement-session E2E cases initially timed out on CI. The test
now keeps tab A backgrounded during B's logout/login by suppressing OS-generated
window-focus events, then explicitly focuses A only after B is authenticated.
It asserts A observed no 401, the verified user is unchanged but session
generation differs, and replay retains the original booking/key. This controls
the before-401 precondition across headless OS window-activation behavior;
it does not mock auth responses or retry the booking automatically.
The test also waits for the real logout POST to return 204 and the anonymous
form to render before navigating/logging in again. A controlled pre-dispatch
gate proves the old session remains valid while logout is pending, covering the
click-versus-completion navigation race without a timing sleep.
