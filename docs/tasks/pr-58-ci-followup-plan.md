# PR #58 remaining CI failures

Continuation requirements: [agent guidance for this branch](pr-58-agent-guidance.md).

Observed on head `c0c447f` in both push and pull-request workflows.

1. **MySQL bootstrap initializer:** the sourced runtime-user script enables
   `nounset` in the official entrypoint shell. The entrypoint subsequently reads
   optional unset variables before starting the final server. Extract the script
   writer, execute the actual generated script in a Bash regression, and isolate
   its strict options in a subshell while retaining SQL failure propagation.
   Commit: `test(deploy): isolate sourced MySQL initializer options`.
2. **Booking query timeout coverage:** the merged fixture supplies a five-second
   production query budget but selects only scopes configured for 0.25 seconds.
   Use one target-budget value for configuration, scope selection, and deadline
   injection; retain separate setup allowances and transaction/lock assertions.
   Commit: `test(booking): align injected timeout with configured query budget`.
3. **Staff outcome state coverage:** a contended push runner returned a service
   timeout before the expected state conflict. Scope larger query/lock budgets to
   that business-state test and exercise both cancelled and pending states with
   a 1.25-second lock step. Keep the 409 and no-write assertions, and include the
   causal exception type if they fail. Commit:
   `test(staff): isolate outcome state assertions from query timing`.
4. **Cold container job budget:** the successful PR run took 39 minutes for
   acceptance, while push setup consumed 8.5 minutes before the same suite. The
   45-minute job cap cancelled push before completion. Allow 60 minutes for the
   whole job, retain operation-level test deadlines, and report test names and
   slowest durations. Commit:
   `ci(containers): budget cold acceptance runs and report timings`.
   Keep the workflow's Python contract test aligned with the 60-minute policy;
   run the complete unit suite after configuration changes.
5. **Worker pool recovery:** the exhausted-pool phase passes, but healthy
   recovery can exceed the default one-second query budget. Keep the 50 ms pool
   failure and one-second failure watchdog, and give only recovery explicit
   request/query/lock allowances. Exercise delayed real schema validation and
   retain zero checked-out connections and exact side-effect counts. Commit:
   `test(worker): isolate pool recovery from fault-injection timing`.

Verify focused regressions and Black/Ruff/Pyright locally. Run real MySQL and
Linux rollout acceptance when Docker is available; otherwise require both push
and pull-request CI to verify those paths before reporting completion. Preserve
all existing monitor fixes and pushed history. Do not merge or deploy.
