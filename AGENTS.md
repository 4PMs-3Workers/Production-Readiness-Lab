# Agent instructions for PR #66 (VT-069)

- Work on `feature/bt3-23001890-vt069`, targeting `develop`; baseline is `12a74f7308550ccc294a783edde79db267a526d1`. Preserve other worktrees and unrelated changes. Do not merge, deploy, or force-push.
- User communication and assignment report are Vietnamese. Commit and GitHub text are English. Label AI review and observed/unverified evidence honestly.
- Before locating code, use CodeGraph if `.codegraph/` exists. Do not create an index.
- For rollout identities, compare exact Git blob bytes (`git show <sha>:<path>`), never Windows CRLF working-tree hashes. Rehearse from the actual PR base. Only Compose and grants currently differ in the reviewed transition; do not broaden the allowlist to make tests pass.
- Validate stored rollout proof against the cached manifest of the release it describes. Keep historical reviewed schema pins readable; new records must use the selected release pin. Render predecessor Compose with its own accepted schema pin.
- Before migration accept the exact predecessor catalog. Same-target resume after migration may accept the complete candidate catalog; reject partial catalogs and changed immutable targets. A refused automatic fallback before service changes must permit candidate resume.
- A healthy host's read-only rollback refusal must not create recovery pending. An already interrupted host must retain its recovery requirement and original recovery target. Test both paths and a subsequent compatible deployment.
- Booking quota is business configuration; default 3 belongs only to migration/seed. Missing/invalid/unreadable runtime configuration must surface 503, never silently default. Preserve existing appointments when lowering quota. A fourth-booking test needs a distinct available slot, and must verify persisted state and side effects.
- Frontend reads, drafts, PUT payloads and recovery comparison must preserve nondefault quota. Avoid hard-coded limit text; show quota differences to admins.
- Synthetic load accounting records exactly one duration per request, including decode errors/timeouts. Report all failures and distinguish harness measurements from deployed performance.
- Use Node 24.x and the pinned npm version, plus the isolated Python environment. Inspect actual `node --version` before installing. Audit all lock roots; never renew expired exceptions without evidence. Prefer patched dependencies and remove obsolete exceptions once audit is clear.
- Before push: run Black, Ruff and Pyright; run the full related rollout unit family (not only new tests), changed frontend tests/typecheck/lint, and collect/run affected MySQL integration tests where available. Check staged diff. After push, inspect every configured workflow on the latest head, including push duplicates; a prior green SHA is not evidence for new code.
- For CI failures download actual job logs/artifacts; record the failed command/assertion and cause before editing. Never hide failures with skip, weakened assertions, global timeout increases, or advisory exceptions.
- Preserve historical RED/GREEN/mutation/load logs and PDF snapshots. Add dated supplemental evidence for follow-ups. Credentials and raw private logs remain outside tracked files.

- When changing a rehearsal predecessor, re-derive behavior assertions from that exact source. The actual PR base already supports configuration-aware availability; an older predecessor's 503 response is not its contract. Keep schema-manifest rollback compatibility checks separate from an in-process availability probe.

- GitHub checkout may be shallow. Capture reviewed baseline bytes as small hash-checked fixtures; never assume historical commits exist inside a test runner. Deadline tests should measure the ASGI operation on one event loop while preserving timeout, response and cleanup assertions.

- Rollout recovery retries must preserve the fact that a deployment already entered migration across a transient resume preflight failure. Keep a durable migration marker when starting a retry, and only accept the candidate schema when the complete candidate catalog and revision both validate. Add a sequence regression: migration succeeds, a later stage fails, first resume is refused at image/preflight, then a second resume succeeds after the transient fault clears.
- During recovery, explicit rollback must complete its read-only rollback preflight before replacing the pending operation. If preflight refuses, retain the original deploy command, target, event history, and recovery requirement so the candidate can still be resumed. Cover refused rollback followed by same-reference candidate resume, and successful rollback recovery after a passing preflight.
- For retry/recovery tests, assert persisted state and subsequent business operation, not only the refusal reason or HTTP/CLI status. A passing single retry does not prove that recovery metadata survives a second attempt.

- Resume regression tests must construct a fresh runtime with no selected overlay, not just reload the controller. Select the candidate before any schema probe, including Python argument evaluation.
- Complete read-only database identity checks before replacing a pending deploy with rollback; carry that checked identity into restore and assert refused rollback followed by candidate resume.
- Preserve the strict v1 wire encoding when migration has not started. Test persisted JSON; adding a default field is not backward reader compatibility. Post-migration marker states require the updated controller.
- Malformed JSON tests must keep the expected HTTP status so the decoder is reached. Hash identity inputs must have enforced LF; never normalize production hash comparisons or expand accepted hashes to bypass checkout differences.
