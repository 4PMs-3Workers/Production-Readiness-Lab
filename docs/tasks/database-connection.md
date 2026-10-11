# Cấu hình kết nối MySQL cho backend

**Ngày thực hiện:** 11/10/2026

**Nhánh:** `feature/database-models`

**Trạng thái:** Đã triển khai và kiểm tra local

## Mục tiêu

Cung cấp hàm tạo async engine dùng chung, bảo đảm các kết nối MySQL tuân theo quy ước
thời gian, SQL mode và isolation level của thiết kế hệ thống.

## File triển khai

- [database.py](../../src/backend/app/core/database.py): `create_database_engine`.
- [requirements.txt](../../requirements.txt): dependencies runtime và các phiên bản được cố định.
- [pyproject.toml](../../pyproject.toml): thông tin package backend và dependencies trực tiếp.

## Nội dung triển khai

- Chỉ nhận URL dùng `mysql+asyncmy`.
- Dùng SQLAlchemy async engine với `READ COMMITTED` và `pool_pre_ping`.
- Mỗi kết nối mới đặt charset `utf8mb4`, `time_zone = '+00:00'` và SQL mode gồm
  `ONLY_FULL_GROUP_BY`, `STRICT_TRANS_TABLES`, `NO_ZERO_DATE`, `NO_ZERO_IN_DATE`,
  `ERROR_FOR_DIVISION_BY_ZERO`, `NO_ENGINE_SUBSTITUTION`.
- Áp dụng cấu hình khởi tạo cho cả các kết nối được mở lại.
- Cho phép truyền TLS/timeout qua `connect_args`, đồng thời giữ các quy tắc charset,
  UTC và SQL mode của hàm tạo engine.
- Cố định SQLAlchemy 2.0.44 và asyncmy 0.2.10; bổ sung cryptography 46.0.3 để hỗ trợ
  xác thực RSA với `caching_sha2_password` của MySQL 8.4 khi không dùng TLS.

## Kết quả kiểm tra

- [x] Tạo được engine và kết nối MySQL 8.4.11 từ môi trường Ubuntu WSL/Python 3.11.
- [x] Truy vấn xác nhận `@@session.time_zone = '+00:00'`.
- [x] Truy vấn xác nhận `@@session.transaction_isolation = 'READ-COMMITTED'`.
- [x] Truy vấn xác nhận các SQL mode nghiêm ngặt cần thiết.
- [x] Các lần mở kết nối mới đều nhận cấu hình trên.
- [x] Engine được dùng thành công trong bộ 43 trường hợp integration tests ở lần rà soát cuối.

Kiểm thử kết nối nằm trong
[test_mysql_models.py](../../tests/backend/integration/test_mysql_models.py).
Fixture dùng `NullPool` để xác nhận cấu hình trên các kết nối mới và đặt connect timeout 5 giây.

## Phạm vi bàn giao

Task này tạo hàm kết nối dùng chung, chưa triển khai vòng đời session của API/worker.
Service sẽ quản lý session, khóa giao dịch và commit/rollback cho từng thao tác nghiệp vụ.
Database production, quyền tài khoản runtime và migration chưa được tạo trong task này.

Thay đổi được bàn giao qua nhánh `feature/database-models`; PR chưa được tạo hoặc merge.
