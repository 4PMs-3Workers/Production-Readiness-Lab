# NFR-03 export load: implementation checkpoint

Branch: `codex/nfr03-export-pool`, based on PR55 `8dcfd1c`.

## Implemented measurement foundation

- Separate opt-in `test_appointment_export_nfr03.py`; historical harness is unchanged.
- All seven bursts, including warmup, must contain 80 unique successful samples and meet nearest-rank p95 <= 2.5 seconds.
- Fixture-derived CSV ID/order oracle rather than trusting the first response.
- Pool checkout/checkin measurements for auth/export acquisition and hold time, peak and invalidation count. Acquisition includes connect/pre-ping/scheduling and must not be reported as pure queue wait.
- Unique report directories, checksum, bounded output, source dirty flag and measured source-file hashes; partial runs cannot pass.
- Diagnostics can be disabled with `NFR03_DIAGNOSTICS=0`. No runtime setting, auth behavior or production pool has been changed.

## Preliminary measurements

Two development runs used Windows, MySQL 8.4.13 on a dedicated loopback lab port, real schema/session authentication, and ASGI transport. Both used pool 5+5 and pool timeout 0.5 seconds. They used an uncommitted diagnostic harness; these are preliminary observations, not immutable release acceptance.

| Run | Diagnostics | Errors / 560 | Successful-request p95 range |
| --- | --- | --- | --- |
| c963ae18787840a48ed1ec617b997ad7 | ON | 331 | 0.793–1.015 s |
| f3c57bb1a88340ea948fef7883a0e407 | OFF | 323 | 0.797–0.996 s |

The measured pool reached 10 checkouts, drained to zero and recorded zero invalidations. Auth hold p95 was about 0.062–0.113 s; export hold p95 about 0.102–0.236 s. Acquisition and send skew were substantial. This does not establish a connection leak or justify a particular pool increase. ASGI client/server scheduling is shared, so the next discriminating experiment needs an external HTTP client and an isolated release-like server.

Raw reports are ignored under `build/nfr03/nfr03-export-v2/8dcfd1c824963ae343e1d87db658d3fd502ad0ac/`. No raw request logs are committed.

## Reproduce the diagnostic

Run in this branch's virtual environment with a dedicated `MYSQL_TEST_URL`, never a shared or production database. The integration fixture owns its UUID schemas.

```powershell
$env:BT3_RUN_LOAD = '1'
$env:NFR03_DIAGNOSTICS = '1' # use 0 for the control
$env:PYTHONUTF8 = '1'
try {
    & .venv/Scripts/python.exe -m pytest 'tests/backend/integration/test_appointment_export_nfr03.py::test_export_nfr03[production]' -q -s --require-mysql
} finally {
    Remove-Item Env:BT3_RUN_LOAD
    Remove-Item Env:NFR03_DIAGNOSTICS
}
```

`tools/load/export_probe.py` currently contains evidence/verdict/pool-measurement helpers. An HTTP probe CLI has not yet been implemented; do not present this module as a completed gateway load runner.

## Remaining implementation and release blockers

Checkpoint validation: 103 tests passed across `tests/load` and export MySQL integration with `--require-mysql` (zero skips). Black, Ruff and targeted Pyright passed. Both opt-in load runs failed their performance gates as recorded above; those failures are not included in the functional pass count. The dedicated MySQL lab was shut down after validation; its private data and ignored reports are retained locally.

1. Validate instrumentation and generator overhead on separate-process HTTP transport; extend SQL/transaction-exit timing and pool-lifecycle regression coverage.
2. Implement gateway probe, isolated fixture profile, target attestation, multi-session controls and sanitized HTTP evidence according to the implementation plan.
3. Diagnose and choose a runtime/configuration fix from that evidence. No production fix has been selected or measured in this checkpoint.
4. Obtain the intended API/worker replica and resource inventory, effective runtime settings and MySQL connection budget before choosing deployment tuning.
5. Complete three repeat acceptance runs, sustained/mixed checks and required final-head CI.

Docker Desktop's Linux daemon was unavailable at the checkpoint. A release-like Linux/gateway lab and its non-secret topology information are needed for release certification. NFR-03 remains **FAIL/open**.


## HTTP isolation and candidate configuration follow-up

