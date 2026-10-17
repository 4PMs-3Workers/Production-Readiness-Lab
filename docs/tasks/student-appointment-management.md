# Lịch sử và quản lý lịch hẹn của sinh viên

Tài liệu này mô tả hợp đồng của issue #18. OpenAPI đầy đủ được sinh từ FastAPI tại
[openapi.yaml](../api/openapi.yaml). Tất cả đường dẫn bên dưới có tiền tố /api/v1.

## Quyền truy cập và phản hồi

Ba endpoint GET đọc ngữ cảnh ngày, lịch sử và chi tiết; hai endpoint POST tạo/hủy chỉ dành
cho tài khoản STUDENT có session hợp lệ. Backend lấy danh tính từ session, không nhận
student_id để quyết định quyền. GET không cần CSRF. Hai thao tác ghi POST tạo và hủy cần
Origin cùng site đã cấu hình (nếu thiếu Origin, Referer được dùng thay thế) và X-CSRF-Token
khớp cookie prl_csrf đã ký.

Tất cả phản hồi của các đường dẫn này, kể cả lỗi, có Cache-Control: private, no-store và
X-Request-ID. Lỗi còn có X-Error-Class. Body lỗi dùng envelope chung:

    {"error":{"code":"VALIDATION_ERROR","message":"Invalid request","request_id":"..."}}

Thông báo lỗi đã được sanitize; không đưa SQL, stack trace, secret, mô tả lịch hay lý do hủy
vào body lỗi. Request ID trong body trùng với header.

## Ngữ cảnh ngày đặt

GET /api/v1/booking-context không nhận query parameter. Trả về timezone IANA cấu hình trong
service_settings, ngày nghiệp vụ hiện tại và cửa sổ ngày đặt:

| Trường | Ý nghĩa |
| --- | --- |
| timezone | Múi giờ nghiệp vụ từ cấu hình DB. |
| business_date | Ngày của một lần lấy giờ server trong timezone trên. |
| min_booking_date | business_date + 1 ngày. |
| max_booking_date | business_date + booking_horizon_days ngày. |

Hai biên ngày đều được chấp nhận. Frontend phải dùng ngày server này thay vì đồng hồ máy
người dùng. Endpoint và bước xác thực POST booking dùng chung quy tắc timezone/cửa sổ; booking
vẫn được kiểm tra lại trong transaction bằng UTC_TIMESTAMP(6) của DB sau khi lấy khóa. Cấu
hình thiếu hoặc không hợp lệ trả 503 SERVICE_UNAVAILABLE, không dùng giá trị dự phòng.

Các lỗi chính: 401 INVALID_SESSION, 403 FORBIDDEN_ROLE, 422 VALIDATION_ERROR nếu có query
parameter hoặc query lặp, 500 INTERNAL_SERVER_ERROR và 503 SERVICE_UNAVAILABLE.

Ví dụ response nếu business date là 2026-10-17 và cấu hình DB đặt booking horizon là 30 ngày:

```json
{
  "timezone": "Asia/Ho_Chi_Minh",
  "business_date": "2026-10-17",
  "min_booking_date": "2026-10-18",
  "max_booking_date": "2026-11-16"
}
```

## Tạo lịch

POST /api/v1/appointments nhận JSON strict, không chấp nhận trường ngoài:

| Trường | Bắt buộc | Quy tắc |
| --- | --- | --- |
| purpose_id | Có | UUID của nhu cầu đang hoạt động. |
| staff_id | Có | UUID của nhân viên đang hoạt động và đang nhận nhu cầu. |
| starts_at | Có | Timestamp ISO 8601 có timezone; backend quy đổi sang UTC. |
| description | Không | Chuỗi hoặc null, tối đa 2.000 ký tự; có thể bắt buộc tùy nhu cầu. |

Header Idempotency-Key bắt buộc, dài 1–128 ký tự và không được chỉ gồm khoảng trắng.
student_id, ends_at, trạng thái và các trường khác không phải input hợp lệ. Nếu nhu cầu yêu
cầu nội dung tự do, description thiếu, null hoặc chỉ có khoảng trắng trả lỗi xác thực.

Tạo mới trả 201 với receipt gồm id, status = UPCOMING và created_at. Retry trùng nội dung
cùng key trả 200 và receipt bất biến của lần đầu, kể cả lịch hiện đã hủy. Cùng key nhưng
khác nội dung trả 409 IDEMPOTENCY_CONFLICT. Slot là preview; backend kiểm tra lại eligibility,
ngày, quota và xung đột thời gian trong transaction trước khi ghi.

