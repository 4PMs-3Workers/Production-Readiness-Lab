# Staff appointment management

Hợp đồng này hoàn thành issue #21. API dùng tiền tố `/api/v1`; OpenAPI đầy đủ nằm tại
[openapi.yaml](../api/openapi.yaml).

## Quyền truy cập

Các endpoint dưới đây chỉ dành cho tài khoản `STAFF` đang hoạt động và có session hợp lệ.
Danh tính nhân viên luôn lấy từ session. `ADMIN` không có quyền vượt qua assignment; lịch của
nhân viên khác và ID không tồn tại cùng trả `404 NOT_FOUND`. GET không cần CSRF; POST cần
Origin cùng site (hoặc Referer nếu thiếu Origin) và `X-CSRF-Token` khớp cookie `prl_csrf` đã
ký. Query không khai báo, query lặp và query trên POST đều trả `422 VALIDATION_ERROR`.

Mọi response thành công/lỗi đều có `Cache-Control: private, no-store` và `X-Request-ID`.
Lỗi dùng envelope chung và thông báo đã sanitize.

## Danh sách và chi tiết

`GET /api/v1/staff/me/appointments` hỗ trợ:

| Query | Mặc định | Quy tắc |
| --- | --- | --- |
| `page` | 1 | Số nguyên từ 1. |
| `page_size` | 20 | Số nguyên từ 1 đến 100. |
| `status` | Không lọc | `UPCOMING`, `CHANGE_PENDING`, `CANCELLED`, `COMPLETED`, `NO_SHOW`. |
| `date` | Không lọc | Ngày `YYYY-MM-DD` theo `service_settings.timezone`. |

Khi có `date`, backend lấy thời điểm UTC đầu tiên của ngày địa phương được chọn và ngày kế
tiếp thành khoảng nửa mở `[starts_at, ends_at)`. Cách này xử lý cả ngày đổi giờ có nửa đêm
bị lặp hoặc bị bỏ qua. Kết quả xếp theo `starts_at ASC, id ASC`. `total` áp dụng cùng
bộ lọc assignment/status/date với items; trang nằm ngoài phạm vi trả danh sách rỗng. Lịch sử
không phụ thuộc trạng thái hoạt động hiện tại của nhu cầu, nhân viên hay liên kết tiếp nhận.
Thời lượng đọc từ snapshot lưu cùng appointment.

Item danh sách gồm ID lịch, nhu cầu, mã/tên nhu cầu hiện tại, ID/tên sinh viên, thời lượng đã
lưu, giờ bắt đầu/kết thúc, địa điểm, trạng thái, thời điểm tạo, hủy và kết quả. Không trả mô
tả tự do hoặc lý do hủy trong danh sách. `GET /api/v1/staff/me/appointments/{appointment_id}`
trả cùng dữ liệu cùng mô tả, lý do hủy và actor hủy/ghi kết quả. Không trường nào chứa session,
hash khóa chống gửi lặp hoặc metadata audit.

Ví dụ:

```http
GET /api/v1/staff/me/appointments?page=1&page_size=20&status=UPCOMING&date=2026-10-20
Cookie: prl_session=...
```

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 20
}
```

## Hủy lịch

`POST /api/v1/staff/me/appointments/{appointment_id}/cancel` nhận body JSON strict, bắt buộc
có object nhưng trường `reason` là tùy chọn, nullable và tối đa 2.000 ký tự. Chỉ lịch
`UPCOMING`/`CHANGE_PENDING` được hủy. Hạn là trước 00:00 theo múi giờ nghiệp vụ của ngày hẹn;
đúng nửa đêm đã quá hạn. Database đọc `UTC_TIMESTAMP(6)` sau khi lấy khóa proposal đang chờ,
nên request đợi khóa qua hạn sẽ bị từ chối. Proposal `PENDING` được chuyển `CANCELLED` cùng
transaction.

Lặp lại với lịch đã hủy trả thời điểm hủy ban đầu mà không đổi actor/lý do hoặc tạo side effect.
`COMPLETED`/`NO_SHOW` trả `409 APPOINTMENT_STATE_CONFLICT`; quá hạn trả
`409 CANCELLATION_DEADLINE_PASSED`.

```http
POST /api/v1/staff/me/appointments/{id}/cancel
Origin: https://app.example.edu
X-CSRF-Token: <signed token matching prl_csrf cookie>
Content-Type: application/json

