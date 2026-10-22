# Durable change-proposal expiry worker — issue #26

Implementation base: `develop` at
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
| `WORKER_BATCH_SIZE` | 100 | Selected candidates per scan, integer 1–1000 |
| `WORKER_GRACE_SECONDS` | 30 seconds | Shutdown cleanup budget; never expiry grace |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | 15 seconds | Independent liveness reporting interval |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | 5, 5 | Finite per-process connection capacity |
| `DB_POOL_TIMEOUT_SECONDS` | 0.5 seconds | Pool acquisition bound |
| `DB_CONNECT_TIMEOUT_SECONDS` | 1 second | Connection bound |
| `DB_QUERY_TIMEOUT_SECONDS` | 1 second | Query/transaction-attempt bound |
| `DB_LOCK_TIMEOUT_SECONDS` | 0.5 seconds | Each locking-stage bound |
| `REQUEST_TIMEOUT_SECONDS` | 3 seconds | Complete candidate operation/retry budget |
| `MAX_DEADLOCK_RETRIES` | 2 | Additional known-aborted transaction attempts |

Settings validation requires positive finite intervals, an integer batch of 1–1000,
positive pool sizes, nonnegative overflow/retries, a MySQL+asyncmy URL and nonempty accepted
revisions. The worker uses the same validated backend environment as the API, without
creating interactive sessions or hashing passwords.

Published JSON diagnostics must distinguish a living process from database progress.
They include generated worker/run IDs, last successful scan, expired/no-op/failed counts,
durations and bounded backlog/oldest-overdue information. Exceptions, SQL, credentials,
tokens and consultation text must not be serialized. Reporting failures must never undo
a committed transition. Database outage backoff is bounded and stop-aware: exponential
from the poll interval (at least 0.1 seconds), capped at 60 seconds, reset after recovery.

## Verification contract

Tests must use independent Alembic-initialized MySQL 8.4 schemas. Missing prerequisites
must fail with `--require-mysql`, and the full backend suite must have no skipped tests.
Black, Ruff, Pyright, migration checks and deterministic OpenAPI remain required. Final
commands, results, reviewed SHA and runtime limitations will be recorded in the PR and
the operational sections below.

## Start, stop and exit status

From the repository root, after the normal backend installation:

```sh
python -m pip install -r requirements-dev.txt
python -m pip install -e .
python -m app.worker --help
python -m app.worker
```

Use a second terminal/process for the API (`python -m uvicorn app.main:app --app-dir
src/backend --no-proxy-headers`). Starting the API does not start a worker. Each process
creates/disposes its own finite pool; include every API/worker process in the connection
budget. Multiple workers are supported through row locks, without a broker or leader lease.

Supply the validated backend environment from `.env` or the environment, including
`DATABASE_URL`, `ACCEPTED_SCHEMA_REVISIONS` and the shared application/cookie settings.
Use actual private values instead of `.env.example` placeholders. Settings are reused,
but the worker does not authenticate users, issue sessions or instantiate the API lifespan.
Provision the normal `service_settings(id=1)` row through the existing authorized setup.
Migrations run separately with a migration account before startup. Missing/old/multiple
schema revisions prevent business writes; the supported head is tested against Alembic.

To process at most one batch and exit, including an empty successful scan:

```sh
python -m app.worker --once
```

Exit codes: 0 for a clean stop or successful single batch, 1 for a failed single scan/
candidate, unexpected runtime failure or failed cleanup, 2 for invalid configuration/CLI
or incompatible schema, and 130 for an interrupt before normal signal handling is active.
A successful batch can leave more work than its configured limit; exit 0 does not prove
an empty backlog. Continuous mode reports recoverable failures and keeps polling.

Use Ctrl+C in the foreground terminal, or SIGINT/SIGTERM on supported systems. Windows
uses the portable signal-handler fallback. Repeated stop requests do not extend the deadline.
The process stops selecting new candidates, gives current work part of the total grace
budget to finish, then cancels/rolls it back and closes resources in the remaining budget.
There is one deadline for work, sessions, engine and publisher cleanup. Failed engine cleanup returns
nonzero and detaches its pool. An external supervisor remains responsible for an OS-level
hard stop if database/native I/O cannot cooperate; a subsequent process recovers from database state.
An unread stdout pipe does not block polling, stop callbacks or process exit.

