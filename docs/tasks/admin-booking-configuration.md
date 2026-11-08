# Administrative booking configuration

An active ADMIN session can manage consultation purposes and the school-wide booking policy.
The API uses the existing session cookie and returns private, non-cacheable responses with
`X-Request-ID`. Writes require an `application/json` body and the signed `X-CSRF-Token` matching
`prl_csrf`. Send the application origin in `Origin` or, when it is absent, `Referer`.

## HTTP contract

| Method and path | Request | Success |
| --- | --- | --- |
| `GET /api/v1/admin/purposes` | Optional `is_active`, `page` (default 1) and `page_size` (default 20, maximum 100) | `200` with `items`, `page`, `page_size` and `total` in code order |
| `POST /api/v1/admin/purposes` | `code`, `name`, `duration_minutes`, `requires_free_text`, `is_active` | `201` with the purpose and its current revision |
| `GET /api/v1/admin/purposes/{purpose_id}` | Purpose UUID | `200` with the purpose, including inactive records |
| `PUT /api/v1/admin/purposes/{purpose_id}` | `expected_updated_at` and all mutable fields from the create request | `200` with the resulting purpose |
| `GET /api/v1/admin/booking-configuration` | No parameters | `200` with timezone, step, horizon, weekly windows and revision |
| `PUT /api/v1/admin/booking-configuration` | `expected_updated_at` and the complete desired policy and windows | `200` with the resulting policy and ordered windows |

A purpose item has `id`, `code`, `name`, `duration_minutes`, `requires_free_text`, `is_active`
and `updated_at`. New codes use 1–50 uppercase ASCII letters, digits or underscores and begin
with a letter. The name is trimmed and must contain 1–150 characters. Duration, step and
horizon accept JSON integers from 1 through 32,767. Boolean fields take JSON booleans.

Set `is_active` to false to deactivate a purpose. Its code, existing appointments and
staff-purpose links are retained. While inactive it is unavailable for new bookings and
proposal acceptance. Reactivation restores eligibility only for staff who are still active
and accepting that purpose. A staff member can turn off an existing association while the
purpose is inactive.

The booking configuration accepts IANA location timezone keys, such as
`Asia/Ho_Chi_Minh`, and the UTC names `UTC` and `Etc/UTC`. Weekly windows use ISO weekdays
1–7 and local `HH:MM:SS` times from `00:00:00` through `23:59:59`. A window must open before it
closes on the same day. Adjacent windows are allowed and remain separate. Duplicates and
same-day overlaps are invalid. An omitted weekday is closed. An empty array closes the
whole week. Reads order windows by weekday, opening time and closing time.

Use the `updated_at` from the preceding GET as `expected_updated_at`. An offset timestamp
must also be representable in UTC. Send all desired windows
in the same request as the policy fields. For example, this payload keeps only the listed
Monday window.

```json
{
  "expected_updated_at": "2026-11-08T07:30:00.123456Z",
  "timezone": "Asia/Ho_Chi_Minh",
  "start_step_minutes": 15,
  "booking_horizon_days": 7,
  "windows": [
    {"weekday": 1, "opens_at": "08:00:00", "closes_at": "11:30:00"}
  ]
}
```

Errors use `error.code`, `error.message`, `error.request_id` and `X-Error-Class`.

| Status | Codes |
| --- | --- |
| `401` | `INVALID_SESSION` |
| `403` | `FORBIDDEN_ROLE`, `INVALID_ORIGIN`, `INVALID_CSRF` |
| `404` | `PURPOSE_NOT_FOUND` |
| `409` | `PURPOSE_CODE_CONFLICT`, `PURPOSE_VERSION_CONFLICT`, `BOOKING_CONFIGURATION_VERSION_CONFLICT` |
| `413` | `PAYLOAD_TOO_LARGE` |
| `415` | `UNSUPPORTED_MEDIA_TYPE` |
| `422` | `VALIDATION_ERROR` for invalid values, unknown fields and unexpected or repeated query keys |
| `500` | `INTERNAL_SERVER_ERROR` |
| `503` | `SERVICE_UNAVAILABLE` for a database or request deadline, unavailable singleton or uncertain commit |

## Concurrency and recovery

ADMIN writes lock `service_settings(id=1)` exclusively before account, session or resource
access. Administrative reads take the shared policy lock and read one committed state.
Existing booking, proposal and staff mutations keep their shared-lock order and read the
current eligibility after acquiring it. A booking that commits first remains at its stored
UTC instants and duration. A waiting booking or proposal acceptance must satisfy the policy
that won the lock. Configuration never rewrites existing appointments.

One transaction applies the complete update and its audit event. The revision advances for
settings-only and window-only changes. Existing window rows present in both versions retain
their IDs and timestamps. A differing request with an old revision returns a conflict. If the
requested state already matches the stored state, the request succeeds with its current
revision and records no new audit event. The server retries only database attempts known to
have aborted.

After an uncertain commit, repeat the exact request or read the current resource before
editing. A repeated POST with the same code and values returns the existing purpose with 201.
A code already bound to different values returns a conflict. A repeated PUT with matching
state succeeds even if its precondition is now old. If another administrator has since
changed that state, refresh the resource and reconcile the change.

Audit metadata contains fixed field names, an initial activation flag or counts of windows
added and removed. It contains no supplied names, appointment descriptions or session data.
The existing microsecond timestamp columns provide the preconditions, so deploying requires
updated runtime grants and no new migration.

## Verification

Run the ADMIN HTTP, transaction and race suites with an isolated MySQL 8.4 server. The
required MySQL flag fails the tests when the prerequisite is missing. The tests provision
separate schemas through Alembic.

```sh
python -m pytest tests/backend/integration/test_admin_booking_configuration.py \
  tests/backend/integration/test_admin_policy_transactions.py \
  tests/backend/integration/test_admin_policy_races.py \
  tests/backend/integration/test_admin_account_endpoints.py -q --require-mysql
python -m pytest tests/backend/unit/test_openapi_export.py -q
python tools/scripts/export_openapi.py
python tools/infra/generate_gateway_routes.py --check
git diff --exit-code -- docs/api/openapi.yaml tools/deploy/nginx/route-map.conf
```

The committed [OpenAPI contract](../api/openapi.yaml) describes the operations and their
sanitized responses. The verification uses fixture data in disposable environments.
