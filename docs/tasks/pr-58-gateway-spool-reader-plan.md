# PR #58 Gateway Spool Reader CI Fix

**Goal:** Preserve the collector rotation acceptance assertions while allowing its bounded file poll to observe files being renamed or removed by the live rotator.

**Evidence:** Pull-request Containerized demo run `42025707336` on head `961e056` completed with 177 passed and one failure in `test_collector_late_start_checkpoint_restart_and_rotation[https]`. Its file reader returned shell status 1 before the polling deadline. The rotation helper removes older files and renames the active file before Nginx reopens it, so a previously expanded glob can contain missing paths.

**Design:** Treat absent files as a transient empty sample. If `cat` fails and its path still exists, fail the read. Keep `check=True` so Docker and shell errors still propagate. Leave the rotator, polling deadline, and collector delivery/deduplication/checkpoint assertions unchanged.

## Commit Plan

One coherent commit: `test(gateway): tolerate rotated files disappearing during reads`.

**Files:** `tests/containers/test_gateway_observability.py`, `tests/backend/unit/test_gateway_spool_reader.py`, and this plan.

1. Reproduce removal after glob expansion and before `cat` by running the actual reader shell command against an owned temporary directory.
2. Verify empty-spool polling eventually returns the requested record and that read failures for existing files remain fatal.
3. Replace the shell loop's implicit exit status with explicit missing-file handling.
4. Run the regressions, backend unit suite, Black, Ruff, Pyright, and diff checks; request a read-only review.
5. Commit and push; verify both push and pull-request workflows and record final CI evidence in the PR.

## Local Verification

- The old reader failed all three missing-file regressions; its two parsing/error-control cases passed.
- The fixed reader passed all five regressions using a real POSIX shell.
- Backend unit suite: 1,058 passed.
- Black passed for the new test and changed reader lines; Ruff, Pyright, and diff checks passed.
- Read-only review found the shell fix sound; its documentation placement finding was resolved by keeping this plan separate from the monitor plan.
- Docker acceptance is verified in CI because the local Linux Docker engine is unavailable.
