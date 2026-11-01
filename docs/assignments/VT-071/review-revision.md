# Revision prompted by external read-only review (2026-11-01)

Starting submission commit: 4cfd6c6feabe4133bae8c04128feb1d54a227fd0.
Original baseline and red/green evidence remain unchanged and preserved.

## Plan before new implementation

1. Test coverage hypothesis on identical TC10 with default and greenlet/thread modes.
   Observed: default misses overflow line 56; greenlet/thread records it. Add scoped
   coverage configuration and compute changed executable-line coverage from Git diff.
2. Strengthen CSV tests with bare tab/CR/LF and full-width formula prefixes; run red,
   then a small formatter patch. Existing tab/CR+formula tests already existed.
3. Add a second source-cardinality oracle that deliberately does not stop at the
   replay status assertion, while the existing TC13 still requires exact receipt 200.
4. On experimental branches only, execute owner-filter, role dependency, overflow,
   and source-replay-plus-test-schema-unique mutations; restore final source. Publish
   mutant commits separately for reviewers; never merge them into submission.
5. Instrument server-side auth/export calls in the opt-in load harness only. Record
   exception class chain and integer DB code, never messages, SQL, cookies or tokens.
   Capture cold burst, a full 80-task warmup burst, then five warm bursts separately.
   Default query/request deadlines remain 1/3 seconds. Keep prior failed runs.
6. Re-run functional/static/regression checks, update traceability, risk legend,
   plan prompt quotation and report using PDF skill + full render verification.

## Review points already checked

- The original 9 red-phase passes are 3 role harness cases, 5 invalid query cases,
  and the real missing-session case; dynamic detail routing shares auth and UUID
  rejection. They are not evidence that export existed. Direct export mutations
  will assess the same oracles after the route exists.
- sessions.find_with_user performs SELECT only; UPDATE occurs in revoke methods.
  No per-read session update was found. Do not invent a shared-session write lock.
- The original mutation stopped at 503 != 200 before CSV cardinality. Keep that
  limited conclusion; add separate evidence rather than rewriting old results.
- Earlier 503s took roughly 1.7-2.0s. Replace wording "fast errors" with the precise
  statement that a latency percentile mixing success and failure cannot establish
  successful capacity. Cold-start/resource hypotheses still require diagnostics.

## Execution attribution

The student explicitly said they have not personally run commands, and requested
Codex execution. Record this as delegated AI-assisted execution, not manual student
execution. Responsibility for reviewing/owning the submitted work can coexist with
delegation; the first-page commitment is retained exactly. Reflection remains a draft.
