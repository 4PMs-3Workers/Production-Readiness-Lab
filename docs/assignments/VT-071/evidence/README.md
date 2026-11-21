# Archived VT-071 evidence

The submitted report records measurements preserved at commit
[`a664a10`](https://github.com/4PMs-3Workers/Production-Readiness-Lab/tree/a664a1073ce6d7c8f116b66fc9498e5469917b57/docs/assignments/VT-071/evidence).
That immutable directory contains all original logs, coverage JSON, load samples,
student ledger and raw archive. Follow those links to reproduce the historical report.

New experiment outputs are written under ignored `build/vt071/`.
Use `tools/scripts/vt071_coverage.py` with pytest arguments to produce
`build/vt071/coverage.json` and its source checksum sidecar, then run
`tools/scripts/vt071_diff_coverage.py --coverage build/vt071/coverage.json`.

Release acceptance still requires zero errors and p95 at most 2.5 seconds.
