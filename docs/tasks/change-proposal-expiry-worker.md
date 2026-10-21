# Durable change-proposal expiry worker — issue #26

Implementation work starts from `develop` at
`7a4888b837eb6ff5b0c53c7cfde4372ebb80cc69`. The prerequisite is completed issue #25,
merged through PR #29 (`b3f23842e58ddec4a6903c3083720c15d4ec3476`), whose required
MySQL checks passed. The GitHub dependency from #26 to #25 already exists.

## Contract and delivery boundaries

The worker runs as a separate backend process with its own engine and session factory.
MySQL rows are the durable queue. Discovery reads due PENDING proposal IDs without locks,
in expiry/UUID order, in bounded batches. A selected ID is only a candidate: the worker
must recheck it through `expire_change_proposal_in_transaction` from
`app.services.change_proposals`, inside its own bounded transaction.

That shared transition acquires settings SHARE, participant accounts in UUID-byte order,
appointment UPDATE, then proposal UPDATE; it reads database time after locking. Only
`PENDING + CHANGE_PENDING + database_now >= expires_at` changes to EXPIRED/CANCELLED.
Disabled participants do not prevent system expiry. The transition stores system actors
as NULL, a shared resolution/cancellation timestamp and reason
`CHANGE_PROPOSAL_EXPIRED`, one audit with that action, and two participant notifications.
The worker must commit these changes atomically. Terminal/missing/not-due candidates are
no-ops; INCONSISTENT is an operational failure, never a successful expiry.

The existing helper owns neither transaction boundaries nor retry policy. The worker
reuses backend engine invariants, rollback, commit-outcome handling and retryable MySQL
codes 1205/1213. An unknown commit is not automatically replayed; a later transaction
must reread durable state. Failed candidates cannot stop the remaining selected batch.
Until a committed cancellation, CHANGE_PENDING still occupies its original slot/quota.

Startup validates backend configuration and the accepted Alembic revision before any
business write. It never migrates, seeds or creates tables. API and worker must ship
compatible transition code together, using schema `20261019_1200_proposal_replay`.
No new schema, proposal endpoints, frontend, deployment stack or notification delivery
transport belongs to this issue.

## Configuration and operational requirements

| Setting | Default | Meaning |
| --- | --- | --- |
| `WORKER_POLL_INTERVAL_SECONDS` | 5 seconds | Delay between successful scans |
| `WORKER_BATCH_SIZE` | 100 | Maximum selected candidates per scan |
| `WORKER_GRACE_SECONDS` | 30 seconds | Shutdown cleanup budget; never expiry grace |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | 15 seconds | Independent liveness reporting interval |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | 5, 5 | Finite per-process connection capacity |
| `DB_POOL_TIMEOUT_SECONDS` | 0.5 seconds | Pool acquisition bound |
| `DB_CONNECT_TIMEOUT_SECONDS` | 1 second | Connection bound |
| `DB_QUERY_TIMEOUT_SECONDS` | 1 second | Query/transaction-attempt bound |
| `DB_LOCK_TIMEOUT_SECONDS` | 0.5 seconds | Each locking-stage bound |
| `REQUEST_TIMEOUT_SECONDS` | 3 seconds | Complete candidate operation/retry budget |
| `MAX_DEADLOCK_RETRIES` | 2 | Additional known-aborted transaction attempts |

Existing Settings validation requires positive finite intervals, positive integer batch
and pool sizes, nonnegative overflow/retries, a MySQL+asyncmy URL and nonempty accepted
revisions. The worker uses the same validated backend environment as the API, without
creating interactive sessions or hashing passwords.

Published JSON diagnostics must distinguish a living process from database progress.
They include generated worker/run IDs, last successful scan, expired/no-op/failed counts,
durations and bounded backlog/oldest-overdue information. Exceptions, SQL, credentials,
tokens and consultation text must not be serialized. Reporting failures must never undo
a committed transition. Database outage backoff is bounded and stop-aware.

## Implementation and verification slices

The feature is organized into approximately fifteen reviewable commits:

1. Record prerequisite and worker contract.
2. Add typed outcomes, generated correlation IDs and safe reporting.
3. Add engine/session lifecycle and schema compatibility gate.
4. Add bounded nonlocking candidate/backlog discovery.
5. Own shared expiry transactions and verify state/side effects.
6. Bound retries, database failures and uncertain commits.
7. Continue selected batches after candidate failures.
8. Add stop-aware polling, outage backoff and heartbeat.
9. Bound graceful shutdown and release resources.
10. Provide executable CLI and signal handling.
11. Verify concurrent workers on real MySQL.
12. Race worker expiry with API decisions, replacement and cancellation.
13. Verify restart, interrupted work, deadlines and recovery.
14. Document operation and repeatable stopped-worker/backlog demonstration.
15. Complete regression/CI checks and release handoff evidence.

Tests must use independent Alembic-initialized MySQL 8.4 schemas. Missing prerequisites
must fail with `--require-mysql`, and the full backend suite must have no skipped tests.
Black, Ruff, Pyright, migration checks and deterministic OpenAPI remain required. Final
commands, results, reviewed SHA and runtime limitations will be recorded in the PR and
the completed operational sections of this document.
