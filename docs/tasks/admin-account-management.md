# Account Administration API

The account API is for authenticated `ADMIN` users only. It manages internal
accounts and does not expose consultation content, account deletion, SSO, or
email-based password recovery. Every response is private and must not be cached.
The shared private API middleware applies its request deadline and body-size
limit to all account routes.

## Routes

| Method and path | Purpose |
|---|---|
| `GET /api/v1/admin/accounts` | List accounts with `page`, `page_size` (1–100), optional exact `role`, optional `is_active`, and optional name/email `search`. |
| `POST /api/v1/admin/accounts` | Create an account. Requires same-origin and CSRF. Email is trimmed/lowercased; password is write-only. |
| `PATCH /api/v1/admin/accounts/{account_id}` | Update profile, role, or active state. Requires same-origin and CSRF plus the current `expected_updated_at`. |
| `POST /api/v1/admin/accounts/{account_id}/password` | Explicitly reset an internal password. Requires same-origin and CSRF plus the current `expected_updated_at`. |

Account responses contain identity, role, active state, student profile fields,
and timestamps. Passwords, password hashes, session tokens, and consultation
content are never returned.

## Account policy

- Accounts are retained; APIs do not delete them.
- At least one active `ADMIN` and one active `STAFF` account must remain. Removing
  the final active account of either role returns `LAST_ACTIVE_ADMIN` or
  `LAST_ACTIVE_STAFF`.
- A `STAFF` account with an `UPCOMING` or `CHANGE_PENDING` appointment cannot be
  deactivated (`STAFF_HAS_OCCUPYING_APPOINTMENTS`).
- A role change is rejected with `ROLE_CHANGE_REFERENCED` if account history
  references the user through an appointment, staff-purpose association, staff
  unavailability, or a change proposal. This preserves historical meaning.
- Creating or changing a role/active state rechecks the acting ADMIN and target
  account while locked. The actor's session is also rechecked for ownership,
  revocation, and expiry after the account locks are acquired. Account rows are
  locked in UUID byte order.
- Student profile fields can only be populated while the target's effective
  role is `STUDENT`; leaving that role clears any existing student profile.

## Concurrency, retries, and audit

Account writes take an exclusive lock on `service_settings(id=1)` before account
locks. Booking takes a shared lock on that row before account locks, so a booking
and staff deactivation are serialized without a new schema object. The same
exclusive lock serializes last-active-account checks across concurrent admins.

`expected_updated_at` is a version precondition. A successful change advances
the timestamp to the later of database UTC now and the previous timestamp plus
one microsecond. A stale value returns `409 ACCOUNT_VERSION_CONFLICT`. An exact
no-op returns the current account without updating its timestamp or adding an
audit event. If a commit outcome is unknown, the service returns
`SERVICE_UNAVAILABLE` and does not replay the write; retrying the prior version
conflicts if the first commit succeeded.

Account changes and their audit event commit atomically. Role, active-state, and
password changes revoke the target's sessions in the same transaction. Audit
metadata contains changed field names and safe state values only; it excludes
email, name, password, hash, token, and request payload.

## Admin portal

In API mode, sign in with an active ADMIN account. The login page and account
home link lead to `/admin/accounts`; `/admin` redirects there. STUDENT and STAFF
sessions cannot access the admin screen or the account API. Mock mode does not
provide account administration.

The account page provides server-side role/active-state/name-email filtering
and pagination. Select an account to edit its permitted fields or explicitly
reset its password. Creating an account requires an initial password; student
profile fields apply only to STUDENT. Empty optional profile fields are saved
as null. Leaving STUDENT clears its profile fields. The portal retains accounts
rather than deleting them and does not expose consultation content.

Role, activation and password changes require confirmation and explain session
revocation. Changing your own role, active state or password requires signing in
again. A last-active-account, occupying-staff or role-history rejection is shown
without substituting another operation. An unchanged edit sends no PATCH.

The editor preserves the original `updated_at` string, including microseconds.
If another ADMIN changes the account, load current data, review it alongside the
unsaved form, and explicitly start a new edit. The portal never silently replaces
the version token and resubmits the prior changes.

After a timeout, service error or lost write receipt, the result can be unknown.
Use **Tải lại để xem xét** to inspect current server data before explicitly
starting a new operation. Search/filter/pagination remain read-only during this
review. Recovery reads independently of the visible list's filters, matches the
stable account ID (or exact normalized email for create), and displays submitted
and current profile fields side by side. Additional pages require an explicit
action; an empty page or another account never unlocks acknowledgement.
Matching fields or a newer version do not attribute a change to the
original request. In particular, an account reread cannot prove which password
was installed. Any later reset requires re-entering the password and confirming
a new reset; it is not an automatic retry. The recovery notice is in memory for
the current admin feature/session; a full reload reads server state and does not
replay previous writes.

Passwords are cleared on submission and are not saved in browser storage,
recovery metadata, URLs or logs. Session changes clear the previous identity's
account data and drafts; returning to a tab rechecks the session before allowing
further actions. Login, `/auth/me` and `/auth/csrf` return a public
`session_generation` marker derived from the session with a purpose-separated
HMAC. It is stable within one session, changes on same-account login, and cannot
authenticate or authorize a request. Admin reads and write preflight check this
marker as well as user ID/role; replacing the session clears old drafts and
confirmations. Revalidating the same session preserves them.

API sign-in accepts normalized email lengths of 1–254 Unicode code points and
passwords of 1–1024 code points, without native email syntax validation or
UTF-16 truncation. Password whitespace is preserved.

## Frontend acceptance tests

Use the existing frontend checks, mock E2E and real API E2E jobs. For real E2E,
provide `MYSQL_TEST_URL` for a local MySQL 8.4 server with permission to create
and remove disposable schemas; never point the harness at a shared database.
Install the repository's Python and npm dependencies and Playwright Chromium.
Build in `NEXT_PUBLIC_APP_MODE=api` with
`API_UPSTREAM_URL=http://127.0.0.1:8101`, then run:

```text
npm --workspace src/frontend run test:e2e:api
```

`tests/frontend/api-e2e/admin-portal.spec.ts` runs on both `api-desktop` and
`api-mobile`. It verifies persisted accounts, sanitized audits, actual session
revocation, concurrent edits and committed writes whose HTTP receipts are lost.
`tools/scripts/frontend_e2e_backend.py` owns an isolated migrated schema, resets
mutated fixture accounts, and permits bounded test-only inspection. Missing
MySQL/build/server/browser prerequisites fail the required suite rather than
silently skipping it. Real API tracing and automatic screenshots remain off.
In CI both frontend suites use an allowlisted reporter containing only run
status, global-error count and attempt index/status/retry. The workflow uploads
only `frontend-safe/summary.json` or `frontend-api-safe/summary.json` under
`playwright-report/`; step parameters, titles, raw errors/output, attachments,
HTML and `test-results/` are excluded. Local HTML diagnostics remain local.

The API deployment prerequisites from #49 still apply, including the existing
column-level `UPDATE(id)` grant on `service_settings` for the locking read. The
portal does not add migrations or broaden runtime grants.
