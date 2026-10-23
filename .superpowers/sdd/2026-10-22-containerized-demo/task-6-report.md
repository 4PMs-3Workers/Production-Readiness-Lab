# Task 6 report — non-destructive Compose smoke and restart acceptance

Added a dedicated Compose Playwright config/spec and isolated real-stack acceptance. The Playwright runner invokes the complete `tests/frontend/compose` suite with no caller-controlled grep option, so student and staff coverage always run together. A focused regression sets the former filter environment value and verifies the runner still builds the unfiltered command.

The acceptance creates a uniquely named `prl-task6-<uuid>` project, disposable seeded credentials/data, and test-owned image tags. It checks API/worker image identity and source labels, worker heartbeat/progress and overdue proposal replay, student booking/history/detail/cancellation/logout, and the staff proposal-backed portal. It also exercises API and MySQL restarts, incompatible-schema fail-closed behavior, bounded database outage and recovery. Cleanup verifies exact project ownership, uses `compose down` without volume removal, and removes only the verified test-owned volume and images. No destructive API E2E reset harness is used.

## Independent-review follow-up

The review found that `PRL_COMPOSE_PLAYWRIGHT_GREP` could select one role test while the outer Compose acceptance treated any successful Playwright exit as full coverage. Removed the environment lookup and `--grep` argument construction. The regression failed before the fix because the command included `--grep student only`, and passed afterward with the same former setting present. The config targets one spec file containing exactly two cases: student and staff.

## Validation (2026-10-22)

- Full isolated acceptance, with the former filter variable unset:
  ```powershell
  $env:RUN_COMPOSE_ACCEPTANCE='1'
  Remove-Item Env:PRL_COMPOSE_PLAYWRIGHT_GREP -ErrorAction SilentlyContinue
  python -m pytest -q --tb=short -p no:cacheprovider tests/containers/test_compose_acceptance.py
  ```
  Result: **3 passed in 176.89s** (two local runner regressions and the full real Compose lifecycle test). The lifecycle test's Playwright invocation completed successfully without a filter. The configured spec lists exactly two cases.
- Focused Playwright-runner regression: **1 passed** after the red run confirmed the old `--grep` argument.
- `python -m ruff check --no-cache tests/containers/test_compose_acceptance.py`: passed.
- `python -m ruff format --check tests/containers/test_compose_acceptance.py`: passed.
- `npx prettier --check tests/frontend/compose/real-demo.spec.ts src/frontend/playwright.compose.config.ts`: passed.
- Playwright discovery:
  ```powershell
  $env:PRL_COMPOSE_BASE_URL='http://localhost:3100'
  npx playwright test --config src/frontend/playwright.compose.config.ts --list
  ```
  Listed the student and staff cases; **2 tests in 1 file**.
- `git diff --check`: passed.
- The passing lifecycle test completed its `prl-demo` before/after snapshot assertion. A separate read-only Docker inventory found no remaining `prl-task6-*` containers, volumes, or networks and no `task6-*` image tags.

No push, PR, or external deployment was performed.
