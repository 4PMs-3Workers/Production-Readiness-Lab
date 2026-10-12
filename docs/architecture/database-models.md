# Models cơ sở dữ liệu

Các model triển khai đầy đủ 11 bảng trong [thiết kế cơ sở dữ liệu](database-design.md),
phiên bản 1.3. Không có bảng nguồn nào bị loại khỏi phạm vi. Phần triển khai này gồm ánh xạ
dữ liệu bằng ORM, bộ chuyển đổi kiểu dữ liệu và các bài kiểm thử. Endpoint API, service nghiệp vụ,
dữ liệu seed và migration Alembic có quản lý phiên bản thuộc các công việc riêng.

## Danh sách bảng và model

Tất cả module nằm trong `src/backend/app/models`. Import `app.models.Base` để đăng ký đầy đủ
các bảng trước khi kiểm tra schema hoặc tự động sinh bản nháp migration.

| Bảng nguồn | Model | Module |
|---|---|---|
| `users` | `User` | `user.py` |
| `sessions` | `Session` | `session.py` |
| `purposes` | `Purpose` | `purpose.py` |
| `staff_purposes` | `StaffPurpose` | `staff_purpose.py` |
| `service_settings` | `ServiceSetting` | `service_setting.py` |
| `service_hours` | `ServiceHour` | `service_hour.py` |
| `staff_unavailability` | `StaffUnavailability` | `staff_unavailability.py` |
| `appointments` | `Appointment` | `appointment.py` |
| `change_proposals` | `ChangeProposal` | `change_proposal.py` |
| `notifications` | `Notification` | `notification.py` |
| `audit_events` | `AuditEvent` | `audit_event.py` |

Mỗi model dùng ánh xạ khai báo của SQLAlchemy 2 có chú thích kiểu, tên SQL bằng tiếng Anh theo
`snake_case`, engine InnoDB và collation `utf8mb4_0900_ai_ci`. Các ràng buộc FK/CHECK/UNIQUE
có tên và các chỉ mục tuân theo thiết kế database. Khóa ngoại dùng `RESTRICT` cho cả thao tác
xóa và cập nhật. Model cung cấp các ID khóa ngoại; truy vấn, phép join và giao dịch của service
được thực hiện tường minh, không khai báo relationship tự tải dữ liệu ngầm.

## Chuyển đổi kiểu dữ liệu

- Các trường UUID nhận `uuid.UUID` hoặc chuỗi UUID, lưu 16 byte theo thứ tự chuẩn trong
  `BINARY(16)` và trả đối tượng UUID khi đọc. Không dùng `UUID_TO_BIN(..., 1)` đảo thứ tự byte
  với các cột này.
- `UTCDateTime` chỉ nhận `datetime` có múi giờ, chuyển về UTC trước khi lưu vào `DATETIME(6)`
  và gắn múi giờ UTC khi đọc. Độ chính xác microsecond được giữ nguyên. Schema API chịu trách
  nhiệm xuất ISO 8601; frontend chịu trách nhiệm chuyển múi giờ hiển thị.
- `TimeOfDay` nhận `datetime.time` biểu diễn giờ địa phương, không có múi giờ hoặc phần lẻ
  của giây, để lưu vào `TIME(0)`. Ràng buộc của giờ phục vụ từ chối giá trị âm hoặc từ 24 giờ trở lên.
- Các trường SHA-256 nhận giá trị hash dạng 32 byte thô, không nhận chuỗi hex. Bộ chuyển đổi
  và ràng buộc CHECK đều kiểm tra độ dài.
- Khi gán email qua ORM, giá trị được chuẩn hóa bằng `strip().lower()`. CHECK tại database
  cũng bảo vệ thao tác SQL trực tiếp. Email dùng `utf8mb4_0900_as_ci`, phân biệt dấu nhưng
  không phân biệt hoa/thường. Mã nghiệp vụ và idempotency key dùng `utf8mb4_0900_bin`;
  key do client gửi được giữ nguyên, kể cả khoảng trắng cuối chuỗi.
- `created_at` có giá trị mặc định tại database là `CURRENT_TIMESTAMP(6)`. `updated_at` được
  gán theo UTC khi insert/update qua ORM; các luồng ghi SQL trực tiếp phải tự cung cấp hoặc
  cập nhật trường này. Cấu hình `eager_defaults` tải các cột mặc định hoặc tự sinh ngay trong
  `flush`, để việc đọc `created_at` hoặc `pending_guard` không phát sinh I/O ngầm trong
  `AsyncSession` trên MySQL. Xem
  [hướng dẫn I/O bất đồng bộ của SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession).
