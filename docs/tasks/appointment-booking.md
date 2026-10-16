# API đặt lịch appointment

API này cho phép sinh viên tạo appointment sau khi xem availability và lấy lại chi tiết hiện tại của lịch thuộc mình. Availability chỉ là bản xem trước; slot được kiểm tra lại trong transaction tạo lịch.

## Tạo lịch

Gửi `POST /api/v1/appointments` bằng session của sinh viên. Request cần same-origin, cookie CSRF đã ký cùng `X-CSRF-Token`, và một `Idempotency-Key` từ 1 đến 128 ký tự không phải toàn khoảng trắng.

```http
POST /api/v1/appointments HTTP/1.1
Origin: https://portal.example.edu
Cookie: prl_session=<session>; prl_csrf=<signed-csrf-token>
X-CSRF-Token: <same-signed-csrf-token>
Idempotency-Key: 41d9ba9c-08e1-4b6d-a3e4-6ef3b00af744
Content-Type: application/json

{
  "purpose_id": "a20c5369-b321-4c20-948e-7a0053b3fa55",
  "staff_id": "cefc186e-b6cd-4f74-9fc5-28ef27c02eb8",
  "starts_at": "2026-10-17T01:00:00Z",
  "description": "Tôi muốn trao đổi về kế hoạch học tập."
}
```

`purpose_id`, `staff_id` và `starts_at` là bắt buộc. `starts_at` phải có timezone; API quy đổi thời điểm sang UTC để xác thực và so sánh. `description` có thể bỏ qua hoặc gửi `null` nếu nhu cầu không bắt buộc nội dung. Khi nhu cầu yêu cầu mô tả, thiếu, `null` hoặc chuỗi chỉ có khoảng trắng bị từ chối. Mô tả được lưu nguyên văn; khoảng trắng chỉ bị loại khi kiểm tra nội dung bắt buộc. Thiếu và `null` được xem là tương đương; chuỗi rỗng khác `null` trong hợp đồng idempotency. Request không nhận `student_id`, `ends_at`, trạng thái hoặc trường phụ khác.

Tạo mới trả `201 Created`:

```json
{
  "id": "4c87b8bb-0b2a-4a97-89e5-d20e6f1f21c8",
  "status": "UPCOMING",
  "created_at": "2026-10-16T02:14:33.123456Z"
}
```

## Idempotency và thử lại

Key được giữ nguyên như client gửi và được scope theo sinh viên. Bản ghi appointment giữ key và hash; key không tự hết hạn khi appointment còn được lưu. Dùng key mới cho một ý định đặt lịch mới. Gửi lại cùng key với nội dung tương đương trả `200 OK` và receipt ban đầu. Cùng key với nội dung khác trả `409 IDEMPOTENCY_CONFLICT`.

Hash được tính từ đúng bốn trường nghiệp vụ: `purpose_id`, `staff_id`, `starts_at` và `description`. UUID dùng dạng chữ thường có dấu gạch nối; thời điểm được đổi sang UTC, có sáu chữ số phần thập phân và hậu tố `Z`. Các trường được serialize thành JSON UTF-8 với key đã sắp xếp, dấu phân cách gọn và Unicode không escape, rồi băm SHA-256. Key, request ID và dữ liệu kỹ thuật không tham gia hash. Hai timestamp biểu diễn cùng thời điểm UTC tạo cùng nội dung canonical; nội dung description được giữ nguyên, nên thay đổi khoảng trắng trong mô tả tạo hash khác.

Frontend phải giữ **cùng key và cùng body** cho đến khi biết kết quả cuối cùng. Nếu timeout, mất kết nối hoặc response không đến sau khi server đã commit, gửi lại đúng request đó. Không tạo key mới để thử lại một kết quả chưa rõ: key mới có thể tạo appointment thứ hai. Sau khi đã nhận lỗi nghiệp vụ cuối cùng và người dùng thay đổi lựa chọn, lần gửi mới cần key mới.

POST replay luôn trả receipt bất biến của lần tạo đầu: `id`, `status: "UPCOMING"` và `created_at` ban đầu. Ví dụ nếu lịch sau đó bị hủy, POST replay vẫn trả receipt `UPCOMING`; GET detail trả trạng thái hiện tại `CANCELLED`.

## Xem chi tiết hiện tại

`GET /api/v1/appointments/{appointment_id}` chỉ trả bản ghi có cả appointment ID và student ID khớp session hiện tại. ID không tồn tại và ID của sinh viên khác đều trả cùng `404 NOT_FOUND`.

```json
{
  "id": "4c87b8bb-0b2a-4a97-89e5-d20e6f1f21c8",
  "purpose_id": "a20c5369-b321-4c20-948e-7a0053b3fa55",
  "staff_id": "cefc186e-b6cd-4f74-9fc5-28ef27c02eb8",
  "duration_minutes": 30,
  "starts_at": "2026-10-17T01:00:00Z",
  "ends_at": "2026-10-17T01:30:00Z",
  "description": "Tôi muốn trao đổi về kế hoạch học tập.",
  "location": null,
  "status": "UPCOMING",
  "created_at": "2026-10-16T02:14:33.123456Z"
}
```

