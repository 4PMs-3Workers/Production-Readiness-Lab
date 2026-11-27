# Bài tập cá nhân 3 - VT-069: Đặc tả

## Thông tin

- Sinh viên: Ngô Quang Hưng - 23001890
- Team: SE2026-T18; vai trò: Thành viên
- Đề tài: APP-03 - Production Readiness Lab
- Biến thể: VT-069
- Repository: https://github.com/4PMs-3Workers/Production-Readiness-Lab
- Baseline: `12a74f7308550ccc294a783edde79db267a526d1` (`origin/develop`, 2026-11-11)
- Branch: `feature/bt3-23001890-vt069`

## Problem statement

Hạn mức ba lịch đang chiếm chỗ của sinh viên là một quy tắc nghiệp vụ được ghi trực tiếp trong service đặt lịch. Nhà trường không thể đổi hạn mức qua cấu hình dùng chung; cần sửa mã và phối hợp phát hành để thay đổi quy tắc này. Đưa hạn mức vào resource cấu hình đặt lịch đã có, để admin có thể cập nhật một lần và các lần đặt sau dùng giá trị đã commit.

## Acceptance criteria

- **AC-1 / R1:** `GET` và `PUT /api/v1/admin/booking-configuration` đọc/ghi `max_active_appointments` như một số nguyên dương trong miền MySQL `SMALLINT` (1..32767); giá trị mặc định cho dữ liệu hiện có là 3.
- **AC-2 / R1:** Sau khi admin lưu hạn mức `2`, sinh viên có hai lịch chiếm chỗ không thể tạo lịch thứ ba; kết quả là `409 BOOKING_QUOTA_EXCEEDED`, và không có appointment/audit/notification mới. Khi lưu lại `3`, cùng điều kiện cho phép tạo lịch thứ ba và ghi đúng một bundle nghiệp vụ.
- **AC-3 / R2:** Chỉ trạng thái `UPCOMING` và `CHANGE_PENDING` tính vào hạn mức trên toàn bộ ngày. Việc giảm hạn mức không hủy lịch đã có; request mới bị chặn cho đến khi số lịch chiếm chỗ nhỏ hơn hạn mức.
- **AC-4 / R3:** Thiếu dòng cấu hình hoặc lỗi đọc cấu hình không được biến thành hạn mức mặc định hay kết quả thành công có vẻ hợp lệ; API đặt lịch trả lỗi dịch vụ đã quy định (`503 SERVICE_UNAVAILABLE`) và không ghi bundle.
- **AC-5 / R4:** Giá trị sai/thiếu bị từ chối ở ranh giới cấu hình (validation/DB); hợp đồng admin và kết quả nghiệp vụ được kiểm tra cùng hai phía qua HTTP thật trên MySQL test schema, không kết luận từ status/schema đơn độc.

## Non-functional constraints

- **NFR-1:** Giữ thứ tự khóa và transaction đặt lịch hiện có; việc cập nhật cấu hình dùng cùng singleton lock/version/audit resource. Hạn mức đọc trong transaction đặt lịch sau khóa cấu hình.
- **NFR-2:** Không thêm round-trip truy vấn cho flow đặt lịch; giá trị được lấy cùng dòng cấu hình vốn đã khóa.
- **NFR-3:** Load harness phải thực sự chạy 60 task đồng thời, tính p95 trên mọi lần hoàn tất kể cả lỗi/timeout; mục tiêu bài tập `p95 <= 2.0 s`. Kết quả local synthetic được ghi rõ và không đại diện SLO production.
- **NFR-4:** Tái lập được bằng dữ liệu giả cố định, lệnh/commit/source state và evidence không chứa credential.

## Contract và invariants (phân tích trước implementation)

### Provider/consumer

- **Provider của cấu hình:** Admin booking-configuration HTTP API và resource DB `service_settings(id=1)`. `PUT` thay toàn bộ policy đã version; `GET` trả snapshot cùng `updated_at`. Admin service chịu trách nhiệm áp quyền ADMIN, CSRF/origin, validate request, optimistic version, khóa ghi và audit. Pydantic request schema kiểm tra miền dữ liệu; constraint DB bảo vệ dữ liệu lưu.
- **Consumer:** Transaction tạo appointment trong `app.services.booking.create_appointment`. Nó khóa `service_settings` bằng shared lock trước khóa account, rồi dùng `max_active_appointments` cùng count lịch chiếm chỗ để quyết định tạo hay trả lỗi nghiệp vụ. Không có cache/hot reload: thay đổi có hiệu lực sau commit cho transaction đặt mới kế tiếp; transaction đang chạy thấy snapshot/khóa theo thứ tự hiện tại.

### Wire/data contract

- Input admin `max_active_appointments`: JSON integer, không nhận bool/string/coercion, miền 1..32767; đơn vị là số appointment đang chiếm chỗ trên mỗi sinh viên.
- Output admin cùng trường là JSON integer, cùng miền. Trường được lưu trong `service_settings`, migration gán mặc định 3 cho DB hiện hữu và ứng dụng seed mới giữ 3.
- Consumer nhận số nguyên dương từ dòng singleton. `UPCOMING` và `CHANGE_PENDING` đều được tính, mọi ngày đều tính; `CANCELLED`, `COMPLETED`, `NO_SHOW` không tính. Nếu `count >= limit`, không ghi appointment, audit hay notification và trả `409 BOOKING_QUOTA_EXCEEDED`; nếu `count < limit` và các kiểm tra booking khác đều hợp lệ, ghi atomically appointment + audit + hai notification.
- Khi cấu hình giảm xuống dưới số hiện có, dữ liệu cũ được giữ; các booking mới bị chặn. Không tự hủy hoặc sửa các lịch cũ.

