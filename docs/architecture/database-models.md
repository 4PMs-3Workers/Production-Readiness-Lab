# Models cơ sở dữ liệu — Production Readiness Lab

**Ngày cập nhật:** 13/10/2026 · **Môi trường:** Python 3.11+, SQLAlchemy 2, asyncmy và MySQL 8.4/InnoDB.

## Mục lục

1. Tổng quan tầng models
2. Tổ chức models
3. Luồng truy cập dữ liệu
4. Quy ước ánh xạ
5. Ràng buộc và giao dịch nghiệp vụ
6. Thiết lập và chạy kiểm tra
7. Phạm vi kiểm thử và CI
8. Kết quả kiểm chứng đã ghi nhận

## 1. Tổng quan tầng models

Tầng models lưu dữ liệu cho ứng dụng đặt lịch tư vấn tại trường đại học. Một lịch hẹn liên quan đến sinh viên, nhân viên, nhu cầu tư vấn, đề nghị đổi lịch, thông báo và lịch sử thao tác.

SQLAlchemy 2 ánh xạ **11 bảng** thành các lớp Python có chú thích kiểu. Các bộ chuyển đổi giữ UUID, giờ UTC, hash và giờ trong ngày nhất quán khi dữ liệu đi qua ORM.

Models chịu trách nhiệm biểu diễn schema và ràng buộc dữ liệu. Service kiểm tra quyền, khả dụng, chuyển trạng thái và quản lý transaction; repository thực hiện truy vấn bằng session được truyền vào.

## 2. Tổ chức models

Các model nằm trong `src/backend/app/models`. Import `app.models.Base` để đăng ký đủ bảng vào metadata trước khi kiểm tra schema hoặc sinh bản nháp migration.

| Bảng | Model | Module |
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

Mỗi model dùng ánh xạ khai báo có chú thích kiểu. Tên SQL theo `snake_case`; engine là InnoDB, collation mặc định là `utf8mb4_0900_ai_ci`.

- FK/CHECK/UNIQUE có tên; khóa ngoại dùng `RESTRICT` khi xóa và cập nhật.
- Chỉ mục phục vụ truy vấn phiên, nhu cầu, khoảng thời gian, lịch sử, thông báo và worker hết hạn.
- Model cung cấp ID khóa ngoại. Repository/service thực hiện truy vấn và join tường minh; không khai báo relationship tự tải dữ liệu ngầm.

Các thành phần dùng chung gồm:

| Thành phần | Trách nhiệm |
|---|---|
| `models/base.py` | Base, quy ước đặt tên constraint, UUID và timestamp mixin. |
| `models/types.py` | Chuyển đổi UUID, thời điểm UTC, giờ trong ngày và SHA-256. |
| `models/__init__.py` | Đăng ký các model để metadata chứa đủ 11 bảng. |
| `core/database.py` | Tạo async engine với các quy tắc kết nối MySQL. |

## 3. Luồng truy cập dữ liệu

Một request hoặc giao dịch worker sử dụng một session riêng. Service quyết định transaction; repository và các thao tác ghi liên quan nhận cùng session đó.

```text
Router hoặc worker
    → Service kiểm tra nghiệp vụ và quản lý transaction
    → Repository truy vấn bằng session được truyền vào
    → SQLAlchemy chuyển đổi giá trị và thực thi SQL
    → MySQL kiểm tra ràng buộc, đọc/ghi dữ liệu
    → Service commit hoặc rollback
```

### 3.1. Kết nối và session

Dùng `app.core.database.create_database_engine` với driver `mysql+asyncmy`. Hàm tạo engine bảo đảm UTC, SQL mode nghiêm ngặt và `READ COMMITTED` cho mỗi kết nối, kể cả sau khi kết nối lại.

`connect_args` cho phép cấu hình TLS/timeout nhưng không thay thế các quy tắc kết nối bắt buộc. Dependency `cryptography` hỗ trợ xác thực RSA của `caching_sha2_password` khi kết nối không sử dụng TLS.

