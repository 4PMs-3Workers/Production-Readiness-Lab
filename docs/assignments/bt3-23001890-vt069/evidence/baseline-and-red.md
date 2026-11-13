# Baseline and RED evidence

## Repository baseline

- Official remote observed: `https://github.com/4PMs-3Workers/Production-Readiness-Lab.git`.
- Base branch: `origin/develop` per `docs/other/GITFLOW.md`.
- Source baseline full SHA: `12a74f7308550ccc294a783edde79db267a526d1`.
- Baseline date: 2026-11-11 20:04:18 +07:00 (commit metadata).
- Branch/worktree: `feature/bt3-23001890-vt069`, `D:\Production-Readiness-Lab-bt3-23001890-vt069`.
- Original checkout kept untouched; it was on stale `develop` SHA `aa8c5b0be057754f9b3409c8699537d4aeb887b9` with pre-existing unstaged `.gitignore` change.
- The CodeGraph directory/index existed only in that original checkout. Its indexed source was at the stale checkout commit, not this baseline; querying the worktree reported no index. No CodeGraph-derived source was used for implementation.

## Baseline full backend suite

- Command: `$env:PYTHONUTF8='1'; .\.venv\Scripts\python.exe -m pytest tests/backend -q`
- Working directory: `D:\Production-Readiness-Lab-bt3-23001890-vt069`.
- Source state: clean source commit `12a74f7308550ccc294a783edde79db267a526d1`; assignment spec/plan files did not alter production source.
- Environment: Python 3.11.4; `requirements-dev.txt`, backend package and `tools/infra/offhost` installed in isolated `.venv`; Windows 11; `PYTHONUTF8=1`. `MYSQL_TEST_URL` absent. Docker CLI present but Docker Desktop Linux engine unavailable (`npipe ... dockerDesktopLinuxEngine ... not found`).
- Expected: suite completes and exposes pre-existing failures/skips independently of assignment changes.
- Actual: **1 failed, 1,774 passed, 638 skipped, 1 warning**; exit code **1**; 236.33 s.
- Skip reason from repository fixture: `MYSQL_TEST_URL` is required to run tests against MySQL 8.4. Skips are not passes.
- Pre-existing failure: `tests/backend/integration/test_http_errors.py::test_uvicorn_logs_unexpected_error_without_sensitive_exception_text` hit an `httpx.ConnectTimeout` while polling the loopback Uvicorn server; failure occurred before a response, so its error-log behavior was not verified. The failure is host/socket timing related by observed traceback; that classification is an inference, not a proven root cause.
- Setup correction: an earlier run before installing the two editable repo packages had collection/import and Windows encoding failures. It is superseded by the properly configured run above and is not used as the final baseline result.

## RED: config contract test before implementation

- Command: `$env:PYTHONUTF8='1'; .\.venv\Scripts\python.exe -m pytest tests/backend/unit/test_booking_quota_contract.py -q`
- Working directory: `D:\Production-Readiness-Lab-bt3-23001890-vt069`.
- Source state: baseline production source at `12a74f7308550ccc294a783edde79db267a526d1`; only the new test module had been added.
- Environment: Python 3.11.4; Pydantic 2.14.1; pytest 9.0.3; isolated `.venv`.
- Expected: valid contract input/response fails because the versioned policy has no quota field; invalid values continue to fail closed.
- Actual: **1 failed, 5 passed**, exit code **1**, 0.24 s. Failing assertion reached `BookingConfigurationReplace.model_validate(...)`; Pydantic reported `max_active_appointments` as `extra_forbidden`. It was not an import/fixture error.

## MySQL integration preflight (not RED)

- Command: `.\.venv\Scripts\python.exe -m pytest tests/backend/integration/test_booking_quota_configuration.py tests/backend/integration/test_admin_booking_configuration.py -q`
- Source state: implementation plus schema/API tests on `feature/bt3-23001890-vt069`; no MySQL-backed code path was executed.
- Expected: provider-consumer contract and existing admin policy tests run against isolated MySQL schemas.
- Actual: **29 skipped**, exit code **0**, because `MYSQL_TEST_URL` is not configured. This is unavailable evidence, not a passing integration result.