Response thành công và lỗi của hai endpoint có `Cache-Control: private, no-store`. Detail trả trạng thái hiện tại, thời lượng đã chụp tại lúc tạo, thời gian bắt đầu/kết thúc, description và location; không trả hash idempotency, audit metadata, session hay dữ liệu sinh viên khác.

## Lỗi ổn định

Lỗi dùng envelope chung và response có `X-Request-ID`, `X-Error-Class` cùng cache policy riêng tư:

```json
{
  "error": {
    "code": "BOOKING_CONFLICT",
    "message": "The requested appointment slot is no longer available",
    "request_id": "gateway-request-id"
  }
}
```

Các mã nghiệp vụ của thao tác tạo:

| HTTP | Mã | Ý nghĩa |
| --- | --- | --- |
| 409 | `BOOKING_CONFLICT` | Slot đã được đặt, bị nhân viên đánh dấu unavailable, nằm ngoài lịch phục vụ hoặc xung đột với lịch đang chiếm chỗ của sinh viên/nhân viên. |
| 409 | `BOOKING_QUOTA_EXCEEDED` | Sinh viên đã có ba appointment ở trạng thái `UPCOMING` hoặc `CHANGE_PENDING`. |
| 409 | `IDEMPOTENCY_CONFLICT` | Key đã dùng cho nội dung canonical khác. |
| 404 | `NOT_FOUND` | Nhu cầu/nhân viên không còn đủ điều kiện; với GET, lịch không tồn tại hoặc không thuộc sinh viên hiện tại. |
| 422 | `VALIDATION_ERROR` | Body, key, timezone hoặc mô tả bắt buộc không hợp lệ. |
| 503 | `SERVICE_UNAVAILABLE` | Database/dependency lỗi, hết deadline hoặc retry bị cạn. Đây không phải kết quả xung đột nghiệp vụ; giữ key và body khi kết quả commit chưa rõ. |

Lỗi không chứa SQL, stack trace, secret, token hoặc description. Request không có session trả `401 INVALID_SESSION`; sai role/CSRF/origin trả `403` với mã auth phù hợp.

## Transaction và cạnh tranh

Trước khi đánh giá quota hoặc slot, service kiểm tra key đã commit để replay kể cả khi trạng thái appointment hiện tại đã thay đổi. Transaction tạo sau đó khóa singleton `service_settings` ở chế độ chia sẻ, rồi khóa các user student/staff theo thứ tự UUID byte tăng dần. Sau các khóa, service đọc `UTC_TIMESTAMP(6)` riêng, nạp lại eligibility, cấu hình, quota, unavailability và lịch đang chiếm chỗ, rồi xác nhận slot. Preview không khóa hoặc giữ chỗ.

Mỗi transaction đọc key hoặc tạo lịch áp dụng `DB_QUERY_TIMEOUT_SECONDS`, bao gồm flush và commit. Mỗi lần chờ khóa cấu hình hoặc account áp dụng thêm `DB_LOCK_TIMEOUT_SECONDS`. Các giới hạn này cùng chịu deadline request ban đầu; retry không làm mới deadline tổng. Timeout phía ứng dụng trả `503 SERVICE_UNAVAILABLE` và giải phóng transaction; nếu xảy ra trong lúc commit, connection bị invalidate và service không tự thử lại thao tác ghi.

Một transaction ghi đồng thời appointment `UPCOMING`, một audit event `APPOINTMENT_CREATED` không chứa mô tả, và đúng hai notification cho sinh viên cùng nhân viên. Bất kỳ lỗi nào trước commit đều rollback cả ba loại ghi. MySQL deadlock `1213` và lock timeout `1205` được retry có giới hạn trong deadline request. Nếu kết quả commit không xác định, connection bị invalidate và service trả lỗi đã sanitize; service không tự chạy lại thao tác ghi. Client có thể gửi lại cùng key/body để xác định receipt đã commit hay chưa.

## Cài đặt và kiểm chứng

Integration tests cần MySQL 8.4. URL test không chỉ định database mặc định; user cần quyền tạo/xóa database vì mỗi test tạo schema riêng. Không trỏ test URL vào dữ liệu dùng chung.

```powershell
$env:MYSQL_TEST_URL = "mysql+asyncmy://test_user:test_password@127.0.0.1:3306"
python -m pytest tests/backend/integration/test_booking_concurrency.py tests/backend/integration/test_booking_failures.py tests/backend/integration/test_booking_service.py tests/backend/integration/test_booking_timeouts.py -q
```

Để chạy migration smoke test, cấu hình `DATABASE_URL` trỏ tới database migration riêng rồi chạy:

```powershell
python -m alembic upgrade head
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
```

Bộ kiểm tra backend đầy đủ và kiểm tra OpenAPI được chạy từ thư mục gốc:

```powershell
python -m pip check
python -m black --check src/backend tests/backend tools/scripts/export_openapi.py
python -m ruff check src/backend tests/backend tools/scripts/export_openapi.py
python -m pyright
python -m pytest tests/backend -q --junitxml=build/backend-tests.xml
python tools/scripts/export_openapi.py
git diff --exit-code -- docs/api/openapi.yaml
```

CI dùng MySQL `8.4.11` và kiểm tra số test skip bằng 0. Race tests đồng bộ các request trước khi service lấy khóa, dùng session/cookie jar độc lập, và đọc invariant sau cùng bằng session mới.