Không dùng chung một `AsyncSession` cho các tác vụ đồng thời. Khi kết thúc request hoặc giao dịch worker, đóng session; khi tiến trình shutdown, đóng engine/pool theo vòng đời ứng dụng.

### 3.2. Ví dụ truy vấn trong repository

Ví dụ dưới đây đọc nhu cầu đang hoạt động bằng session do service cung cấp:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Purpose


async def list_active_purposes(session: AsyncSession) -> list[Purpose]:
    result = await session.scalars(
        select(Purpose).where(Purpose.is_active.is_(True))
    )
    return list(result)
```

Hàm truy vấn không tự tạo session hoặc commit. Với thao tác ghi, service có thể gọi nhiều repository bằng cùng session để toàn bộ thay đổi nằm trong một transaction.

## 4. Quy ước ánh xạ

### 4.1. UUID và hash

- Các trường UUID nhận `uuid.UUID` hoặc chuỗi UUID, lưu 16 byte theo thứ tự chuẩn trong `BINARY(16)` và trả đối tượng UUID khi đọc. Không dùng `UUID_TO_BIN(..., 1)` đảo thứ tự byte với các cột này.

- Các trường SHA-256 nhận giá trị hash dạng 32 byte thô, không nhận chuỗi hex. Bộ chuyển đổi và ràng buộc CHECK đều kiểm tra độ dài.

### 4.2. Thời gian và timestamp

**Thời điểm:** `UTCDateTime` nhận `datetime` có múi giờ, chuyển về UTC trước khi lưu vào `DATETIME(6)` và gắn UTC khi đọc. Độ chính xác microsecond được giữ nguyên.

Schema API xuất ISO 8601; frontend chuyển thời điểm sang múi giờ hiển thị.

**Giờ trong ngày:** `TimeOfDay` nhận `datetime.time` địa phương, không có múi giờ hoặc phần lẻ của giây, lưu vào `TIME(0)`. Ràng buộc giờ phục vụ từ chối giá trị âm hoặc từ 24 giờ trở lên.

**Timestamp và giá trị mặc định:**

- Database gán `created_at` bằng `CURRENT_TIMESTAMP(6)`.
- ORM gán `updated_at` theo UTC khi insert/update. Luồng SQL trực tiếp tự cung cấp hoặc cập nhật trường này.
- `eager_defaults` tải giá trị mặc định/tự sinh trong `flush`, gồm `created_at` và `pending_guard`.

Chỉ đọc các thuộc tính đã tải trong `AsyncSession`. Truy vấn hoặc tải lại dữ liệu được thực hiện bằng lời gọi async tường minh để tránh I/O ngầm.

### 4.3. Email và mã đối chiếu

Email được chuẩn hóa bằng `strip().lower()` khi gán qua ORM. CHECK tại database bảo vệ quy tắc chuẩn hóa khi ghi bằng SQL trực tiếp.

- Email dùng `utf8mb4_0900_as_ci`: phân biệt dấu, không phân biệt hoa/thường.
- Mã nghiệp vụ và idempotency key dùng `utf8mb4_0900_bin`: so sánh chính xác.
- Key client được giữ nguyên, kể cả khoảng trắng cuối chuỗi.

### 4.4. Audit và cột sinh tự động

SQLAlchemy dành riêng tên thuộc tính `metadata`, nên cột audit cùng tên được ánh xạ thành `AuditEvent.event_metadata`. Mặc định khi insert tạo một dictionary mới cho mỗi bản ghi.

Tài khoản runtime chỉ có quyền INSERT/SELECT với audit. Service loại bỏ mật khẩu, token và nội dung tư vấn khỏi metadata.

`pending_guard` là cột sinh tự động dạng virtual; ứng dụng không gán giá trị trực tiếp. UNIQUE `(appointment_id, pending_guard)` cho phép tối đa một đề nghị đang chờ và nhiều đề nghị đã kết thúc trên cùng lịch.

## 5. Ràng buộc và giao dịch nghiệp vụ

CHECK bảo vệ tính hợp lệ trong từng hàng: trạng thái, độ dài, khoảng thời gian và các mốc theo trạng thái. FK bảo vệ tham chiếu, UNIQUE bảo vệ mã yêu cầu và giới hạn một đề nghị đang chờ.

Các điều kiện về quyền, người thao tác và dữ liệu giữa nhiều hàng được kiểm tra tại service. Do giới hạn kết hợp FK/CHECK của MySQL, service cũng kiểm tra các cột actor `cancelled_by`, `outcome_by` và `resolved_by`.

### 5.1. Quy tắc nghiệp vụ cần bảo vệ

- Tài khoản có một vai trò `STUDENT`, `STAFF` hoặc `ADMIN`; sinh viên tự đặt lịch cho mình.
- `UPCOMING` và `CHANGE_PENDING` chiếm chỗ; mỗi sinh viên có tối đa ba lịch thuộc hai trạng thái này.
- Khoảng thời gian dùng dạng `[starts_at, ends_at)`: hai lịch tiếp giáp được phép, giao khoảng của cùng sinh viên hoặc nhân viên bị từ chối.
- Khi chờ đổi lịch, giờ gốc vẫn giữ chỗ. Chấp thuận phải kiểm tra lại giờ mới; từ chối hoặc hết hạn phản hồi hủy lịch.
- `Idempotency-Key` được lưu thành `creation_request_key`, unique theo sinh viên. Cùng key/hash trả kết quả tạo ban đầu; khác hash trả xung đột.
- Lịch, đề nghị, audit và thông báo phải commit hoặc rollback cùng nhau.

Ngày nghiệp vụ dùng `Asia/Ho_Chi_Minh`. Cửa sổ đặt mặc định gồm bảy ngày D+1 đến D+7 tính từ ngày hiện tại D; bước giờ 15 phút lấy đầu khoảng phục vụ làm mốc. Người tham gia hủy trước 00:00 ngày hẹn. Hạn phản hồi đổi lịch là 00:00 ngày hẹn sớm hơn giữa lịch gốc và đề nghị, được lưu theo UTC vào `expires_at`; khi `now >= expires_at` thì không được phản hồi. Các giá trị múi giờ, cửa sổ đặt và bước giờ lấy từ cấu hình; thời lượng lấy từ nhu cầu.

`cancelled_by` chỉ ghi người tham gia của lịch hoặc NULL cho hệ thống; `outcome_by` phải là nhân viên nhận lịch; `resolved_by` phải phù hợp với thao tác giải quyết đề nghị và để NULL khi hết hạn. Model không tự xác thực các danh tính này; service kiểm tra trước khi commit.

### 5.2. Thứ tự khóa và kiểm tra

1. Khóa `service_settings(id = 1)` bằng `FOR SHARE`; thao tác quản trị sửa cấu hình/danh mục/giờ chung dùng `FOR UPDATE`.
2. Khóa từng tài khoản sinh viên và nhân viên bằng `FOR UPDATE`, theo thứ tự 16 byte UUID tăng dần. Khóa hàng tài khoản ngay cả khi chưa có lịch.
3. Với lịch đã tồn tại, khóa lịch rồi đề nghị hoặc liên kết nhu cầu liên quan. Nếu xử lý nhiều lịch, lấy khóa theo ID tăng dần.
4. Đọc lại dữ liệu sau khi có đủ khóa; lấy thời gian hiện tại bằng câu lệnh riêng `SELECT UTC_TIMESTAMP(6)`.
5. Kiểm tra quyền, trạng thái, hạn, số lịch, giờ phục vụ, khoảng không nhận và giao khoảng trước khi ghi.

Service quản lý commit/rollback; repository nhận session của giao dịch đó. Deadlock được retry bằng transaction mới trong ngân sách timeout. Lock timeout phải rollback toàn bộ thao tác; không commit phần dữ liệu đã ghi trước lỗi.

`Base.metadata.create_all` chỉ được dùng trong các bài kiểm thử tích hợp độc lập. Thay đổi
schema production phải dùng migration Alembic đã được review và giữ khả năng tương thích
theo yêu cầu rollback.

## 6. Thiết lập và chạy kiểm tra

### 6.1. Điều kiện môi trường

- Chạy các lệnh từ thư mục gốc repository.
- Cài Python 3.11 trở lên. Dependencies được khóa trong `requirements.txt` và `requirements-dev.txt`.
- Cài Docker để chạy MySQL riêng cho integration tests, hoặc cung cấp MySQL 8.4 dành riêng cho tests.
- Cài Node.js 22 và npm để chạy Pyright 1.1.407.
- Khi Windows Application Control chặn DLL của asyncmy, chạy bộ kiểm thử trong WSL/Linux với môi trường Python riêng ở đó.

### 6.2. Môi trường Python và kiểm tra cơ bản

**PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m black --check src/backend tests/backend
.\.venv\Scripts\python.exe -m ruff check src/backend tests/backend
.\.venv\Scripts\python.exe -m pytest tests/backend/unit -q
```

