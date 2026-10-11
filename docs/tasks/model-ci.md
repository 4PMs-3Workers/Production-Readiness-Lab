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

- Tên workflow: `Backend models`; runner `ubuntu-24.04` để tránh thay đổi hệ điều hành
  khi GitHub chuyển nhãn `ubuntu-latest` sang Ubuntu 26.04.
- Kích hoạt khi mở PR tới `develop` hoặc `main`, và khi push các nhánh
  `feature/**`, `fixbug/**`, `hotfix/**`.
- Quyền workflow giới hạn ở `contents: read`.
- Service MySQL dùng image `mysql:8.4.11`, mật khẩu dành riêng cho tests,
  kiểm tra sẵn sàng bằng `mysqladmin ping`.
- Thiết lập Python 3.11, Node.js 22 và cache pip.
- Dùng `actions/checkout@v7`, `actions/setup-python@v7` và `actions/setup-node@v7`.
  Các Actions này chạy bằng Node.js 24, thay cho các bản dùng Node.js 20 đã bị ngừng hỗ trợ.
  Node.js 22 cài cho Pyright là runtime của công cụ trong job, khác với runtime nội bộ của Actions.
- Tắt cache npm tự động vì workflow chỉ cài Pyright global, không dùng lockfile npm của dự án.
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
- [x] CI của [PR #2](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/2)
  được xác nhận thành công ở commit `6ce0302`:
  [Backend models — run 38115510161](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38115510161).

Lần chạy trên GitHub đã xác nhận runner Ubuntu, service MySQL 8.4.11, cài dependencies,
Black, Ruff, Pyright và toàn bộ tests đều thành công. Đây là CI của lần push trên nhánh
`feature/database-models`. PR #2 đã được mở từ `feature/database-models` vào `develop`;
review/approve và merge chưa hoàn thành.

Các cấu hình runtime được đối chiếu với `action.yml` chính thức của
[checkout](https://github.com/actions/checkout/blob/v7/action.yml),
[setup-python](https://github.com/actions/setup-python/blob/v7/action.yml) và
[setup-node](https://github.com/actions/setup-node/blob/v7/action.yml).
Runner được đối chiếu với [danh sách image của GitHub](https://github.com/actions/runner-images).
