# Final unit coverage and synthetic load evidence

Environment: worktree `D:\Production-Readiness-Lab-bt3-23001890-vt069`, Python 3.11.4, pytest 9.0.3, coverage.py 7.17.3. Source includes implementation commit `bb34804de3dbc092233174e9c17639f3823e25c4` and release fix `fb24a3b`.

## Coverage

Command: `python -m coverage run --branch -m pytest tests/backend/unit/test_booking_quota_contract.py tests/backend/unit/test_booking_service_retries.py -q`, followed by `python -m coverage report -m --include='src/backend/app/domain/booking.py,src/backend/app/services/booking.py,src/backend/app/services/admin_booking_configuration.py'`.

Observed: 29 passed in 2.53s; exit 0. Line/branch report: domain policy 92% (1/19 statements missed, 1 partial branch); booking service 60% (93/248 statements missed, 19 partial branches); admin configuration service 38% (20/36 statements missed). Combined: 60% line coverage (114/303 statements missed, 20 partial branches). MySQL-bound admin integration behavior is not covered by this local unit coverage; percentage is not a quality conclusion.

## Performance

Command: `python tools/scripts/vt069_booking_quota_load.py`. Workload: five warm-up requests, then 60 barrier-synchronized in-process ASGI tasks; observed maximum concurrency is instrumented. Duration uses a monotonic clock; p95 uses nearest-rank over all 60 measured requests. No error/timeout samples are dropped.

Observed JSON: 60/60 business-correct responses, 30 creates and 30 quota rejections; 0 other failures, 0 timeouts; measured duration 0.100296s; p95 0.088716s; throughput 598.23 requests/s; concurrency requested/observed 60/60. This synthetic module result is below the 2.0s threshold, but does not exercise MySQL, deployed API/gateway or production resources and cannot establish production SLO.
