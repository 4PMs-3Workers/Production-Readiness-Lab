# Task 2 report: booking request and idempotency contract

## Changes

- Added strict `AppointmentCreateRequest` and immutable creation receipt DTOs. The request accepts only `purpose_id`, `staff_id`, timezone-aware `starts_at`, and optional `description` up to 2,000 characters; extra client-controlled fields are rejected.
- Added an `IdempotencyKey` Pydantic type that accepts 1–128 nonblank characters and preserves valid header text exactly, including surrounding whitespace.
- Added a pure purpose-dependent description check that only tests stripped blankness and returns original text unchanged.
- Added canonical request bytes and a raw SHA-256 digest over only `description`, canonical lowercase-hyphen purpose/staff UUIDs, and UTC `starts_at` formatted with six fractional digits and `Z`. JSON is sorted, compact, unescaped Unicode UTF-8. Null and empty description remain distinct.
- Added focused schema and canonicalization tests with exact byte and digest expectations. No database imports, persistence mapping, or routes were added.

## Verification

- RED: focused tests initially failed collection because the requested schema and domain modules did not exist.
- GREEN: focused tests — **11 passed**.
- Full backend suite — **303 passed, 142 skipped, 2 warnings** in 31.65s. The skips include unavailable MySQL integration checks; the two websockets deprecation warnings match the recorded baseline.
- Black check — passed. Ruff check — passed. `git diff --check` — passed.
- Pyright could not run because the `pyright` module is not installed (`No module named pyright`).

## Review notes

- Canonical bytes include only documented business fields. Timestamp normalization occurs before serialization. Valid idempotency key text is retained exactly by validation.
- Required description is not decided in the DTO because it depends on the database-loaded purpose; the pure `require_description` helper supports the next service task.

## Review fix: UTC conversion overflow (commit `9a26f39`)

- Added DTO validation that converts `starts_at` to UTC before accepting it and turns conversion overflow into a Pydantic input validation error. The original aware timestamp remains unchanged for accepted requests; canonicalization for representable values is unchanged.
- Added regression cases for `0001-01-01T00:00:00+01:00` and `9999-12-31T23:59:59-01:00`.
- RED: both new cases failed because validation accepted the out-of-range instants. GREEN: focused schema/contract tests — **13 passed**.
- Full backend suite — **305 passed, 142 skipped, 2 warnings** in 30.09s. Black check, Ruff check, and `git diff --check` passed. The two websockets warnings and unavailable MySQL integration environment are unchanged.