## Transaction and recovery details

`app.repositories.expiry_worker.discover` captures `UTC_TIMESTAMP(6)` from MySQL, then
selects `status=PENDING AND expires_at <= database_now`, ordered by expiry and UUID,
limited to `WORKER_BATCH_SIZE`. Candidate and monitoring queries are nonlocking. The
clock is authoritative database time, not host time, and equality is eligible. The shared
transition obtains a fresh database clock after its business locks, so discovery cannot
authorize a stale decision. No grace interval is added to persisted expiry.

Each candidate gets a new session/transaction and generated audit request ID. The request
budget includes retries and reserves time for cleanup. Each attempt is also query-bounded;
shared transition locks use the existing per-lock timeout. The pool and connect bounds
apply to acquiring a connection too. Only known-aborted 1205/1213 transactions retry,
with rollback/close before a fresh attempt and bounded delay inside the remaining budget.

An unknown commit produces `COMMIT_UNKNOWN`, connection invalidation and a failed
observation. It is not immediately retried. A later scan/restart reads the durable pair:
if the commit succeeded, the proposal is terminal and no side effects are added; otherwise
it remains pending and can be expired normally. A hard crash after commit but before
acknowledgement has the same recovery rule. Audit and notification rows, rather than
the last stdout message, are the authoritative outcome.

A failed candidate does not stop its selected batch. Fairness uses an in-memory cursor
only as a scan hint: each finite due-time cohort is scanned in expiry/UUID order, then
the cursor wraps to older failed rows. Newly due rows join the next cohort. An inconsistent
oldest row therefore cannot permanently block later healthy rows even at batch size 1.
The cursor is not a persisted queue/checkpoint; restarting starts from durable pending rows.
An empty end-of-cohort page can coexist with a nonempty global backlog estimate.

Account activity/roles are intentionally irrelevant for system cleanup. Terminal and
superseded proposals are no-ops even if another proposal is now pending. INCONSISTENT
pairs remain untouched and emit a failed observation for authorized operational repair.
The worker does not rewrite proposal versions, proposed values, meeting content or original
appointment intervals. It only applies the prerequisite's system expiry transition.

## Permissions and release compatibility

