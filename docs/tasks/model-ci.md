# Cấu hình CI kiểm tra database models

**Ngày thực hiện:** 11/10/2026

**Nhánh:** `feature/database-models`

**Trạng thái:** Đã cấu hình và chạy thành công trên GitHub Actions

## Mục tiêu

Tự động kiểm tra chất lượng mã và chạy toàn bộ tests của backend models khi push nhánh
triển khai hoặc mở Pull Request.

## File triển khai

[.github/workflows/backend-models.yml](../../.github/workflows/backend-models.yml)

## Cấu hình đã thêm

- Tên workflow: `Backend models`; runner `ubuntu-latest`.
- Kích hoạt khi mở PR tới `develop` hoặc `main`, và khi push các nhánh
  `feature/**`, `fixbug/**`, `hotfix/**`.
- Quyền workflow giới hạn ở `contents: read`.
- Service MySQL dùng image `mysql:8.4.11`, mật khẩu dành riêng cho tests,
  kiểm tra sẵn sàng bằng `mysqladmin ping`.
- Thiết lập Python 3.11, Node.js 22 và cache pip.
- Tạo `.venv`, cài dependencies từ `requirements-dev.txt` vào môi trường này để
  Pyright dùng cùng dependencies với tests.
- Cài Pyright 1.1.407 bằng npm.
- Cấu hình `MYSQL_TEST_URL` cho integration tests để không bỏ qua phần kiểm tra MySQL.
- Chạy lần lượt Black, Ruff, Pyright trên toàn bộ source/tests và `pytest -q`.

## Trạng thái kiểm chứng

- [x] File workflow đã được tạo và đối chiếu với các lệnh kiểm tra của dự án.
- [x] Các lệnh Black/Ruff và bộ tests dùng trong workflow đã đạt ở môi trường local
  Ubuntu WSL/Python 3.11 với MySQL 8.4.11; xem [Viết và chạy tests cho models](model-tests.md).
- [x] Pyright 1.1.407 kiểm tra toàn bộ source/tests ở môi trường local Windows:
  0 lỗi, 0 cảnh báo.
- [x] Workflow được thực thi thành công trên GitHub Actions ở commit `62acd33`:
  [Backend models — run 38112810486](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38112810486).
- [ ] Kết quả CI của PR được xác nhận thành công.

Lần chạy trên GitHub đã xác nhận runner Ubuntu, service MySQL 8.4.11, cài dependencies,
Black, Ruff, Pyright và toàn bộ tests đều thành công. Đây là CI của lần push trên nhánh
`feature/database-models`; chưa có PR để xác nhận review/approve hoặc merge.