Giữ nguyên key và body khi kết quả request chưa rõ. Sau lỗi trước commit, retry kiểm tra
nghiệp vụ lại. Khi commit có thể thành công nhưng kết nối lỗi, API invalidate connection và
trả 503 SERVICE_UNAVAILABLE mà không tự chạy lại transaction. Client không được xem 503 là
bằng chứng rollback; gửi lại cùng key và body cho phép lấy receipt nếu lần trước đã commit.

## Lịch sử và chi tiết

GET /api/v1/appointments nhận các query sau:

| Query | Mặc định | Giới hạn |
| --- | --- | --- |
| page | 1 | Số nguyên từ 1 trở lên. |
| page_size | 20 | Số nguyên từ 1 đến 100. |
| status | Không lọc | UPCOMING, CHANGE_PENDING, CANCELLED, COMPLETED hoặc NO_SHOW. |

Query ngoài danh sách, giá trị sai và khóa query lặp đều trả 422 VALIDATION_ERROR. Response
có items, page, page_size và total; total dùng cùng điều kiện chủ sở hữu/trạng thái với items.
Lịch sắp theo starts_at giảm dần rồi id giảm dần. Trang ngoài phạm vi trả items rỗng và
total không đổi.

Mỗi item có các trường: id, purpose_id, purpose_code, purpose_name, staff_id, staff_name,
duration_minutes, starts_at, ends_at, location, status và created_at. Timestamp trả về UTC
với hậu tố Z. GET /api/v1/appointments/{appointment_id} trả cùng các trường cùng với
description, cancelled_at và cancel_reason. Chỉ sinh viên sở hữu appointment mới nhận được
detail; UUID không tồn tại và UUID của sinh viên khác cùng trả 404 NOT_FOUND.

Ví dụ `GET /api/v1/appointments?page=1&page_size=20&status=UPCOMING`:

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000001",
      "purpose_id": "00000000-0000-4000-8000-000000000002",
      "purpose_code": "COUNSELING",
      "purpose_name": "Academic counseling",
      "staff_id": "00000000-0000-4000-8000-000000000003",
      "staff_name": "Advisor A",
      "duration_minutes": 30,
      "starts_at": "2026-10-20T03:30:00Z",
      "ends_at": "2026-10-20T04:00:00Z",
      "location": "Room 101",
      "status": "UPCOMING",
      "created_at": "2026-10-17T02:00:00Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

Lịch sử không phụ thuộc vào trạng thái hoạt động hiện tại của purpose, staff hay liên kết
staff-purpose. purpose_code, purpose_name và staff_name được đọc từ các bản ghi liên quan
hiện tại nên có thể phản ánh tên được đổi sau khi lịch tạo. duration_minutes được lưu cùng
appointment lúc tạo và giữ nguyên dù thời lượng cấu hình của purpose thay đổi.

## Hủy lịch

POST /api/v1/appointments/{appointment_id}/cancel nhận body strict:

| Trường | Bắt buộc | Quy tắc |
| --- | --- | --- |
| reason | Không | Chuỗi hoặc null, tối đa 2.000 ký tự. |

Body JSON là bắt buộc; `{}` được chấp nhận khi không có lý do, còn bỏ hẳn body trả 422.
Không nhận trường khác. Thành công trả 200 với id, status = CANCELLED và cancelled_at.
Ví dụ request:

```json
{"reason": "I need to reschedule."}
```

