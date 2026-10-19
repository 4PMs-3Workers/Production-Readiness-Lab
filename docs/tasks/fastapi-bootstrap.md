# FastAPI bootstrap — Issue #4

**Branch:** `feature/fastapi-bootstrap` · **Base:** `develop` at `cce3e89` · **Date:** 13/10/2026.

This task adds the shared FastAPI runtime and HTTP contract. It consumes the initial Alembic
revision `a1b2c3d4e5f6` delivered by [Issue #3](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/3)
through the reviewed, merged [PR #9](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/9).
Migration commands and recovery remain in `docs/tasks/database-migrations.md`.

## Delivered modules

- `app.core.config`: environment settings, finite positive timeout/pool checks, origin and CIDR parsing,
  required secret/revision checks, cookie policy and sanitized configuration errors. Origin and
  cookie-domain hostnames reject whitespace, backslashes and malformed DNS labels.
- `app.factory` and `app.main`: injectable app factory and Uvicorn entry point. Lifespan constructs
  the engine with `create_database_engine()` and a session factory; shutdown or partial setup
  disposes the engine. Startup itself does not query MySQL or run migrations.
- `app.dependencies.database`: one session per request, rollback of an open transaction and close;
  business services own commits.
- `app.core.request_context`: accepts one 1–128-character ASCII request ID only from an immediate
  peer in `TRUSTED_GATEWAY_CIDRS`; otherwise generates a new ID. The middleware never trusts
  `X-Forwarded-For`.
- `app.api.errors`: stable status/code/class mapping and one sanitized JSON envelope. The
  response header and body carry the same request ID; `405` retains `Allow`. Health errors also
  return `Cache-Control: no-store`. Unexpected exceptions are logged by class only, without
  propagating their raw message or traceback to the ASGI server logger. If an exception interrupts
  a response after headers have been sent, the app raises a safe interruption error so the server
  closes the incomplete transfer instead of marking it successful.
- `app.api.health`: liveness without DB access; readiness uses one deadline for connection,
  `SELECT 1`, and reading the single installed Alembic revision. Only explicit allowlist members
  are accepted. A timed-out probe returns promptly while its tracked task finishes connection
  cleanup, including SQLAlchemy's shielded connection close; shutdown waits for that cleanup
  before disposing the engine. Health responses are `no-store`, and the exported health error
  contract documents `X-Error-Class`.
- `app.schemas.common`: shared error, pagination and timezone-aware timestamp schemas.
- `tools/scripts/export_openapi.py`: deterministic YAML export with explicit validated placeholder
  settings isolated from runtime environment and `.env`, no lifespan and no DB connection.
  Artifact: `docs/api/openapi.yaml`.

## Configuration and runtime

The complete variable table with names, units, allowed values, defaults and required flags is in
`README.md` under “Chạy nền tảng FastAPI”. `.env.example` contains placeholders; secrets and
credential-bearing URLs belong in local `.env` or the deployment environment, never in Git.
<!-- current-schema-revision:start -->
The current release expects schema revision `20261017_2350_student_profile`, as configured in
`.env.example`. Readiness succeeds only when the database contains exactly one revision and that
revision is in `ACCEPTED_SCHEMA_REVISIONS`. List multiple revisions only after compatibility review
confirms the release supports all accepted schemas.
<!-- current-schema-revision:end -->

**Historical context:** this task originally documented `20261016_1200_audit_reqid128` as the
accepted revision before migration `20261017_2350_student_profile` became the current release head.
The initial schema revision was `a1b2c3d4e5f6`.

Use `python -m uvicorn app.main:app --app-dir src/backend --no-proxy-headers` from the repo root.
Keep the immediate Nginx peer visible to the ASGI app and configure only that peer's CIDR as
trusted. If a deployment enables proxy-header rewriting, it must retain the original peer for
request-ID trust decisions; client-supplied forwarded headers never grant trust. Gateway/Nginx
configuration is a separate deployment task.

The default readiness deadline is 2 seconds. The timeout tests allow 0.5 seconds of scheduling
and test-client overhead beyond their 0.2-second budget; the slow-cleanup test allows 0.15
seconds beyond a 0.05-second budget. These tolerances are for tests only.
Query, lock, request and worker/retry settings are validated now and reserved for later business
services and worker implementation.

## Verification

Tests use a separate MySQL 8.4.11 container and `MYSQL_TEST_URL`; each integration test creates
and drops only its own random schema through the migration fixture. The full backend suite covers
config, lifecycle, session rollback, request IDs, error envelope, health compatibility/deadlines,
OpenAPI and the pre-existing model/migration behavior.

Commands to repeat from the repo root:

```text
python -m black --check src/backend tests/backend tools/scripts/export_openapi.py
python -m ruff check src/backend tests/backend tools/scripts/export_openapi.py
python -m pyright
python -m pytest tests/backend -q
python tools/scripts/export_openapi.py
git diff --exit-code -- docs/api/openapi.yaml
```

Local verification on 13/10/2026 used Python 3.11.4 and a dedicated `mysql:8.4.11` container
bound to `127.0.0.1:33071`. `pytest` reported **154 passed, 0 failed, 0 skipped**; its JUnit
report (`build/backend-tests.xml`, ignored by Git) also recorded zero skips. Black reported
41 unchanged Python files; Ruff and Pyright reported zero errors, `pip check` reported no broken
requirements, and OpenAPI export completed. The live Uvicorn smoke test with a deliberately
unreachable MySQL URL returned `/health/live` 200 and `/health/ready` 503, both with
`Cache-Control: no-store`; readiness used `X-Error-Class: service`. The local Docker container
was used only for tests and is removed after verification.

Review-fix verification on 14/10/2026 used the same dedicated MySQL image: **169 passed, 0 failed,
0 skipped**. The new regression tests cover shielded close/disposal order, Uvicorn log sanitization,
malformed hostnames, health error cache headers and OpenAPI error headers. Black, Ruff, Pyright,
`pip check` and artifact consistency were rerun for this revision.

The follow-up streaming fix adds real Uvicorn tests for interrupted chunked and fixed-length
responses. The local backend run reported **117 passed, 54 skipped** because Docker Desktop's
MySQL engine was unavailable on this workstation. The CI workflow requires a dedicated MySQL
instance and rejects skipped backend tests; its result for the follow-up commit must be checked
before merge.

The existing CI job retains both Alembic upgrade checks, Black/Ruff/Pyright and the MySQL
service. It now runs the full backend suite, fails if any required test skips, regenerates
OpenAPI and fails on artifact drift. The [Backend models run](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38415708743)
passed for code commit `6273ca0` on the pushed feature branch. The latest PR checks must pass
before merge.

## Handoff and limits

- No business routes, authentication/CSRF enforcement, appointment transactions, expiry worker
  or telemetry stack are implemented here. `api_v1_router` reserves `/api/v1` for those tasks.
- `audit_events.request_id` uses `varchar(128)` to match the HTTP contract's maximum length.
- The current Starlette test client emits a deprecation warning for `httpx`; it does not affect
  test behavior. Alembic emits an existing `path_separator` warning from its configuration.
- Windows sandboxing on this workstation blocks even an empty asyncio event loop. Async tests
  were run outside that sandbox against a dedicated local MySQL test container.
