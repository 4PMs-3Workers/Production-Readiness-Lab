# Tạo SQL models cho toàn bộ database

- **Ngày triển khai và kiểm chứng:** 11/10/2026.
- **Ngày cập nhật tài liệu:** 12/10/2026.
- **Nhánh:** `feature/database-models`.
- **Issue:** [#1 — Tạo SQL models cho toàn bộ database](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/1).
- **Pull Request:** [#2 — Thêm SQLAlchemy models cho đủ 11 bảng](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/2).

Tài liệu này tổng hợp toàn bộ phần việc đã thực hiện cho task tạo SQL models: ánh xạ bảng,
chuyển đổi kiểu dữ liệu, kết nối MySQL, kiểm thử, CI, cấu hình import và rà soát thiết kế.
Hướng dẫn sử dụng và dựng môi trường nằm trong
[tài liệu models](../architecture/database-models.md).

## Mục tiêu và phạm vi

- Liệt kê và tạo models cho toàn bộ bảng trong database.
- Tuân theo tên, kiểu dữ liệu, ràng buộc và chỉ mục của
  [database-design.md v1.3](../architecture/database-design.md).
- Tổ chức code theo [CONVENTION](../other/CONVENTION.md), đối chiếu trách nhiệm tầng dữ liệu
  với [system-design.md](../architecture/system-design.md).
- Thêm kiểm thử chất lượng dữ liệu, chạy trên MySQL thật và cập nhật tài liệu.
- Bàn giao qua nhánh feature và PR vào `develop` theo [GITFLOW](../other/GITFLOW.md).

Đã triển khai và kiểm chứng **11/11 bảng**; không có bảng nào bị loại khỏi phạm vi.
Tiêu chí hoàn thành còn bao gồm review/approve và merge PR. Trạng thái bàn giao được ghi nhận
ngày 11/10/2026 ở cuối tài liệu; kết quả kiểm thử dưới đây thuộc lần kiểm chứng đó.

## Danh sách bảng và models

| Bảng | Model | File triển khai |
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

## Ánh xạ schema và ràng buộc

- Dùng SQLAlchemy 2 với `Mapped`/`mapped_column`; tên lớp theo `PascalCase`, tên module,
  bảng và cột theo `snake_case`.
- Tạo [Base và mixin](../../src/backend/app/models/base.py) cho UUID, `created_at`,
  `updated_at`; đăng ký toàn bộ models qua
  [models/__init__.py](../../src/backend/app/models/__init__.py).
- Dùng InnoDB, charset `utf8mb4`, collation mặc định `utf8mb4_0900_ai_ci`.
- Khai báo PK, FK, CHECK, UNIQUE có tên và đủ 12 chỉ mục truy vấn theo thiết kế.
  Khóa ngoại dùng `RESTRICT` cho cả xóa và cập nhật.
- `staff_purposes` dùng khóa chính ghép; `appointments` tham chiếu đúng cặp
  `(staff_id, purpose_id)` bằng FK ghép tới bảng này.
- UNIQUE `(student_id, creation_request_key)` bảo vệ mã yêu cầu tạo lịch.
- Generated column virtual `pending_guard` và UNIQUE `(appointment_id, pending_guard)`
  bảo đảm tối đa một đề nghị `PENDING`, đồng thời giữ được nhiều đề nghị đã kết thúc.
- Ánh xạ cột SQL `audit_events.metadata` thành `AuditEvent.event_metadata` vì SQLAlchemy
  dành riêng tên thuộc tính `metadata`. `default=dict` tạo `{}` riêng cho từng bản ghi;
  ràng buộc NOT NULL của cột từ chối SQL NULL.
- Bật `eager_defaults` để tải giá trị mặc định/tự sinh trong `flush`, tránh I/O ngầm
  khi đọc `created_at` hoặc `pending_guard` qua `AsyncSession` trên MySQL.
- Models cung cấp ID khóa ngoại; truy vấn, phép join và giao dịch được thực hiện tường minh
  ở repository/service, không khai báo relationship tự tải dữ liệu ngầm.

## Chuyển đổi và kiểm tra kiểu dữ liệu

Các bộ chuyển đổi dùng chung nằm trong [types.py](../../src/backend/app/models/types.py).

| Bộ chuyển đổi | Kiểu MySQL | Hành vi |
|---|---|---|
| `BinaryUUID` | `BINARY(16)` | Nhận UUID hoặc chuỗi UUID; lưu byte theo thứ tự chuẩn, đọc thành UUID; từ chối đầu vào sai kiểu. |
| `UTCDateTime` | `DATETIME(6)` | Chỉ nhận datetime có múi giờ; chuyển về UTC khi lưu, gắn UTC khi đọc; giữ microsecond. |
| `TimeOfDay` | `TIME(0)` | Nhận giờ trong ngày không có múi giờ/phần lẻ giây; xử lý timedelta của driver và kiểm tra phạm vi. |
| `SHA256Hash` | `VARBINARY(32)` | Nhận đúng 32 byte hash thô; từ chối sai kiểu hoặc độ dài. |

- UUID không dùng cơ chế đảo thứ tự byte `UUID_TO_BIN(..., 1)`.
- Email được chuẩn hóa bằng `strip().lower()` khi gán qua ORM; collation
  `utf8mb4_0900_as_ci` phân biệt dấu, không phân biệt hoa/thường.
  CHECK tại database kiểm tra cả thao tác SQL trực tiếp.
- Mã nghiệp vụ và idempotency key dùng `utf8mb4_0900_bin`. Key do client gửi được giữ nguyên,
  gồm cả hoa/thường và khoảng trắng cuối chuỗi.
- `created_at` có mặc định `CURRENT_TIMESTAMP(6)` tại database. `updated_at` được backend
  gán theo UTC khi insert/update qua ORM; luồng ghi SQL trực tiếp phải tự cập nhật trường này.
- Đã kiểm tra UUID đọc/ghi đúng byte, datetime có offset giữ cùng thời điểm UTC và microsecond,
  từ chối datetime thiếu múi giờ, giờ/hash không hợp lệ và tính phân biệt key
  `booking`, `Booking`, `booking ` trên MySQL.

## Kết nối MySQL và dependencies

Hàm `create_database_engine` nằm trong [database.py](../../src/backend/app/core/database.py).

- Chỉ nhận URL dùng `mysql+asyncmy`.
- Dùng async engine với `READ COMMITTED` và `pool_pre_ping`.
- Mỗi kết nối mới đặt charset `utf8mb4`, `time_zone = '+00:00'` và SQL mode gồm
  `ONLY_FULL_GROUP_BY`, `STRICT_TRANS_TABLES`, `NO_ZERO_DATE`, `NO_ZERO_IN_DATE`,
  `ERROR_FOR_DIVISION_BY_ZERO`, `NO_ENGINE_SUBSTITUTION`.
- Áp dụng cấu hình khởi tạo cho cả các kết nối được mở lại.
- Cho phép truyền TLS/timeout qua `connect_args`, đồng thời giữ các quy tắc charset,
  UTC và SQL mode của hàm tạo engine.
- Pin dependencies trong [requirements.txt](../../requirements.txt) và
  [requirements-dev.txt](../../requirements-dev.txt); khai báo package và công cụ trong
  [pyproject.toml](../../pyproject.toml).
- Dùng SQLAlchemy 2.0.44, asyncmy 0.2.10 và cryptography 46.0.3. Dependency cryptography
  hỗ trợ xác thực RSA với `caching_sha2_password` của MySQL 8.4 khi không dùng TLS.

Integration tests đã truy vấn xác nhận UTC, isolation level và SQL mode trên các kết nối mới.
Fixture dùng `NullPool` để kiểm tra việc khởi tạo từng kết nối và đặt connect timeout 5 giây.
Timeout của fixture là cấu hình kiểm thử; ngân sách pool/timeout vận hành cần được xác định
khi triển khai API và worker.

## Cấu hình import Python cho editor

Package `app` nằm trong `src/backend/app`. Cấu hình `pythonpath` của pytest chỉ giải quyết
import khi chạy tests, nên đã bổ sung đường dẫn cho editor để xử lý cảnh báo tại
`from app.models import Base`.

| File | Thay đổi |
|---|---|
| [pyproject.toml](../../pyproject.toml) | Thêm `tool.pyright`, `extraPaths = ["src/backend"]`, phạm vi source/tests, `.venv` và Python 3.11. |
| [.vscode/settings.json](../../.vscode/settings.json) | Đặt interpreter mặc định tại `${workspaceFolder}/.venv`. |
| [test_model_schema.py](../../tests/backend/unit/test_model_schema.py) | Kiểm tra kiểu tên constraint và `computed` trước khi truy cập các thuộc tính tương ứng. |
| [Tài liệu models](../architecture/database-models.md) | Bổ sung hướng dẫn chọn interpreter và cài backend dạng editable. |

Đã xác nhận import `app.models.Base` từ thư mục gốc sau khi cài backend dạng editable,
đăng ký đủ 11 bảng và Pyright kiểm tra toàn bộ source/tests đạt 0 lỗi, 0 cảnh báo.
Cấu hình được sửa tại nguồn; không tắt cảnh báo hoặc thêm `type: ignore`.

Trong VS Code, mở thư mục gốc repo, dùng `Python: Select Interpreter` để chọn Python
trong `.venv` nếu workspace đang dùng interpreter khác. Thiết lập mặc định không thay thế
lựa chọn interpreter đã lưu trước đó. Chưa kiểm tra trực tiếp trạng thái gạch vàng trong
giao diện editor của người dùng.

Để import `app` bằng Python thông thường từ thư mục gốc, chạy
`python -m pip install -e . --no-deps` sau khi cài dependencies.

## Kiểm thử và chất lượng dữ liệu

| File | Nội dung kiểm tra |
|---|---|
| [test_model_types.py](../../tests/backend/unit/test_model_types.py) | UUID, UTC, giờ trong ngày, SHA-256 và chuẩn hóa email. |
| [test_model_schema.py](../../tests/backend/unit/test_model_schema.py) | Đủ bảng, cột/kiểu/NULL theo từ điển dữ liệu, collation, mapper, DDL MySQL, FK ghép, tên ràng buộc và generated column. |
| [conftest.py](../../tests/backend/integration/conftest.py) | Tạo schema test riêng, tạo bảng, kiểm tra phiên bản MySQL và dọn schema. |
| [test_mysql_models.py](../../tests/backend/integration/test_mysql_models.py) | Ghi/đọc models, cấu hình kết nối và ràng buộc trên MySQL thật. |

Các tình huống đã kiểm tra:

- Đăng ký đủ 11 bảng và ghi/đọc lại dữ liệu của tất cả models.
- Đối chiếu tự động cột, kiểu, NULL và collation với từ điển dữ liệu trong thiết kế database.
- Giá trị mặc định, generated column và việc đọc chúng sau `flush` không gây I/O ngầm.
- UTC, isolation level và SQL mode trên các kết nối mới.
- Ghi SQL trực tiếp với dữ liệu sai: vai trò/trạng thái, cờ boolean, hash, khoảng giờ,
  thời lượng, mã yêu cầu và timestamp theo trạng thái.
- FK ghép nhân viên–nhu cầu, FK tới tài khoản và giới hạn xóa dữ liệu được tham chiếu.
- UNIQUE/collation của idempotency key và email; truy vấn SQL trực tiếp với email viết hoa.
- SQL mode từ chối ngày bằng không và ngày không tồn tại với lỗi MySQL `1292`.
- Chỉ một đề nghị `PENDING`, đồng thời giữ nhiều đề nghị đã kết thúc.
- JSON audit mặc định độc lập giữa các bản ghi, không nhận SQL NULL; backend cập nhật
  `updated_at` khi sửa dữ liệu qua ORM.

### Tạo database và bảng phục vụ tests

Fixture tạo schema ngẫu nhiên `prl_models_test_*` cho từng test và gọi
`Base.metadata.create_all` để tạo 11 bảng cùng chỉ mục. Sau test, fixture xóa đúng schema đó.
Tài khoản test cần quyền CREATE/DROP DATABASE; các database có sẵn không bị thay đổi.
Việc tạo schema này phục vụ kiểm thử; chưa tạo database production hoặc migration Alembic.

Nếu thiếu `MYSQL_TEST_URL`, integration tests được bỏ qua. Nếu có URL nhưng kết nối lỗi
hoặc MySQL sai phiên bản, tests thất bại. Lần kiểm chứng đầy đủ ngày 11/10/2026 không bỏ qua test.

### Kết quả đã kiểm chứng

Môi trường integration: Ubuntu WSL, Python 3.11.17, SQLAlchemy 2.0.44, asyncmy 0.2.10,
MySQL 8.4.11/InnoDB. Pyright chạy trên Windows với `.venv` của workspace.

| Hạng mục | Kết quả |
|---|---|
| Unit tests | 27 đạt. |
| Integration tests | 43 đạt trên MySQL thật; tạo bảng và ghi/đọc đủ 11 models. |
| Tổng tests | 70 đạt; 0 lỗi, 0 thất bại, 0 bỏ qua. |
| Black | Đạt; 21 file Python không cần định dạng lại. |
| Ruff | Đạt. |
| Pyright 1.1.407 | Toàn bộ source/tests: 0 lỗi, 0 cảnh báo. |
| Cài backend dạng editable | Thành công; import `app.models.Base` từ thư mục gốc. |
| Dọn môi trường test | Schema tạm đã xóa; MySQL riêng cho tests đã dừng. |

Docker Desktop lỗi khởi tạo `dockerInference` và Application Control của Windows chặn
một DLL của asyncmy. Bộ kiểm tra đầy đủ đã chạy trong WSL, kết nối tới MySQL 8.4.11
bản ZIP chính thức dành riêng cho tests. Việc sửa Docker Desktop/chính sách Windows
không thuộc phần triển khai models.

Báo cáo JUnit local `build/model-review-tests.xml` là file tạm đã được dọn trước commit.
Có thể tạo lại sau khi cài dependencies và đặt `MYSQL_TEST_URL` tới MySQL 8.4 dành cho tests:

```bash
python -m black --check src/backend tests/backend
python -m ruff check src/backend tests/backend
pyright
python -m pytest -q --tb=short -p no:cacheprovider --junitxml=build/model-review-tests.xml
```

## CI kiểm tra models

Workflow [backend-models.yml](../../.github/workflows/backend-models.yml) tự động chạy
kiểm tra chất lượng mã và toàn bộ tests khi push hoặc mở PR.

- Tên workflow `Backend models`, runner `ubuntu-24.04`, quyền `contents: read`.
- Kích hoạt với PR tới `develop`/`main` và push nhánh `feature/**`, `fixbug/**`, `hotfix/**`.
- Service MySQL dùng image `mysql:8.4.11`, mật khẩu dành riêng cho tests và healthcheck
  bằng `mysqladmin ping`.
- Thiết lập Python 3.11, Node.js 22, cache pip; tạo `.venv` và cài `requirements-dev.txt`.
- Dùng `actions/checkout@v7`, `actions/setup-python@v7`, `actions/setup-node@v7` với runtime
  nội bộ Node.js 24; Node.js 22 là runtime cài cho công cụ Pyright trong job.
- Tắt cache npm tự động vì job chỉ cài Pyright global, không dùng lockfile npm của dự án.
- Cài Pyright 1.1.407, cấu hình `MYSQL_TEST_URL` và chạy Black, Ruff, Pyright, `pytest -q`.

Sau khi cập nhật Actions và pin runner ở commit `e52e05f`, đã xác nhận hai lần chạy ngày
11/10/2026 thành công và không có annotation cảnh báo/thông báo:

| Sự kiện | Kết quả GitHub Actions |
|---|---|
| Push `feature/database-models` | [Run 38116069355 — thành công](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38116069355) |
| Pull Request #2 | [Run 38116072315 — thành công](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38116072315) |

Các lần chạy này xác nhận runner, MySQL service, dependencies, Black, Ruff, Pyright và
toàn bộ tests. Kết quả CI không thay thế review/approve theo GITFLOW.

## Rà soát theo thiết kế

### Đối chiếu database-design.md

| Phần kiểm tra | Kết quả |
|---|---|
| Phạm vi bảng | Đủ 11/11 bảng, không loại bảng nào. |
| Từ điển dữ liệu | Cột, kiểu SQL, độ dài, độ chính xác, NULL, mặc định và PK đã đối chiếu với mục 3. |
| Khóa và ràng buộc | FK `RESTRICT`, FK ghép nhân viên–nhu cầu, UNIQUE và CHECK khớp thiết kế. |
| Đề nghị đang chờ | `pending_guard` virtual và UNIQUE bảo vệ một `PENDING`, giữ được lịch sử. |
| Chỉ mục | Đủ 12 chỉ mục truy vấn trong mục 7, đúng thứ tự cột/chiều sắp xếp và chỉ mục cần cho FK. |
| Lưu trữ/collation | InnoDB, utf8mb4; email và mã/key dùng collation theo thiết kế. |
| Chuyển đổi kiểu | UUID chuẩn, datetime UTC giữ microsecond, TIME(0) và SHA-256 đúng 32 byte. |
| Kết nối | UTC, `READ COMMITTED`, SQL mode nghiêm ngặt và `pool_pre_ping`. |

Không phát hiện sai lệch schema cần sửa trong 11 models sau rà soát độc lập và chạy DDL
trên MySQL 8.4.11. Các phát hiện đã sửa tập trung ở tests, cấu hình import và CI.

### Đối chiếu system-design.md

| Mục thiết kế | Phần đã thực hiện | Phạm vi cần triển khai tiếp |
|---|---|---|
| 2.2 — Truy cập dữ liệu | SQLAlchemy 2, asyncmy, MySQL 8.4/InnoDB, `READ COMMITTED`. | Alembic và quy trình tái lập bản phát hành. |
| 5.1–5.2 — Module và tầng | Đủ bảng cho Accounts, Catalog, Availability, Appointments, Notifications, Audit; models chỉ ánh xạ lưu trữ. | Repository/service, session theo request/worker, commit/rollback. |
| 5.3 — Cấu trúc code | Dùng `src/backend/app/models`, `src/backend/app/core`, `tests/backend` theo skeleton repository. | Tài liệu thiết kế dùng đường dẫn rút gọn `backend/app`, `backend/tests`. |
| 6.2 — Thời gian | `DATETIME(6)` UTC, session `+00:00`, timezone nghiệp vụ mặc định `Asia/Ho_Chi_Minh`. | ISO 8601 tại API và lấy giờ database sau khóa trong service. |
| 6.3 — Đồng thời/chống gửi lặp | UNIQUE, `pending_guard`, key/hash lưu trên lịch. | Khóa hàng, retry, kiểm tra hash và dựng lại response. |
| 9.2 — Quyền database | Dữ liệu phiên/audit; schema test riêng. | Tài khoản migration/runtime, quyền audit INSERT/SELECT và phân quyền API. |
| 10 — Pool và timeout | `pool_pre_ping`, connect timeout trong fixture. | Ngân sách pool, timeout, retry và shutdown của API/worker. |
| 12–13 — Kiểm chứng/phát hành | Unit/integration tests, lint/typecheck và CI MySQL. | Build/E2E, migration/rollback, đồng thời, tải và SLO. |

Dependencies Python đã pin bằng `==`; CI chọn Python `3.11` và MySQL tag `8.4.11`.
Chưa có quy trình sinh/xác minh lockfile release, pin Python patch hoặc MySQL image digest.
Phần models và engine phù hợp trách nhiệm được giao; chưa đủ bằng chứng để xác nhận toàn bộ
hệ thống đáp ứng thiết kế.

### Các phát hiện đã sửa

| Phát hiện | Bản sửa |
|---|---|
| Editor thiếu đường dẫn package `app`. | Thêm cấu hình Pyright/Pylance, interpreter và hướng dẫn editable install. |
| CI chưa kiểm tra kiểu toàn bộ source/tests. | Thêm Pyright 1.1.407, Node.js 22 và `.venv` vào workflow. |
| Tám chỗ đọc `DBAPIError.orig.args[0]` chưa xử lý khả năng `None`. | Dùng helper kiểm tra `orig` và mã lỗi là số nguyên trước khi xác nhận lỗi MySQL. |
| ORM chuẩn hóa email che mất việc kiểm chứng collation database. | Thêm truy vấn SQL trực tiếp với email viết hoa, giữ kiểm tra phân biệt dấu. |
| Chưa tự động đối chiếu toàn bộ cột/kiểu/NULL. | Thêm unit test đọc từ điển dữ liệu và so sánh metadata; bổ sung test collation. |
| Fixture lịch/đề nghị và hạn xử lý chưa đúng nghiệp vụ. | Dùng `CHANGE_PENDING` cùng `PENDING`, lịch sử `SUPERSEDED` có người xử lý, hạn đúng 00:00 giờ Việt Nam. |
| Chưa kiểm chứng SQL mode bằng ngày không hợp lệ. | Thêm hai integration tests, xác nhận lỗi MySQL `1292`. |
| GitHub Actions cảnh báo runtime Node.js 20 và thay đổi `ubuntu-latest`. | Nâng Actions lên v7, pin `ubuntu-24.04`, xác nhận CI không còn annotations. |

## Công việc ngoài phạm vi

Các phần cần triển khai ở task khác:

- API, repository/service nghiệp vụ và worker hết hạn.
- Vai trò/quyền sở hữu, giao khoảng, hạn mức ba lịch, lịch không nhận, thứ tự khóa,
  chuyển trạng thái, actor, hạn đặt/hủy, trường bất biến và giao dịch audit/thông báo.
- Migration Alembic, dữ liệu seed, database production và quyền tài khoản runtime/audit.
- Kiểm thử đồng thời, tải, E2E, SLO và pipeline phát hành/rollback.