Ví dụ response:

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "status": "CANCELLED",
  "cancelled_at": "2026-10-17T02:05:00Z"
}
```

ID không thuộc sinh viên hoặc không tồn tại trả cùng 404 NOT_FOUND.

Lịch UPCOMING và CHANGE_PENDING được hủy trước 00:00 theo timezone nghiệp vụ của ngày hẹn.
Thời điểm đúng 00:00 đã quá hạn. API chỉ kiểm tra cutoff sau khi lấy đầy đủ khóa và đọc
UTC_TIMESTAMP(6) từ DB; nếu request chờ khóa đi qua nửa đêm, request bị từ chối. Lịch đã ở
CANCELLED trả lại trạng thái và thời điểm hủy đã lưu, giữ nguyên người hủy/lý do và không
thêm audit/notification, kể cả khi cutoff đã qua. COMPLETED và NO_SHOW trả 409
APPOINTMENT_STATE_CONFLICT; quá cutoff trả 409 CANCELLATION_DEADLINE_PASSED.

Thứ tự khóa hủy là:

1. service_settings với FOR SHARE.
2. Tài khoản student và staff theo thứ tự UUID dạng 16 byte tăng dần.
3. Appointment.
4. Proposal PENDING nếu trạng thái appointment là CHANGE_PENDING.

Sau khi đủ khóa, service đọc lại quyền sở hữu, role/activity và trạng thái. Nếu hủy thành
công, cập nhật appointment, hủy proposal đang chờ nếu có, thêm đúng một audit
APPOINTMENT_CANCELLED và hai notification cho student/staff trong cùng transaction. Lý do
không nằm trong audit metadata, logs hay traces. Lỗi trước commit rollback toàn bộ.

Deadlock và lock timeout MySQL được retry có giới hạn trong deadline request. Commit có kết
quả không xác định thì connection bị invalidate; service trả 503 SERVICE_UNAVAILABLE và không
tự retry ghi. Client không được suy luận rằng 503 đồng nghĩa rollback. Có thể gửi lại yêu cầu
hủy: nếu transaction trước đã commit, server trả thời điểm hủy gốc mà không nhân đôi side
effects; nếu transaction trước không commit, server đánh giá lại trạng thái và cutoff hiện tại.

## Hợp đồng lỗi

| HTTP | Mã | Áp dụng |
| --- | --- | --- |
| 401 | INVALID_SESSION | Session thiếu, hết hạn hoặc không hợp lệ. |
| 403 | FORBIDDEN_ROLE | Tài khoản không có role STUDENT. |
| 403 | INVALID_CSRF | CSRF header/cookie thiếu, lặp hoặc không khớp khi POST. |
| 403 | INVALID_ORIGIN | Origin/Referer không cùng site khi POST. |
| 404 | NOT_FOUND | Detail/cancel không thuộc sinh viên; hoặc purpose/staff không còn đủ điều kiện để tạo. |
| 409 | BOOKING_CONFLICT | Slot không còn hợp lệ hoặc bị chiếm. |
| 409 | BOOKING_QUOTA_EXCEEDED | Đạt hạn mức ba lịch đang chiếm chỗ. |
| 409 | IDEMPOTENCY_CONFLICT | Idempotency-Key đã dùng với body khác. |
| 409 | APPOINTMENT_STATE_CONFLICT | Appointment đã COMPLETED hoặc NO_SHOW. |
| 409 | CANCELLATION_DEADLINE_PASSED | Thời điểm hủy bằng hoặc sau nửa đêm địa phương ngày hẹn. |
| 422 | VALIDATION_ERROR | UUID, query, header, body, trường extra hoặc dữ liệu nghiệp vụ đầu vào sai. |
| 500 | INTERNAL_SERVER_ERROR | Lỗi không dự kiến, trả thông báo đã sanitize. |
| 503 | SERVICE_UNAVAILABLE | DB/configuration/deadline/lock/query budget lỗi hoặc kết quả commit chưa xác định. |

## Kiểm chứng

Exporter OpenAPI lấy contract từ schema và route FastAPI. Từ thư mục gốc repository, chạy:

    python tools/scripts/export_openapi.py
    git diff --exit-code -- docs/api/openapi.yaml

Các lệnh kiểm thử migration, backend đầy đủ và frontend được ghi trong plan issue #18.
Chạy integration với MySQL 8.4.11 trên schema test riêng được khởi tạo qua Alembic; không
thay race/failure tests bằng SQLite. Bản ghi nghiệm thu cần ghi SHA cuối, phiên bản MySQL,
số passed/failed/skipped, lệnh đã chạy và kiểm tra còn giới hạn.

### Kết quả nghiệm thu

Mã API và kiểm thử đã được xác nhận tại commit `95d1479` trên nhánh
`feature/student-appointment-management`. MySQL server là 8.4.11. Bộ integration race,
failure/recovery và transaction helper của Task 4 đạt 30 passed; regression appointment/
booking đạt 184 passed. Lượt backend đầy đủ đạt 620 passed và không có test bị skip; test
OpenAPI duy nhất ban đầu thất bại vì expectation cũ chưa liệt kê hai endpoint và các trường
detail mới. Sau khi cập nhật expectation, cả hai test trong module OpenAPI đạt 2 passed.

`pip check`, Ruff, Pyright (0 errors/warnings/informations), Black (13 file không cần sửa),
`git diff --check` và so sánh byte OpenAPI exporter/artifact đều đạt. Migration smoke trên
schema riêng chạy thành công `alembic upgrade head`, `alembic current`, `alembic heads` và
lặp lại `alembic upgrade head`; schema tạm đã được xóa sau kiểm tra. Frontend format, lint,
typecheck, unit (70), build và E2E (66/66) đã đạt trước đó trong cùng nhánh. Backend phát ra
các warning deprecation có sẵn từ Alembic `path_separator` và Starlette TestClient; không có
test skip. Không push và không tạo pull request.