**Bash trên Linux/macOS/WSL:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m black --check src/backend tests/backend
.venv/bin/python -m ruff check src/backend tests/backend
.venv/bin/python -m pytest tests/backend/unit -q
```

Các lệnh gọi trực tiếp interpreter trong `.venv`, nên không cần kích hoạt môi trường trước khi chạy.

### 6.3. Cấu hình import trong editor

Package `app` nằm dưới `src/backend`, nên editor cần đường dẫn này để phân tích các import
như `from app.models import Base`. `pyproject.toml` đã khai báo `extraPaths` cho Pyright/Pylance;
`.vscode/settings.json` đặt môi trường Python mặc định là `.venv` của workspace.

Mở thư mục gốc repo trong VS Code. Nếu workspace đã chọn một interpreter khác, chạy
`Python: Select Interpreter` và chọn Python trong `.venv`, rồi tải lại cửa sổ nếu cảnh báo
import chưa cập nhật. Cấu hình đường dẫn của pytest chỉ áp dụng lúc chạy tests; nó không
tự cấu hình interpreter cho editor.

Để dùng package `app` từ Python thông thường ở thư mục gốc, cài backend vào môi trường
đang dùng bằng lệnh cài editable sau khi đã cài dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

Trên Bash, thay `.\.venv\Scripts\python.exe` bằng `.venv/bin/python`. Pytest và Pyright đã có cấu hình đường dẫn `src/backend`; cài editable phục vụ việc import ngoài hai công cụ này.

### 6.4. Kiểm thử tích hợp với MySQL

Khởi động MySQL riêng cho tests bằng lệnh dùng được trên cả PowerShell và Bash:

```text
docker run --name prl-model-tests -e MYSQL_ROOT_PASSWORD=test-only-password -p 127.0.0.1:33070:3306 -d mysql:8.4.11
```

Kiểm tra readiness bằng lệnh sau; chỉ chạy tests khi lệnh thành công và báo `mysqld is alive`:

```text
docker exec prl-model-tests mysqladmin ping -h 127.0.0.1 -ptest-only-password
```

**PowerShell:**

```powershell
$env:MYSQL_TEST_URL = 'mysql+asyncmy://root:test-only-password@127.0.0.1:33070'
.\.venv\Scripts\python.exe -m pytest tests/backend/integration -q
```

**Bash:**

```bash
export MYSQL_TEST_URL='mysql+asyncmy://root:test-only-password@127.0.0.1:33070'
.venv/bin/python -m pytest tests/backend/integration -q
```

Tài khoản test cần quyền CREATE/DROP DATABASE. Mỗi test tạo schema riêng có tên ngẫu nhiên
`prl_models_test_*`, kiểm tra bằng các ràng buộc MySQL thật và chỉ xóa schema đó khi dọn dẹp.
Các database đã tồn tại không bị thay đổi. Nếu thiếu `MYSQL_TEST_URL`, các bài kiểm thử tích hợp
được bỏ qua; nếu đã cung cấp URL nhưng không kết nối được hoặc MySQL sai phiên bản, tests sẽ thất bại.

Mật khẩu trong ví dụ dành cho container test cục bộ, cổng chỉ bind vào loopback. Sau khi chạy xong, dọn container test bằng:

```text
docker stop prl-model-tests
docker rm prl-model-tests
```

### 6.5. Kiểm tra kiểu và xuất báo cáo

Sau khi tạo `.venv` và cài dependencies, chạy Pyright bằng phiên bản cố định:

```text
npx --yes pyright@1.1.407
```

Để chạy toàn bộ tests và xuất JUnit, giữ `MYSQL_TEST_URL` trỏ đến MySQL test đang chạy, tạo thư mục `build` nếu chưa có, rồi thực hiện:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/backend -q --junitxml=build/model-review-tests.xml
```

