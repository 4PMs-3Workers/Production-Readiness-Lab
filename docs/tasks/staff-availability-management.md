# Staff consultation preferences and unavailability (issue #22)

Backend implementation on `feature/staff-availability-management`, based on `develop`
at `0725df8daa8626049d8e8d17ee3ed1614e1965d4` (merged PR #28). This work uses the existing
authentication, catalog, booking locks and database schema. Fixtures are independent of
the demo seed and frontend. The generated contract is [OpenAPI](../api/openapi.yaml).

## HTTP contract

Every endpoint requires a valid `prl_session` with role `STAFF`; `ADMIN` and `STUDENT`
receive `403 FORBIDDEN_ROLE`, and an invalid/missing session receives `401 INVALID_SESSION`.
Staff identity comes exclusively from the session. Unexpected body/query fields, including
ownership or actor fields, are rejected with `422 VALIDATION_ERROR`. DELETE accepts no body
or query string. Duplicate query parameters are rejected.

| Method and path (prefix `/api/v1`) | Success |
| --- | --- |
| `GET /staff/me/purposes` | `200`, paginated active purposes plus retained inactive associations |
| `PUT /staff/me/purposes/{purpose_id}` | `200`, current purpose and acceptance state |
| `GET /staff/me/unavailability` | `200`, paginated owned periods |
| `POST /staff/me/unavailability` | `201` creation; `200` exact normalized interval replay |
| `DELETE /staff/me/unavailability/{unavailability_id}` | `204`, empty body |

Lists use `page >= 1` (default 1), `1 <= page_size <= 100` (default 20) and the existing
`items`, `page`, `page_size`, `total` envelope. Pages beyond the result set contain no items.
All successes and failures carry `Cache-Control: private, no-store` and `X-Request-ID`.
Errors use the shared `error.code`, `error.message`, `error.request_id` envelope and
`X-Error-Class` (`input`, `business`, or `service`). Full payloads, credentials, session/CSRF
tokens and consultation content are never included in logs, traces or audit metadata.

PUT, POST and DELETE require `X-CSRF-Token` matching the signed `prl_csrf` cookie for the
current session, plus a matching application `Origin`, or same-origin `Referer` when Origin
is absent. Origin takes precedence. GET needs no CSRF. Body size is bounded by
`API_MAX_BODY_BYTES` before parsing; oversized writes return `413 PAYLOAD_TOO_LARGE`.

### Consultation preferences

GET includes purpose `id`, `code`, `name`, `duration_minutes`, `requires_free_text`,
`is_active` and `is_accepting`. Results are ordered by purpose name, then UUID, ascending.
Every active purpose is visible; without an owned association, `is_accepting` is false.
An inactive purpose is visible only if this staff member retains its association, and its
stored acceptance value is returned unchanged. Another staff member's association grants
no visibility or acceptance to the caller.

PUT accepts exactly `{"is_accepting": true}` or `{"is_accepting": false}`. Strings, numbers,
null and other coercible values are invalid. A missing purpose returns `404 NOT_FOUND`.
An active purpose can acquire an association even when the initial flag is false. Updates
preserve the composite `(staff_id, purpose_id)` identity. Associations are never deleted.

An existing inactive association may be disabled. Enabling an inactive purpose, or creating
any new inactive association, returns `409 PURPOSE_INACTIVE` (`business`). An unchanged
allowed PUT returns the current state without writes, timestamp changes or audit events.
An already-disabled inactive association can therefore be safely retried as false.

Disabling does not cancel, move or modify any appointment, its history or consultation
content. Fresh student staff-catalog and availability reads reflect the committed flag;
booking still checks eligibility after locking. Staff cannot edit global purpose names,
durations, activity, service settings, weekly service hours or another staff member's flag.

### Periods and overlap filters

POST accepts exactly `starts_at` and `ends_at` as offset-bearing ISO timestamps. Naive,
numeric, invalid or non-positive intervals return `422`. Both values are normalized to UTC
before comparison/storage; responses serialize UTC with `Z`. Values must fit MySQL
`DATETIME(6)` (years 1000–9999 after normalization). Precision is microseconds.

All intervals are half-open `[starts_at, ends_at)`: overlap means
`existing.starts_at < candidate.ends_at AND existing.ends_at > candidate.starts_at`.
An adjacent appointment or period is allowed. Multi-day, historical, far-future and
non-slot-aligned periods are allowed; booking horizon, consultation duration and weekly
service windows do not constrain absence declarations.

After taking the staff lock, POST first looks for the same owned normalized UTC start/end.
If found, it returns the original ID, timestamps and creation receipt as `200`, without
another row or audit event. Replay precedes conflict checks, including when a legacy
appointment now overlaps the existing declaration. Concurrent identical POSTs create one
period and one audit event. Otherwise any assigned `UPCOMING` or `CHANGE_PENDING`
appointment overlap returns `409 UNAVAILABILITY_CONFLICT` (`business`). `CANCELLED`,
`COMPLETED`, `NO_SHOW`, and other staff members' appointments do not block the new period.

Distinct overlapping absence periods are allowed. Availability excludes their union. Deleting
one period leaves all remaining periods, appointments and service rules effective. DELETE
only removes an owned period; foreign, nonexistent and previously-deleted IDs all return
the same `404 NOT_FOUND` (`input`). Repeated deletion produces no extra audit event.

GET orders by `starts_at`, then UUID, ascending. Optional timezone-aware `from` and `to`
select periods overlapping the query interval: `ends_at > from`, `starts_at < to`.
Either bound may be omitted; both require `from < to`. Boundary equality does not overlap.
The filters have the same timestamp validation/UTC normalization as POST and never change
ownership scope. An unfiltered GET is the complete owned-list recovery path, with pagination.

### Examples

Authenticate as a staff member using the [session authentication examples](session-authentication.md).
The following shell examples assume `cookies.txt` contains that session, `BASE_URL` is the
application origin, `CSRF_TOKEN` is the current `prl_csrf` cookie and IDs came from the
staff's responses. Do not commit or log cookie files or tokens.

```sh
curl -b cookies.txt "$BASE_URL/api/v1/staff/me/purposes?page=1&page_size=20"

curl -b cookies.txt -X PUT \
  -H "Origin: $BASE_URL" -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' --data '{"is_accepting":false}' \
  "$BASE_URL/api/v1/staff/me/purposes/$PURPOSE_ID"

curl -b cookies.txt -X POST \
  -H "Origin: $BASE_URL" -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"starts_at":"2027-01-04T16:07:00+07:00","ends_at":"2027-01-06T09:13:00+07:00"}' \
  "$BASE_URL/api/v1/staff/me/unavailability"

curl -b cookies.txt --get \
  --data-urlencode 'from=2027-01-04T00:00:00+07:00' \
  --data-urlencode 'to=2027-01-07T00:00:00+07:00' \
  --data-urlencode 'page_size=20' \
  "$BASE_URL/api/v1/staff/me/unavailability"

curl -b cookies.txt -X DELETE \
  -H "Origin: $BASE_URL" -H "X-CSRF-Token: $CSRF_TOKEN" \
  "$BASE_URL/api/v1/staff/me/unavailability/$UNAVAILABILITY_ID"
```

The POST response contains `id`, `starts_at`, `ends_at`, `created_at`. Retrying it with
`2027-01-04T09:07:00Z` and `2027-01-06T02:13:00Z` returns the same receipt as `200`.

## Transactions, failures and recovery

Every write owns one InnoDB transaction under `READ COMMITTED`:

1. Lock `service_settings(id=1)` with `FOR SHARE` (SQLAlchemy's MySQL spelling is
   `LOCK IN SHARE MODE`). A missing policy returns sanitized `503 SERVICE_UNAVAILABLE`.
2. Lock the authenticated staff's `users` row with `FOR UPDATE`, even if there are no
   existing appointments/periods/associations. Recheck active `STAFF` authority after waiting;
   a changed role or disabled/deleted account is rejected before mutation.
3. Lock the relevant association or owned period with `FOR UPDATE`. Preference changes also
   read-lock the purpose to inspect its current active state. Resolve replay/no-op and
   validate mutable business state after the staff lock.
4. Write the configuration and its audit event, flush, and commit atomically. No-op/replay
   releases the transaction without a write. Failure rolls back the configuration and audit.

The `users` lock is the same row booking and appointment transitions lock. If absence
creation commits first, an overlapping booking fails; if booking commits first, the new
absence fails. If disabling acceptance commits first, booking rejects the now-ineligible
staff member; if booking commits first, disabling preserves that appointment. An empty
overlap query alone cannot provide this serialization at `READ COMMITTED`.

Reuse `REQUEST_TIMEOUT_SECONDS`, `DB_QUERY_TIMEOUT_SECONDS`, `DB_LOCK_TIMEOUT_SECONDS`,
`MAX_DEADLOCK_RETRIES` and the shared rollback/commit helpers. Query timeout covers each
transaction attempt; the request budget bounds authentication, parsing, retries and commit.
Only known-aborted MySQL 1205/1213 failures can retry the complete transaction, within the
retry/request budgets. Timeouts, cancellation and errors release/invalidate connections and
locks. All database failures exposed to clients are sanitized `503 SERVICE_UNAVAILABLE`.

A connection failure during commit can mean the write committed even though no response
arrived. The server invalidates the connection and never automatically replays that write.
Do not interpret `503` as proof of rollback:

- PUT: repeat the same boolean; an unchanged result creates no new audit/timestamps.
- POST: retry the exact normalized interval; the stored receipt returns as `200`, or a
  successful first creation returns `201` if the previous write rolled back.
- DELETE: refresh all pages of the owned list. Absence confirms removal; if still present,
  retry deletion. Repeated deletion remains `404`, not a fabricated successful receipt.

Recovery uses current authority and business state. For example, an intervening global
purpose deactivation can make a later `true` PUT return `PURPOSE_INACTIVE`. POST replay
identity lasts while that period exists; explicitly deleting it removes its replay record.

## Audit, migration compatibility and scope

| Action | Entity | Metadata |
| --- | --- | --- |
| `STAFF_PURPOSE_SET` | `STAFF_PURPOSE`, ID `staff_uuid:purpose_uuid` | `staff_id`, `purpose_id`, `is_accepting` |
| `STAFF_UNAVAILABILITY_CREATED` | `STAFF_UNAVAILABILITY`, period UUID | `staff_id`, UTC `starts_at`, UTC `ends_at` |
| `STAFF_UNAVAILABILITY_DELETED` | `STAFF_UNAVAILABILITY`, period UUID | `staff_id`, UTC `starts_at`, UTC `ends_at` |

Each event records authenticated `actor_id` and sanitized request ID. Actual changes create
one event; unchanged PUT, POST replay, failed writes and repeated DELETE create none.
These changes do not create appointment notifications.

No model or Alembic changes are required. The tested migration head remains
`20261019_1200_proposal_replay`; existing composite appointment references, UTC storage,
strict SQL mode and `READ COMMITTED` remain compatible. Existing `ACCEPTED_SCHEMA_REVISIONS`
configuration needs no new revision. Runtime DML needs SELECT on relevant policy/accounts/
purposes/appointments/configuration, INSERT/UPDATE on `staff_purposes`, INSERT/DELETE on
`staff_unavailability`, and INSERT on `audit_events`, alongside existing auth privileges.
No DDL, association DELETE or audit UPDATE/DELETE permission is needed.

This delivery contains backend APIs, tests and documentation. It does not implement staff
frontend pages, global administration, recurring/bulk absence rules, calendar sync or a new
worker. Distinct overlapping periods are separate records; there is no automatic merge or
expiry. Availability remains a preview; booking is the final transaction-time authority.

## Verification evidence

Run from the repository root after installing the pinned development dependencies and the
editable backend package. Set `MYSQL_TEST_URL` to a dedicated MySQL 8.4 test server whose
test account can create/drop isolated schemas. Never use the demo database. Tests migrate a
new schema per fixture with Alembic; `--require-mysql` fails when the server is unavailable.

```sh
python -m black --check --workers 1 src/backend tests/backend tools/scripts
python -m ruff check src/backend tests/backend tools/scripts
pyright
python -m pytest tests/backend -q --require-mysql --junitxml=build/backend-tests.xml
python tools/scripts/export_openapi.py
git diff --exit-code -- docs/api/openapi.yaml
```

The targeted issue-22 run passed **87 tests**, including 45 isolated MySQL fixtures on
MySQL 8.4.11. Black, Ruff, Pyright and the four OpenAPI export/contract tests passed.
The PR records the final reviewed commit SHA and full-suite/CI results.

Coverage is in `test_staff_availability_schemas.py`, `test_staff_availability_http.py`,
`test_staff_configuration.py`, `test_staff_configuration_failures.py` and
`test_openapi_export.py`. It includes session/role/ownership, strict inputs and pagination
(including out-of-range pages with `page=18446744073709551616` on both list endpoints),
retained inactive associations, UTC/half-open filters, cross-day/exact replay, occupying
states and adjacent intervals, absence unions, history preservation and fresh student reads.
Concurrency tests use separate connections and `performance_schema.data_lock_waits` as
server evidence for both orderings of booking/configuration, exact POST races and account
authority changes while waiting. Failure injection covers audit rollback for all writes,
commit loss before/after commit and explicit recovery, retryable 1205/1213 errors, retry
exhaustion, real lock/query/request deadlines, cancellation cleanup and timeout after flush.