The branch now includes loopback HTTP transport with a separately owned Uvicorn process, real readiness preflight and bounded cleanup. Set `NFR03_TRANSPORT=http`; `NFR03_PROFILE` accepts `default`, `retain10`, `capacity20`, `capacity40` and `candidate40`. Server-side diagnostic wrappers are opt-in with `NFR03_SERVER_DIAGNOSTICS=1`, recorded separately from acceptance. This remains a test entry point, not a production HTTP CLI.

Preliminary experiments retained all outcomes: default HTTP 242/560 failures; retaining 10 connections 250/560; capacity20 194/560; capacity40 4/560 in an unprofiled run and 26/560 in a separately profiled run. Server instrumentation located the remaining profiled errors at auth pool acquisition. A single-query auth experiment produced 241/560 failures and was discarded; no auth code change is retained.

The candidate configuration is `tools/deploy/profiles/nfr03-candidate.env.example`: pool40, overflow0, queue0.75s, query1s and request3s. It is not automatically enabled and leaves production defaults unchanged. The queue budget remains inside the existing transaction/request deadlines; those deadlines were not increased. The bounded example requires deployment review: if one API and one worker share these settings, reserve up to 80 application connections plus explicit operational headroom. The lab server reports max_connections=151; this is not evidence of the actual production capacity or RAM/CPU budget.

Six alternating runs at clean source `25a65bdb6beb9d05ed433ad51fa262379b10e33f` used order A/B/B/A/A/B, where A=default and B=candidate40. Every report recorded source_dirty=false and generator_saturated=false.

| Run ID | Profile | Errors / 560 | Burst p95 success range, s |
| --- | --- | --- | --- |
| e868c35a3eb8447ab20e39254cffa556 | default | 242 | 0.742–0.872 |
| 835ca5664ae246019774320c3c077a51 | candidate40 | 0 | 1.061–1.288 |
| 71c5884c74db4d2cbad03d9b0269a52b | candidate40 | 0 | 1.099–1.298 |
| b333d6bd4a44418ba556edfcfbc41c70 | default | 278 | 0.740–0.927 |
| d709dd962aef44baa726facc4e02dfee | default | 238 | 0.733–0.887 |
| 0f91e7c6908a46cb9deec09f297224bb | candidate40 | 0 | 1.146–1.836 |

These are Windows native HTTP/MySQL lab results, not gateway/TLS/container acceptance. The new `NFR-03 isolated HTTP lab` workflow records a Linux default baseline, requires three candidate runs, checks contracts with zero skips and retains sanitized artifacts for 30 days. Its API runs in a native process and has no container CPU quota or worker; its MySQL service has a 1 CPU/1 GiB limit. Do not compare its numbers directly to Windows as a before/after improvement.

Current remaining release gates: actual gateway/TLS and target attestation, multi-session/sustained mixed workload, intended replica/resource/connection budgets and final candidate deployment review. The local three-run success is evidence for the candidate profile, not closure of production NFR-03. Earlier checkpoint statements above describe their dated stage and are superseded by this section where noted.


## Review remediation, 27 November 2026

The follow-up integrates current develop (quota/account schema merge included) and addresses all five findings on PR69. It does not change application runtime code, pool profiles or business deadlines.

Protocol `nfr03-export-v3` separates 10 ms lag sampling from 250 ms MySQL snapshots, observes at least 30 ms and starts the sampler before the workload barrier. Response latencies end when the body is received; observation/cleanup time is recorded separately. Missing lag evidence is incomplete, not measured saturation, and cannot pass. ASGI occupancy sampling uses the outer transport target again.

The native lab explicitly passes `--workers 1`, even under `WEB_CONCURRENCY=2`. The workflow sets that inherited value intentionally to exercise the override. Observer I/O has connect/read/write timeouts of 1 second; snapshot/metadata deadlines are 2 seconds. Watcher stop waits 1 second then cancels and drains for at most another second. Monitor/client cleanup is bounded. Completed and cancelled response records are retained in partial reports, and observer failures cannot yield PASS.

The workflow tracks dependency manifests, root/backend fixtures, integration inputs, migrations/configuration inputs and the installed offhost package. Harness regression tests run without the load opt-in and are included in formatting, lint, typecheck and contracts. Base moves to develop now that PR55 is merged.

The original seven regression cases failed before the fix for the reviewed boundaries; the expanded twelve-case suite passes after the fix. Old protocol-v2 results above are retained as historical measurements and do not certify the new sampler or the newly combined source. Final-head workflow and v3 lab results are recorded in PR69, with sanitized artifacts rather than committed raw JSON/logs. Production release limitations remain open.
