# Actual AI review after green

Input: actual diff, new service/unit files, green.txt: 25 passed in 18.70s, exit 0.
Reviewer: Codex /root/implementation_plan, read-only. No human approval claimed.

## Findings and root evaluation

C1 (high confidence, ACCEPT): owner comes from context.user_id; SQL always filters
Appointment.student_id; exact ordered IDs plus foreign canary tested against MySQL.
This supports owner scope for the executed two-student fixture. Evidence:
api/appointments.py, repositories/appointments.py, test_export_real_login_scope_contract_and_inactive_catalog.

C2 (high confidence, ACCEPT): LIMIT 1001 and >1000 rejection occur before Response;
real 1000/1001 test verifies no silent truncation at the observed boundary.

C3 (high confidence, ACCEPT WITH LIMIT): deliberate invalid SQL fails on real MySQL
while plausible mocked rows pass; sanitized 503 and next-request recovery observed.
This is an injected contract-drift experiment, not an observed preexisting schema bug.

C4 (small true concern, FIX): pure formatter fixture starts_at == ends_at violates
the real DB interval constraint. It was not used for DB evidence, but make ends_at
30 minutes later. This is a real AI-generated fixture limitation, not an invented flaw.

H1 (low confidence, INSUFFICIENT EVIDENCE): synchronous model_dump/render_csv may
become CPU bottleneck with 80 requests x 1000 rows and exceed p95 2.5s. Code shows
synchronous work, but green suite duration is not per-request performance evidence.
Do not accept this as a demonstrated defect or introduce threads/streaming on that
basis. Measure 80-task fixture; 1000-row load remains a separate unproven scenario.

H2 (gap, ACCEPT and ADD TEST): real MySQL-backed STAFF/ADMIN sessions were missing;
initial role denials used require_auth overrides. Add real-login role tests. Initial
green could not support a claim of full session-role integration.

H3 (gap, RETAIN LIMIT): parser and prefix tests do not prove actual Excel/LibreOffice
behavior. No spreadsheet-client compatibility claim without those experiments.

Scope/design review: thin route, separate service, repository query, static route
before dynamic UUID; no new migration/job/runtime dependency or fake GET guard.
Required gates still pending at review time: mutation, coverage, flaky experiments,
80-task load, deterministic OpenAPI, final group review.

## Two AI outputs that were not immediately trustworthy

1. Initial page-loop proposal: READ COMMITTED plus count/offset does not provide
   stable cross-page snapshot; rejected before implementation (plan-review.md P1).
2. Formatter unit fixture with zero-length appointment: plausible public model
   instance but impossible database interval; corrected after review (C4).
3. Initial context-only role tests: status assertions did not prove real session
   boundary; supplemented after review (H2). Mock pass is not DB contract evidence.

## Actual revision post-test review

Input: current diff, review-green.txt (41 passed/36.32s), first cold/warm review run
(560/560 status200). Same read-only coding agent, no human approval claimed.

- Supported: CSV new cases first failed7/10 then passed; TC18 independently reaches
  cardinality while TC13 retains200/original receipt; same-TC10 coverage comparison
  supports greenlet/thread configuration.
- Supported: test-only server wrappers use per-task ContextVar, re-raise errors,
  record types/integer codes, never str(error), SQL, cookie/token or response body.
- Small true concern: set-ID load assertion loses multiplicity. Fixed by requiring
  exactly100 exported rows AND expected set; rerun retains actual count in samples.
- Insufficient evidence: "warmup/instrumentation fixed historical503 root cause".
  Reject: no real failure in new review runs, so no failing exception chain or
  causal experiment. Two injected probes validate logging, not historical causality.

Direct mutations were executed after this review; final outcomes come from logs,
including the primary-role survivor, not the agent's prediction.
