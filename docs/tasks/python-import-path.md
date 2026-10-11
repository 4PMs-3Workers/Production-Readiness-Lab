# Cấu hình đường dẫn import Python cho editor

**Ngày thực hiện:** 11/10/2026

**Nhánh:** `feature/database-models`

**Trạng thái:** Đã sửa cấu hình và kiểm tra local

## Mục tiêu

Xử lý cảnh báo tại `from app.models import Base` khi editor phân tích các file tests
trong workspace gốc của repository.

## Nguyên nhân xác định trong cấu hình

Package `app` nằm trong `src/backend/app`. Trước khi sửa, `src/backend` chỉ được khai báo
trong `pythonpath` của pytest, nên tests tìm được package khi chạy nhưng editor chưa có
cấu hình đường dẫn tương ứng. Python chạy trực tiếp từ thư mục gốc cũng chưa tìm thấy
package này nếu chưa cài backend hoặc thiết lập đường dẫn import.

## File đã cập nhật

| File | Thay đổi |
|---|---|
| [pyproject.toml](../../pyproject.toml) | Thêm `tool.pyright`, khai báo `extraPaths = ["src/backend"]`, phạm vi source/tests, `.venv` và phiên bản Python 3.11. |
| [.vscode/settings.json](../../.vscode/settings.json) | Đặt interpreter mặc định của workspace tại `${workspaceFolder}/.venv`. |
| [test_model_schema.py](../../tests/backend/unit/test_model_schema.py) | Kiểm tra tên constraint là chuỗi trước khi gọi `len`; kiểm tra `computed` khác `None` trước khi truy cập `persisted`. |
| [database-models.md](../architecture/database-models.md) | Bổ sung hướng dẫn cấu hình editor và cài backend dạng editable. |

Cấu hình import được sửa tại nguồn; không tắt cảnh báo hoặc thêm `type: ignore` để che lỗi.

## Kết quả kiểm tra

- [x] Theo đường dẫn đã khai báo, `from app.models import Base` import thành công và
  đăng ký đủ 11 bảng.
- [x] Pyright 1.1.407 kiểm tra `test_model_schema.py` và `integration/conftest.py`:
  **0 errors, 0 warnings**.
- [x] Chạy lại bộ unit tests: **25 đạt**.
- [x] Black và Ruff kiểm tra file test đã sửa: đạt.
- [x] Cấu hình TOML và JSON đọc thành công.

Ở lần xử lý cảnh báo import, không chạy lại integration tests vì models, driver và cấu hình
kết nối MySQL không thay đổi. Lần rà soát toàn bộ sau đó đã chạy đủ 70 tests và Pyright trên
toàn bộ source/tests, đạt 0 lỗi, 0 cảnh báo; xem [báo cáo rà soát models](model-review.md).
Chưa kiểm tra trực tiếp trạng thái gạch vàng trong giao diện editor của người dùng.

## Sử dụng trong editor

Mở thư mục gốc repo. Nếu VS Code đã chọn một interpreter khác, dùng
`Python: Select Interpreter` để chọn Python trong `.venv`, rồi tải lại cửa sổ nếu cần.
Pylance sử dụng interpreter đang được chọn trong VS Code; thiết lập mặc định không thay thế
lựa chọn interpreter đã lưu trước đó.

Để import `app` bằng Python thông thường từ thư mục gốc, cài backend vào môi trường đang
dùng bằng `python -m pip install -e . --no-deps` sau khi đã cài dependencies.

Tham khảo [cấu hình Pyright](https://github.com/microsoft/pyright/blob/main/docs/configuration.md)
và [cấu hình Python của VS Code](https://code.visualstudio.com/docs/python/settings-reference).
Thay đổi được bàn giao qua nhánh `feature/database-models`; PR chưa được tạo hoặc merge.
