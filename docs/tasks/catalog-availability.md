# Bàn giao catalog và availability — Issue #13

Tài liệu này mô tả ba API đọc được thêm cho Issue #13, cách gọi bằng session đã được cấp,
các quy tắc nghiệp vụ và bằng chứng kiểm thử. API chỉ cung cấp dữ liệu để sinh viên chọn
nhu cầu, nhân viên và xem giờ phù hợp; API không tạo hoặc giữ lịch.

## Điều kiện dữ liệu và cấu hình

Chạy backend sau khi cài `requirements-dev.txt`, cài package dạng editable và nâng database
MySQL 8.4 riêng lên Alembic head theo [hướng dẫn migration](database-migrations.md).
Tài khoản gọi API phải là `STUDENT` đang hoạt động và đã được cấp bởi môi trường triển khai.
Không có tài khoản hoặc seed demo mặc định. GET dùng session cookie `prl_session`; gọi login
cần Origin trùng `APPLICATION_ORIGIN`. GET không cần CSRF/Origin.

Availability đọc singleton `service_settings` cùng `service_hours`, purpose, quan hệ staff,
unavailability và lịch đã có. Giá trị khởi tạo được mong đợi là `Asia/Ho_Chi_Minh`, bước giờ
15 phút và horizon 7 ngày, nhưng các giá trị chạy thực tế lấy từ database. API không tự tạo
singleton và không thay dữ liệu thiếu bằng default. Thiếu/sai timezone, step, horizon hoặc
giờ phục vụ trả `503 SERVICE_UNAVAILABLE`; không được diễn giải lỗi cấu hình/database thành
một danh sách slot rỗng. Database test phải là schema riêng được tạo qua fixture Alembic;
không trỏ `MYSQL_TEST_URL` vào dữ liệu demo hoặc database dùng chung.

## Hợp đồng HTTP

Mọi đường dẫn dưới đây có tiền tố `/api/v1`, yêu cầu session hợp lệ với vai trò `STUDENT`.
ID sinh viên luôn lấy từ session, không nhận `student_id` từ query.
Hai route phân trang đếm `total` trên toàn bộ tập đã lọc trước khi áp page; trang vượt dữ liệu
giữ nguyên metadata và trả `items: []`.

| Method và path | Query | Kết quả thành công |
|---|---|---|
| `GET /purposes` | `page` mặc định 1; `page_size` mặc định 20, từ 1 đến 100 | `PageResponse[PurposeItem]`; item có `id`, `code`, `name`, `duration_minutes`, `requires_free_text`. Chỉ purpose hoạt động, sắp theo `(code, id)`. |
| `GET /staff` | `purpose_id` bắt buộc, cùng `page`, `page_size` | Cùng envelope phân trang; item chỉ có `id`, `name`. Chỉ staff hoạt động, đang nhận đúng purpose, sắp theo `(name, id)`. |
| `GET /availability` | `purpose_id`, `staff_id`, `date` bắt buộc (`YYYY-MM-DD`) | `{date, timezone, slots}`; mỗi slot chỉ có `starts_at`, `ends_at`. |

Query thừa hoặc lặp, UUID sai và ngày không đúng chuỗi ASCII `YYYY-MM-DD` trả
`422 VALIDATION_ERROR`. Catalog trả `404 NOT_FOUND` cho purpose không hoạt động/không tồn tại
hoặc staff không đủ điều kiện. Trang ngoài dữ liệu là `200` với `items: []` và giữ nguyên
metadata; purpose hợp lệ chưa có staff cũng trả danh sách rỗng. Ngày ngoài booking horizon
trả `422 VALIDATION_ERROR`. Lỗi dịch vụ hoặc cấu hình trả `503 SERVICE_UNAVAILABLE`; lỗi
không dự kiến trả `500 INTERNAL_SERVER_ERROR`. Phiên không hợp lệ trả `401 INVALID_SESSION`,
vai trò khác trả `403 FORBIDDEN_ROLE`.

Response của ba path (kể cả slash variant, lỗi và redirect) có `Cache-Control: private,
no-store` và `X-Request-ID`. Error envelope giữ `error.code`, `error.message` và
`error.request_id`; lỗi còn có `X-Error-Class`. Nội dung SQL, stack trace, thông tin session,
mật khẩu và nội dung tư vấn không được đưa vào response/log.

## Quy tắc tính giờ

- Ngày hiện tại `D` được tính từ clock một lần trong timezone cấu hình DB. Chỉ nhận ngày từ
  `D+1` đến `D+booking_horizon_days`, tính cả hai đầu. Ngày dịch vụ không có window trả slot rỗng.
