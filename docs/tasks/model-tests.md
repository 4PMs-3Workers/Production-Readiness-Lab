# Viết và chạy tests cho database models

**Ngày thực hiện:** 11/10/2026

**Nhánh:** `feature/database-models`

**Trạng thái:** Hoàn thành kiểm tra local

## Mục tiêu

Kiểm chứng models, bộ chuyển đổi kiểu và các ràng buộc dữ liệu bằng unit tests và
integration tests trên MySQL 8.4 thật theo quy ước dự án.

## File triển khai

| File | Nội dung |
|---|---|
| [test_model_types.py](../../tests/backend/unit/test_model_types.py) | UUID, UTC, giờ trong ngày, SHA-256 và chuẩn hóa email. |
| [test_model_schema.py](../../tests/backend/unit/test_model_schema.py) | Đủ bảng, cột/kiểu/NULL theo từ điển dữ liệu, collation, mapper, DDL MySQL, FK ghép, tên ràng buộc và generated column. |
| [conftest.py](../../tests/backend/integration/conftest.py) | Tạo schema test riêng, tạo bảng, kiểm tra phiên bản MySQL và dọn schema. |
| [test_mysql_models.py](../../tests/backend/integration/test_mysql_models.py) | Ghi/đọc models và kiểm tra ràng buộc trên database thật. |
| [pyproject.toml](../../pyproject.toml) | Cấu hình pytest, pytest-asyncio, Black và Ruff. |
| [requirements-dev.txt](../../requirements-dev.txt) | Dependencies phục vụ kiểm thử và kiểm tra chất lượng. |

## Các tình huống đã kiểm tra

- Đăng ký đủ 11 bảng và ghi/đọc lại dữ liệu của tất cả models.
- Đối chiếu tự động cột, kiểu dữ liệu và NULL với từ điển dữ liệu trong thiết kế database;
  kiểm tra collation của email và các mã nghiệp vụ.
- UUID theo thứ tự byte chuẩn, datetime UTC giữ microsecond và giờ trong ngày.
- Giá trị mặc định, cột generated và việc đọc chúng sau `flush` không gây I/O ngầm.
- UTC, isolation level và SQL mode trên các kết nối mới.
- Ghi SQL trực tiếp với dữ liệu sai: vai trò/trạng thái, cờ boolean, hash, khoảng giờ,
  thời lượng, mã yêu cầu và timestamp theo trạng thái.
- FK ghép nhân viên–nhu cầu, FK tới tài khoản và giới hạn xóa dữ liệu được tham chiếu.
- Unique/collation của idempotency key và email.
- Truy vấn email bằng SQL trực tiếp để kiểm tra không phân biệt hoa/thường tại database.
- SQL mode nghiêm ngặt từ chối ngày bằng không và ngày không tồn tại với lỗi MySQL `1292`.
- Chỉ một đề nghị `PENDING`, đồng thời giữ nhiều đề nghị đã kết thúc.
- JSON audit mặc định độc lập giữa các bản ghi và không nhận SQL NULL.
- Backend cập nhật `updated_at` khi sửa dữ liệu qua ORM.

## Tạo database và bảng phục vụ tests

Fixture tạo schema ngẫu nhiên `prl_models_test_*` cho từng test và gọi
`Base.metadata.create_all` để tạo các bảng/chỉ mục. Sau test, fixture xóa đúng schema đó.
Tài khoản test cần quyền CREATE/DROP DATABASE; các database có sẵn không bị thay đổi.

Đã tạo và kiểm tra thành công 11 bảng trên MySQL thật. Việc tạo schema ở đây chỉ phục vụ
kiểm thử; chưa tạo database production hoặc triển khai migration Alembic.

Nếu thiếu `MYSQL_TEST_URL`, integration tests được bỏ qua. Nếu có URL nhưng kết nối lỗi
hoặc MySQL sai phiên bản, tests thất bại. Lần xác nhận cuối đã chạy đầy đủ, không bỏ qua test.

## Môi trường và kết quả

Môi trường: Ubuntu WSL, Python 3.11.17, SQLAlchemy 2.0.44, asyncmy 0.2.10,
MySQL 8.4.11/InnoDB.

| Hạng mục | Kết quả |
|---|---|
| Unit tests | 27 đạt. |
| Integration tests | 43 đạt trên MySQL thật. |
| Tổng tests | 70 đạt; 0 lỗi, 0 thất bại, 0 bỏ qua. |
| Black | Đạt; 21 file Python không cần định dạng lại ở lần kiểm tra cuối. |
| Ruff | Đạt. |
| Pyright 1.1.407 | Kiểm tra toàn bộ source/tests: 0 lỗi, 0 cảnh báo. |
| Dọn môi trường test | Schema tạm đã được xóa; MySQL riêng cho tests đã dừng. |

Báo cáo JUnit local `build/model-review-tests.xml` là file tạm, đã được dọn trước commit.
Có thể tạo lại bằng lệnh xác nhận bên dưới.
Các bản sửa sau rà soát được ghi trong [báo cáo rà soát models](model-review.md).

Docker Desktop lỗi khởi tạo `dockerInference`; Application Control của Windows chặn
một DLL của asyncmy. Bộ kiểm tra đầy đủ đã chạy trong WSL, kết nối đến MySQL 8.4.11
bản ZIP chính thức dành riêng cho tests. Task này không sửa Docker Desktop hoặc chính sách Windows.

## Lệnh xác nhận

Sau khi cài dependencies và đặt `MYSQL_TEST_URL` tới MySQL 8.4 dành cho tests:

```bash
python -m black --check src/backend tests/backend
python -m ruff check src/backend tests/backend
pyright
python -m pytest -q --tb=short -p no:cacheprovider --junitxml=build/model-review-tests.xml
```

Hướng dẫn dựng môi trường nằm trong [tài liệu models](../architecture/database-models.md).
Kết quả trong bảng là kiểm tra local. Bộ kiểm tra cũng đã chạy thành công trên GitHub Actions
ở commit `62acd33`; xem [kết quả workflow](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38112810486).
Thay đổi được bàn giao qua nhánh `feature/database-models`; PR chưa được tạo hoặc merge.