{"reason":"Student requested a new time."}
```

```json
{"id":"00000000-0000-4000-8000-000000000001","status":"CANCELLED","cancelled_at":"2026-10-17T16:00:00Z"}
```

## Ghi kết quả

`POST /api/v1/staff/me/appointments/{appointment_id}/outcome` nhận đúng một trường `status`,
giá trị `COMPLETED` hoặc `NO_SHOW`. Ghi chỉ hợp lệ khi lịch đang `UPCOMING` và database time
phải lớn hơn `ends_at`. Tại hoặc trước `ends_at` trả `409 APPOINTMENT_NOT_ENDED`. Retry
cùng kết quả trả `outcome_at` gốc, không thêm audit/notification; kết quả trái ngược hoặc
trạng thái không tương thích trả `409 APPOINTMENT_STATE_CONFLICT`.

```http
POST /api/v1/staff/me/appointments/{id}/outcome
Origin: https://app.example.edu
X-CSRF-Token: <signed token matching prl_csrf cookie>
Content-Type: application/json

{"status":"COMPLETED"}
```

```json
{"id":"00000000-0000-4000-8000-000000000001","status":"COMPLETED","outcome_at":"2026-10-17T18:46:00Z"}
```

## Transaction, hạn mức và vận hành

Các thao tác ghi theo `READ COMMITTED` và thứ tự khóa chung:

1. `service_settings FOR SHARE`.
2. Từng tài khoản student/staff theo UUID byte tăng dần.
3. Appointment.
4. Proposal `PENDING` nếu hủy lịch `CHANGE_PENDING`.

Sau khi có đủ khóa, service xác thực lại actor, assignment, trạng thái và đọc DB clock. Một
chuyển trạng thái thành công cùng ghi một audit event và hai notification cho student/staff
trong cùng transaction. Lý do tự do không vào audit metadata, logs hoặc traces. Deadlock/lock
timeout được retry có giới hạn trong deadline request. Nếu kết quả commit không rõ, connection
bị invalidate và API trả 503 mà không tự replay. Client có thể gửi lại thao tác: trạng thái đã
commit sẽ trả receipt gốc, không nhân đôi side effect.

Hạn mức sinh viên tiếp tục chỉ đếm `UPCOMING` và `CHANGE_PENDING`. `COMPLETED`/`NO_SHOW` giải
phóng hạn mức do trạng thái; lịch `UPCOMING` quá ngày nhưng chưa được xử lý vẫn tính. Không có
migration mới: các cột outcome/cancel, audit, notification và proposal đã có trong schema tại
base `c865b1e07dd25cca0e1a1269b29ebdff84606637`.

## Kiểm chứng

Kiểm chứng cuối trên branch `feature/staff-appointment-management`, từ base
`c865b1e07dd25cca0e1a1269b29ebdff84606637`:

- `python -m black --check --workers 1 src/backend/app tests/backend tools/scripts/export_openapi.py`: đạt, 126 file không cần format.
- `python -m ruff check src/backend/app tests/backend tools/scripts/export_openapi.py`: đạt.
- `python -m pyright --venvpath D:\Production-Readiness-Lab`: đạt, 0 lỗi/cảnh báo.
- `python -m pytest tests/backend -q --tb=short`: 417 passed, 260 skipped, 0 failed, 1 warning trong 65.49 giây.
- Hai lần chạy `python tools/scripts/export_openapi.py` tạo cùng SHA-256 artifact.
- `git diff --check`: đạt.

260 bài skip là integration cần MySQL. Trên máy này không đặt `MYSQL_TEST_URL`, không có MySQL
listener trên `127.0.0.1:3306`, và Docker engine không chạy được. Vì vậy các test MySQL 8.4.11,
bao gồm race, proposal lock, rollback/commit recovery, quota và migration smoke, chưa được xác
nhận ở môi trường này; skip không được tính là pass. Không tạo migration vì các trường và bảng
cần thiết đã có ở base. Chưa push branch hoặc mở PR.
