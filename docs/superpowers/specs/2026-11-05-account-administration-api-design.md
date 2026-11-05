# Account Administration API Design

## Intent and goals
Provide ADMIN-only account management APIs that preserve account history, authorization, booking behavior, session correctness, and sanitized audit evidence.

## Scope
The API supports filtered/paginated account reads, account creation, profile and role/active-state updates, and explicit internal password reset. It does not delete accounts, deliver reset emails, provide SSO, or expose consultation content.

## Policy decisions
- At least one active ADMIN and one active STAFF account must remain. Concurrent role/active changes are serialized by exclusively locking `service_settings(id=1)` before account rows.
- STAFF accounts with an occupying appointment cannot be deactivated. A role change is rejected for any account referenced by appointments, staff-purpose assignments, staff unavailability, or change proposals; FK history is never deleted.
- Account state changes recheck the actor and target under account locks. Account locks are acquired in UUID byte order after the exclusive settings lock; this serializes with booking's shared settings lock and its settings-then-sorted-users order.
- Mutations require the account's `updated_at` value as a precondition. Each changed write advances it to the greater of database UTC now and the prior value plus one microsecond, so two writes cannot share a version if the database clock repeats. A stale value returns `409 ACCOUNT_VERSION_CONFLICT`; an exact no-op performs no write or audit. After an unknown commit, retrying with the old version conflicts if the first write committed, while a retry after a known rollback can proceed.
- Password input uses `SecretStr` and existing password policy/service. Passwords and hashes never appear in output, audit, or logs. A successful password reset revokes all sessions in the same transaction.
- Email is stripped and lowercased using the same normalization as login; uniqueness follows MySQL's `utf8mb4_0900_as_ci` collation and duplicate identities return a stable conflict.

## Approaches considered
- Add endpoints to `auth.py` and mix account persistence into authentication: rejected because it couples administrator workflows to session routes and expands auth module responsibilities.
- Add a separate account API, service, and repository methods using the existing `User`, `Session`, and `AuditEvent` models: selected because it follows the modular API/service/repository split and reuses current transaction, password, lock, and audit infrastructure.
- Add a migration for a dedicated version or idempotency table: not needed; the existing `updated_at` timestamp provides stale-write protection and a safe unknown-outcome retry signal without adding state.

## API and implementation
Add `/api/v1/admin/accounts` routes behind ADMIN authorization and CSRF for writes. Keep handlers thin. A typed schema defines bounded pagination/filter inputs and responses that omit password fields. A focused service owns finite-deadline transactions, lock ordering, rules, and audit. Repository methods perform explicit account queries and history/reference checks. The generated OpenAPI document is checked in.

## Verification
Use real-MySQL integration tests for authorization, CSRF, collation duplicates, account history, account policy races, booking races, session revocation, stale writes, no-op behavior, and atomic audit. Run the required backend formatting, lint, typecheck, full MySQL suite, and deterministic OpenAPI export where local prerequisites permit.
