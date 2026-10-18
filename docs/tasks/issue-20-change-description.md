# Issue #20 — Mô tả thay đổi và rà soát trước commit

Ngày rà soát: **18/10/2026**. Branch: `feature/frontend-api-integration`.
Nền triển khai: `c865b1e07dd25cca0e1a1269b29ebdff84606637` từ `develop` (PR #19).
Bản mô tả trước commit ban đầu được giữ dưới đây. Thay đổi đã lên
[PR #23](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/23);
phần bổ sung cuối tài liệu ghi các sửa đổi sau review.

## Vấn đề và kết quả

Cổng sinh viên trước đây chỉ chạy dữ liệu giả và giữ kết quả trong bộ nhớ.
Bản thay đổi nối giao diện với FastAPI/MySQL cho đăng nhập, đặt lịch, lịch sử,
chi tiết, hủy lịch và hồ sơ cá nhân. Lịch đã ghi nhận có UUID để mở lại sau reload.
Chế độ API là mặc định; chế độ mock vẫn dùng được khi cấu hình rõ lúc build.

Các phản hồi trong lần thử thủ công được xử lý như sau:

| Phản hồi                                        | Hành vi sau thay đổi                                                                     |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Login có ba link Đăng nhập / Đặt lịch / Kết quả | Login API bỏ điều hướng demo này.                                                        |
| Bấm Khám phá luồng đặt lịch không thấy gì       | Thay bằng Cách đặt lịch tư vấn, mở bốn bước ngay trên trang login.                       |
| Hiển thị mã Asia/Ho_Chi_Minh                    | UI dùng Giờ Việt Nam (UTC+7); phép tính vẫn theo timezone backend.                       |
| Có thêm ô Ngày tư vấn, định dạng mm/dd/yyyy     | Chỉ còn thẻ chọn ngày; nhãn đầy đủ dùng dd/mm/yyyy.                                      |
| Mở đặt lịch tới 30 ngày                         | Fixture và môi trường thử tay dùng D+1…D+7 theo thiết kế nghiệp vụ.                      |
| Bước 1 chỉ có một nhu cầu                       | Harness có lệnh nạp đủ năm nhu cầu để thử tay; UI đọc catalog từ API.                    |
| Thông tin sinh viên nằm trên Home, thiếu trường | Chuyển sang /profile và mục sidebar riêng; bổ sung mã sinh viên, khoa, ngành, giới tính. |

Frontend không tự mở thêm ngày ngoài cửa sổ backend. Khả năng đọc cấu hình cửa sổ
khác được kiểm tra bằng fixture riêng trong unit/component test; đó không phải
cấu hình 30 ngày của môi trường hiện tại. Catalog E2E tự động cố ý tối thiểu một
nhu cầu có mã tùy ý để kiểm chứng hợp đồng; catalog thử tay có năm nhu cầu.

## Luồng chức năng

### Phiên và phân quyền

- Khởi động/reload xác minh `/api/v1/auth/me` trước khi hiện màn hình cá nhân.
- Route cần đăng nhập gồm `/home`, `/profile`, `/booking`, `/booking/result`,
  `/appointments` và `/appointments/<uuid>`. Backend kiểm tra quyền trên từng API.
- Return path giới hạn trong các route nội bộ được hỗ trợ. STAFF/ADMIN thấy thông
  báo chưa được hỗ trợ, không được giả lập thành sinh viên.
- Quay lại tab thì ẩn và vô hiệu hóa UI cá nhân trong lúc xác minh. Cùng tài khoản
  giữ draft; đổi danh tính/vai trò hoặc logout xóa dữ liệu phiên cũ. Hết phiên ẩn
  UI cá nhân và chỉ giữ intent unknown trong bộ nhớ để đúng sinh viên phục hồi.
- Logout lỗi vẫn ẩn dữ liệu riêng và báo lỗi; chỉ xác nhận kết thúc phiên khi backend
  trả thành công hoặc INVALID_SESSION.

### Đặt lịch và phục hồi kết quả

1. Đọc nhu cầu và cố vấn có phân trang từ API. Chỉ yêu cầu mô tả khi nhu cầu cần nó.
2. Lấy ngày nghiệp vụ, timezone và biên ngày từ `/booking-context`; chọn ngày rồi
   tải availability. Đổi lựa chọn loại bỏ giờ cũ và hủy request cũ.
3. Kiểm tra lại thông tin sinh viên và cuộc hẹn. Khi gửi, tạo một intent gồm user ID,
   khóa ngẫu nhiên và body bất biến; các lần bấm đồng thời dùng chung một Promise.
4. Lấy CSRF kèm ID người sở hữu, xác minh lại danh tính rồi gửi body đã duyệt bằng
   đúng token đó. Lỗi mạng/timeout/5xx giữ khóa và body để thử lại cùng yêu cầu;
   lỗi CSRF/hết phiên ở retry không xóa kết quả unknown của lần trước. Không tự
   gửi yêu cầu mới với khóa khác.
5. Khi có receipt, mở `/booking/result?id=<uuid>` và GET chi tiết hiện tại. GET lỗi
   vẫn giữ UUID để tải lại; receipt lúc tạo không thay thế trạng thái hiện tại.

BOOKING_CONFLICT bỏ giờ đã chọn và tải lại availability; quota hướng tới lịch sử;
404/422 làm mới ngữ cảnh/lựa chọn; IDEMPOTENCY_CONFLICT yêu cầu kiểm tra lịch sử.
Lỗi hiển thị thông báo tiếng Việt, mã hỗ trợ và Retry-After nếu có; không hiện raw
error body, SQL hoặc stack trace.

### Lịch sử và hủy

Danh sách phân trang phía server và lọc đủ năm trạng thái. Chi tiết lấy tên cố vấn,
nhu cầu và thời lượng từ dữ liệu lịch sử, không cần danh mục hiện tại còn hoạt động.
Hủy có bước xác nhận và lý do tùy chọn. Backend kiểm tra quyền, trạng thái và hạn
trước 00:00 ngày hẹn theo múi giờ nghiệp vụ. Sau thao tác, UI đọc lại chi tiết;
kết quả chưa rõ cũng được đối chiếu trước khi cho gửi lại.

### Hồ sơ sinh viên

Mục sidebar **Thông tin sinh viên** mở `/profile`, có breadcrumb và trạng thái đang
chọn. Trang hiển thị họ tên, email, mã sinh viên, khoa, ngành, giới tính, vai trò.
Thông tin chưa có hiển thị **Chưa cập nhật**. MALE/FEMALE/OTHER/UNDISCLOSED lần lượt
hiển thị Nam/Nữ/Khác/Không cung cấp. Trang chỉ đọc; Home tập trung vào dịch vụ.

`GET /api/v1/students/me` lấy user ID từ session STUDENT đang hoạt động, từ chối
mọi query parameter, trả các trường công khai được khai báo trong response schema.
Không trả password hash hoặc cho phép chọn hồ sơ khác. Thành công và lỗi đều dùng
`private, no-store`, X-Request-ID và middleware giới hạn thời gian hiện có.
Contract `/auth/login` và `/auth/me` giữ nguyên.

## Kiến trúc, cấu hình và dữ liệu

| Thành phần                | Trách nhiệm                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------- |
| ApplicationProvider       | Chọn ApiProvider hoặc DemoProvider theo mode lúc build.                               |
| ApiProvider / SessionGate | Xác minh danh tính, ranh giới dữ liệu cá nhân, logout và return path.                 |
| ApiServices               | URL tương đối cùng origin, DTO, lỗi, cookie, CSRF, abort và timeout 15 giây/request.  |
| BookingIntentStore        | Giữ một khóa/body trong bộ nhớ qua điều hướng client và retry chưa rõ kết quả.        |
| useApiResource            | Loading/error/retry, hủy request và bỏ kết quả cũ kể cả chuỗi A → B → A.              |
| Next rewrite              | Chuyển /api/v1/* tới API_UPSTREAM_URL ở phía server.                                  |
| FastAPI profile           | Route mỏng → service giao dịch đọc có timeout → repository giới hạn STUDENT hiện tại. |

`NEXT_PUBLIC_APP_MODE` nhận `api` hoặc `mock`; đổi mode cần build lại. Không có
fallback sang dữ liệu giả khi API lỗi. `API_UPSTREAM_URL` phải là HTTP(S) origin,
không có credentials/path/query/hash; không đưa upstream hoặc secret backend vào
biến `NEXT_PUBLIC_*`. Cookie phiên là HttpOnly; credentials/token/nội dung tư vấn
không được ghi vào localStorage hoặc sessionStorage trong chế độ API.

### Thay đổi schema

Migration **20261017_2350_student_profile**, nối tiếp **20261016_1200_audit_reqid128**:

| Cột users    | Kiểu                          | Ràng buộc                                      |
| ------------ | ----------------------------- | ---------------------------------------------- |
| student_code | varchar(50)                   | NULL khi chưa có dữ liệu.                      |
| faculty      | varchar(150)                  | Nullable.                                      |
| major        | varchar(150)                  | Nullable.                                      |
| gender       | varchar(20), utf8mb4_0900_bin | Nullable; CHECK MALE/FEMALE/OTHER/UNDISCLOSED. |

Upgrade không sửa danh tính, mật khẩu hoặc vai trò tài khoản cũ. Downgrade từ chối
nếu bất kỳ cột hồ sơ nào có dữ liệu, trước khi xóa cột. Không bổ sung ràng buộc duy
nhất cho mã sinh viên hoặc luồng nhập/chỉnh sửa dữ liệu vì chưa có yêu cầu đó.
`.env.example`, readiness assertions, từ điển/ERD và OpenAPI theo revision mới.

### Chạy local và thứ tự triển khai

1. Dùng Node 24, npm 12.2.0, Python 3.11+ và MySQL 8.4. Cài `npm ci`,
   `python -m pip install -r requirements-dev.txt`, `python -m pip install -e .`.
2. Thiết lập backend theo [hướng dẫn session](session-authentication.md). Sao lưu
   database cần giữ trước migration; chạy `python -m alembic upgrade head`, kiểm tra
   `python -m alembic current` và chỉ một `python -m alembic heads`.
3. Đặt `ACCEPTED_SCHEMA_REVISIONS=20261017_2350_student_profile`; triển khai backend
   mới trước frontend có `/profile`. Readiness phải thành công sau migration.
4. Local HTTP: `APPLICATION_ORIGIN=http://127.0.0.1:3000`, `COOKIE_SECURE=false`.
   Chạy Uvicorn theo [hướng dẫn tích hợp](frontend-api-integration.md).
5. Copy `src/frontend/.env.example` sang `.env.local` cùng thư mục, đặt mode API và
   upstream backend, rồi chạy `npm --workspace src/frontend run dev`.
6. Kiểm tra login → profile → booking → reload → history/detail → cancel. Khi
   triển khai HTTPS, dùng origin thực tế và cấu hình cookie phù hợp môi trường.

Nếu cần quay lại bản ứng dụng cũ, không tự động downgrade schema đã có thông tin
hồ sơ. Có thể giữ các cột bổ sung; bản ứng dụng cũ cần cấu hình readiness phù hợp
với revision thực tế. Chỉ downgrade sau khi đã bảo toàn và xử lý dữ liệu theo kế
hoạch được duyệt; guard migration không được bỏ qua để xóa dữ liệu.

## Kiểm thử và bằng chứng

Kết quả local cuối cùng, trước lần rà soát tài liệu này:

| Kiểm tra                    | Kết quả                       | Phạm vi/bằng chứng                                                                                       |
| --------------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------- |
| Unit/component frontend     | 118 passed                    | HTTP/DTO, intent, session, stale response, ngày giờ, hồ sơ thiếu dữ liệu và hồi quy mock.                |
| Mock E2E                    | 66 passed, 0 retry            | Desktop/mobile; chạy thêm 2 ca direct-route sau khi bổ sung /profile và đều đạt.                         |
| Real API E2E                | 16 passed, 0 retry/skip/flaky | Next production → FastAPI → MySQL 8.4.11; desktop/mobile với timezone browser khác nghiệp vụ.            |
| Backend full                | 638 passed, 0 failed/skipped  | Lần chạy cuối 738,85 giây; XML local build/backend-profile-final-tests.xml.                              |
| Backend focused             | 5 passed                      | Hai ca hủy kết quả chưa rõ, readiness, hai ca migration. Các ca này cũng nằm trong full suite.           |
| Build/type/style            | Đạt                           | API/mock production builds, TypeScript, lint/Prettier, Black/Ruff, Pyright 0 errors/warnings, pip check. |
| OpenAPI                     | Khớp export mới               | SHA-256 6E442F51218B0B7800A6B9A611EE5B86DBEC0FA0650F5EBAF91B762110B164E9.                                |
| Runtime giới hạn ngày       | Đạt                           | Ngày nghiệp vụ 18/10: chỉ 19–25/10; D+8 availability 422, tạo lịch 409, không thêm appointment.          |
| Runtime catalog/profile     | Đạt                           | 5 nhu cầu khi thử tay; profile private/no-store, đổi tài khoản không còn dữ liệu người trước.            |
| Kiểm tra giao diện thủ công | Đạt các đường đi đã quan sát  | Hướng dẫn login, chọn nhu cầu 30/45/60 phút, OTHER cần mô tả, ngày cuối cửa sổ, hồ sơ desktop/mobile.    |

Các số lượng không cộng thành một tổng test vì có ca chạy bổ sung/chạy lại.
Lần full backend trước có 634/636 passed: readiness còn mong revision cũ và một ca
hủy trả SERVICE_UNAVAILABLE khi chạy cùng E2E. Đã cập nhật revision; ca hủy chạy
riêng đạt mà không sửa code hủy/tăng timeout. Full run cuối chạy riêng đủ 638 đạt.
Giữ XML lần trước ở `build/backend-profile-tests.xml` để đối chiếu.

Lần review này đối chiếu SHA-256 của toàn bộ **79 file ban đầu** với bản ghi đã
kiểm thử: **0 file khác**. Không sửa mã chạy hoặc test sau lần kiểm thử đó; chỉ sửa
tài liệu, thay hai ảnh bằng ảnh của chính API E2E đã đạt và bổ sung ảnh bảy ngày.
Vì vậy không chạy lại full suite chỉ để xác nhận thay đổi tài liệu. Đã kiểm tra lại
diff whitespace, liên kết nội bộ, danh sách file và nguồn của các ảnh bổ sung.

Máy local dùng Node 24.19.0, Python 3.12.14, MySQL 8.4.11 native do Docker Desktop
gặp lỗi inference manager. Đây là bằng chứng local; chưa có kết quả CI cho commit
mới. Workflow sẽ chạy mock và API/MySQL riêng khi thay đổi được push/PR.

### Ảnh trong repository

| Ảnh                                                                                   | Nội dung                                                                      |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [Kết quả desktop](../evidence/frontend-api-integration/desktop-result.png)            | Ảnh API E2E cuối, có sidebar hồ sơ và nhãn Giờ Việt Nam; che nội dung tư vấn. |
| [Kết quả mobile](../evidence/frontend-api-integration/mobile-result.png)              | Cùng flow API E2E trên mobile; che nội dung tư vấn.                           |
| [Cửa sổ bảy ngày](../evidence/frontend-api-integration/booking-window-seven-days.jpg) | Thử tay ngày 18/10, thẻ 19–25/10 và ca khóa luận 60 phút.                     |
| [Hồ sơ desktop](../evidence/student-profile/desktop.jpg)                              | Sidebar đang chọn và đủ bảy thông tin.                                        |
| [Hồ sơ mobile đầu trang](../evidence/student-profile/mobile-top.jpg)                  | Điều hướng và hồ sơ xếp dọc.                                                  |
| [Hồ sơ mobile phần chi tiết](../evidence/student-profile/mobile-details.jpg)          | Đã cuộn kiểm tra các trường và footer, không tràn ngang.                      |

Tất cả tài khoản và thông tin trong ảnh đều là fixture hư cấu. Ảnh kết quả PNG lấy
nguyên bản từ `test-results/frontend-api/*/real-result.png` của run 16 ca đạt.
Ảnh thử tay JPEG được giữ nguyên định dạng nguồn. Không đưa mật khẩu/token vào ảnh.

### Chạy lại các bộ kiểm tra

Từ gốc repository, xem các lệnh kiểm tra code ở [README](../../README.md).
Tách riêng hai build E2E; không dùng build API để chạy suite mock hoặc ngược lại.

Mock trên PowerShell:

```powershell
$env:NEXT_PUBLIC_APP_MODE='mock'
npm --workspace src/frontend run build
npm --workspace src/frontend run test:e2e
```

API E2E trên PowerShell, không chạy đè server thử tay ở 3101/8101:

```powershell
$env:MYSQL_TEST_URL='mysql+asyncmy://test_user:test_password@127.0.0.1:3306'
$env:FRONTEND_E2E_PYTHON=(Resolve-Path '.venv/Scripts/python.exe').Path
$env:NEXT_PUBLIC_APP_MODE='api'
$env:API_UPSTREAM_URL='http://127.0.0.1:8101'
npm --workspace src/frontend run build
npm --workspace src/frontend exec -- playwright install chromium
npm --workspace src/frontend run test:e2e:api
```

MYSQL_TEST_URL phải trỏ đến MySQL kiểm thử riêng có quyền tạo/xóa schema; harness
chỉ ghi/xóa schema `prl_frontend_e2e_<uuid>` mà nó sở hữu. Thiếu DB thì fail, không
skip. Teardown dọn schema. Lệnh `manual-catalog` trong hướng dẫn tích hợp dành cho
thử tay; khởi động lại harness trước E2E để trở về fixture tối thiểu.

Backend, trong môi trường đã cài dependencies và đặt MYSQL_TEST_URL:

```powershell
python -m black --check src/backend tests/backend tools/scripts/export_openapi.py tools/scripts/frontend_e2e_backend.py
python -m ruff check src/backend tests/backend tools/scripts/export_openapi.py tools/scripts/frontend_e2e_backend.py
pyright
python -m pytest tests/backend -q --junitxml=build/backend-tests.xml
```

Export OpenAPI sang file tạm theo tùy chọn `--output` của
`python tools/scripts/export_openapi.py`, rồi so sánh với `docs/api/openapi.yaml`.
Không gộp kết quả local thành tuyên bố CI đã đạt.

## Danh sách toàn bộ file được rà soát

Đối chiếu **HEAD → working tree và toàn bộ file untracked**, gồm cả phần staged
và unstaged. Tổng sau bổ sung tài liệu/ảnh trong lần review này: **81 file**.
Mỗi dòng dưới đây đã được đọc diff/toàn bộ file mới hoặc kiểm tra trực quan với ảnh.

### Cấu hình và tài liệu — 15 file

| File từ gốc repository                                                 | Nội dung đã đối chiếu                                                          |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `.env.example`                                                         | Revision schema được backend chấp nhận sau migration hồ sơ.                    |
| `.github/workflows/frontend.yml`                                       | Mock job rõ mode; API job MySQL 8.4.11, schema riêng, E2E không retry.         |
| `README.md`                                                            | Hướng dẫn API mặc định, /profile, hai cách build/test riêng và liên kết mô tả. |
| `docs/api/openapi.yaml`                                                | Schema profile và GET students/me; export khớp mã nguồn.                       |
| `docs/architecture/database-design.md`                                 | ERD, kiểu và nullable của bốn cột hồ sơ; ngày/version tài liệu.                |
| `docs/tasks/frontend-api-integration.md`                               | Setup, session, DTO, intent, retry/cancel, fixtures, CI và kết quả hiện tại.   |
| `docs/tasks/frontend-handoff.md`                                       | Đánh dấu tài liệu mock lịch sử, dẫn sang hướng dẫn API hiện tại.               |
| `docs/tasks/student-profile.md`                                        | Yêu cầu hồ sơ, trường/API, quyền, migration, kiểm thử và ảnh.                  |
| `docs/tasks/issue-20-change-description.md`                            | Mô tả đầy đủ, review từng file và giới hạn trước commit.                       |
| `docs/evidence/frontend-api-integration/desktop-result.png`            | Ảnh desktop mới nhất, đã che description, không có dữ liệu thật.               |
| `docs/evidence/frontend-api-integration/mobile-result.png`             | Ảnh mobile mới nhất, đã che description.                                       |
| `docs/evidence/frontend-api-integration/booking-window-seven-days.jpg` | Bảy ngày và ca 60 phút từ lần thử tay.                                         |
| `docs/evidence/student-profile/desktop.jpg`                            | Hồ sơ desktop của tài khoản hư cấu B.                                          |
| `docs/evidence/student-profile/mobile-top.jpg`                         | Hồ sơ và điều hướng mobile.                                                    |
| `docs/evidence/student-profile/mobile-details.jpg`                     | Các trường hồ sơ mobile và footer.                                             |

### Backend — 8 file

| File từ gốc repository                                             | Nội dung đã đối chiếu                                                       |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| `src/backend/app/api/__init__.py`                                  | Đăng ký router hồ sơ vào api_v1_router.                                     |
| `src/backend/app/api/student_profile.py`                           | Session STUDENT, không nhận ID/query, response khai báo rõ.                 |
| `src/backend/app/core/response_policy.py`                          | Private middleware và request deadline bao gồm profile GET.                 |
| `src/backend/app/models/user.py`                                   | Bốn cột nullable và CHECK gender đồng nhất migration.                       |
| `src/backend/app/repositories/users.py`                            | Truy vấn theo session user ID, STUDENT, active.                             |
| `src/backend/app/schemas/student_profile.py`                       | Chỉ các trường được phép, enum gender, tương thích tài khoản thiếu dữ liệu. |
| `src/backend/app/services/student_profile.py`                      | Giao dịch đọc có timeout, response không lộ trường nội bộ.                  |
| `src/backend/migrations/versions/20261017_2350_student_profile.py` | Đúng cha/head, upgrade giữ tài khoản cũ, downgrade có guard dữ liệu.        |

### Frontend — 44 file

| File từ gốc repository                                   | Nội dung đã đối chiếu                                                                |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `src/frontend/.env.example`                              | Mode và server-only upstream; không có secret thật.                                  |
| `src/frontend/app/layout.tsx`                            | ApplicationProvider và metadata cổng sinh viên.                                      |
| `src/frontend/app/login/page.tsx`                        | Link khám phá cũ chỉ còn trong mock.                                                 |
| `src/frontend/app/profile/layout.tsx`                    | PortalShell chọn đúng mục profile.                                                   |
| `src/frontend/app/profile/page.tsx`                      | Metadata và route composition.                                                       |
| `src/frontend/components/app-shell.tsx`                  | Điều hướng/banner demo chỉ hiện khi mode mock.                                       |
| `src/frontend/components/application-provider.tsx`       | Provider theo mode và gate route riêng tư trong Suspense.                            |
| `src/frontend/components/demo-page.tsx`                  | Action tùy chọn để login API bỏ link không phù hợp.                                  |
| `src/frontend/components/portal-account.tsx`             | Tách account/profile API và mock, không gọi nhầm context.                            |
| `src/frontend/components/portal-shell.tsx`               | Sidebar hồ sơ, aria-current, breadcrumb và nhãn mode.                                |
| `src/frontend/features/api/api-account.tsx`              | Danh tính thật và logout qua API provider.                                           |
| `src/frontend/features/api/api-appointments.tsx`         | History phân trang/filter, GET detail, trạng thái hiện tại, xác nhận hủy và refresh. |
| `src/frontend/features/api/api-booking-fields.tsx`       | Catalog/staff phân trang, ngày có biên, một selector, availability và thời lượng.    |
| `src/frontend/features/api/api-booking-flow.tsx`         | Wizard/reducer, context server, immutable intent và hướng xử lý lỗi.                 |
| `src/frontend/features/api/api-feedback.tsx`             | Loading/error/retry và focus thông báo.                                              |
| `src/frontend/features/api/api-home.tsx`                 | Trang chủ API không còn thẻ hồ sơ trùng.                                             |
| `src/frontend/features/api/api-login.tsx`                | Login thật, abort, bỏ password sau submit, hướng dẫn mở tại chỗ.                     |
| `src/frontend/features/api/api-provider.tsx`             | Bootstrap/focus verification, account switch, clear session/intent, logout lỗi.      |
| `src/frontend/features/api/api-student-profile.tsx`      | GET profile và loading/error/retry.                                                  |
| `src/frontend/features/api/session-gate.tsx`             | Bảo vệ UI, safe return path, role và giữ draft khi xác minh cùng người.              |
| `src/frontend/features/api/use-api-resource.ts`          | Request identity, cancellation và loại response cũ.                                  |
| `src/frontend/features/booking/booking-flow.tsx`         | Dispatch wizard API/mock qua component riêng.                                        |
| `src/frontend/features/booking/booking-history.tsx`      | Dispatch history API/mock.                                                           |
| `src/frontend/features/booking/booking-result.tsx`       | Dispatch result/detail API/mock.                                                     |
| `src/frontend/features/booking/booking-steps.tsx`        | Summary nhận timezone API; mô tả chỉ hiển thị khi có.                                |
| `src/frontend/features/booking/booking.module.css`       | Phân trang co dòng và khu vực hủy.                                                   |
| `src/frontend/features/home/home-view.tsx`               | Dispatch API/mock, gỡ hồ sơ mock khỏi Home.                                          |
| `src/frontend/features/home/home.module.css`             | Home một cột, xóa style thẻ hồ sơ không dùng.                                        |
| `src/frontend/features/login/login-flow.tsx`             | Dispatch API/mock với hook trong component tương ứng.                                |
| `src/frontend/features/login/login.module.css`           | Hướng dẫn details/summary, focus keyboard.                                           |
| `src/frontend/features/profile/profile-view.tsx`         | Chọn hồ sơ API hoặc mock, không tạo giả thông tin còn thiếu.                         |
| `src/frontend/features/profile/profile.module.css`       | Grid desktop/mobile và ngắt dòng email/tên dài.                                      |
| `src/frontend/features/profile/student-profile-card.tsx` | Bảy thông tin, null/blank fallback và nhãn giới tính.                                |
| `src/frontend/lib/application-mode.ts`                   | API mặc định; mock chỉ khi cấu hình rõ.                                              |
| `src/frontend/lib/business-time.ts`                      | Nhãn giờ Việt Nam, timezone server, ISO timestamps microseconds.                     |
| `src/frontend/next.config.ts`                            | Validate mode/upstream, rewrite cùng origin.                                         |
| `src/frontend/package.json`                              | Script test:e2e:api, không thay phiên bản dependency.                                |
| `src/frontend/playwright.api.config.ts`                  | Hai viewport, timezone browser khác, một worker, không retry, server riêng.          |
| `src/frontend/services/api/booking-intent.ts`            | Khóa/body bất biến, deduplicate, account guard, unknown outcome và receipt.          |
| `src/frontend/services/api/client.ts`                    | DTO wire/UI, cookie/CSRF, timeout/abort, mã lỗi và endpoints thật.                   |
| `src/frontend/services/api/errors.ts`                    | Mã nghiệp vụ riêng, lỗi mạng/timeout, support ID và Retry-After.                     |
| `src/frontend/types/api.ts`                              | DTO profile/session/context/page/appointment/body/receipt riêng.                     |
| `src/frontend/types/booking.ts`                          | Purpose code theo server; tách khỏi DTO kết quả mock.                                |
| `src/frontend/vitest.config.ts`                          | Hồi quy mock đặt mode tường minh; test API vẫn dùng provider/services thật.          |

### Kiểm thử và công cụ — 14 file

| File từ gốc repository                                        | Nội dung đã đối chiếu                                                                                               |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `tests/backend/integration/test_health.py`                    | Readiness nhận migration head mới.                                                                                  |
| `tests/backend/integration/test_mysql_models.py`              | Revision/current/no-op upgrade theo head mới.                                                                       |
| `tests/backend/integration/test_student_profile.py`           | Owned fields, NULL, anonymous/roles, ID/query và lỗi DB được làm sạch.                                              |
| `tests/backend/integration/test_student_profile_migration.py` | Legacy account được giữ và downgrade từ chối mất hồ sơ.                                                             |
| `tests/backend/unit/test_openapi_export.py`                   | Endpoint profile nằm trong tập paths export xác định.                                                               |
| `tests/frontend/api-e2e/global-teardown.ts`                   | Dọn schema qua harness, giữ cùng Python/PYTHONPATH.                                                                 |
| `tests/frontend/api-e2e/portal.spec.ts`                       | 8 kịch bản × 2 viewport; hồ sơ/reload/đổi tài khoản, vòng đời lịch, response loss, quyền, expiry, role, pagination. |
| `tests/frontend/components/api-resources.test.tsx`            | Thiếu hồ sơ, stale A → B → A, cửa sổ chuẩn 7 ngày và cấu hình khác có biên.                                         |
| `tests/frontend/components/api-session.test.tsx`              | Hướng dẫn login, draft/session/focus/logout và detail hiện tại.                                                     |
| `tests/frontend/e2e/frontend-smoke.spec.ts`                   | Direct-route thêm /profile và keyboard tới main.                                                                    |
| `tests/frontend/e2e/login-flow.spec.ts`                       | Mock profile chuyển sang sidebar rồi tiếp tục đặt lịch.                                                             |
| `tests/frontend/unit/api-services.test.ts`                    | HTTP/DTO/CSRF, errors, timeout/abort, immutable retry, deduplicate và account switch.                               |
| `tests/frontend/unit/business-time.test.ts`                   | Timestamp backend microseconds ở timezone khác.                                                                     |
| `tools/scripts/frontend_e2e_backend.py`                       | Schema ownership, Alembic, fixture hư cấu 7 ngày, loss sau commit, đếm DB, manual catalog và teardown.              |

## Kết luận review và phần còn giới hạn

Không phát hiện lỗi chặn mới trong mã được rà soát. Đã sửa hai điểm lệch của tài
liệu/bằng chứng: PR body cũ còn số test 115/14/628 và thiếu hồ sơ; ảnh kết quả cũ
chưa có sidebar hồ sơ/nhãn giờ mới. README cũng được tách lệnh build/test theo mode
để tránh chạy suite mock trên build API. Bản mô tả này và draft PR local dùng kết
quả 118/66/16/638 hiện tại; không đặt placeholder commit như một kết quả đã commit.

Các giới hạn cần giữ rõ khi review:

- Intent chưa có receipt chỉ tồn tại trong bộ nhớ. Full reload/đóng tab mất intent;
  người dùng phải kiểm tra lịch sử trước khi tạo yêu cầu khác. Lịch đã commit vẫn còn.
- Hồ sơ chỉ đọc, bốn trường mới nullable. Việc cung cấp dữ liệu thật/luồng cập nhật
  và chính sách mã sinh viên duy nhất cần yêu cầu riêng.
- Catalog/khả dụng/quota/cửa sổ ngày lấy từ backend. Lệnh nạp năm nhu cầu chỉ phục vụ
  schema test, không tự seed production hoặc thay cấu hình business database khác.
- Docker Desktop trên máy vẫn lỗi; kiểm thử DB đã chạy bằng MySQL native. Chưa kiểm
  chứng triển khai production, required CI checks hoặc approval trên một PR mới.
- Working tree có cả staged, unstaged và file mới. Trước commit cần đưa đúng toàn bộ
  phạm vi cuối vào index và kiểm tra diff staged; không dùng riêng index cũ để commit.

Các report XML/HTML, bản ghi hash và draft PR trong `build/`, `test-results/`,
`playwright-report/` đều là artifact local bị ignore, không thuộc 81 file dự kiến.
Bản ghi `build/issue20-verification.json` giữ base SHA, kết quả đã chạy và hash từng
file; `build/manual-issue20/final-report.md` ghi các bước thử tay đã quan sát.
Không thực hiện commit/push/PR trong bước rà soát này.

## Bổ sung theo comment review PR #23 ngày 18/10/2026

Lượt full backend phát hiện thêm lỗi parser duplicate-key khi UUID/key chứa ký
tự nháy. Đã tái hiện cố định trước sửa và kiểm tra 23 ca retry/timeout sau sửa.
`src/backend/app/services/booking.py` đọc message gốc từ driver; hai file
`tests/backend/unit/test_booking_service_retries.py` và
`tests/backend/integration/test_booking_timeouts.py` bao phủ nháy, backslash và
chuỗi giả constraint nằm trong entry. Timeout ứng dụng không thay đổi; kết quả
full backend/CI được xác minh riêng trên head mới.

Đã đồng bộ `develop` tại `3502047` sau PR #24 để giải quyết xung đột chặn CI.
Bốn file hợp nhất là `src/backend/app/api/__init__.py` (đăng ký cả hai router),
`src/backend/app/core/response_policy.py` (privacy/deadline cho cả hai nhóm),
`tests/backend/unit/test_openapi_export.py` (hợp nhất tập paths) và
`docs/api/openapi.yaml` (export lại). Các file staff API còn lại đi theo lịch sử
`develop`; mô tả của phần đó tại [staff appointment management](staff-appointment-management.md).

## Follow-up review: phục hồi POST đầu tiên và chọn lại giờ sau conflict

Trước commit: 141 unit/component, 30 real API E2E trên desktop/mobile và build/
TypeScript, lint/format đều đạt; harness Black/Ruff đạt. Thử trực tiếp first
in-flight expiry đã phục hồi đúng UUID và chỉ có một bundle booking trong MySQL.
Bằng chứng và lệnh chạy riêng probe được ghi trong tài liệu tích hợp; CI được
xác nhận trên head mới sau push.

Mốc rà soát `4924297`: theo dõi POST dispatch để giữ cả yêu cầu đầu tiên đang
chờ receipt khi hết phiên; chỉ phục hồi cho cùng sinh viên. Phân loại response
POST BOOKING_CONFLICT/quota sau replay thành kết quả xác định và giải phóng intent,
giúp wizard tải lại availability và chọn giờ khác. Lỗi bootstrap/CSRF/session và
transport chưa rõ kết quả vẫn giữ key/body; logout/đổi người vẫn xóa dữ liệu.

| File                                                      | Vai trò trong bản sửa follow-up                                                             |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `src/frontend/services/contracts.ts`                      | Callback nội bộ báo thời điểm dispatch POST, không đổi HTTP body.                           |
| `src/frontend/services/api/client.ts`                     | Báo dispatch sau auth/abort checks, phân loại kết quả POST xác định.                        |
| `src/frontend/services/api/errors.ts`                     | Kiểu BookingRejection để phân biệt với lỗi trước replay/transport.                          |
| `src/frontend/services/api/booking-intent.ts`             | Giữ first in-flight intent, version guard và giải phóng kết quả terminal.                   |
| `tests/frontend/components/api-booking-recovery.test.tsx` | Focus expiry, cùng/khác người/logout, receipt cũ và chọn lại giờ sau conflict.              |
| `tests/frontend/unit/api-services.test.ts`                | Ranh giới dispatch/bootstrap và terminal/retry không rõ kết quả.                            |
| `tests/frontend/api-e2e/portal.spec.ts`                   | Hai ca thật × desktop/mobile: receipt còn pending và conflict sau unknown.                  |
| `tools/scripts/frontend_e2e_backend.py`                   | Giữ một response thật sau commit, release/reset/cleanup có biên; không đổi business routes. |
| `docs/tasks/frontend-api-integration.md`                  | Cập nhật state/recovery và hướng dẫn kiểm chứng.                                            |
| `docs/tasks/issue-20-change-description.md`               | Mô tả/manifest của lần follow-up này.                                                       |

Hai lỗi được tái hiện: đổi cookie giữa kiểm tra danh tính và lấy CSRF có thể
gửi body A dưới phiên B; lỗi 403 của retry xóa intent dù POST trước có thể đã
commit. Bản sửa ràng buộc chủ sở hữu token/intent và giữ kết quả unknown xuyên
suốt lỗi retry hoặc đăng nhập lại cùng sinh viên. Xem
[luồng và kiểm chứng mới](frontend-api-integration.md#review-pr-23-giữ-kết-quả-unknown-qua-lỗi-thử-lại).

| File                                               | Thay đổi sau review                                                                    |
| -------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `src/backend/app/schemas/auth.py`                  | CSRF response thêm `user_id` bắt buộc.                                                 |
| `src/backend/app/api/auth.py`                      | Token và ID chủ sở hữu sinh từ cùng AuthContext.                                       |
| `docs/api/openapi.yaml`                            | Export contract mới; triển khai backend trước frontend.                                |
| `src/frontend/services/api/client.ts`              | Bootstrap CSRF → kiểm tra owner/identity → POST đúng token; cùng AbortSignal.          |
| `src/frontend/services/api/booking-intent.ts`      | Abort/version guard; giữ key/body unknown qua lỗi retry và session expiry.             |
| `src/frontend/features/api/api-provider.tsx`       | Ẩn private UI khi hết phiên; chỉ phục hồi cho cùng STUDENT, xóa khi logout/đổi người.  |
| `tests/backend/integration/test_auth_endpoints.py` | CSRF owner theo session và từ chối token người khác dù cookie/header khớp.             |
| `tests/frontend/unit/api-services.test.ts`         | Owner/session races, clear trong request chờ và giữ unknown qua lỗi retry.             |
| `tests/frontend/components/api-session.test.tsx`   | Global INVALID_SESSION handler, login cùng/khác người và logout.                       |
| `tests/frontend/api-e2e/portal.spec.ts`            | Giữ request auth để đổi cookie; receipt loss → CSRF/expiry → phục hồi; kiểm đếm MySQL. |
| `docs/tasks/frontend-api-integration.md`           | Contract, deployment order, dữ liệu riêng tư, recovery và bằng chứng kiểm tra mới.     |
| `docs/tasks/session-authentication.md`             | Contract CSRF trả owner và thứ tự triển khai tương thích client cũ.                    |
| `docs/tasks/issue-20-change-description.md`        | Cập nhật luồng và manifest bổ sung này.                                                |
