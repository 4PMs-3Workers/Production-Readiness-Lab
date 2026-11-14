# Bổ sung kiểm chứng CI ngày 14/11/2026

## Phạm vi và provenance

PR #66: https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/66.
Head khảo sát: `a70f599a6ca2528391baa130c6888728fa63cd6a`; base develop
`12a74f7308550ccc294a783edde79db267a526d1`. Không có CodeGraph index.
Worktree: `D:\Production-Readiness-Lab-bt3-23001890-vt069`.
Không thay đổi workflows, triggers, assertions nghiệp vụ, timeout, coverage gate hoặc exceptions.
PDF A4 portrait và evidence RED/GREEN trước đây được giữ nguyên như snapshot lịch sử.

## Bảng nguyên nhân

| Check/job | Lỗi trên head khảo sát | Nguyên nhân | Bản sửa | Kiểm chứng |
| --- | --- | --- | --- | --- |
| Frontend / Frontend real API checks | 14 failures, 144 passes, 158 attempts; tất cả failure thuộc admin-booking-configuration, cả desktop/mobile | PR thêm field bắt buộc `max_active_appointments` ở backend, nhưng frontend type, parser, copyPolicy và draft bỏ field khi full-resource PUT; hai payload E2E dựng tay cũng thiếu field | Giữ quota nhận từ provider xuyên draft/PUT/receipt comparison; reject quota thiếu/sai, không default; bổ sung field cho fixture và payload E2E | Regression RED 11 failed/14 passed; sau patch 39 focused tests pass; full frontend 500 pass với 2 workers; CI Linux sẽ kiểm chứng MySQL/browser |
| Dependency security / dependency-advisories | unhandled CVE-2026-126360 / GHSA-mf5v-w3w3-f6cg, opentelemetry-api 1.45.1 | Dependency pin kế thừa nguyên từ develop; advisory được ghi nhận 13/11 sau snapshot CI trước | Nâng đồng bộ OpenTelemetry API/SDK/exporters 1.45.2 và semantic/transport packages 0.66b2, cả pyproject và requirements; không thêm exception | Fresh npm audit + pip-audit toàn bộ runtime/offhost/dev requirements: policy approved, 0 findings; dependency graph compatible; 57 observability tests pass |

Nguồn remediation chính thức: https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-mf5v-w3w3-f6cg.
SDK 1.45.2 yêu cầu API 1.45.2 và semantic conventions 0.66b2 theo PyPI metadata;
resolver đã cài thành công các pin đồng bộ.

## Logs CI và phân loại checks

- Frontend: https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/43557702011;
  job 132334187613 thất bại ở Real MySQL desktop and mobile E2E. Setup/build/probes pass;
  mock frontend job, build, typecheck và lint pass. Sanitized summary artifact 14099955514
  có 158 attempts, 14 failed, 0 globalErrors; không lưu raw browser/customer data vào evidence.
- Security: https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/43557702032;
  job 132334187204: collector chạy thành công, policy chặn đúng advisory nói trên.
- Backend: run 43557702018, job 132334187074: **2512 passed, 1 warning, 1033.92 s**;
  CI yêu cầu MySQL và zero skips, rồi query-plan check pass 1 test. Đây là evidence runtime
  MySQL trên head khảo sát, bổ sung cho giới hạn local trong PDF cũ.
- Containerized demo, serialized rollback, MySQL recovery, encrypted recovery và immutable
  application release đều success trên head khảo sát; không có cancelled/timed-out/pending.
- Tám workflows PR dự kiến chạy: Backend models, Frontend (mock và real API), Dependency
  security, Containerized demo, Serialized release rollback, MySQL recovery, Encrypted
  off-host recovery và Immutable application release (source/candidate).
- Các reusable backend/frontend/security/container/recovery/rollback jobs bên trong
  `release.yml` chỉ chạy workflow_dispatch; publish còn đòi develop và protected environment.
  Chúng được thiết kế skip trên PR; các standalone workflows vẫn chạy độc lập. Không dispatch
  publish/deploy. Danh sách required contexts trong branch protection chưa được xác minh;
  yêu cầu hoàn tất ở đây là toàn bộ tám workflow PR dự kiến thành công trên head mới.

## Kiểm chứng local sau patch

Windows, Node 24.22.1, npm 12.2.0 tải trong tmp (repo chưa có node_modules), Python 3.11.4.
Docker Desktop Linux engine không có pipe; không có MySQL local, nên E2E thật dùng CI Linux.
Dependencies Python chỉ cài trong .venv; audit tool dùng môi trường riêng của uv.

Lệnh và kết quả (từ worktree trên):

