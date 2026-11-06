# Agent requirements for PR #58

Scope: `feature/issue-48-offhost-https-probe`, PR #58, Issue #48. Agents continuing
this branch must read this file and [the CI follow-up plan](pr-58-ci-followup-plan.md)
before changing code or reporting completion.

## Establish the current state

- Worktree: `D:/Production-Readiness-Lab-issue-48`; target branch: `develop`.
- Fetch origin and inspect the current remote PR head, base, local changes, and
  mergeability. Preserve other worktrees and unrelated local changes.
- Keep pushed history. Push feature commits without force; never merge the PR
  or deploy as part of this task.
- The main checkout's CodeGraph index can lag this branch. Compare the indexed
  commit and source before using it; new or modified branch files need current
  source inspection. Do not create an index automatically.

## Investigate each CI failure

1. Read failed steps and complete failure/exception context on the current SHA.
   Check both push and pull-request runs; PR jobs can execute a synthetic merge
   with a newer base.
2. Classify assertion failures, production exceptions, operation timeouts, job
   cancellation, and external download errors separately. Read the next failing
   run's logs rather than assuming it has the previous run's cause.
3. Use a focused reproduction and add regression coverage for the actual broken
   boundary. Preserve business assertions, production budgets, and zero-skip
   gates. Do not turn expected 409/503 outcomes into interchangeable results.
4. Keep changes scoped and commit each completed logical correction with its
   directly related tests. Review the complete diff before pushing.
5. Workflow/configuration files have Python contract-test consumers. Search for
   those consumers before editing and update the intended contract together.
   Run the complete backend unit suite after workflow changes; YAML parsing alone
   missed the existing 45-minute assertion when the job budget became 60 minutes.

## Failure mechanisms already encountered

| Boundary | Requirement and historical evidence |
| --- | --- |
| Sourced MySQL init script | Strict shell options must stay inside a subshell. A sourced `set -u` leaked into the official entrypoint and its later optional-variable reads prevented final-server startup. The real Bash regression must verify both parent-shell continuation and nonzero SQL failure propagation. |
| Runtime health probe | Standalone Docker fixtures must create the runtime identity and forward its password when they execute the deployed runtime-authenticated probe. Do not log secrets or place SQL passwords in process arguments. |
| Booking timeout injection | Use one target-budget value for the production-requested query timeout, scope selection, and rescheduled cancellation. The merged five-second configuration with a 0.25-second matcher produced seven deterministic failures. |
| State assertions on slow CI | A one-second query budget or 0.5-second lock budget can expire before a state conflict is reached. Give the particular business-state test explicit bounded allowances and inject a slow database step. Keep dedicated timeout tests and all no-write assertions. Include causal exception types in failures; a bare 503 alone does not prove which timeout or database error occurred. |
| Whole container job | A successful acceptance run took about 39 minutes, and a cold push runner spent 8.5 minutes on setup. The original 45-minute job cap cancelled the full suite. The job has a 60-minute total budget; operation-level deadlines and zero-skip requirements remain active. Verbose test names and slow-test timings identify future stalls. |
| Fault injection and healthy recovery | Keep the resource-exhaustion trigger and its short acquisition timeout. Give healthy recovery its own bounded query/lock/request allowance and a delayed real step regression. Use the same pool, preserve leak/side-effect assertions, and include the full safe result classification when recovery fails. |
| Notification durability | Persist every evaluator transition in order with its check state in one atomic save. Allow multiple queued events for the same check; compare a check with the last queued state instead of suppressing by check name. Retain the latest persisted acknowledgement/attempt state across cancellation. For retired checks, keep state while any later event for the check remains pending; remove it only after the final resolved event is acknowledged. Test repeated outage/recovery transitions and removed checks with mixed resolved/firing events through queue pressure and restart. |
| Notification queue backpressure | Attempt due outbox delivery before applying the probe-capacity gate. Keep at most one delivery-cycle budget per `run_once`: use one deadline across every outbox batch, and when notifications were processed before probing, persist any new transitions for the next run and mark delivery degraded instead of processing the outbox again. Reserve one outbox slot per configured check before probing, require queue capacity for all checks at config load, and defer an evaluation with explicit degraded/capacity-limited state when that reserve is unavailable. Never persist only part of a probe batch's transitions. Test that delivery recovery frees capacity and resumes probing without a second cycle. |
| Monitor detection latency | Bound fault-to-firing from an arbitrary fault time, including the wait to the first probe and one pre-probe delivery budget: `failure_threshold * max(interval, timeout + delivery_cycle_timeout) + timeout + delivery_cycle_timeout`. Test long intervals with threshold one, slow serial cycles, and pending alerts before a probe; report the SLA as suspended during explicit outbox backpressure. |

## Evidence required before completion

- Use the worktree's `.venv/Scripts/python.exe` on Windows. Do not substitute a
  different Python from PATH and mistake missing dependencies for test failures.
- Run focused regressions and required Black/Ruff/Pyright checks. Broaden tests
  when a change, failure, or unresolved risk calls for it.
- After monitor runtime/configuration changes, run the full backend unit suite,
  the off-host monitor integration suite, and monitor-specific Black/Ruff checks
  before pushing. A focused regression alone does not establish CI readiness.
- If Docker Linux is unavailable locally, report that limit and obtain real
  MySQL, rollback, recovery, and container evidence from GitHub Actions.
- Check the final pushed SHA. All applicable push and PR workflows must finish
  successfully; cancelled jobs and results from older heads do not establish
  completion. Poll at bounded intervals and investigate new failures.
- Update the PR description with results actually observed for that SHA. Keep
  unfinished CI explicitly pending; do not report a fix as verified merely
  because code was pushed or local unit tests passed.
- Report remaining dependency warnings and environment-level validation limits
  accurately. Keep reviewer approval separate from CI success.
