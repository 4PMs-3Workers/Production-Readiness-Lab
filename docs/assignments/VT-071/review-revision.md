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
- Archived sample audit: 503 latencies span 1.418-1.947s (initial 1.418-1.619s; diagnostic 1.511-1.947s). Replace wording "fast errors" with the precise
  statement that a latency percentile mixing success and failure cannot establish
  successful capacity. Cold-start/resource hypotheses still require diagnostics.

## Execution attribution

The student explicitly said they have not personally run commands, and requested
Codex execution. Record this as delegated AI-assisted execution, not manual student
execution. Responsibility for reviewing/owning the submitted work can coexist with
delegation; the first-page commitment is retained exactly. At that earlier stage reflection remained a draft; the02/11 student self-check
now supplies genuine execution observations for an updated AI-assisted reflection.

## Observed revision results

- CSV red7fail/10pass -> green41pass. Restored coverage41pass; regression1037pass.
- Same-TC10 greenlet hypothesis confirmed; full export coverage34/34 and8/8;
  diff executable lines50/50, arcs10/10. No manual metric editing.
- New source+unique mutant reaches CSV count2 !=1. Owner/overflow/both-role guards
  killed; primary-role-only survives because query guard still enforces STUDENT.
- Review load before restart confirmation:560/560 success; p95cold1.516s,warm1.316..1.527s. Historical
  58/400 and246/400 errors remain; no failing chain reproduced, no causal fix claim.
- Actual post-test reviewer found load set-ID oracle lost multiplicity; fixed exact
  row count and reran. Two injected probes show diagnostic messages/SQL are omitted.
- First mutant-runner attempt had an AST-selector/classifier issue, restored itself,
  and is archived as attempt1. Corrected run supplies final outcomes and public SHAs.

## Public experiment refs (never merge)

- Original: https://github.com/4PMs-3Workers/Production-Readiness-Lab/tree/codex/bt3-23001952-vt071-mutation
- New: https://github.com/4PMs-3Workers/Production-Readiness-Lab/tree/codex/bt3-23001952-vt071-mutants-verified
- Exact immutable links/results: evidence/review-mutants.json and execution-notes.md.

## Subsequent same-harness startup confirmation

A fresh MySQL process (preflight uptime12s) plus unchanged review harness produced
276/560503, including warm failures. Export-stage chains contain ServiceUnavailable,
TimeoutError,CancelledError; no DB integer code. The1s read deadline expired; deeper
CPU/connection/query causality is not isolated. New logs/JSON are separate and the
previous report restored byte-for-byte. No code/harness/deadline changed.

## Deployment pool and finer timing

Follow-up actually measured factory defaults5+5/0.5s, not just proposed it. Confirmed
295/560 then302/560 auth pool queue timeouts; initial340/560auth errors lacked exact
leaf-type instrumentation. NullPool comparisons0/560,83/560,0/560, with83 cancellations
located at rollback and Aborted_clients+83. These are two distinct failure paths.
Final process snapshots exclude observer and show no persistent active owned query;
nominal250ms sampling cannot rule out transient orphans. README and pool summary
record order, all attempts, metrics and limits; performance remains open FAIL.

## Student execution now provided

The02/11student self-check resolves the lack of any personal command execution:
41functional/OpenAPI pass, TC18 pass, coverage50/50 and four killed/one survived
mutants, plus312/560auth pool timeouts. Earlier Codex302/560 remains separate and
NFR-03 remainsFAIL. Full code-review/group approval is not inferred. New raw data
is compressed with manifest rather than expanding another large per-request diff.
See evidence/student-20261102/README.md for source and attribution.
