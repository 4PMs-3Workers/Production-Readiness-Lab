# Chuyển đổi và kiểm tra kiểu dữ liệu của models

**Ngày thực hiện:** 11/10/2026

**Nhánh:** `feature/database-models`

**Trạng thái:** Đã triển khai và kiểm tra local

## Mục tiêu

Ánh xạ đúng dữ liệu Python với kiểu MySQL theo thiết kế database, tránh mất độ chính xác
hoặc thay đổi ý nghĩa dữ liệu khi ghi/đọc.

## File triển khai

- [types.py](../../src/backend/app/models/types.py): các bộ chuyển đổi dùng chung.
- [base.py](../../src/backend/app/models/base.py): kiểu ID, timestamp và collation dùng chung.
- [user.py](../../src/backend/app/models/user.py): chuẩn hóa email khi gán qua ORM.
- [appointment.py](../../src/backend/app/models/appointment.py): key/hash yêu cầu tạo lịch.

## Nội dung triển khai

| Bộ chuyển đổi | Kiểu MySQL | Hành vi |
|---|---|---|
| `BinaryUUID` | `BINARY(16)` | Nhận UUID hoặc chuỗi UUID; lưu byte theo thứ tự chuẩn, đọc thành UUID; từ chối đầu vào sai kiểu. |
| `UTCDateTime` | `DATETIME(6)` | Chỉ nhận datetime có múi giờ; chuyển về UTC khi lưu, gắn UTC khi đọc; giữ microsecond. |
| `TimeOfDay` | `TIME(0)` | Nhận giờ trong ngày không có múi giờ/phần lẻ giây; xử lý timedelta của driver và kiểm tra phạm vi. |
| `SHA256Hash` | `VARBINARY(32)` | Nhận đúng 32 byte hash thô; từ chối sai kiểu hoặc độ dài. |

- UUID không dùng cơ chế đảo thứ tự byte `UUID_TO_BIN(..., 1)`.
- Email được chuẩn hóa bằng `strip().lower()`; collation `utf8mb4_0900_as_ci` phân biệt dấu,
  không phân biệt hoa/thường. CHECK tại database kiểm tra cả thao tác SQL trực tiếp.
- Mã nghiệp vụ và idempotency key dùng `utf8mb4_0900_bin`. Key do client gửi được giữ nguyên,
  gồm cả hoa/thường và khoảng trắng cuối chuỗi.
- `created_at` có mặc định `CURRENT_TIMESTAMP(6)` tại database. `updated_at` được backend
  gán theo UTC khi insert/update qua ORM.

## Kết quả kiểm tra

- [x] UUID lưu đúng byte và được đọc lại thành cùng UUID.
- [x] Datetime có offset được quy về cùng thời điểm UTC, giữ microsecond; datetime thiếu
  múi giờ bị từ chối.
- [x] Kiểu giờ từ chối múi giờ/phần lẻ giây và các giá trị timedelta ngoài phạm vi hỗ trợ.
- [x] Hash sai độ dài bị từ chối; giá trị 32 byte hợp lệ được giữ nguyên.
- [x] Email được chuẩn hóa; MySQL xác nhận collation và unique đúng thiết kế.
- [x] MySQL phân biệt các key `booking`, `Booking` và `booking `.

Các bài kiểm tra thuộc [test_model_types.py](../../tests/backend/unit/test_model_types.py)
và [test_mysql_models.py](../../tests/backend/integration/test_mysql_models.py), đã đạt trong
bộ kiểm tra ghi tại [Viết và chạy tests cho models](model-tests.md).

Thay đổi được bàn giao qua nhánh `feature/database-models`; PR chưa được tạo hoặc merge.
