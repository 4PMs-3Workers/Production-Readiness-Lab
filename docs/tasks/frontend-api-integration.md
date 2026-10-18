# Tích hợp cổng sinh viên với API thật — issue #20

Nền triển khai: `develop` tại `c865b1e07dd25cca0e1a1269b29ebdff84606637`
(PR #19). Giữ giao diện HUS, bốn bước đặt lịch và tiếng Việt; backend quyết định
danh tính, quyền, cửa sổ ngày đặt, khả dụng, quota và trạng thái lịch.

Kết quả hiện tại ngày 18/10/2026: **118 unit/component, 66 mock E2E, 16 API E2E,
638 backend tests đạt**. Phạm vi cuối cùng, danh sách từng file, các điểm đã rà soát
và cách triển khai nằm trong [mô tả thay đổi issue #20](issue-20-change-description.md).
Các số liệu ngày 17/10 ở cuối tài liệu được giữ làm lịch sử trước phần bổ sung hồ sơ.

## Chế độ ứng dụng và chạy local

`NEXT_PUBLIC_APP_MODE=api` là mặc định; `mock` phải được cấu hình rõ trước khi
dev/build. Chỉ mock khởi tạo DemoProvider và hiển thị demo controls. API dùng
ApiProvider riêng, không gọi `getDemoStudent`, không dùng demo clock và không
chuyển sang fixtures khi backend lỗi. Mode được đóng vào client bundle lúc build;
đổi mode cần build lại. Không đổi mode giữa build và start.

1. Dùng Node 24 theo `.nvmrc`, npm 12.2.0; chạy `npm ci` từ gốc repository.
2. Cài Python dependencies bằng `python -m pip install -r requirements-dev.txt`
   và `python -m pip install -e .` từ gốc; thiết lập `.env` backend dựa vào
   [session authentication](session-authentication.md). Dùng MySQL 8.4 và một
   database phát triển riêng, chạy `python -m alembic upgrade head`.
3. Đặt backend `APPLICATION_ORIGIN=http://127.0.0.1:3000`, `COOKIE_SECURE=false`,
   `ENVIRONMENT=local`, `ACCEPTED_SCHEMA_REVISIONS=20261017_2350_student_profile`.
   Khởi động `python -m uvicorn app.main:app --app-dir src/backend --host 127.0.0.1 --port 8000 --no-proxy-headers --timeout-keep-alive 30`.
4. Sao chép `src/frontend/.env.example` thành `.env.local` cùng thư mục. Giữ
   `NEXT_PUBLIC_APP_MODE=api`, `API_UPSTREAM_URL=http://127.0.0.1:8000`.
5. Chạy `npm --workspace src/frontend run dev`, mở `/login` tại origin ở bước 3.
   Dùng tài khoản STUDENT trong database phát triển; seed demo #8 là tùy chọn,
   không phải phụ thuộc của test hoặc CI.

`API_UPSTREAM_URL` là origin server-only, không chứa path/query/credentials.
Rewrite Next.js giữ `/api/v1/*` trên origin trình duyệt và chuyển tiếp Cookie,
Set-Cookie, Origin, CSRF header. Dùng nhất quán `127.0.0.1` hoặc `localhost` cùng
scheme và port; thay cổng dev thì phải đổi `APPLICATION_ORIGIN`. Không đưa
DATABASE_URL, cookie secret hay CSRF secret vào `NEXT_PUBLIC_*`.

## HTTP và DTO

`services/api/client.ts` tập trung fetch, DTO và lỗi. Mọi request dùng đường dẫn
tương đối, `credentials: same-origin`, `cache: no-store`, không theo redirect,
AbortSignal và timeout 15 giây mỗi request. Backend trả `private, no-store` cho
dữ liệu riêng tư. Không có response cá nhân trong server-rendered HTML tĩnh.

| Hợp đồng backend                         | DTO/UI                                                                           |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| User `name`, `role`                      | `displayName`, role thực; STAFF/ADMIN có trạng thái chưa hỗ trợ                  |
| `duration_minutes`, `requires_free_text` | `durationMinutes`, `requiresFreeText`; chấp nhận code tùy backend                |
| `items`, `page`, `page_size`, `total`    | `Page<T>`, điều khiển trang trước/sau cho catalog, staff, history                |
| `booking-context`                        | timezone, business date và min/max dùng cho dãy ngày và chuyển tuần              |
| `availability.slots`                     | giữ nguyên starts/ends có timezone; key UI dẫn xuất; location = null             |
| POST appointment                         | chỉ purpose_id, staff_id, starts_at và description khi cần                       |
| Receipt 201/200                          | id, UPCOMING lúc tạo, created_at ban đầu; không dùng làm trạng thái hiện tại     |
| List/detail                              | tên purpose/staff từ dữ liệu lịch sử, duration lưu lúc tạo, toàn bộ 5 trạng thái |

Request cũ bị hủy khi đổi lựa chọn; resource hook dùng danh tính request để xử lý
A → B → A. Giờ hiển thị theo timezone server, kể cả khi browser ở múi giờ khác.
Không giả định availability trả slot ID hay địa điểm. Null location hiển thị
“Địa điểm sẽ được thông báo sau”.

Trang đăng nhập API không có điều hướng demo “Đăng nhập / Đặt lịch / Kết quả”.
“Cách đặt lịch tư vấn” mở hướng dẫn ngay trên trang, không dẫn vào route bị chặn
khi chưa đăng nhập. Tên múi giờ hiển thị bằng tiếng Việt; mã IANA vẫn dùng khi tính
ngày giờ. Chỉ có một bộ chọn ngày dạng thẻ, chuyển tuần trong cửa sổ server cho
phép; nhãn ngày đầy đủ và khoảng ngày dùng `dd/mm/yyyy`, không phụ thuộc định dạng
date input của trình duyệt.

Lỗi envelope được ánh xạ theo code, không hiển thị raw body/SQL/stack trace.
INVALID_CREDENTIALS và LOGIN_THROTTLED/Retry-After có thông báo riêng; 401 đi qua
session handling. CSRF/origin/role, conflict/quota/idempotency, validation,
network/timeout và 503 có hướng xử lý riêng. X-Request-ID xuất hiện khi cần hỗ trợ.
BOOKING_CONFLICT bỏ slot và tải lại giờ trống; quota dẫn tới lịch sử; 404/422 tải
lại ngữ cảnh và lựa chọn. IDEMPOTENCY_CONFLICT không tự sinh key mới để bỏ qua.

## Session và dữ liệu riêng tư

Đầu phiên/reload gọi `/auth/me`; private routes chưa hiển thị dữ liệu khi đang
kiểm tra. `/home`, `/booking`, `/booking/result`, `/appointments` và detail đều
được bảo vệ ở UI; `/profile` là trang hồ sơ riêng theo [yêu cầu bổ sung](student-profile.md).
Quyền thật được backend kiểm tra lại trên mỗi request.
Return path chỉ cho phép các route nội bộ được hỗ trợ.

Login gửi JSON và dùng origin validation. Trước booking/cancel/logout, lấy token
từ `/auth/csrf` rồi gửi X-CSRF-Token cùng cookie. Session cookie là HttpOnly.
Không lưu password/session/CSRF token trong localStorage, sessionStorage hay log.
Quay lại tab thì xác minh lại danh tính trước khi hiện private UI, giữ draft nếu
vẫn cùng danh tính. Lỗi mạng khi kiểm tra phiên giữ intent chờ xác minh; đổi tài khoản,
logout hoặc INVALID_SESSION hủy requests và xóa pending intent/dữ liệu phiên cũ.
Logout lỗi giữ UI cá nhân ở trạng thái đã xóa và báo lỗi, không tuyên bố đã thu hồi
session nếu backend chưa xác nhận.

## Ý định đặt lịch và phục hồi

BookingIntentStore nằm trên route components: user ID, random key, body bất biến,
phase và receipt chỉ ở bộ nhớ của UI session. Description giữ nguyên whitespace.
Gửi lặp đồng thời dùng chung một Promise. Trước khi gửi/retry xác minh `/auth/me`
vẫn là cùng sinh viên; đổi người dùng thì xóa intent, không gửi body cũ.

Timeout, mất kết nối, abort hoặc lỗi server không rõ commit giữ cùng key/body.
UI cung cấp “Thử lại cùng yêu cầu” và liên kết kiểm tra lịch sử; điều này hoạt động
qua điều hướng client/remount. Không tự tạo key mới khi chưa biết kết quả.
Sau receipt, URL `/booking/result?id=<uuid>` gọi GET detail. GET lỗi vẫn giữ ID
và cho phép tải lại detail, không POST booking mới. Receipt replay UPCOMING vẫn
phải hiển thị CANCELLED nếu detail hiện tại đã bị hủy.

Full reload giữ được lịch đã commit qua URL/history. Intent chưa có receipt bị
mất sau reload/đóng tab; không lưu nội dung tư vấn vào browser storage để khôi
phục draft. Trước khi đặt lại, người dùng cần kiểm tra lịch sử. Ứng dụng không
tự POST sau reload.

Hủy cần xác nhận và lý do tùy chọn. Server quyết định cutoff/quyền/trạng thái.
Thành công hoặc lỗi cutoff/state đều tải lại chi tiết; khi kết quả không rõ,
đọc trạng thái hiện tại rồi cho phép gửi lại nếu vẫn chưa hủy. History và
availability luôn fetch lại khi mở route; không có cache lịch cũ để dùng lại.

## Kiểm chứng

Chạy format/lint/typecheck/unit trước. Unit/component suite đặt rõ mode mock cho
các bài hồi quy cũ; test API import provider/services thật và fake riêng transport.

```bash
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test
```

Hồi quy mock: build với `NEXT_PUBLIC_APP_MODE=mock`, rồi `npm --workspace src/frontend run test:e2e`.
E2E API dùng schema riêng khởi tạo bằng Alembic, fixture độc lập (hai sinh viên,
staff, admin và catalog/giờ phục vụ), không phụ thuộc seed 42 tài khoản:

```powershell
$env:MYSQL_TEST_URL='mysql+asyncmy://test_user:test_password@127.0.0.1:3306'
$env:FRONTEND_E2E_PYTHON=(Resolve-Path '.venv/Scripts/python.exe').Path
$env:NEXT_PUBLIC_APP_MODE='api'
$env:API_UPSTREAM_URL='http://127.0.0.1:8101'
npm --workspace src/frontend run build
npm --workspace src/frontend exec -- playwright install chromium
npm --workspace src/frontend run test:e2e:api
```

Trên Linux dùng Python đã cài dependencies trên PATH hoặc đặt FRONTEND_E2E_PYTHON.
MySQL phải là 8.4 và user có quyền tạo/xóa schema. Harness chỉ thao tác schema
`prl_frontend_e2e_<random UUID>` của chính lần chạy, xác thực tên trước cleanup.
Không có MYSQL_TEST_URL hoặc sai phiên bản sẽ fail; không skip. Teardown xóa
schema. Nếu runner bị kill trước teardown, chạy từ gốc với PYTHONPATH=src/backend:
`python tools/scripts/frontend_e2e_backend.py cleanup` bằng cùng MYSQL_TEST_URL.

Harness mặc định chỉ nạp một nhu cầu với mã tùy ý để kiểm tra hợp đồng API, không
phải danh mục cho người dùng cuối. Để thử tay đủ năm nhu cầu trong thiết kế, khi
harness đang chạy, dùng cùng MYSQL_TEST_URL và PYTHONPATH để chạy:
`python tools/scripts/frontend_e2e_backend.py manual-catalog`. Lệnh chỉ ghi vào
schema kiểm thử do harness sở hữu, nạp đủ nhu cầu và liên kết với cố vấn hư cấu;
ẩn nhu cầu E2E ban đầu khỏi catalog nhưng giữ nó cho lịch sử. Cửa sổ đặt của
harness và môi trường thử tay là D+1 đến D+7 theo thiết kế nghiệp vụ. Có thể chạy lặp mà
không thêm bản ghi trùng. Khởi động lại harness trước khi chạy suite E2E tự động
để trở về fixture tối thiểu. Frontend luôn đọc catalog thật từ API.

Suite chạy browser khác timezone nghiệp vụ, desktop/mobile và chứng minh vòng
đời login/book/reload/history/cancel, quyền hai tài khoản, session expiry,
unsupported roles, pagination/empty/deep links. Probe mất phản hồi chỉ làm rỗng
một HTTP response thành công **sau khi FastAPI thật đã commit**; không thay
catalog/booking bằng fixture HTTP hay dùng page.route. Query MySQL kiểm chứng
đúng 1 appointment + 1 creation audit + 2 creation notifications; hủy thêm đúng
1 audit + 2 notifications. Traces và screenshot lỗi tự động tắt để không thu
credentials/nội dung. Ảnh kết quả chủ động chụp sau login và che description.

Uvicorn của harness giữ kết nối idle 30 giây để không đóng socket cùng ranh giới
5 giây với proxy trong Next.js trên Windows. Không bật retry tự động cho thao tác
ghi để che lỗi kết nối; bài mất phản hồi vẫn xác minh cùng key/body qua MySQL.

Ảnh đã kiểm tra trực quan: [desktop](../evidence/frontend-api-integration/desktop-result.png)
và [mobile](../evidence/frontend-api-integration/mobile-result.png). Chỉ có fixture
hư cấu; nội dung trao đổi được che trước khi lưu.

CI `Frontend checks` chạy mock; `Frontend real API checks` build API và chạy
MySQL 8.4.11 thật. Backend workflow hiện có tiếp tục chạy đủ regression.
Không merge/đóng issue trước review approval và required checks trên head được review.

## Kết quả local ngày 17/10/2026

Node 24.19.0, Python 3.12.14, MySQL 8.4.11; native MySQL tại loopback thay Docker
Desktop vì Docker trên máy gặp lỗi khởi động inference manager. Mọi schema test
tách riêng; không dùng dữ liệu demo #8. Hai production build (API/mock), format,
lint, TypeScript, pip check, Black/Ruff, Pyright và so sánh OpenAPI đều đạt.

- Unit/component: 115 passed, bao gồm hồi quy A → B → A, timezone/microseconds,
  giữ draft/intent qua xác minh phiên và xóa intent khi đổi danh tính.
- Mock E2E desktop/mobile: 66 passed, 0 retry.
- API E2E desktop/mobile: 14 passed, 0 retry; query database xác nhận tính duy nhất
  của creation/cancellation audit và notifications.
- Backend: 628 passed, 0 failed, 0 skipped trong 408,10 giây. Chỉ có các warning
  deprecation sẵn có của Alembic/Starlette TestClient.
- Migration smoke: upgrade head, current, heads và upgrade head lần hai đều đạt
  trên schema riêng; revision `20261016_1200_audit_reqid128`.

Đây là bằng chứng local; approval và kết quả CI trên PR head phải được xác nhận
riêng trước merge. Hiện thay đổi còn local, chưa tạo commit/PR. Bản ghi local tại
`build/issue20-verification.json` lưu SHA nền và kết quả; khi tạo PR cần ghi thêm
SHA của commit triển khai đã được review.

## Bổ sung sau review thủ công ngày 18/10/2026

Đã bỏ điều hướng demo khỏi login API, thay liên kết khám phá quay vòng bằng hướng
dẫn mở tại chỗ, Việt hóa tên múi giờ, bỏ date input trùng và giữ cửa sổ thử tay
D+1…D+7. Harness có lệnh manual-catalog nạp đủ năm nhu cầu hư cấu từ database.
Hồ sơ chuyển sang mục sidebar riêng, thêm bốn trường lưu ở backend và endpoint
đọc hồ sơ của chính sinh viên; xem [hồ sơ sinh viên](student-profile.md).

Kết quả cập nhật: 118 unit/component, 66 mock E2E, 16 API E2E và 638 backend tests
đều đạt. Bộ backend cuối chạy riêng, 0 failed/0 skipped; E2E 0 retry. Migration
head là `20261017_2350_student_profile`; kiểm tra tài khoản cũ và từ chối downgrade
làm mất hồ sơ đều đạt. Hai build production, lint/format, TypeScript/Pyright và
artifact OpenAPI được kiểm tra lại. Ảnh hồ sơ desktop/mobile được liên kết trong
tài liệu riêng. Thay đổi vẫn local, chưa commit hoặc tạo PR.

## Sửa CI sau khi push PR #23

Commit đầu `5dd8bb4` gặp lỗi ở bước ESLint: `fixture` trong
`tests/frontend/api-e2e/portal.spec.ts` không còn được dùng sau khi thay date input
bằng thẻ ngày. Đã xóa helper và import `readFileSync` đi kèm; giữ nguyên các kịch
bản và assertions E2E. Lint toàn bộ frontend/tests, Prettier và TypeScript đã chạy
lại local và đạt. Kết quả lint được ghi trước commit đầu chưa phản ánh đúng file
E2E cuối cùng; không dùng kết quả cũ thay cho lần kiểm tra lại này.

Các job API E2E trên commit đầu đã đạt. CI của commit sửa cần được xác nhận riêng
trên [PR #23](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/23).
Review approval là điều kiện riêng của branch protection, không được thay thế
bằng kết quả test hoặc việc tác giả tự kiểm tra.