Trên Bash, dùng `.venv/bin/python` với cùng tham số. Báo cáo nằm trong `build/`, là đầu ra kiểm thử và không được commit.

## 7. Phạm vi kiểm thử và CI

Bộ tests kiểm tra việc ghi/đọc lại dữ liệu của tất cả bảng, thứ tự byte UUID, độ chính xác UTC, giá trị mặc định, chuyển đổi giờ, cấu hình kết nối, CHECK khi ghi SQL trực tiếp, khóa ngoại ghép, giới hạn xóa dữ liệu được tham chiếu, tính duy nhất và collation của idempotency key, ràng buộc đề nghị đang chờ và lịch sử đề nghị, collation email, JSON audit mặc định và timestamp của backend.

Workflow `.github/workflows/backend-models.yml` chạy trên pull request vào `develop`/`main` và push branch `feature/**`, `fixbug/**`, `hotfix/**`. Job chạy toàn bộ tests trên Linux với MySQL 8.4, kèm Black, Ruff và Pyright. Workflow tạo `.venv`
để Pyright dùng đúng dependencies, và cài Pyright 1.1.407 bằng Node.js 22.

Tests models xác minh tầng lưu trữ và ràng buộc từng hàng. Quyền API, giao dịch đặt lịch, hạn mức ba lịch, xử lý đổi lịch và tính đúng đắn dưới tải đồng thời cần bộ kiểm thử service/API riêng; kết quả models không thay thế các tiêu chí đó.

