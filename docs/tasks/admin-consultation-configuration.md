# Admin consultation configuration — issue #52

The ADMIN portal manages purposes, weekly service hours and booking policy through the
reviewed #50 API. The shared session guard and account screens come from #51.

## Routes and behavior

| Route                     | Behavior                                                                                                                             |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `/admin/purposes`         | Server-filtered/paginated purposes, creation, full mutable-field replacement, deactivation and reactivation. Codes remain immutable. |
| `/admin/service-hours`    | Seven-day editor with multiple local-time windows, seconds, overlap validation and closed-day display.                               |
| `/admin/booking-settings` | Timezone, start step and booking horizon.                                                                                            |

The hours and settings pages share one in-memory draft and base revision. Switching between
them retains changes. Saving reviews the complete desired policy and week, then sends one
atomic PUT. Reloading a dirty draft requires an explicit discard. Empty windows close the
whole week; adjacent windows remain separate.

All writes require review. No success appears before a valid receipt or an explicit full-state
reconciliation. The UI explains that new configuration affects future eligibility while
confirmed appointments retain their stored UTC instants and duration. A timezone change can
change the local presentation of an existing instant.

Use API mode, sign in as ADMIN, and follow the admin navigation. STUDENT/STAFF receive a denied
view and cannot use the backend administrative endpoints. Anonymous deep links return to the
exact allowed admin page after login. Mock mode does not provide administrative operations.

## Concurrency and uncertain outcomes

- Preserve the raw `updated_at` value as `expected_updated_at`, including microseconds and offset.
- Full policy/window payloads are copied before CSRF/identity preflight; hidden form fields are retained.
- Different state with a stale revision enters review/reload. The UI never silently rebases a draft.
- Matching state succeeds with an old precondition under #50 and creates no extra audit event.
- Purpose POST replay uses the identical immutable code and values; different values conflict.
- A lost/malformed receipt after dispatch is uncertain. Use the workspace recovery controls to
  read the current resource or deliberately resend the exact original request with fresh CSRF.
- Recovery compares all submitted values and canonical windows. Failed reads do not unlock
  another intent. A mismatch does not prove rollback; another administrator may have edited later.
- One owner-bound mutation lane prevents repeated submission and survives navigation inside
  the portal. Session replacement, account/role change, expiry or logout clears the old workspace
  and aborts its requests. Old completions cannot update a new owner's UI.
- Drafts and request bodies are not persisted in browser storage. A full reload starts with
  authoritative reads and does not claim to recover the history of an uncertain transaction.

The transport retains the existing same-origin session, signed CSRF, no-store, deadline and
authorization behavior. Business APIs, schema, runtime grants and transaction policy are unchanged.
See [the backend runbook](admin-booking-configuration.md) for the exact API contract.

## Verification

The implementation plan and acceptance matrix are in
[the issue #52 plan](../plans/issue-52-implementation-plan.md). Exact tested source SHAs,
command outcomes and CI run links belong in the delivery PR; results from an older head or
the dependency PRs are not acceptance evidence for this change.

```powershell
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test
python -m black --check tools/scripts/frontend_e2e_backend.py
python -m ruff check tools/scripts/frontend_e2e_backend.py
python -m pyright tools/scripts/frontend_e2e_backend.py tests/backend/unit/test_frontend_admin_probe.py tests/backend/integration/test_frontend_catalog_harness.py
python -m pytest tests/backend/unit/test_frontend_admin_probe.py -q
python -m pytest tests/backend/integration/test_frontend_catalog_harness.py tests/backend/integration/test_admin_booking_configuration.py tests/backend/integration/test_admin_policy_transactions.py tests/backend/integration/test_admin_policy_races.py tests/backend/integration/test_admin_account_endpoints.py -q --require-mysql
```

Real API E2E requires MySQL 8.4.11, an environment-supplied `MYSQL_TEST_URL` with permission to
create/drop isolated schemas, Python backend dependencies and Playwright Chromium. Ports
8101 and 3101 must be available. Set `FRONTEND_E2E_PYTHON` to the installed interpreter if needed.
Never print real credentials or point the harness at a shared application database.

```powershell
$env:PYTHONPATH = 'src/backend'
$env:NEXT_PUBLIC_APP_MODE = 'api'
$env:API_UPSTREAM_URL = 'http://127.0.0.1:8101'
npm --workspace src/frontend run build
npm --workspace src/frontend run test:e2e:api -- admin-catalog.spec.ts admin-booking-configuration.spec.ts
npm --workspace src/frontend run test:e2e:api
```

The two new specs discover 40 cases across `api-desktop` and `api-mobile`. They cover real
persistence/reload, immutable codes, referenced-purpose deactivation, atomic policy changes,
student slots, stored appointment preservation, concurrent ADMIN edits, no-op/replay,
invalid CSRF, denial, session replacement and genuinely lost post-commit receipts.

The harness captures a catalog/policy/windows/links baseline with revisions, and restores it
inside its owned schema before every case. The new reset integration test alters all of these
and verifies two consecutive resets. The response probe now allowlists the purpose POST/PUT
and configuration PUT, holds/truncates only after the real application finishes, and passes
non-success results through unchanged.

The API safe reporter uses the same invocation's `onBegin` suite as its expected case set.
Required runs fail on missing/skipped/interrupted/failed cases, unexpected retries, unknown
case identities or global errors. Discovery-only `--list` is not an acceptance run. Both local
and CI API runs write `playwright-report/frontend-api-safe/summary.json`; CI uploads only this
allowlisted file. It excludes titles, steps, errors, output, attachments and application payloads.

After API checks, build mock mode separately before the existing mock E2E regressions:

```powershell
$env:NEXT_PUBLIC_APP_MODE = 'mock'
npm --workspace src/frontend run build
npm --workspace src/frontend run test:e2e
git diff --check
```

Local Windows verification can be limited by Application Control blocking the installed
`asyncmy` extension. Such a prerequisite failure is reported as an error, not a skip or pass.
Linux CI must supply the real MySQL acceptance evidence when this occurs. Production-build
prerendering may also need execution outside the filesystem sandbox on this Windows host.

All acceptance measurements use disposable fixture data. They are automated lab proof,
not a deployment measurement or proof of runtime grants used by another environment.
