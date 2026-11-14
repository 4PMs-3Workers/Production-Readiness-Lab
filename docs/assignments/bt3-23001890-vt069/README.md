# BT3 / VT-069 - Cách chạy

## Môi trường

- Python 3.11+; phiên bản dùng khi kiểm tra: Python 3.11.4.
- Dependencies từ gốc repo: `python -m pip install -r requirements-dev.txt`, `python -m pip install -e .`, `python -m pip install -e tools/infra/offhost`.
- PDF renderer trong môi trường cô lập cần `reportlab` và `pypdf`; ví dụ: `uv pip install --python .venv/Scripts/python.exe reportlab pypdf`, sau đó `python docs/assignments/bt3-23001890-vt069/render_report.py`.
- MySQL integration cần MySQL 8.4, một tài khoản test được tạo/xóa schema riêng và `MYSQL_TEST_URL` không chứa database mặc định. Không dùng database demo.
- Migration tạo `service_settings.max_active_appointments NOT NULL` mặc định 3. Demo seed cũng giữ 3. Admin có thể đổi qua `PUT /api/v1/admin/booking-configuration` với session `ADMIN`, same-origin/CSRF và `expected_updated_at` lấy từ `GET`.

## Commands

Các lệnh dưới đây chạy từ repository root. Trên PowerShell:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = 'src/backend'
python -m pytest tests/backend/unit/test_booking_quota_contract.py tests/backend/unit/test_booking_service_retries.py -q
python -m pytest tests/backend/integration/test_booking_quota_configuration.py tests/backend/integration/test_admin_booking_configuration.py -q --require-mysql
python -m pytest tests/backend/integration/test_booking_quota_configuration.py -q --require-mysql
python -m ruff check src/backend tests/backend tools/scripts tools/deploy
python -m black --check src/backend tests/backend tools/scripts tools/deploy
python -m pyright
python tools/scripts/export_openapi.py --output build/openapi-vt069.yaml
python tools/scripts/vt069_booking_quota_load.py
```

- Unit: policy JSON integer/range and quota decision boundary.
- Contract/API integration: admin API provider → persisted versioned config → booking API consumer; assert `409`/`201`, code/state, appointment count, audit and notification effects. MySQL failure injection asserts `503` and zero writes.
- E2E: no separate browser flow is needed for this vertical slice; the business contract terminates at admin and booking HTTP endpoints. Frontend E2E does not expose the admin policy editor.
- Coverage (install `coverage` in the isolated environment if needed): `python -m coverage run --branch -m pytest tests/backend/unit/test_booking_quota_contract.py tests/backend/unit/test_booking_service_retries.py -q`, then `python -m coverage report -m --include='src/backend/app/domain/booking.py,src/backend/app/services/booking.py,src/backend/app/services/admin_booking_configuration.py'`.
- Performance command measures 5 warm-ups plus 60 barrier-synchronized in-process ASGI requests, true observed handler concurrency and nearest-rank p95 over every sample. It exercises only the quota decision helper; it is not the deployed API, MySQL, Nginx gateway, or production SLO.
- Rollout CI found that the immutable predecessor backend returns 503 after the additive schema migration. This branch refuses rollback to that predecessor before changing running services or the database. The Linux regression passed on PR #66 (`8e5d686`, 4 passed); this verifies safe refusal, not successful rollback across the migration.
- Mutation is an inert patch under `evidence/mutation/`; to replay safely, create a disposable worktree at the implementation/test commit, apply the patch to `src/backend/app/services/booking.py`, then run only `test_config_read_exception_is_not_swallowed_into_default_policy` from `tests/backend/unit/test_booking_service_retries.py`. This deterministic test does not need MySQL. Delete only the disposable worktree after confirming its resolved path. Never apply or push the mutation on the submission branch.

## Evidence

Bổ sung CI 14/11/2026: xem [`ci-remediation-20261114.md`](evidence/ci-remediation-20261114.md) cho nguyên nhân frontend bỏ field quota, bản vá OpenTelemetry và kiểm chứng mới. PDF giữ snapshot evidence lịch sử; trạng thái CI mới nhất nằm ở PR #66.

See [`evidence/`](evidence/), the final report source [`report.md`](report.md), and generated [`BT3_23001890_VT-069.pdf`](../../../output/pdf/BT3_23001890_VT-069.pdf). Integration results that skip without MySQL are explicitly labeled unverified. Load output is a synthetic module benchmark only.