- `node node_modules/vitest/vitest.mjs run --config src/frontend/vitest.config.ts tests/frontend/unit/admin-configuration-api.test.ts`: RED 11 failed, 14 passed; quota 5 bị mất thành undefined và input thiếu/sai được chấp nhận trước patch.
- Thêm component module vào lệnh sau patch: 39 passed, 3.72 s.
- Full frontend chạy mặc định trên Windows: 498 pass, 2 failure ở admin-session-generation và booking-submission do deadline khi tải nhiều jsdom workers; đây là quan sát local, không gán là CI regression.
- Chạy lại full frontend với `--maxWorkers=2`, giữ nguyên timeout/isolation/assertions: **500 passed, 44 files**, 66.43 s. Không đổi config CI để che failure local.
- `node node_modules/next/dist/bin/next typegen src/frontend`; `node node_modules/typescript/bin/tsc --noEmit -p src/frontend/tsconfig.json`: exit 0.
- `node tools/scripts/lint-frontend.mjs`: exit 0. Prettier trên toàn bộ file TS đã sửa: pass.
- `.venv/Scripts/python.exe -m pytest tests/backend/unit/test_dependency_advisories.py tests/backend/unit/test_dependency_pins.py tests/backend/unit/test_worker_telemetry_ownership.py tests/backend/unit/test_booking_service_retries.py tests/backend/unit/test_booking_quota_contract.py -q`: **68 passed**, 5.55 s.
- `.venv/Scripts/python.exe -m pytest tests/backend/unit/observability -q`: **57 passed**, 12.26 s.
- `uv pip check --python .venv/Scripts/python.exe`: 72 packages, all compatible.
- `npm audit --package-lock-only --json` và `pip-audit -r requirements.txt -r requirements-offhost.txt -r requirements-dev.txt --no-deps --disable-pip --format=json`, qua đúng `check_dependency_advisories.py`: **approved (0 findings)**, exit 0. Advisory exceptions cũ không đổi, không có finding được miễn mới.

## Bảo toàn bài VT-069

Backend quota/transaction, DB side-effect contract tests, mutation patch inert, load harness
60 tasks và coverage/flaky commands không thay đổi. Test failure-path
`test_config_read_exception_is_not_swallowed_into_default_policy` nằm trong 68 tests pass;
không có default fallback được thêm vào implementation. Không chạy lại benchmark/mutation
vì hai sửa đổi không đổi quota transaction hay harness; kết quả lịch sử giữ nguyên provenance.
Frontend regression dùng quota khác default (5), assert payload giá trị thật và comparison
quota khác nhau; bổ sung negative tests cho undefined/0/32768/string/bool/null.

## Theo dõi head mới

### Finding bổ sung trên c35d8d3: budget production lẫn vào test correctness

- Frontend PR run 43606347940 đã pass: **158 E2E attempts, 0 globalErrors**; security cũng pass cả PR/push.
- Backend PR run 43606347923: 1 failed, 2511 passed, 790.22 s tại `test_lock_sql_orders_settings_sorted_users_appointment_proposal_then_clock`, trả ServiceUnavailable. Push run 43606343645: 2 failed, 2510 passed, 887.47 s tại competing proposal creation replay và terminal shared expiry. Các test/service này kế thừa nguyên từ base, không phải patch quota/frontend.
- Fixture `auth_settings` dùng query budget 1 s và lock budget 0.5 s. `ordered_race` chờ observer đọc `performance_schema.data_lock_waits` tối đa 3 s trước khi nhả holder; đây là correctness barrier thật, nhưng budget production có thể hết trước khi barrier hoàn tất. Test thứ tự SQL cũng thực hiện toàn bộ transaction dưới query budget 1 s dù không có assertion latency.
- Service suppress context trong output bằng `raise ServiceUnavailable() from None`; log CI không chứng minh được chính xác underlying exception thuộc timeout hay driver. Hypothesis là latency làm budget hết; controlled local probe dùng đúng cancellation service với attempt trì hoãn 1.05 s tái hiện `ServiceUnavailable.__context__ == TimeoutError` ở budget production và hoàn tất với fixture policy có sẵn. Probe là synthetic, không được gọi là tái hiện MySQL CI.
- Sửa ở test scope: chuyển fixture `policy_settings` có sẵn (query 10 s, lock 5 s, request 20 s) lên integration conftest và chỉ dùng thêm cho ba correctness tests này. Không đổi production settings, workflow timeout hay assertion thứ tự SQL/replay/state/side effects. Test SQL-order thêm case delay 1.05 s trước settings lock và vẫn yêu cầu đúng năm locks, đúng sorted users, clock sau locks, chỉ một lần attempt. Đây là tách đúng contract correctness khỏi latency budget, không dùng retry/sleep để làm run pass.
- Các test timeout có kiểm soát vẫn giữ nguyên budget 0.02 s (cancellation) và 0.25 s (proposal), cùng assertions abort/no writes/release locks. Các test khác không tự động nhận fixture mới.
- Local: controlled probe pass sau khi bổ sung cấu hình schema pin bắt buộc (lần đầu probe lỗi setup, không tính RED); Black/Ruff 3 file pass; 60 integration cases collect thành công (chưa execute MySQL local); 30 unit tests cancellation/quota/retry pass. CI mới sẽ quyết định hiệu lực patch trên DB thật; không dùng kết quả c35d8d3 để kết luận head tiếp theo.

Hai nhóm code được commit riêng; source và lệnh kiểm chứng được ghi trước push.
Kết quả cuối cùng phải lấy theo head mới nhất trong PR, không suy từ các run cũ.
Các liên kết run/check và trạng thái cuối được cập nhật trong mô tả PR và báo cáo trả về;
tài liệu này giữ lại provenance của investigation và local verification trước lần chạy đó.