- Biên ngày được dựng từ nửa đêm local của ngày chọn và ngày tiếp theo, rồi đổi từng biên sang
  UTC. Response timestamp có timezone, chuẩn hóa UTC với `Z` và giữ microsecond.
- Start đầu tiên được neo vào `opens_at` của từng service window; các start kế tiếp tăng theo
  `start_step_minutes`, giữ phần giây có trong giờ cấu hình. Toàn bộ buổi gặp phải vừa trong
  một window; hai window tiếp giáp không được gộp. Duration lấy từ purpose đang hoạt động.
- Khoảng là nửa mở `[starts_at, ends_at)`. Slot bị loại khi
  `slot.start < busy.end and slot.end > busy.start`; hai khoảng chỉ tiếp giáp không xung đột.
  Chỉ `UPCOMING` và `CHANGE_PENDING` chiếm chỗ. Lịch gốc của `CHANGE_PENDING` vẫn chiếm chỗ;
  giờ được đề nghị trong change proposal chưa chiếm chỗ, kể cả đề nghị đã quá hạn nhưng worker
  chưa xử lý.
- Busy gồm unavailability của staff được chọn, lịch đang chiếm chỗ của staff đó và lịch đang
  chiếm chỗ của sinh viên trong session, bất kể purpose/staff của lịch hiện hữu. Không áp quota
  ba lịch trong GET availability.
- Availability chạy một transaction đọc với deadline, tối đa 7 SELECT nghiệp vụ (settings,
  purpose, staff eligibility, windows, unavailability, lịch staff, lịch sinh viên). Lịch chỉ
  chiếu hai cột thời gian cần thiết; không query theo từng slot, không DML, không locking read.
  Transaction được rollback sau khi đọc. GET chỉ là preview: booking sau này phải xác minh lại
  quyền, quota và xung đột trong transaction trước khi ghi, vì dữ liệu có thể đổi sau response.

## Cách gọi

README có script tương tác dùng `getpass`, HTTPX cookie jar trong bộ nhớ và ID dữ liệu trả về
từ API; không cần tài khoản của issue #8 và không in cookie/mật khẩu. Script là ví dụ sử dụng,
không phải bằng chứng smoke test của môi trường MySQL hiện tại. Kiểm tra điều kiện môi trường,
cấu hình Origin, chọn một purpose/staff thực tế và một ngày trong horizon trước khi chạy.

## Ma trận kiểm chứng

| Yêu cầu | Bằng chứng trong repository |
|---|---|
| Schema, query chính xác, phân trang, trường public | `tests/backend/unit/test_catalog_schemas.py`; `tests/backend/integration/test_catalog_endpoints.py`; `tests/backend/unit/test_openapi_export.py`. |
| Neo start, duration, window/break, overlap nửa mở, microsecond, timezone, DST, horizon, ngày biên | `tests/backend/unit/test_availability_rules.py`. |
| Catalog paging/repository kể cả trang cực lớn không overflow OFFSET | `tests/backend/unit/test_catalog_repository.py`; MySQL contract trong `tests/backend/integration/test_catalog_endpoints.py`. |
| Chính sách cache/request ID, root path, slash, auth/method/error, sanitize/deadline | `tests/backend/integration/test_catalog_http.py`; `tests/backend/integration/test_availability_endpoints.py`. |
| Truy vấn busy cắt qua ngày, status/identity scope, config/window, projection riêng tư, query budget/read-only và snapshot bảng | `tests/backend/integration/test_availability_queries.py`; `tests/backend/integration/test_availability_service.py`. |
| Session-derived student, response đầy đủ, lỗi, clock và GET không ghi | `tests/backend/integration/test_availability_endpoints.py`. |
| MySQL thật và migration isolation | Các integration module ở trên dùng marker `mysql`, schema riêng qua `tests/backend/integration/conftest.py` và Alembic. |
| Contract export | `tests/backend/unit/test_openapi_export.py`, `docs/api/openapi.yaml` và `.github/workflows/backend-models.yml`. |

## Kết quả và giới hạn môi trường

Kết quả local khi triển khai ngày 16/10/2026 được giữ làm mốc, ghi riêng test chạy thật,
test bị skip và bước chưa thể chạy. Test bị skip không được tính là pass. Bằng chứng CI
MySQL sau khi tạo PR được ghi ở phần tiếp theo.

