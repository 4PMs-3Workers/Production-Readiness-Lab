# Mutation experiment - VT-069

- Base/source/test commit: `bb34804de3dbc092233174e9c17639f3823e25c4` (`feat: make student booking quota configurable`).
- Isolated worktree: `D:\Production-Readiness-Lab-bt3-23001890-vt069-mutation`, detached at the same commit.
- Mutation: `src/backend/app/services/booking.py`, `_run_attempt_with_commit_safety`, immediately around the `lock_service_settings` read. Catch `SQLAlchemyError`, construct a plausible default policy (Asia/Ho_Chi_Minh, 15-minute step, 7-day horizon, quota 3), and continue. The exact inert diff is `swallow-config-exception.patch`.
- Command: `$env:PYTHONUTF8='1'; $env:PYTHONPATH='src/backend'; D:\Production-Readiness-Lab-bt3-23001890-vt069\.venv\Scripts\python.exe -m pytest tests/backend/unit/test_booking_service_retries.py::test_config_read_exception_is_not_swallowed_into_default_policy -q`.
- Working directory: isolated mutation worktree above. Runtime: Python 3.11.4; pytest 9.0.3; no MySQL required for this deterministic repository-boundary fault injection.
- Expected: fail because the mutation turns a config read exception into a success outcome instead of raising `ServiceUnavailable`.
- Actual: `1 failed in 1.17s`, exit code 1. Pytest failed at `with pytest.raises(ServiceUnavailable):` with `Failed: DID NOT RAISE <class 'app.core.errors.ServiceUnavailable'>`.
- Contract assertion violated: a dependency failure must remain `503 SERVICE_UNAVAILABLE`; it must not become a success with default policy. Inference from the mutation's control flow: if the test continued past the exception assertion, the default policy would allow subsequent booking work; the observed failing assertion itself proves the failure was swallowed. The failure is semantic, not syntax/import/setup.
- Conclusion: **killed** by `test_config_read_exception_is_not_swallowed_into_default_policy`.
- Cleanup: the mutation file was restored from the isolated worktree's HEAD; worktree status was checked clean before removal. Submission branch was never mutated.