Use the existing backend runtime role, separate from migration credentials. The expiry
path reads `alembic_version`, `service_settings`, `users`, `appointments` and
`change_proposals`; updates appointments/proposals; inserts audit and notification rows.
The participant `FOR UPDATE` reads also need a suitable locking-read privilege on `users`
(such as the runtime role's UPDATE privilege), although expiry does not update accounts.
MySQL documents SELECT plus UPDATE/DELETE/LOCK TABLES for FOR UPDATE, and SELECT for
FOR SHARE in its [locking-read reference](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html).
Do not grant the worker DDL or audit UPDATE/DELETE privileges. Operator verification of
audit/notification counts additionally needs SELECT on those tables.

This issue has no migration and keeps head `20261019_1200_proposal_replay`. Release API
and worker from the same compatible backend source/image. Stop both appropriately before
an incompatible migration or rollback. Preserve durable proposal/audit/notification data;
do not reset expiry or delete receipts to make an older image appear compatible.

## Read heartbeat and backlog reports

Stdout contains JSON events `starting`, `ready`, `heartbeat`, `candidate`, `scan`,
`stopping`, `stopped` and sanitized `fatal` events. Readiness is reported after a successful
schema-validated discovery; a heartbeat before that can still show a live but blocked process.

Publishing uses a single daemon thread and a queue of **64 records**, each limited to
**8192 UTF-8 bytes**, plus at most one in-flight record. Admission never waits for I/O or
queue space. A full queue drops the newest record; expired queued records are discarded.
Each record has a **0.25-second publication deadline**, including queue time. Sink errors,
oversize records, full-queue drops, deadlines and shutdown discards increment
`reporting_failures`; asynchronous failures are collected on the next emit or cleanup.
`Reporter.emit()` returning true means accepted for publication, not delivered.

Stdout writes use a duplicated raw file descriptor on the publisher thread, without a
Python stdout/logging lock or the asyncio default executor. A blocked native write may
outlive its publication deadline, but cannot block the event loop. No additional writer
is spawned to retry it. Publisher cleanup waits at most **0.1 seconds**, further limited
by the existing shutdown deadline, then discards queued work and abandons the daemon
writer. The writer closes its descriptor when it returns; process exit releases a still
blocked descriptor without joining the thread or flushing its output through Python.
An unsupported stdout descriptor is a reporting failure; no synchronous fallback is used.

Reports are best effort: overload can lose records, including the final `stopped`/`fatal`
event, and a process exit can leave an incomplete final line. Healthy output is drained
within the cleanup budget. Reporting loss does not change a successful business exit
status. Restore/drain the collector to resume output; inspect durable rows when logs are
missing. These publisher limits are fixed code bounds, not additional environment settings.

| Field | Interpretation |
| --- | --- |
| `worker_id`, `current_run_id` | Generated process and scan identities |
| `phase` | starting, running, stopping or stopped |
| `database_ok` | Latest scan/candidate observations, separate from process liveness |
| `last_successful_scan` | UTC database timestamp of the last successful discovery; unchanged on failure |
| `successful_scans`, `failed_scans` | Cumulative successful/failed discovery counts, including successful empty scans |
| `expired`, `noops`, `failed` | Cumulative acknowledged candidate outcomes |
| `selected`, `batch_expired`, `batch_noops`, `batch_failed` | Most recent selected batch and its observed outcomes |
| `last_scan_duration_seconds` | Last completed cycle/failure duration, including its candidate processing |
| `backlog_count`, `backlog_capped` | Global due rows observed, capped at batch size; true means more rows existed |
| `oldest_overdue_seconds` | Age of the oldest due row at discovery; null for no due rows |
| `reporting_failures` | Failed reporting attempts; best-effort publishing never rolls back committed work |

Monitoring reads examine at most `batch_size + 1` due rows. Counts/ages are observations,
can race concurrent commits, and remain last-known values during an outage. They must not
be used to decide business state. `selected=0` alone does not prove an empty durable queue.
Candidate records contain only proposal/request IDs, typed outcome/reason, attempt count
and duration. Correlation IDs have the form `expiry.<worker>.<run>.<proposal>` and fit the
shared 1–128 ASCII character contract. Audit metadata remains appointment/proposal IDs only.

Operators detect a dead/stopped reporter through missing heartbeats; database trouble
through failed scans, `database_ok=false` and aging last-success timestamps; and business
lag through growing backlog age, failures and counters that stop advancing. Allow for a
batch's configured transaction budgets when interpreting scan age; heartbeats and per-candidate
progress distinguish a busy batch from a stalled scan. Alert on INCONSISTENT failures and
inspect the identified pair without automatically overwriting terminal appointments.

After a database outage, restore connectivity/permissions and observe successful scan
timestamps and counters advancing. Backoff is interruptible and resets after recovery.
After an unknown commit, inspect the proposal/audit by its correlation IDs or let the next
cohort/restart reread it. No manual replay of raw SQL updates or saved side-effect inserts
is needed. A functioning stdout consumer is needed for diagnostic delivery; database
progress and shutdown remain independent of it. Full telemetry transport, dashboards,
alert delivery and an HTTP worker health endpoint are separate deployment work.

## Repeatable stopped-worker demonstration

For a fast demonstration without demo seed or production data, supply `MYSQL_TEST_URL`
for a dedicated MySQL 8.4 test server with schema create/drop privileges (race tests also
read `performance_schema` lock/connection observations), then run:

```sh
python -m pytest tests/backend/integration/worker/test_recovery.py \
  -k stopped_worker_backlog_demonstration -s -q --require-mysql
```

The test creates a fresh Alembic schema and an independent pending proposal ten seconds
from expiry. A real worker process scans before the deadline and stops. The database clock
passes expiry while it is stopped; the appointment still occupies quota and its slot.
Restarting the module expires the row and records one audit/two notifications; another
restart adds none. The schema is removed afterward. The crash tests in the same file
also terminate separate processes immediately before commit or immediately after commit
but before acknowledgement; restart proves rollback/recovery without duplicates.

To demonstrate the same sequence against an explicitly configured local application:

1. With a normal student session, create an appointment in the configured booking window.
   Read its staff proposal context, then create a pending proposal with the assigned staff
   session using the [prerequisite HTTP examples](appointment-change-proposals.md). Record
   appointment/proposal IDs and the persisted `expires_at`. The API cutoff is business-day
   midnight; allow that actual deadline to pass rather than altering application clocks.
2. Stop the foreground worker before that deadline using Ctrl+C/SIGTERM. Keep the API and
   MySQL running. Observe the `stopped` event. Until a worker commits expiry, history still
   shows CHANGE_PENDING and the original interval/quota remain occupied.
3. At or after database UTC time reaches persisted expiry, restart `python -m app.worker
   --once`, or continuous mode. Read proposal history and current appointment separately:
   EXPIRED and CANCELLED, matching resolution/cancellation timestamps, system actors NULL.
4. With authorized read access, verify the affected row bundle (replace UUID placeholders):

```sql
SET @proposal = UUID_TO_BIN('PROPOSAL_UUID');
SET @appointment = UUID_TO_BIN('APPOINTMENT_UUID');
SELECT UTC_TIMESTAMP(6);
SELECT status, resolved_at, resolved_by FROM change_proposals WHERE id = @proposal;
SELECT status, cancelled_at, cancelled_by FROM appointments WHERE id = @appointment;
SELECT COUNT(*) AS expiry_audits FROM audit_events
 WHERE action = 'CHANGE_PROPOSAL_EXPIRED' AND entity_id = 'PROPOSAL_UUID';
SELECT COUNT(*) AS expiry_notifications FROM notifications
 WHERE event_type = 'CHANGE_PROPOSAL_EXPIRED' AND appointment_id = @appointment;
```

Expected counts are one and two. Run the worker again and compare unchanged timestamps
and counts. Filter specifically on these IDs because other proposals can expire concurrently.
Do not print consultation content, session/CSRF tokens, credentials or full exception values.

## Verification commands and limitations

```sh
python -m black --check --workers 1 src/backend tests/backend tools/scripts
python -m ruff check src/backend tests/backend tools/scripts
pyright
python -m app.worker --help
python -m pytest tests/backend -q --require-mysql --junitxml=build/backend-tests.xml
python tools/scripts/export_openapi.py
git diff --exit-code -- docs/api/openapi.yaml
```

Worker tests live in `tests/backend/integration/worker`, `test_worker_schema.py` and
`tests/backend/unit/test_worker_*.py`. They cover exact expiry, no-ops/inconsistency,
inactive accounts, audit/notification atomicity, original slot/quota, bounded/fair scans,
pool/lock/query/request timeouts, 1205/1213 retries, outages, independent heartbeats,
shutdown, two real worker processes, authenticated API races in both orders, persisted
backlog restart and hard crashes before/after commit. `test_backpressure.py` runs the
actual CLI against file and unread-pipe stdout with 20 due rows, verifies all 20 transitions,
20 audit records and 40 notifications before reading stdout, then tests a scheduled stop
with a 0.1-second grace while pending work remains. That probe checks the original pool's
checked-out count, released locks, reporting failures and durable restart without duplicates.
The test observation timeout includes startup; it is not a product expiry SLA.
No integration prerequisite is
silently skipped when `--require-mysql` is supplied.

Local release verification on 2026-10-22 used Python 3.12.14 and an isolated MySQL
8.4.11 instance. The worker integration/schema tests plus `test_worker_*.py` unit tests
passed **107 tests, zero skips**. The complete backend unit suite passed **451 tests,
zero skips** (including the worker unit tests). Black checked all 209 backend/test/tool
Python files; Ruff passed; Pyright reported zero errors and warnings. Exporting OpenAPI
left `docs/api/openapi.yaml` unchanged. The CLI regression also verifies that safe JSON
diagnostics survive external logging reconfiguration. A slow, failed or unread stdout sink
is isolated from business processing and bounded cleanup without leaking traceback details.
CI checks the module's `--help`
before database setup, then runs migrations and the complete backend suite on Python 3.11.
The PR records those full-suite results and their exact reviewed head SHA.

There is no new API/OpenAPI route or schema migration. Polling has no hard expiry-latency
SLA: lag depends on load, contention, outages and configured limits. Notifications are
durable database records; delivery through external channels is outside this process.
Inconsistent pairs need operator diagnosis/authorized repair. Monitor stdout and persist
its safe records externally; reporting is best effort and acknowledged counters can miss
a commit whose process/connection died before acknowledgement. Database state is durable.
The final PR records the tested head SHA, full suite/check results and schema compatibility.
