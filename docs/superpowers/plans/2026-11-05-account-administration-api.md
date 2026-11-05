# Account Administration API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an ADMIN-only account API with safe account lifecycle changes, session revocation, audit, and deterministic OpenAPI.

**Architecture:** Add account-specific Pydantic contracts, route handlers, a transaction-owning service, and explicit repository queries. Reuse `User`, `Session`, `AuditEvent`, `PasswordService`, authentication/CSRF dependencies, and the existing settings-then-sorted-user lock order; no migration is planned.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLAlchemy 2 async, MySQL 8.4/InnoDB, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-account-administration-api-design.md`

## Global Constraints

- ADMIN-only reads and mutations; writes require same-origin and CSRF.
- Never delete referenced users or expose password/hash/session tokens.
- Minimum active account counts: one ADMIN and one STAFF.
- STAFF with occupying appointments cannot be disabled; referenced accounts cannot change roles.
- Account writes use `updated_at` preconditions, bounded deadlines, and atomic sanitized audit.
- Exclusively lock `service_settings(id=1)` before sorted account UUID locks for state-changing flows; booking uses a shared lock on that row.

## Review Focus

- Email variants equivalent under MySQL collation must not create duplicate accounts — Task 1 tests.
- Concurrent removal of the final active ADMIN/STAFF must leave one active account of each role — Task 2 race tests.
- Booking racing STAFF deactivation must produce either a valid booking or a rejected booking, never an orphaned appointment — Task 2 race tests.
- A stale write or retry after an unknown commit must not overwrite newer data or add duplicate audit — Task 2 tests.
- Password/session material and unnecessary user profile fields must not leak through API or audit — Tasks 1–2 response/audit tests.

---

## Task 1: Account listing and creation

**Acceptance:** ADMIN can list accounts with bounded pagination and exact role/active/search filters; account creation validates/normalizes input and returns no secrets; non-ADMIN and duplicate collation-equivalent email are rejected.

**Files:** `src/backend/app/api/admin_accounts.py` (new), `src/backend/app/api/__init__.py`, `src/backend/app/schemas/admin_accounts.py` (new), `src/backend/app/repositories/users.py`, `src/backend/app/services/admin_accounts.py` (new), `tests/backend/integration/test_admin_account_endpoints.py` (new).

**Dependencies:** Existing role/CSRF dependencies, public auth error conventions, user collation, password service, request ID, and integration fixtures.

- [x] Add integration tests for ADMIN/non-ADMIN access, pagination/filtering, output secrecy, account creation, and collation-equivalent duplicate email. Local execution was attempted but is blocked by the unavailable MySQL engine; CI must run these tests.
- [x] Implement schemas and explicit repository list/create helpers.
- [x] Implement read/create service transactions with finite deadlines, actor recheck, password hashing, sanitized audit, and serialized account creation.
- [x] Register the router and run focused tests plus backend formatting, lint, and type checks.
- [ ] Review/stage only Task 1 files and commit as `feat(admin): add account listing and creation APIs`.

## Task 2: Safe account updates and password reset

**Acceptance:** Profile/role/active updates use stale-write preconditions and defined last-account/reference/occupying-booking rules; password reset revokes sessions atomically; audit is atomic and sanitized; no-op and retry behavior is deterministic.

**Files:** `src/backend/app/repositories/users.py`, `src/backend/app/repositories/appointments.py` (only if required for locking/reference checks), `src/backend/app/repositories/sessions.py` (only if needed), `src/backend/app/services/admin_accounts.py`, `src/backend/app/api/admin_accounts.py`, `src/backend/app/schemas/admin_accounts.py`, `tests/backend/integration/test_admin_account_endpoints.py`, `tests/backend/integration/test_admin_account_concurrency.py` (new if focused races warrant it), `docs/tasks/admin-account-management.md`.

**Dependencies:** Task 1 contracts and service; `service_settings`-first and sorted account locking; existing `revoke_user_sessions` helper.

- [ ] Add tests for stale preconditions, no-op audit behavior, role-reference denial, active ADMIN/STAFF floor, STAFF occupying appointments, password reset, atomic session revocation/audit, and concurrent account/booking updates; observe correct failures.
- [ ] Implement locked mutation checks and repository queries; retry only known-aborted transactions and never replay uncertain commits.
- [ ] Verify revoked sessions cannot authenticate or authorize actions and a failed mutation leaves account/session/audit unchanged.
- [ ] Run focused tests plus changed-file formatting/lint/type checks; review and commit as `feat(admin): add audited account updates and session revocation`.

## Task 3: Contract export and final checks

**Acceptance:** `docs/api/openapi.yaml` is a deterministic export of the new endpoints; the complete backend suite and required checks pass, or unavailable prerequisites are precisely recorded.

**Files:** `docs/api/openapi.yaml`, `docs/tasks/admin-account-management.md` (if not included in Task 2), tests/config only if review identifies a required fix.

**Dependencies:** Tasks 1–2 complete.

- [ ] Export OpenAPI and verify there is no second diff on repeated export.
- [ ] Run backend Black, Ruff, typecheck, and full pytest with required MySQL mode; reject silent skips.
- [ ] Review the full diff against `origin/develop`, resolve in-scope issues, and commit any meaningful correction separately.
- [ ] Push the feature branch, open/update the PR to `develop`, and inspect CI without merging.

**Commit:** `docs(api): export account administration contract` when the export/doc change is independently meaningful; otherwise include the export in Task 2's implementation commit.
