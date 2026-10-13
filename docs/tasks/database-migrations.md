# Database Migrations — Production Readiness Lab

**Revision ID:** `a1b2c3d4e5f6`
**Migration tool:** Alembic 1.16.2
**Target database:** MySQL 8.4 (InnoDB, utf8mb4)

---

## Table of Contents

1. [Environment Setup](#1-environment-setup)
2. [Running Migrations](#2-running-migrations)
3. [Generating New Revisions](#3-generating-new-revisions)
4. [Schema Verification](#4-schema-verification)
5. [Deployment Procedure](#5-deployment-procedure)
6. [Adoption — Stamping an Existing Database](#6-adoption--stamping-an-existing-database)
7. [Downgrade Behavior](#7-downgrade-behavior)
8. [Recovery from Partial DDL](#8-recovery-from-partial-ddl)
9. [Release Rollback](#9-release-rollback)
10. [Implementation Notes](#10-implementation-notes)
11. [Verification Results](#11-verification-results)

---

## 1. Environment Setup

### Dependencies

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements-dev.txt
```

Alembic and its dependency Mako are pinned in both `requirements.txt` and
`pyproject.toml`. All other database dependencies (`SQLAlchemy`, `asyncmy`,
`cryptography`) are also pinned and installed via `requirements-dev.txt`.

### Environment Variables

Copy `.env.example` and fill in real values. **Never commit credentials.**

```bash
cp .env.example .env
# Edit .env:
DATABASE_URL=mysql+asyncmy://migration_user:secret@localhost:3306/production_readiness_lab
MYSQL_TEST_URL=mysql+asyncmy://root:secret@localhost:3306
```

`DATABASE_URL` is consumed by `src/backend/migrations/env.py` for all Alembic
CLI commands. `MYSQL_TEST_URL` is consumed by the integration test fixture.

The migration environment reads the URL from the shell variable; it is never
printed to logs or error messages. If `DATABASE_URL` is absent or uses the
wrong driver scheme, a `RuntimeError` is raised with instructions.

### Running alembic commands

All commands must be run from the **repository root** (where `alembic.ini` is):

```bash
# Activate venv first, then:
alembic upgrade head
alembic current
alembic heads
alembic history
```

---

## 2. Running Migrations

### Initial setup — fresh database

```bash
export DATABASE_URL=mysql+asyncmy://migration_user:secret@host:3306/production_readiness_lab
alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Context impl MySQLImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> a1b2c3d4e5f6, Initial schema
```

### Verify current revision

```bash
alembic current
# Expected: a1b2c3d4e5f6 (head)
```

### Verify single head

```bash
alembic heads
# Expected: a1b2c3d4e5f6 (head)
```

### Idempotency check — running again on an already-migrated database

```bash
alembic upgrade head
# Expected: no output (no migrations to run — already at head)
```

Data committed before this command is preserved.

---

## 3. Generating New Revisions

Schema changes after the initial revision must be introduced in **new, separate
revision files**. Never edit `a1b2c3d4e5f6_initial_schema.py` once it has been
applied to a shared environment.

```bash
# 1. Make model changes in app/models/*.py

# 2. Generate a draft revision (autogenerate is a starting point, not final):
alembic revision --autogenerate -m "add_column_appointments_priority"

# 3. Review the generated file in src/backend/migrations/versions/:
#    - Verify MySQL-specific DDL (collation, generated columns, engine/charset)
#    - Check that pending_guard computed columns use sa.Computed(..., persisted=False)
#    - Confirm named constraints match the naming_convention in Base.metadata
#    - Add a DESTRUCTIVE warning in downgrade() if it drops tables or columns

# 4. Apply to a development database and run tests:
alembic upgrade head
pytest -q

# 5. Commit the reviewed revision file.
```

### Review checklist for every new revision

- [ ] Named constraints match `pk_`, `fk_`, `ck_`, `uq_`, `ix_` convention
- [ ] String columns use explicit collation where required
- [ ] `DATETIME(fsp=6)` for timestamp columns, `TIME(fsp=0)` for time-of-day
- [ ] `BINARY(16)` for UUID columns, `LargeBinary(32)` for SHA-256 hashes
- [ ] Virtual generated columns use `sa.Computed(..., persisted=False)`
- [ ] `mysql_engine="InnoDB"`, `mysql_charset="utf8mb4"`, `mysql_collate="utf8mb4_0900_ai_ci"` on every new table
- [ ] `downgrade()` is labeled DESTRUCTIVE if it drops tables or non-nullable columns
- [ ] There is exactly **one** Alembic head after adding the revision

---

## 4. Schema Verification

After upgrading, verify the migrated schema using these commands against MySQL:

### Table count

```sql
-- Expect 12 rows: 11 app tables + alembic_version
SHOW TABLES;
```

### Engine, charset, collation

```sql
SHOW CREATE TABLE users\G
-- Must show: ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
```

### Binary collation on code columns

```sql
SHOW CREATE TABLE appointments\G
-- Must show: utf8mb4_0900_bin on creation_request_key and status columns
```

### Email collation

```sql
SHOW CREATE TABLE users\G
-- Must show: utf8mb4_0900_as_ci on email column
```

### Virtual generated column (pending_guard)

```sql
SHOW CREATE TABLE change_proposals\G
-- Must contain: GENERATED ALWAYS AS (case when ...) VIRTUAL
```

### DATETIME(6) precision

```sql
SHOW CREATE TABLE sessions\G
-- Must show: datetime(6) on expires_at, created_at
```

### Autogenerate emptiness check

```bash
# Should produce no upgrade/downgrade operations — only "No changes in schema detected."
alembic revision --autogenerate -m "verify" --rev-id verify_check
# Then inspect the generated file and delete it.
```

> **Note:** An empty autogenerate result is a supporting signal, not sufficient
> evidence on its own. Always verify constraints, collations, and generated
> columns by inspecting `SHOW CREATE TABLE` output.

---

## 5. Deployment Procedure

### Account separation

| Account | Permissions needed |
|---|---|
| **Migration account** | `CREATE`, `DROP`, `ALTER`, `INDEX`, `REFERENCES` on the target schema<br/>`SELECT`, `INSERT`, `UPDATE`, `DELETE` on `alembic_version` table<br/>(Future data migrations may require DML on application tables) |
| **API runtime account** | `SELECT`, `INSERT`, `UPDATE`, `DELETE` on the target schema only |

The API runtime account must **not** have DDL permissions. Migration runs use
the migration account; the running application uses the runtime account.

**Migration account DML requirements:**
- Alembic reads/writes the `alembic_version` table to track applied revisions
- Future data migrations (INSERT/UPDATE application rows during schema changes) require DML grants on affected tables
- Grant pattern: `GRANT SELECT, INSERT, UPDATE, DELETE ON dbname.* TO 'migration_user'@'%';` (or specific tables as needed)

### Serialized deployment job

Migrations must run exactly once per deployment, as a **serialized, sequential
job** before any new API instances start serving traffic:

```
1. Run database migration job (migration account):
       alembic upgrade head
   Verify: alembic current == expected revision

2. Start new API instances (runtime account).

3. If step 1 fails, halt the deployment. Do not start new API instances.
```

Running multiple parallel migration jobs against the same database is not
supported. Alembic's `alembic_version` table provides an advisory record of the
applied revision, but does not prevent concurrent DDL execution.

### One head policy

There must always be exactly one Alembic head before deploying:

```bash
alembic heads
# Must output exactly one line ending in "(head)"
```

If multiple heads are present, resolve them by merging before deployment.

---

## 6. Adoption — Stamping an Existing Database

> [!CAUTION]
> Never auto-stamp an existing database. An incorrect stamp records a revision as
> applied when the actual schema does not match, causing future migrations to
> produce incorrect DDL.

To adopt an existing database that was created by a means other than Alembic
(e.g., via `Base.metadata.create_all()`):

1. **Verify the actual schema** matches the initial revision by running
   `SHOW CREATE TABLE` for each of the 11 application tables and comparing
   against the DDL in `a1b2c3d4e5f6_initial_schema.py`.

2. Confirm all named constraints, collations, generated columns, and indexes are
   present and correct.

3. Only after verification, stamp the revision:
   ```bash
   alembic stamp a1b2c3d4e5f6
   ```

4. Verify:
   ```bash
   alembic current
   # Must output: a1b2c3d4e5f6 (head)
   ```

---

## 7. Downgrade Behavior

### Initial revision — DESTRUCTIVE

The `downgrade()` function in `a1b2c3d4e5f6_initial_schema.py` drops all 11
application tables in reverse FK order.

**This is a permanent, unrecoverable operation.** It is intended only for:

- Local development databases where you want to start fresh
- Disposable test schemas

**Do NOT run downgrade on shared development, staging, or production databases.**

```bash
# Development / test only:
alembic downgrade base
```

### Future revisions

Each new revision must define an explicit `downgrade()`. If a downgrade path is
not supported (e.g., a revision that adds a non-nullable column with no
backwards-compatible default), the `downgrade()` function must raise an
exception rather than silently succeed:

```python
def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from this revision is not supported. "
        "Restore from a database backup."
    )
```

---

## 8. Recovery from Partial DDL

> [!WARNING]
> MySQL executes DDL statements non-transactionally. If a migration is
> interrupted (process kill, network failure, MySQL crash), some DDL statements
> may have been applied and others may not. **Do not assume a failed migration
> rolled back all changes.**

### Recovery procedure

1. **Inspect `alembic_version`:**
   ```sql
   SELECT version_num FROM alembic_version;
   ```
   If the table does not exist or is empty, the migration did not complete the
   initial revision. If it contains `a1b2c3d4e5f6`, the revision was stamped
   (possibly incorrectly).

2. **Inspect the actual schema:**
   ```sql
   SHOW TABLES;
   SHOW CREATE TABLE <table_name>\G
   ```
   Compare each table's DDL against `a1b2c3d4e5f6_initial_schema.py`.

3. **Identify the completed and incomplete steps.** DDL in the upgrade function
   is ordered: `users` → `purposes` → `service_settings` → `service_hours` →
   `sessions` → `staff_purposes` → `staff_unavailability` → `appointments` →
   `change_proposals` → `notifications` → `audit_events`.

4. **Apply missing DDL manually** using the SQL equivalent of the incomplete
   `op.create_table()` / `op.create_index()` calls in the revision file.

5. **Verify** the full schema against `SHOW CREATE TABLE` output for all 11
   tables.

6. **Stamp the revision** only after the schema is verified:
   ```bash
   alembic stamp a1b2c3d4e5f6
   alembic current  # must show (head)
   ```

---

## 9. Release Rollback

Application rollback restores compatible container/process images and **does
not automatically run migration downgrades.**

A rollback to a previous application release that is schema-compatible with the
current database revision requires no migration action. The previous release
must be validated against the current schema before deployment.

If a schema change is incompatible with the previous release, a compatible
intermediate schema must be prepared and deployed before restoring the previous
application images.

**Migration downgrades are not part of the standard rollback procedure** for
shared or production environments. Downgrade is permitted only for the initial
revision on disposable databases (see [Section 7](#7-downgrade-behavior)).

---

## 10. Implementation Notes

### Async driver pattern

Alembic's standard API is synchronous. The backend uses `mysql+asyncmy`, which
is an async driver. The migration environment (`src/backend/migrations/env.py`)
uses `AsyncEngine.sync_engine` to obtain a synchronous connection proxy when
running from the CLI. In test fixtures, the caller passes an active connection
via `config.attributes["connection"]` and `run_sync`, so Alembic never creates
its own engine.

### `pending_guard` virtual generated column

`ChangeProposal.pending_guard` is declared as
`Computed("CASE WHEN status = 'PENDING' THEN 1 ELSE NULL END", persisted=False)`.
MySQL stores this as a `VIRTUAL` generated column (not persisted on disk).
Alembic's autogenerate does not reliably detect virtual generated columns in
MySQL. The initial revision renders `pending_guard` using an explicit
`sa.Computed(...)` clause and must be verified with `SHOW CREATE TABLE
change_proposals\G` to confirm the `GENERATED ALWAYS AS ... VIRTUAL` syntax is
correct.

### Constraint naming convention

All constraints use the `MetaData` naming convention defined in `Base.metadata`:

| Type | Pattern | Example |
|---|---|---|
| PK | `pk_<table>` | `pk_users` |
| FK | `fk_<table>_<col>_<ref_table>` | `fk_sessions_user_id_users` |
| CHECK | `ck_<table>_<name>` | `ck_users_email_not_empty` |
| UNIQUE | `uq_<table>_<col>` | `uq_users_email` |
| INDEX | `ix_<table>_<col>` | `ix_sessions_user_id` |

---

## 11. Verification Results

### Revision

| Property | Value |
|---|---|
| Revision ID | `a1b2c3d4e5f6` |
| Down revision | `None` (initial) |
| Created | 2026-10-13 |

### Table count

| Category | Count |
|---|---|
| Application tables | 11 |
| Alembic version table | 1 |
| **Total** | **12** |

### Application tables

`appointments`, `audit_events`, `change_proposals`, `notifications`, `purposes`,
`service_hours`, `service_settings`, `sessions`, `staff_purposes`,
`staff_unavailability`, `users`

### CI verification commands

```bash
alembic upgrade head  # initial: applies a1b2c3d4e5f6
alembic current       # a1b2c3d4e5f6 (head)
alembic heads         # a1b2c3d4e5f6 (head)
alembic upgrade head  # second run: no-op, data preserved
pytest -q             # all tests pass
```

> Full CI evidence (run logs, SHOW CREATE TABLE output) will be recorded in the
> Pull Request for this issue.
