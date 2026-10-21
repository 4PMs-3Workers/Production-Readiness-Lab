# Tích hợp cổng sinh viên với API thật — issue #20

Nền triển khai: `develop` tại `c865b1e07dd25cca0e1a1269b29ebdff84606637`
(PR #19). Giữ giao diện HUS, bốn bước đặt lịch và tiếng Việt; backend quyết định
danh tính, quyền, cửa sổ ngày đặt, khả dụng, quota và trạng thái lịch.

Kết quả trước review ngày 18/10/2026: **118 unit/component, 66 mock E2E, 16 API E2E,
638 backend tests đạt**. Kết quả sau review được bổ sung ở cuối tài liệu.
Phạm vi, danh sách từng file, các điểm đã rà soát
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
   `ENVIRONMENT=local`, `ACCEPTED_SCHEMA_REVISIONS=20261019_1200_proposal_replay`.
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
| User `name`, `role`                      | `displayName`, role thực; STAFF/ADMIN chưa được hỗ trợ tại thời điểm issue #20   |
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

Cập nhật issue #27 (21/10/2026): STAFF nay có cổng riêng tại
`/staff/appointments`, `/staff/preferences` và `/staff/unavailability`; thông tin
chi tiết nằm trong [bàn giao cổng STAFF](staff-portal-integration.md). ADMIN vẫn
chưa có giao diện. Các kết quả kiểm thử issue #20 bên dưới là kết quả lịch sử.

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
Nếu wizard đã remount và mất lựa chọn cục bộ, conflict đưa về bước còn thiếu
để chọn lại từ catalog hiện tại, thay vì mở bước ngày/giờ rỗng.

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
vẫn cùng danh tính. Lỗi mạng khi kiểm tra phiên giữ intent chờ xác minh. Đổi tài
khoản hoặc logout hủy requests và xóa intent/dữ liệu phiên cũ. INVALID_SESSION
ẩn UI cá nhân, hủy requests; giữ intent unknown hoặc có POST đang chờ receipt
trong bộ nhớ. Chỉ cho dùng tiếp sau khi xác minh lại đúng sinh viên sở hữu intent.
Logout lỗi giữ UI cá nhân ở trạng thái đã xóa và báo lỗi, không tuyên bố đã thu hồi
session nếu backend chưa xác nhận.

## Ý định đặt lịch và phục hồi

BookingIntentStore nằm trên route components: user ID, random key, body bất biến,
phase và receipt chỉ ở bộ nhớ của UI session. Description giữ nguyên whitespace.
Gửi lặp đồng thời dùng chung một Promise. Trước khi gửi/retry lấy CSRF cùng ID
người sở hữu từ `/auth/csrf`, rồi xác minh `/auth/me` vẫn là cùng sinh viên;
đổi người dùng thì xóa intent, không gửi body cũ. POST dùng đúng token đã kiểm tra.

Timeout, mất kết nối, abort hoặc lỗi server không rõ commit giữ cùng key/body.
UI cung cấp “Thử lại cùng yêu cầu” và liên kết kiểm tra lịch sử; điều này hoạt động
qua điều hướng client/remount. Không tự tạo key mới khi chưa biết kết quả.
Khi intent đã unknown, lỗi CSRF/session trước replay hoặc lỗi transport không
chứng minh lần POST trước chưa commit, nên vẫn giữ nguyên key/body. Ngược lại,
POST trả 409 BOOKING_CONFLICT/BOOKING_QUOTA_EXCEEDED sau kiểm tra idempotency là
kết quả xác định: giải phóng intent để xử lý chọn lại giờ hoặc quản lý hạn mức.
Không suy diễn lỗi bootstrap hay lỗi validate trước replay thành kết quả booking.
Đăng nhập lại cùng sinh viên phục hồi nút thử lại; đăng nhập người khác hoặc
đăng xuất chủ động xóa intent. IDEMPOTENCY_CONFLICT khóa yêu cầu để kiểm tra.
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
catalog/booking bằng fixture HTTP. Các regression đổi tài khoản chỉ giữ thời điểm
gửi request auth bằng `page.route(...).continue()`; response vẫn từ API thật.
Query MySQL kiểm chứng
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

## Ổn định test deadline sau khi chia lịch sử commit

Trên head `35c29f3`, backend push đạt nhưng lượt PR có 637/638 test đạt. Ca
`duplicate-winner-query` trả 503 trước khi vào câu SQL chậm cần kiểm tra. Budget
250 ms còn áp dụng cho lookup đầu tiên; các budget lock/write/request của bước
chuẩn bị cũng có thể hết trước lần đọc lại sau lỗi trùng khóa.

Test đã tách budget chuẩn bị khỏi budget của lần đọc đích, vẫn giữ 250 ms cho
truy vấn đó. Thêm ca làm chậm lookup đầu và lấy lock 750 ms mỗi bước; kiểm tra đã
qua chuẩn bị, đi qua đúng ba lookup, hủy truy vấn chậm, dọn transaction, giải phóng
lock và giữ nguyên số appointment/audit/notification. Thay đổi nằm trong test,
không sửa cấu hình hay timeout của ứng dụng.

Đã tái hiện ca lỗi trước sửa trên MySQL 8.4.11 local. Lần chạy cuối của file timeout
có 12 passed, 0 failed/skipped; Black, Ruff và Pyright đạt. Báo cáo local ở
`build/booking-timeout-before-fix.xml` và `build/booking-timeout-final.xml`.
CI của commit sửa được kiểm tra riêng trên PR #23; bộ backend có thêm một ca,
nên số lượng dự kiến là 639.

## Review PR #23: ràng buộc người sở hữu booking với CSRF

Comment ngày 18/10/2026 chỉ ra khoảng trống giữa xác minh danh tính và lấy CSRF:
tab khác có thể đổi cookie từ A sang B, khiến body của A được POST bằng phiên B.
Luồng tạo lịch nay lấy CSRF trước, kiểm tra chủ sở hữu token và `/auth/me`, rồi
POST bằng chính token vừa kiểm tra; không lấy token mới sau bước xác minh.
Token đã được backend ký theo session nên đổi cookie sau đó sẽ bị từ chối.

`GET /api/v1/auth/csrf` bổ sung `user_id` từ cùng AuthContext đã sinh token.
Frontend đối chiếu ID này với người sở hữu intent, kể cả khi cookie đổi B → A → B
giữa các request. Không thêm ID người đặt vào body booking. Backend cần được
triển khai trước frontend; thiếu metadata này thì frontend từ chối tạo lịch.
OpenAPI được export lại từ schema. Logout/cancel vẫn dùng contract CSRF cũ.

Khi xóa intent, AbortSignal được truyền xuyên suốt bootstrap → xác minh → POST;
response cũ không thể ghi đè intent mới, kể cả transport không tuân thủ abort.
Các regression kiểm tra đổi tài khoản lúc chờ CSRF hoặc `/auth/me`, token cũ
không dùng được với phiên khác, thiếu metadata và việc hủy trong khi chờ.

Kiểm tra trước commit: 125 unit/component, 28 auth integration trên MySQL 8.4.11,
4 regression API browser desktop/mobile đều đạt; production build API, ESLint,
Black/Ruff và Pyright đạt. Đã thử tay bằng trình duyệt: đăng nhập A → chọn nhu cầu,
cố vấn, ngày/giờ → xác nhận thành công → reload vẫn thấy cùng mã lịch hẹn.
Ảnh fixture local: `build/review-p1-manual-result.png`.

## Review PR #23: giữ kết quả unknown qua lỗi thử lại

Một retry bị từ chối CSRF hoặc hết phiên không quyết định kết quả của POST trước.
Store giữ key/body bất biến cùng trạng thái unknown khi retry gặp lỗi; request
đang chờ bị hủy có version guard để không thay đổi intent mới. INVALID_SESSION
chỉ giữ phần phục hồi trong bộ nhớ, đồng thời xóa session UI và ẩn nội dung riêng.
Login cùng ID/vai trò STUDENT cho phép phục hồi. Login khác ID/vai trò hoặc logout
chủ động xóa phần phục hồi, kể cả khi session trước đã hết hạn.

Sáu regression unit/component được chạy trước sửa và đều thất bại: lỗi ở CSRF,
identity hoặc POST của retry; session expiry rồi login cùng người, login khác
người và logout. Sau sửa cả sáu đạt. Bộ API browser thêm mất receipt sau commit →
retry bị INVALID_CSRF thật → retry thành công; và expiry → login cùng/khác sinh
viên. Test chỉ sửa một header để server tự từ chối, không tạo response booking
giả. Các POST phục hồi phải giữ cùng key/body, cùng receipt; MySQL phải có đúng
1 appointment, 1 creation audit và 2 creation notifications.

Kiểm tra trước commit phục hồi: 131 unit/component, 66 mock E2E và 26 API browser
desktop/mobile đạt, không retry. Production build API/mock, ESLint, Prettier và
TypeScript đạt. Đã thử trực tiếp với fixture hư cấu: mất receipt sau commit →
hiện nút thử lại → làm hết phiên → redirect login, nội dung riêng đã ẩn →
đăng nhập lại A → nút thử lại vẫn còn → phục hồi lịch thành công. Query MySQL
có đúng 1 appointment, 1 creation audit và 2 notifications. Ảnh local ở
`build/review-p2-manual-restored-intent.png` và `build/review-p2-manual-result.png`.

Lượt backend đầy đủ đầu tiên có 639 passed, 1 failed: ca duplicate-winner không
nhận diện được lỗi unique khi repr của driver escape ký tự nháy trong entry.
Lỗi này được tái hiện và sửa riêng trong phần kiểm chứng bổ sung; không xem
lượt chạy này là toàn bộ backend đạt.

## Sửa nhận diện unique constraint từ lỗi MySQL

Lỗi duplicate-winner còn lại không do budget: `str(error.orig)` của driver định
dạng tuple và escape nháy trong tên constraint khi entry chứa nháy kép. UUID nhị
phân ngẫu nhiên có thể tạo đúng tình huống này. Parser đọc message gốc `args[1]`,
chỉ nhận tên constraint ở cuối message với dấu nháy đóng/mở khớp; chuỗi giống tên
constraint nằm trong giá trị entry không được dùng để nhận diện.

Trước sửa: hai regression unit chứa nháy và hai ca MySQL duplicate-winner với key
chứa nháy đều thất bại. Sau sửa: 23 ca trong unit retry/timeout integration đạt,
bao gồm hai ca MySQL đó, rollback/lock cleanup và kiểm đếm appointment/audit/
notification. Black, Ruff và Pyright đạt. Không đổi timeout hoặc retry budget.
Bộ backend đầy đủ có thêm bốn parameter unit, dự kiến 644 ca; kết quả đầy đủ
và required checks được đối chiếu trên head mới của PR #23 trước khi báo hoàn tất.

## Đồng bộ develop sau PR #24

Bộ backend đầy đủ tại `e0b6ee6` đã đạt 644/644, không skip. Khi kiểm tra PR,
`develop` đã tiến tới `3502047` (staff appointment management), làm PR xung đột
và chưa chạy được checks pull_request. Hợp nhất router registry, private response/
deadline policy và danh sách paths OpenAPI của cả student profile và staff API;
export lại YAML từ application, không chọn bỏ contract của bên nào.

Sau hợp nhất, 339 ca gồm toàn bộ backend unit và API hồ sơ/lịch cố vấn đã đạt;
Black/Ruff trên 137 file Python và Pyright đạt. Bộ backend thu thập đủ 716 ca.
26 API browser desktop/mobile đã chạy lại sau hợp nhất và đạt, không retry.
CI được kiểm tra trên merge commit mới; không dùng số 644
trước hợp nhất làm kết quả toàn bộ 716 ca.

## Follow-up review trên `4924297`: POST đang chờ và conflict sau unknown

Theo dõi riêng thời điểm bàn giao POST cho fetch, sau CSRF/identity checks và
kiểm tra AbortSignal. Nếu phiên mất khi POST đầu tiên đang chờ receipt, store
chuyển intent sang unknown trước khi hủy operation/version cũ. Body/key giữ
nguyên trong bộ nhớ; private UI bị ẩn. Login cùng STUDENT phục hồi yêu cầu,
login khác người hoặc logout vẫn xóa. Response cũ không ghi đè lần phục hồi.
Mất phiên trong bootstrap đầu tiên khi chưa gửi POST không giữ nhầm intent.

Service phân loại `BookingRejection` chỉ cho response POST 409 với hai mã
BOOKING_CONFLICT/BOOKING_QUOTA_EXCEEDED, vốn được backend kiểm tra sau replay.
Store giải phóng intent cho kết quả này. Xử lý BOOKING_CONFLICT sẵn có của wizard
bỏ slot, tải lại availability và cho chọn giờ khác; key mới chỉ xuất hiện khi
người dùng xác nhận lựa chọn mới. CSRF/session, lỗi transport và validation trước
replay không được coi là bằng chứng giải quyết POST trước đó.

Regression component kiểm tra first POST còn pending → focus INVALID_SESSION →
login cùng/khác người hoặc logout; đồng thời kiểm tra receipt đến muộn. Regression
wizard đi từ lỗi mạng → retry conflict → availability mới → chọn giờ khác và tạo
intent mới. Unit bao phủ bootstrap chưa dispatch, conflict/quota terminal và mã
booking xuất hiện ở sai giai đoạn bootstrap. Các probe chính đã lỗi trước sửa.

Kiểm tra local trước commit follow-up: 141 unit/component, 30 real API E2E
desktop/mobile đạt, không retry; production build/TypeScript, ESLint, Prettier,
Black/Ruff của harness đều đạt. Không thay đổi backend business code hoặc schema.
Các số 131/26/716 ở các mốc trên là bằng chứng lịch sử; CI được kiểm tra riêng
trên head follow-up sau push.

Đã thử trực tiếp: POST đầu tiên hiển thị Đang xác nhận, harness giữ receipt sau
commit; expire phiên A rồi mở lịch sử bằng điều hướng client gây INVALID_SESSION.
Private UI chuyển về login, không reload. Login lại A thấy một lịch đã commit;
quay về booking vẫn có Thử lại cùng yêu cầu. Retry trả cùng UUID; MySQL vẫn đúng
1 appointment, 1 creation audit và 2 notifications. Ảnh local không chứa nội dung
tư vấn: `build/review-followup-manual-restored.png`.

Probe `arm-hold` của harness chỉ giữ response thật sau commit, có giới hạn 30 giây;
`release-response`, reset và cleanup nhả/dọn probe. Ca expiry phải xảy ra khi UI
vẫn Đang xác nhận, trước timeout request 15 giây, để không lẫn với ca đã unknown.
E2E đợi marker commit, xác minh bundle MySQL, expire và phát focus event rồi login
cùng người. Ca conflict abort POST đầu trước backend, xác minh history rỗng, tạo
winner bằng key độc lập, rồi kiểm tra 409 thật, giờ mới và key mới.

Sau khi setup/build API như phần trên, chạy riêng bốn probe desktop/mobile bằng:

```powershell
npm --workspace src/frontend run test:e2e:api -- --grep "session loss while|authoritative conflict"
```

## Follow-up review trên `aa2e23d`: conflict sau khi wizard remount

Intent unknown thuộc provider và sống qua điều hướng client, còn lựa chọn của
wizard thuộc reducer cục bộ. Khi quay từ Booking → Home → Bắt đầu đặt lịch,
wizard mới chưa có nhu cầu/người tư vấn. Retry vẫn gửi nguyên key/body, nhưng
409 BOOKING_CONFLICT trước đây mở thẳng bước ngày/giờ dù thiếu hai lựa chọn đó.

Reducer xử lý conflict bằng cách bỏ slot và kiểm tra điều kiện vào từng bước.
Có nhu cầu hợp lệ và người tư vấn thì trở về ngày/giờ, giữ phần còn lại của draft.
Thiếu người tư vấn thì về bước chọn người; thiếu nhu cầu hoặc mô tả bắt buộc thì
về bước nhu cầu. Sau remount, sinh viên chọn lại từ catalog hiện tại rồi tải
availability mới. Chỉ lần xác nhận giờ mới sinh key mới. Không khôi phục metadata
nhu cầu/cố vấn bằng cách tự suy đoán từ các ID trong intent.

Regression component giữ nguyên provider, tháo/gắn lại booking flow sau lỗi mạng,
retry nhận conflict, chọn lại nhu cầu/người/ngày/giờ và xác nhận bằng key mới.
Ca không remount vẫn kiểm tra giữ draft và tự refresh giờ. Unit bổ sung draft
rỗng, thiếu cố vấn và thiếu mô tả bắt buộc; cả bốn ca mới đều thất bại trước sửa.

API browser chạy cả có/không remount trên desktop và mobile. POST đầu bị abort
trước backend, history rỗng; key độc lập đặt được slot đó. Điều hướng dùng link
client và xác nhận không phát sinh document request. Retry nguyên key/body nhận
409 thật, trở về nhu cầu khi draft rỗng, cho dùng Quay lại/Tiếp tục, tải lại
availability rồi đặt giờ khác. MySQL phải có đúng hai appointment; mỗi lịch có
một creation audit và hai creation notifications, không có booking từ POST đầu.

Kiểm tra trước commit: 145 unit/component đạt; build production API/TypeScript,
ESLint và Prettier đạt. Lượt API đầy đủ đầu có 30 ca đạt và hai ca remount dừng
ở selector link quá chặt (nhãn có thêm mũi tên), chưa tới kiểm tra conflict.
Sau sửa selector, bốn ca conflict có/không remount trên desktop/mobile đều đạt,
không retry. Report riêng: `build/review-remount-api-targeted.json`. Toàn bộ
32 API E2E và 66 mock E2E được kiểm tra tiếp bằng CI trên head mới sau push.

Thử tay trên fixture hư cấu ngày 19/10/2026: giữ khóa booking settings ngắn hạn
trong schema test làm POST đầu trả 503, UI giữ unknown và MySQL không có lịch.
Nhả khóa, tab độc lập đặt 08:00–08:30 ngày 20/10. Tab đầu dùng link Home →
Bắt đầu đặt lịch, giữ nút thử lại; retry nhận conflict và hiện bước nhu cầu.
Chọn lại, thử Quay lại/Tiếp tục, tải giờ mới không còn 08:00, đặt được 08:30–09:00.
MySQL đúng hai lịch, mỗi lịch một creation audit và hai notifications. Ảnh
không chứa nội dung tư vấn: `build/review-remount-manual-recovered.png`;
kiểm đếm: `build/review-remount-manual-database.json`. Probe abort trước backend
và giữ nguyên key/body được xác minh riêng trong API E2E, không thay bằng ca 503.
