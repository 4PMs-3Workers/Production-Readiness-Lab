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