- Cột SQL `metadata` của audit được ánh xạ thành `AuditEvent.event_metadata` vì SQLAlchemy
  dành riêng tên thuộc tính `metadata`. Giá trị mặc định khi insert tạo một dictionary mới
  cho mỗi bản ghi. Phần triển khai hạ tầng/service phải giới hạn quyền audit của tài khoản
  runtime ở INSERT/SELECT và loại bỏ dữ liệu nhạy cảm khỏi metadata.
- `pending_guard` là cột sinh tự động dạng virtual. Code ứng dụng không được gán giá trị cho
  cột này. Unique trên `(appointment_id, pending_guard)` cho phép tối đa một đề nghị đang chờ
  và nhiều đề nghị đã kết thúc trên cùng lịch hẹn.

Dùng `app.core.database.create_database_engine` với URL `mysql+asyncmy://`. Mỗi kết nối
được cấu hình UTC, SQL mode nghiêm ngặt và `READ COMMITTED`, kể cả sau khi kết nối lại.
Service quản lý session và vòng đời commit/rollback; mỗi request hoặc giao dịch worker dùng
một session riêng. Tham số tùy chọn `connect_args` cho phép cấu hình TLS/timeout, trong khi
hàm tạo engine vẫn giữ các quy tắc UTC và SQL mode nghiêm ngặt. Dependency `cryptography`
được bổ sung để hỗ trợ xác thực RSA của `caching_sha2_password`, cơ chế mặc định của MySQL 8.4,
khi kết nối không sử dụng TLS.

## Ràng buộc database và trách nhiệm giao dịch nghiệp vụ

CHECK bảo vệ tính hợp lệ trong từng hàng: tập trạng thái, độ dài, khoảng thời gian và các mốc
thời gian theo trạng thái. Do giới hạn kết hợp FK/CHECK của MySQL, các kiểm tra phụ thuộc vào
người thao tác được thực hiện ở service. Service phải kiểm tra `cancelled_by`, `outcome_by`
và `resolved_by`, gồm điều kiện NULL và vai trò của người thao tác cho từng lần chuyển trạng thái.

Service tiếp tục chịu trách nhiệm về vai trò/quyền sở hữu, lịch giao khoảng, hạn mức ba lịch,
xung đột với thời gian không nhận, các trường bất biến, chuyển trạng thái hợp lệ, thời lượng
đề nghị khớp với lịch, hạn đặt/hủy và việc ghi audit/thông báo trong cùng giao dịch. Thứ tự khóa
là cấu hình → tài khoản người tham gia theo thứ tự byte UUID → lịch hẹn → đề nghị. Sau khi lấy
khóa, phải đọc lại và kiểm tra trước khi ghi, theo mục 6–7 của thiết kế database. Bộ model này
chỉ cung cấp lớp ánh xạ lưu trữ; luồng đặt lịch được triển khai tại service.

`Base.metadata.create_all` chỉ được dùng trong các bài kiểm thử tích hợp độc lập. Thay đổi
schema production phải dùng migration Alembic đã được review và giữ khả năng tương thích
theo yêu cầu rollback.

## Chạy kiểm tra

