# Tạo models cho toàn bộ database

**Ngày thực hiện:** 11/10/2026

**Nhánh:** `feature/database-models`

**Trạng thái:** Đã triển khai và kiểm tra local

## Mục tiêu

Tạo SQLAlchemy models cho đầy đủ các bảng trong [thiết kế database phiên bản 1.3](../architecture/database-design.md),
tuân theo [CONVENTION](../other/CONVENTION.md) và cấu trúc `src/backend/app/models`.

## Models đã tạo

| Bảng | Model | File |
|---|---|---|
| `users` | `User` | [user.py](../../src/backend/app/models/user.py) |
| `sessions` | `Session` | [session.py](../../src/backend/app/models/session.py) |
| `purposes` | `Purpose` | [purpose.py](../../src/backend/app/models/purpose.py) |
| `staff_purposes` | `StaffPurpose` | [staff_purpose.py](../../src/backend/app/models/staff_purpose.py) |
| `service_settings` | `ServiceSetting` | [service_setting.py](../../src/backend/app/models/service_setting.py) |
| `service_hours` | `ServiceHour` | [service_hour.py](../../src/backend/app/models/service_hour.py) |
| `staff_unavailability` | `StaffUnavailability` | [staff_unavailability.py](../../src/backend/app/models/staff_unavailability.py) |
| `appointments` | `Appointment` | [appointment.py](../../src/backend/app/models/appointment.py) |
| `change_proposals` | `ChangeProposal` | [change_proposal.py](../../src/backend/app/models/change_proposal.py) |
| `notifications` | `Notification` | [notification.py](../../src/backend/app/models/notification.py) |
| `audit_events` | `AuditEvent` | [audit_event.py](../../src/backend/app/models/audit_event.py) |

Đã tạo đủ **11/11 bảng**; không có bảng nguồn nào bị loại khỏi phạm vi.

## Nội dung triển khai

- Dùng SQLAlchemy 2 với `Mapped`/`mapped_column`; tên lớp theo `PascalCase`, tên module,
  bảng và cột theo `snake_case`.
- Tạo [Base và mixin](../../src/backend/app/models/base.py) cho khóa UUID, `created_at`,
  `updated_at`; đăng ký toàn bộ models qua [models/__init__.py](../../src/backend/app/models/__init__.py).
- Dùng InnoDB, charset `utf8mb4`, collation mặc định `utf8mb4_0900_ai_ci`.
- Khai báo PK, FK, CHECK, UNIQUE và chỉ mục có tên theo thiết kế. FK dùng `RESTRICT`
  cho cả xóa và cập nhật.
- Lịch hẹn tham chiếu đúng cặp nhân viên–nhu cầu bằng FK ghép tới `staff_purposes`.
- Unique `(student_id, creation_request_key)` bảo vệ mã yêu cầu tạo lịch.
- Generated column virtual `pending_guard` và unique `(appointment_id, pending_guard)`
  bảo đảm tối đa một đề nghị `PENDING`, giữ được nhiều đề nghị đã kết thúc.
- Ánh xạ cột SQL `audit_events.metadata` thành `AuditEvent.event_metadata` vì SQLAlchemy
  dành riêng tên thuộc tính `metadata`; mặc định tạo dictionary mới cho từng bản ghi.
- Bật `eager_defaults` để tải các giá trị mặc định/tự sinh trong `flush`, tránh I/O ngầm
  khi đọc `created_at` hoặc `pending_guard` qua `AsyncSession` trên MySQL.

## Kết quả kiểm tra

- [x] Đăng ký được tất cả mapper và sinh DDL theo dialect MySQL.
- [x] Tạo thành công 11 bảng cùng các chỉ mục trên MySQL 8.4.11/InnoDB thật.
- [x] Ghi và đọc lại dữ liệu của cả 11 models; xác nhận giá trị mặc định và ràng buộc.
- [x] Black và Ruff đạt.

Chi tiết kiểm thử nằm tại [Viết và chạy tests cho models](model-tests.md).

## Phạm vi bàn giao

Models cung cấp lớp ánh xạ lưu trữ. Chống lịch giao khoảng, hạn mức ba lịch, quyền sở hữu,
vai trò, chuyển trạng thái, hạn đặt/hủy và audit/thông báo cùng giao dịch thuộc tầng service.
Migration Alembic, seed và tạo database production chưa được triển khai trong task này.

Thay đổi được bàn giao qua nhánh `feature/database-models` theo GITFLOW, với các commit
tách theo chức năng. Tiêu chí tạo PR, review/approve và merge chưa hoàn thành.