| Kiểm tra | Lệnh/phạm vi | Kết quả |
|---|---|---|
| Dependencies | `.venv\Scripts\python.exe -m pip check` | Pass: `No broken requirements found.` |
| Black | Chạy `.venv\Scripts\python.exe -m black --check <file>` riêng cho từng file trong `src/backend`, `tests/backend`, `tools/scripts` | Pass cho 91 file. CLI một lệnh trên toàn bộ nhiều path treo trên host Windows; per-file kiểm tra cùng Black formatter thay vì bỏ qua file. |
| Ruff | `.venv\Scripts\python.exe -m ruff check src/backend tests/backend tools/scripts/export_openapi.py` | Pass: `All checks passed!` |
| Pyright | `.venv\Scripts\python.exe -m pyright` | Pass: 0 errors, 0 warnings, 0 informations. |
| Backend pytest | `.venv\Scripts\python.exe -m pytest tests/backend -q --junitxml=build/backend-tests.xml` | 290 passed, 141 skipped, 0 failed, 0 errors, 1 deprecation warning. Cả 141 skip đều ghi `Set MYSQL_TEST_URL to run tests against MySQL 8.4`; chúng không phải test pass. |
| OpenAPI | `.venv\Scripts\python.exe tools/scripts/export_openapi.py --output build/openapi-verified.yaml` rồi so sánh hash với `docs/api/openapi.yaml` | Pass: hai file byte-identical; OpenAPI artifact không cần cập nhật. |
| README script | Biên dịch cú pháp snippet HTTPX bằng Python `compile(...)` | Pass cú pháp; chưa gọi HTTP thật vì host thiếu MySQL/session fixture. |
| Whitespace | `git diff --check` | Pass. |
| Alembic smoke | Yêu cầu MySQL disposable riêng | Chưa chạy vì `MYSQL_TEST_URL` không được đặt và host không có MySQL listener. |
| GitHub Actions | `.github/workflows/backend-models.yml` | Đã kiểm tra workflow giữ MySQL `8.4.11`, Alembic smoke, full pytest/JUnit zero-skips và OpenAPI consistency; job không được chạy trong lượt local này. |

Các kết quả local trên không xác nhận MySQL integration hoặc migration trên schema disposable;
hai phần này đã được kiểm chứng riêng qua CI bên dưới. Smoke bằng account triển khai, SLO và
EXPLAIN trên dữ liệu production-like chưa được chạy trong lượt bàn giao này.

## Bằng chứng CI và review PR

[PR #16](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/16) đưa nhánh
`feature/catalog-availability` vào `develop`, thực hiện
[issue #13](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/13).
Review ngày 16/10/2026 đã đối chiếu diff với issue và các tài liệu thiết kế, chưa phát hiện
lỗi cần chặn merge tại head `ba1ca2880e302051ef56cf66325450912feda5a6`.

[Backend CI job 116793000951](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38844870700/job/116793000951)
đã thành công ngày 16/10/2026. Log xác nhận checkout merge thử `69aa64f` của head `ba1ca28`
vào base `b99a2b2` và ghi nhận:

- MySQL **8.4.11**, integration tests dùng schema riêng khởi tạo qua Alembic.
- Toàn bộ backend: **431 passed, 0 skipped**; bước kiểm tra JUnit không phát hiện test skip.
- Black: 91 file không cần đổi; Ruff đạt; Pyright: 0 errors, 0 warnings.
- Alembic `upgrade head`, `current`, `heads`, rồi `upgrade head` lần hai đều đạt;
  revision duy nhất là `a1b2c3d4e5f6`.
- Xuất OpenAPI và `git diff --exit-code -- docs/api/openapi.yaml` đều đạt.

[Frontend CI job 116793001908](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38844870691/job/116793001908)
cũng thành công trên cùng head. Các liên kết và kết quả trên là bằng chứng cho phiên bản
được nêu; commit bổ sung phải được kiểm tra bằng CI của chính commit đó trước khi merge.

Workflow `.github/workflows/backend-models.yml` hiện khởi động MySQL `8.4.11`, cài dependencies,
chạy Black/Ruff/Pyright, Alembic upgrade/current/heads/upgrade, toàn bộ backend pytest rồi thất
bại nếu JUnit có bất kỳ test skip nào. Sau đó workflow export OpenAPI và yêu cầu artifact không
đổi. Bằng chứng MySQL zero-skip, Alembic smoke và OpenAPI consistency của PR đã có ở trên.

## Trạng thái bàn giao

- Thay đổi được bàn giao qua PR #16 từ `feature/catalog-availability` vào `develop`.
- Review và CI của phiên bản `ba1ca28` đã hoàn tất như phần bằng chứng ở trên.
- Trước khi merge, xác nhận approval và toàn bộ checks trên head mới nhất của PR.
- Chỉ đóng issue #13 sau khi PR được merge và các tiêu chí nghiệm thu đã đạt; nếu merge vào
  `develop` không tự đóng issue thì đóng rõ ràng với trạng thái completed.
