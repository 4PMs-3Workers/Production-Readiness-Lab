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