### Failure/default behavior

- Không có dòng `service_settings`, dữ liệu không hợp lệ hoặc không đọc được DB là trạng thái lỗi dịch vụ, không phải policy mặc định. Config default `3` chỉ dùng để migrate/seed một cấu hình hợp lệ; không dùng làm fallback runtime cho lỗi đọc.
- Bỏ qua exception rồi trả policy mặc định sẽ vi phạm contract vì có thể cho phép booking trái với hạn mức đã cấu hình và che dấu outage. Test phải kích hoạt đúng failure path và assert response/error contract cùng DB state không đổi.

### Invariants

1. Một config version đã commit là nguồn duy nhất cho timezone, bước giờ, horizon, lịch tuần và hạn mức; GET round-trip trả đúng giá trị đã lưu.
2. Quota được kiểm tra trong transaction sau khóa student, do đó các request đồng thời của cùng student không vượt hạn mức.
3. Count dùng cùng predicate statuses `UPCOMING`/`CHANGE_PENDING` như các quy tắc giữ chỗ; không phụ thuộc ngày.
4. Từ chối trước commit không để lại appointment, audit, notification hoặc receipt.
5. DB read/config failure không tạo thành một outcome nghiệp vụ thành công.

## Đối chiếu với requirements thật

| Contract/invariant | Evidence của repository tại baseline |
| --- | --- |
| Singleton config được admin đọc/ghi, có version và lịch tuần | `src/backend/app/api/admin_booking_configuration.py` (`GET/PUT /booking-configuration`); `src/backend/app/services/admin_booking_configuration.py` (`POLICY_FIELDS`, `replace_configuration`, lock/version/audit); `tests/backend/integration/test_admin_booking_configuration.py`. |
| Nguồn hiện tại có timezone/step/horizon nhưng chưa có quota | `src/backend/app/models/service_setting.py`; `src/backend/app/schemas/admin_booking_configuration.py`; `docs/architecture/database-design.md` mục `service_settings`. |
| Booking khóa config rồi account, đếm quota trong transaction | `src/backend/app/services/booking.py` (`_run_attempt_with_commit_safety`); `src/backend/app/repositories/appointments.py` (`lock_service_settings`, `count_blocking_for_student`); `docs/tasks/appointment-booking.md` mục Transaction and concurrency. |
| Giữ chỗ đúng hai status, cũ vẫn giữ chỗ khi change pending | `README.md` mục Quy tắc cốt lõi; `docs/architecture/database-design.md` mục appointment states; `src/backend/app/repositories/appointments.py`. |
| Booking ghi appointment/audit/2 notification trong một bundle | `src/backend/app/services/booking.py` (`add_creation_bundle`); integration tests `test_booking_service.py`. |
| Config lỗi không thành empty/default outcome | `docs/tasks/catalog-availability.md` mục Điều kiện dữ liệu và cấu hình; `src/backend/app/services/booking.py` maps service/dependency failures to `ServiceUnavailable`. |
| DB hiện có cần migration tương thích; runtime dùng column-level grant | `src/backend/migrations/versions/`; `tools/deploy/mysql/grants.sql`. |

Không tìm thấy Lecture 06/07 đầy đủ trong repository ở khảo sát ban đầu; đặc tả này bám trực tiếp yêu cầu biến thể và contract/tài liệu/code hiện có, không gán nội dung cho lecture.

## Scope / out of scope

- **In scope:** persisted config field, migration/default/constraint, admin request/response, transaction booking enforcement, API contract test có outcome/state, regression/unit checks, deliberate isolated failure-swallowing mutation, load harness 60-concurrency, coverage/flaky checks và evidence/report/PDF.
- **Out of scope:** admin UI mới, thay đổi quota của lịch change proposal đã được chấp nhận (vốn chỉ đổi một lịch hiện hữu), hot reload/cache, tự hủy lịch khi giảm quota, thay đổi các giới hạn hạ tầng, production deployment hoặc tuyên bố SLO production.

## Assumptions

- Admin hiện là người có thẩm quyền đổi policy nghiệp vụ; reuse endpoint versioned hiện có tốt hơn tạo endpoint/resource thứ hai.
- Hạn mức 1..32767 được suy ra từ kiểu `SMALLINT` và validation pattern `PositiveSmallInt`; business deployment có thể chọn giá trị nhỏ hơn mà không phải thay đổi source. Không đặt thêm một max nghiệp vụ tùy tiện.
- Default `3` giữ behavior và dữ liệu hiện hữu; một request có thể đổi giá trị policy toàn trường.
- MySQL 8.4 là integration target của repository. Nếu không có lab DB khả dụng, kết quả kiểm thử HTTP thật sẽ được đánh dấu chưa kiểm chứng và không thay bằng mock hai phía.