Thực hiện tại thư mục gốc của repo với Python 3.11 trở lên:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m black --check src/backend tests/backend
python -m ruff check src/backend tests/backend
python -m pytest tests/backend/unit -q
```

### Cấu hình import trong editor

Package `app` nằm dưới `src/backend`, nên editor cần đường dẫn này để phân tích các import
như `from app.models import Base`. `pyproject.toml` đã khai báo `extraPaths` cho Pyright/Pylance;
`.vscode/settings.json` đặt môi trường Python mặc định là `.venv` của workspace.

Mở thư mục gốc repo trong VS Code. Nếu workspace đã chọn một interpreter khác, chạy
`Python: Select Interpreter` và chọn Python trong `.venv`, rồi tải lại cửa sổ nếu cảnh báo
import chưa cập nhật. Cấu hình đường dẫn của pytest chỉ áp dụng lúc chạy tests; nó không
tự cấu hình interpreter cho editor. Xem [cấu hình Pyright](https://github.com/microsoft/pyright/blob/main/docs/configuration.md)
và [cấu hình Python của VS Code](https://code.visualstudio.com/docs/python/settings-reference).

Để dùng package `app` từ Python thông thường ở thư mục gốc, cài backend vào môi trường
đang dùng bằng `python -m pip install -e . --no-deps` sau khi đã cài dependencies.

Để chạy kiểm thử tích hợp, khởi động một MySQL 8.4 riêng cho tests, ví dụ bằng Docker:

```bash
docker run --name prl-model-tests -e MYSQL_ROOT_PASSWORD=test-only-password -p 127.0.0.1:33070:3306 -d mysql:8.4.11
# Chờ đến khi mysqladmin ping xác nhận MySQL đã sẵn sàng.
export MYSQL_TEST_URL='mysql+asyncmy://root:test-only-password@127.0.0.1:33070'
# PowerShell: $env:MYSQL_TEST_URL = 'mysql+asyncmy://root:test-only-password@127.0.0.1:33070'
python -m pytest -q
docker stop prl-model-tests
docker rm prl-model-tests
```

Tài khoản test cần quyền CREATE/DROP DATABASE. Mỗi test tạo schema riêng có tên ngẫu nhiên
`prl_models_test_*`, kiểm tra bằng các ràng buộc MySQL thật và chỉ xóa schema đó khi dọn dẹp.
Các database đã tồn tại không bị thay đổi. Nếu thiếu `MYSQL_TEST_URL`, các bài kiểm thử tích hợp
được bỏ qua; nếu đã cung cấp URL nhưng không kết nối được hoặc MySQL sai phiên bản, tests sẽ thất bại.

Bộ tests kiểm tra việc ghi/đọc lại dữ liệu của tất cả bảng, thứ tự byte UUID, độ chính xác UTC,
giá trị mặc định, chuyển đổi giờ, cấu hình kết nối, CHECK khi ghi SQL trực tiếp, khóa ngoại ghép,
giới hạn xóa dữ liệu được tham chiếu, tính duy nhất và collation của idempotency key, ràng buộc
đề nghị đang chờ và lịch sử đề nghị, collation email, JSON audit mặc định và timestamp của backend.
Workflow `.github/workflows/backend-models.yml` được cấu hình chạy toàn bộ tests trên Linux
với MySQL 8.4, kèm Black, Ruff và Pyright, khi push branch hoặc mở PR. Workflow tạo `.venv`
để Pyright dùng đúng dependencies, và cài Pyright 1.1.407 bằng Node.js 22.

Kiểm tra kiểu cho toàn bộ source/tests sau khi tạo `.venv` và cài dependencies:

```bash
npm install --global pyright@1.1.407
pyright
```

## Kết quả kiểm tra local — 11/10/2026

- Môi trường: Ubuntu WSL, Python 3.11.17, SQLAlchemy 2.0.44, asyncmy 0.2.10,
  MySQL 8.4.11/InnoDB.
- Đạt 70 tests: 27 unit tests và 43 trường hợp kiểm thử tích hợp MySQL; không bỏ qua test.
  Black và Ruff đều đạt. Pyright kiểm tra toàn bộ source/tests: 0 lỗi, 0 cảnh báo.
- Tạo thành công cả 11 bảng và các chỉ mục trên MySQL. Các schema test tạm đã được fixture
  xóa sau khi kiểm tra. Báo cáo JUnit `build/model-review-tests.xml` là file tạm, đã được dọn
  trước commit; có thể tạo lại theo lệnh trong [báo cáo task](../tasks/database-models.md).
- Đã đối chiếu cột, kiểu dữ liệu, NULL, khóa, CHECK, collation và 12 chỉ mục truy vấn với
  `database-design.md` v1.3. Chi tiết các phát hiện và bản sửa nằm trong
  [báo cáo task tạo models](../tasks/database-models.md).
- Khi chạy trực tiếp trên Windows, Application Control chặn một DLL của asyncmy. Toàn bộ
  kiểm thử ORM/driver đã được chạy trong WSL, kết nối đến MySQL riêng dành cho tests.
- Workflow trên GitHub Actions đã chạy thành công ở commit `62acd33`, gồm service MySQL,
  Black, Ruff, Pyright và bộ tests:
  [Backend models — run 38112810486](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38112810486).