## 8. Kết quả kiểm chứng đã ghi nhận

Các kết quả sau thuộc lần kiểm chứng ngày **11/10/2026**, được giữ làm mốc đối chiếu:

- Môi trường: Ubuntu WSL, Python 3.11.17, SQLAlchemy 2.0.44, asyncmy 0.2.10,
  MySQL 8.4.11/InnoDB.
- Đạt 70 tests: 27 unit tests và 43 trường hợp kiểm thử tích hợp MySQL; không bỏ qua test.
  Black và Ruff đều đạt. Pyright kiểm tra toàn bộ source/tests: 0 lỗi, 0 cảnh báo.
- Tạo thành công cả 11 bảng và các chỉ mục trên MySQL. Các schema test tạm đã được fixture
  xóa sau khi kiểm tra. Báo cáo JUnit `build/model-review-tests.xml` là file tạm, đã được dọn
  trước commit; có thể tạo lại bằng lệnh xuất JUnit tại mục 6.5.
- Đã đối chiếu cột, kiểu dữ liệu, NULL, khóa, CHECK, collation và 12 chỉ mục truy vấn với
  thiết kế cơ sở dữ liệu phiên bản 1.3.
- Khi chạy trực tiếp trên Windows, Application Control chặn một DLL của asyncmy. Toàn bộ
  kiểm thử ORM/driver đã được chạy trong WSL, kết nối đến MySQL riêng dành cho tests.
- Workflow GitHub Actions được ghi nhận thành công ở commit `62acd33`, run `38112810486`, gồm service MySQL, Black, Ruff, Pyright và bộ tests.
