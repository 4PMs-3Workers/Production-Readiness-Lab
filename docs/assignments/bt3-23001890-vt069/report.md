# Bài tập cá nhân 3 — Production Readiness Lab

## Trang thông tin

**Ngô Quang Hưng — 23001890** · **VT-069** · **SE2026-T18 — Thành viên**  
**APP-03 – Production Readiness Lab** · Repository: [Production-Readiness-Lab](https://github.com/4PMs-3Workers/Production-Readiness-Lab)  
Baseline: `12a74f7308550ccc294a783edde79db267a526d1` (`origin/develop`)  
Branch: [feature/bt3-23001890-vt069](https://github.com/4PMs-3Workers/Production-Readiness-Lab/tree/feature/bt3-23001890-vt069)  
Draft PR: [#66 - Individual Assignment 3: configurable booking quota (VT-069)](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/66)

> “Tôi chịu trách nhiệm về toàn bộ code, test và nội dung bài làm; tôi đã khai báo việc sử dụng AI.”

## 1. Specification, acceptance criteria và contract

**Vấn đề.** Hạn mức ba lịch đang chiếm chỗ nằm trong booking service, nên thay chính sách đòi sửa/phát hành mã. Đưa hạn mức vào singleton `service_settings` đã được admin quản lý. Phạm vi implementation là field, migration, validation, API round-trip, enforcement, kiểm thử và evidence. Không thêm UI admin, cache/hot reload, tự hủy lịch cũ hay thay đổi quota của change proposal đang xử lý.

**Acceptance criteria.** AC-1: admin GET/PUT trao đổi `max_active_appointments` là JSON integer 1–32767; bản ghi cũ mặc định 3. AC-2: limit=2 và hai lịch đang chiếm chỗ thì lịch mới trả `409 BOOKING_QUOTA_EXCEEDED` và không ghi side effect; tăng limit=3 cho phép tạo, ghi appointment + audit + hai notification. AC-3: chỉ `UPCOMING` và `CHANGE_PENDING` được đếm trên mọi ngày; giảm limit không sửa/hủy lịch hiện có. AC-4: thiếu config hoặc lỗi đọc DB cho ra `503`, không trả default thành công/không ghi bundle. AC-5: kiểm thử provider/consumer bao gồm dữ liệu, outcome và state, cùng kiểm tra validation/error behavior.

**Contract và invariant.** Provider là admin booking-configuration API và dòng DB `service_settings(id=1)`; consumer là transaction `create_appointment`. Request schema, admin service và DB chịu trách nhiệm lần lượt validate wire input, version/lock/audit và miền giá trị lưu trữ. Quota tính trên số lịch/student, không phải theo ngày. Config mới có hiệu lực sau commit và được transaction đặt sau đọc cùng lock hiện có; không thêm query hoặc cache. Default 3 chỉ để migrate/seed. Thiếu dòng, giá trị sai hoặc lỗi DB là lỗi dịch vụ; default trong runtime sẽ che outage và vi phạm hạn mức. Từ chối trước commit không được tạo appointment, audit, notification hoặc receipt. Chi tiết provider/consumer, units, failure behavior và mapping requirement nằm trong [`spec.md`](spec.md).

**Đối chiếu repo.** Trước khi code, hợp đồng được đối chiếu với `api/admin_booking_configuration.py` (endpoint), `services/admin_booking_configuration.py` (`POLICY_FIELDS`, version/audit/lock), `models/service_setting.py`, `services/booking.py` (`_run_attempt_with_commit_safety`), repository `count_blocking_for_student`, và `docs/tasks/appointment-booking.md` (atomicity). Các nguồn mô tả count/status, lock order và side effects; đặc tả giữ semantics có sẵn. Không tìm thấy Lecture 06/07 đầy đủ; báo cáo dựa trên yêu cầu bài và repo, không trích nội dung lecture.

## 2. Initial AI plan và review bằng evidence

Plan ban đầu được giữ nguyên trong [`initial-plan.md`](initial-plan.md) và commit trước implementation. Ba giả định ban đầu phải mở rộng sau khi khảo sát: (1) plan chưa nêu runtime grant/demo fixtures dù grant SQL giới hạn từng cột; (2) plan nói assert outcome nhưng chưa yêu cầu kiểm tra mọi DB side effect dù service ghi appointment/audit/two notifications trong bundle; (3) migration đơn lẻ chưa đủ vì readiness, worker, demo seed và release dùng exact schema revision. Những quyết định thay thế và file evidence được ghi tại cuối plan.

Điểm sửa thứ tư phát hiện trong review sau implementation: `test_reviewed_predecessor_requires_opt_in_and_exact_configuration` fail vì transition release vẫn pin hash `grants.sql` cũ sau khi thêm quyền cột quota. Test RED xác định lỗi ở exact hash; cập nhật riêng current reviewed hash, giữ predecessor hash cũ. [`evidence/release-manifest-fix.md`](evidence/release-manifest-fix.md) ghi reproduction, hash, GREEN. Nhóm manifest/schema/preflight: 75 passed.

Điểm thứ năm đến từ CI PR #66: job rollback fail khi fixture khởi động host predecessor. Test mới chứng minh `Artifacts.manifest(root=base)` nhầm dùng schema pin hiện tại thay vì pin của source tree được đóng gói. Fixture nay đọc pin theo `root`; regression group liên quan đạt 78 passed. Bằng chứng CI và giới hạn chờ remote rerun ở [`evidence/ci-rollout-fixture.md`](evidence/ci-rollout-fixture.md).

## 3. Risk-based strategy

Unit kiểm tra miền số, predicate status/quota, lỗi DB được inject và không bị nuốt; nhanh, bao phủ decision/failure path. API/integration MySQL kiểm tra producer→persistence→consumer, response cùng appointment/audit/notification state; đây là mức bằng chứng quyết định cho contract. E2E browser riêng không thêm confidence cho vertical slice hiện không có UI admin; API integration qua hai endpoint là consumer path phù hợp. Do không có MySQL, nhóm này **chưa kiểm chứng**; không thay bằng mock hai phía. Không chạy database migration/deploy stack thật vì máy không có MySQL/Docker engine. OpenAPI, migration revision, seed, grants và exact rollout pins có regression checks tĩnh/unit.

Test data dùng các lớp tương đương: limit hợp lệ (1, 2, 3, 32767), sát biên dưới/trên (0, 32768, bool/string/null), số lịch `limit-1`, `limit`, `limit+1`, status giữ chỗ và không giữ chỗ. UUID/dữ liệu giả tách biệt; failure injection deterministic. Bảng test tối thiểu:

| ID | AC/risk | Mức; test | Input/precondition | Expected | Ưu tiên |
|---|---|---|---|---|---|
| T01 | AC-1 contract | Unit `test_booking_quota_contract` | integer 1 | schema chấp nhận | P1 |
| T02 | AC-1 boundary | Unit | 32767 | schema chấp nhận | P2 |
| T03 | AC-5 invalid | Unit | 0 / 32768 | validation reject | P1 |
| T04 | AC-5 type | Unit | bool, string, null | reject, không coercion | P1 |
| T05 | AC-3 invariant | Unit | upcoming/change-pending/cancelled | chỉ 2 status đầu tính | P1 |
| T06 | AC-2 changed outcome | MySQL API provider→consumer | đặt limit=2; đã có 2 lịch | 409; không có bundle mới | P0, chưa chạy |
| T07 | AC-2 changed outcome | MySQL API provider→consumer | đổi limit=3; đã có 2 lịch | 201 và đúng bundle | P0, chưa chạy |
| T08 | AC-4 missing config | MySQL integration | singleton row vắng | 503; không có bundle | P0, chưa chạy |
| T09 | AC-4 failure mutation | Unit failure injection | settings lock raises SQLAlchemyError | ServiceUnavailable, không tạo mặc định | P0 |
| T10 | AC-5 cross-component contract | MySQL integration | admin PUT/GET rồi booking API | JSON + semantic outcome + DB side effects | P0, chưa chạy |
| T11 | NFR-1 concurrency | MySQL integration | hai booking cùng student sát quota | không vượt limit | P1, chưa chạy |
| T12 | regression rollout | Unit | exact predecessor + sửa file chưa duyệt | approved predecessor cho phép; drift reject | P1 |
| T13 | predecessor fixture schema | Unit | manifest source tree cũ | revision khớp chính source tree đó | P1 |

Contract T10 xác định provider admin API/`service_settings` và consumer booking API; kiểm tra wire integer/round-trip, version, status và quota code, rồi đếm appointment, audit, notification bằng DB session mới. Cấu trúc, ý nghĩa lẫn error contract có assertion. T09 độc lập tiêm lỗi repo boundary để mutation có thể chạy không cần MySQL; không thay thế T10.

## 4. Evidence RED → patch → GREEN

RED trước implementation: unit contract `1 failed, 5 passed`; fail đúng tại `max_active_appointments` bị `extra_forbidden`, không phải setup. Patch thêm cột `SMALLINT NOT NULL DEFAULT 3`/constraint và Alembic `20261113_1200_booking_quota`, schema/API/admin field, predicate/service enforcement và cập nhật seed/grant/OpenAPI/rollout consumers. Giữ lock order/transaction và atomic creation bundle.

GREEN: quota/service/availability unit `81 passed`; targeted API/export/worker/demo checks trước đó `10 passed, 23 skipped`; release-manifest/schema/preflight plus fixture tests `78 passed`; failure-path unit `1 passed`; full backend suite trên source có fixture fix `1,794 passed, 646 skipped, 1 warning`. Static: Ruff pass, Black 408 files unchanged, Pyright 0 errors/warnings/information. Chi tiết lệnh, source state, output/exit code trong evidence files; baseline full suite có 1 loopback ConnectTimeout trước khi nhận response, được ghi là baseline failure với nguyên nhân timing chỉ là suy luận.

MySQL HTTP contract tests không chạy: `MYSQL_TEST_URL` không cấu hình và Docker Desktop Linux engine không khả dụng. Vì vậy không khẳng định provider/consumer DB semantics, migration execution hay audit/notification persistence đã được xác minh trong runtime MySQL.

## 5. Mutation experiment

Trong disposable detached worktree tại implementation commit, mutation bắt `SQLAlchemyError` quanh đọc khóa settings và tiếp tục với policy mặc định có vẻ hợp lệ (timezone Asia/Ho_Chi_Minh, step 15, horizon 7, quota 3). Diff và kết quả nằm trong [`evidence/mutation/`](evidence/mutation/). Lệnh test failure-path `test_config_read_exception_is_not_swallowed_into_default_policy`: mutation **killed**, `1 failed` tại `pytest.raises(ServiceUnavailable)` vì không raise; lỗi là semantic. Cùng test trên source đúng: `1 passed`. Worktree mutation đã restore sạch rồi gỡ; branch nộp không chứa mutation.

## 6. Coverage, flaky review và performance

Branch coverage cho 2 unit modules: 29 passed; domain policy 92%, booking service 60%, admin config service 38%, combined 60% (303 statements, 66 branches). Các phần admin service/API phần lớn cần MySQL nên chưa được cover. Con số không chứng minh chất lượng; các AC DB chưa thực thi vẫn là residual risk.

Flaky review: ba module quota, retry và availability được chạy 5 lần với thứ tự module xen kẽ; mỗi lượt 81 passed (1.11–1.18 giây). Việc này nhắm time/timezone, UUID/test isolation và order/shared state. Không thấy lỗi trong năm lượt, nhưng không loại trừ flaky concurrency hoặc MySQL; MySQL tests không chạy.

Harness `tools/scripts/vt069_booking_quota_load.py`: 5 warm-up, 60 task barrier-synchronized; concurrency quan sát 60; monotonic duration; nearest-rank p95 trên đủ 60 mẫu không bỏ timeout/lỗi. Lượt cuối: 60 response đúng business, 30 tạo/30 quota reject, 0 lỗi khác/0 timeout; p95 **0.088716 s**, throughput **598.23 req/s**, tổng đo **0.100296 s**; dưới mục tiêu bài 2.0 s. Đây là synthetic in-process ASGI quota module, không phải deployed API, DB, gateway hay production SLO. [`evidence/coverage-load-green.md`](evidence/coverage-load-green.md).

## 7. Traceability, AI usage và reflection

| AC/risk | Test | Implementation | Commit | Evidence/result |
|---|---|---|---|---|
| AC-1 / AC-5 input contract | `test_booking_quota_contract` | admin schemas/model/migration | `bb34804` | RED + 81 unit group GREEN |
| AC-2 / AC-3 business outcome | MySQL `test_booking_quota_configuration` | `domain/booking.py`, `services/booking.py` | `bb34804` | API contract chưa chạy (MySQL thiếu) |
| AC-4 exception swallow | `test_config_read_exception_is_not_swallowed_into_default_policy` | booking error mapping | `bb34804` | source GREEN; isolated mutation killed |
| AC-5 provider/consumer side effects | `test_booking_quota_configuration` | admin/booking transaction | `bb34804` | chưa kiểm chứng DB |
| NFR release predecessor | `test_reviewed_predecessor_requires_opt_in_and_exact_configuration` | `tools/deploy/release_manifest.py` | `fb24a3b` | 75 release/rollout tests pass |
| NFR predecessor fixture schema | `test_manifest_uses_schema_revision_from_selected_source_tree` | `tests/rollout/fixtures.py` | `6b1de05` | RED then 78 focused tests pass; rerun remote rollback pending |
| NFR concurrency/performance | `vt069_booking_quota_load.py` | `domain/booking.py` | `bb34804` | 60 concurrency synthetic harness |

Commits: `39cd620` (spec/baseline plan), `bb34804` (implementation/tests), `fb24a3b` (release predecessor fix), `2409a37` (README/evidence/report/PDF), `222a8ae` (PR link/PDF), `6b1de05` (predecessor fixture fix and evidence), followed by the final CI/evidence/PDF commit. Draft PR #66 targets `develop`; it is not merged.

**Reflection draft (sinh viên cần tự chỉnh; 250–350 từ; 250 từ theo số token phân cách bằng khoảng trắng, tính nội dung ba đoạn bên dưới).** Confidence tăng rõ nhất từ failure-path test ở ranh giới đọc cấu hình: lỗi repository phải giữ thành lỗi dịch vụ, không thành policy mặc định. Mutation cấp quota mặc định bị phát hiện tại assertion `ServiceUnavailable`; test phân biệt dependency failure với thành công, thay vì chỉ kiểm tra status hoặc schema trên đường bình thường. Test-first cũng ghi nhận RED vì schema chưa có field mới. Harness đo tải đồng bộ đủ 60 task và cả 60 outcome đúng trong ASGI tổng hợp.

Confidence vẫn có giới hạn. Test provider-consumer trên MySQL không chạy vì thiếu `MYSQL_TEST_URL`; chưa chứng minh migration trên MySQL, config commit được đọc xuyên request, hoặc appointment/audit/notification được ghi hay giữ nguyên atomically trong database thật. Harness không đi qua database, connection pool, gateway hay production. Full suite có timeout loopback ở baseline; timing chỉ là suy luận. Coverage service admin thấp một phần vì tầng tích hợp chưa chạy; concurrency MySQL cũng chưa xác minh. Vì vậy pass unit và benchmark synthetic không đồng nghĩa production readiness.

Test tiếp theo có giá trị cao nhất là chạy hai module trên MySQL 8.4 cô lập từ schema rỗng: kiểm tra GET/PUT version, migration/default, quota, missing row, lỗi đọc và appointment/audit/notification qua session riêng. Sau đó kiểm tra concurrency và đo trên deployment-like stack. Người học cần tự đọc diff, đối chiếu contract, hiểu assertion và chỉnh reflection. Bản nháp có AI hỗ trợ; chưa xác nhận sinh viên review hay trải nghiệm kết quả.

<!-- APPENDIX: AI usage log and supplementary execution evidence; excluded from the 8-page main body. -->

## Phụ lục A — AI Usage Log

- **Công cụ/model:** Codex desktop, GPT-6 (model gia đình hiển thị trong môi trường; không suy đoán snapshot/version thấp hơn). Người thực hiện agent: Codex; xác nhận review của sinh viên: chưa có.
- **Prompt thực tế:** tóm tắt — người dùng yêu cầu hoàn thành bài VT-069 trên repo thật theo yêu cầu chi tiết trong thread: configurable business quota, contract-first/test-first, risk matrix, mutation, 60 concurrency, report/PDF, branch và Draft PR. Đây là tóm tắt prompt, không phải trích nguyên văn toàn bộ yêu cầu.
- **Output được dùng:** khảo sát repo, spec/plan, implementation/test, test harness, evidence notes và bản nháp reflection/report. Thay đổi chính gắn với các commit `39cd620`, `bb34804`, `fb24a3b`, `6b1de05`.
- **Output được sửa/bác bỏ:** plan ban đầu bỏ sót runtime grant/seed/release exact pins và DB side effects; được bổ sung sau khi đọc code/docs/tests. Tài liệu task ban đầu do AI tạo dùng `Add File` đã ghi đè nhầm file tracked 113 dòng; diff phát hiện trước commit, khôi phục nguyên trạng và thêm đúng subsection. Cập nhật schema head ban đầu bỏ sót reviewed predecessor hash; full suite tìm ra, regression test và commit `fb24a3b` sửa. CI tiếp tục phát hiện fixture predecessor dùng nhầm schema pin từ source hiện tại; test failure được tái hiện cục bộ rồi fixture chuyển sang đọc pin theo `root`. Một câu mutation log ban đầu khẳng định code đã tới bước tạo booking; được sửa thành inference vì assertion `pytest.raises` dừng trước các assertion tiếp theo.
- **Ví dụ AI code/test cần kiểm tra:** (1) hard-coded hash chuyển tiếp release trông hợp lệ nhưng không còn khớp grants; test cụ thể trên phát hiện. (2) file task docs có nội dung mới hợp lý nhưng thao tác ghi file đã thay thế cả tài liệu nền; `git diff` cho thấy mất 113 dòng, được restore. Đây là evidence rằng output plausible không tự chứng minh đúng.
- **Review:** AI review dựa trên test/repo evidence; không ghi nhận sinh viên đã review hoặc hiểu code nếu chưa xác nhận. Người học cần tự đối chiếu contract, đọc diff, xác nhận cam kết trước khi nộp.

## Phụ lục B — Evidence index

- [`evidence/baseline-and-red.md`](evidence/baseline-and-red.md): remote, baseline, test-first RED, baseline test failures/skips, CodeGraph freshness.
- [`evidence/mutation/result.md`](evidence/mutation/result.md) và `swallow-config-exception.patch`: isolated mutation diff, command, assertion/outcome.
- [`evidence/release-manifest-fix.md`](evidence/release-manifest-fix.md): RED/GREEN fix release predecessor.
- [`evidence/coverage-load-green.md`](evidence/coverage-load-green.md): branch coverage và tải tổng hợp.
- [`evidence/final-verification.md`](evidence/final-verification.md): full suite/static/flaky/DB preflight và git/CI trạng thái cuối.
- [`evidence/ci-rollout-fixture.md`](evidence/ci-rollout-fixture.md): CI failure root cause and fixture regression test.

## Phụ lục C — Execution log excerpts

- Baseline command `python -m pytest tests/backend -q`: 1 failed, 1,774 passed, 638 skipped, 1 warning; exit 1. New contract test before patch: 1 failed, 5 passed; đúng lỗi `max_active_appointments` là `extra_forbidden`.
- Release predecessor regression before fix: 1 failed at `assert previous is not None`. After exact grant hash update: 75 passed (manifest, rollout schema, preflight).
- Final command `python -m pytest tests/backend -q`: 1,794 passed, 646 skipped, 1 warning; exit 0; 250.35 seconds. Separate `--require-mysql` run: 30 setup errors because `MYSQL_TEST_URL` is absent; test bodies did not execute.
- Quality checks: Ruff pass; Black 408 files unchanged; Pyright 0 errors/warnings/information. Quota/retry/availability modules: five runs with alternating order, each 81 passed. Branch coverage across two unit modules: 29 passed; combined line coverage 60% (303 statements, 66 branches).
- First PR CI head `222a8ae` failed its rollback rehearsal at `Fixture schema failed`; the test-failure evidence and fix are in `ci-rollout-fixture.md`. The fixture fix is now pushed with the final report commit; remote rerun status is recorded in `final-verification.md` when observed.
- Mutation replay: isolated exception-swallowing diff; expected `ServiceUnavailable`, actual mutation did not raise; test failed at that semantic assertion (killed). Correct source test passed.
- Synthetic 60-task run: observed concurrency 60/60; 60 correct (30 create, 30 reject); no errors/timeouts; p95 0.088716 s, throughput 598.23 requests/s. This is not production stack evidence.
