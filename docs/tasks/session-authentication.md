# Session authentication — Issue #5

Branch: `feature/session-authentication`, từ `origin/develop` tại `e416ffd`.
Ngày thực hiện: 15/10/2026. Branch đã push và mở PR #12 vào `develop`; chưa merge hoặc đóng Issue.

## Phạm vi và module

Backend dùng lại bảng `users`, `sessions`, revision `a1b2c3d4e5f6` và nền FastAPI #4.
Không sửa migration, không thêm frontend/seed/account administration/business endpoints.

| Module | Trách nhiệm |
|---|---|
| `api/auth.py`, `schemas/auth.py` | Bốn endpoint và schema công khai; user chỉ có id/email/name/role |
| `api/auth_errors.py` | Mã lỗi auth trên AppError/ErrorResponse chung; OpenAPI responses |
| `core/passwords.py` | Argon2id và offload có giới hạn; độc lập FastAPI/settings/database |
| `core/session_tokens.py`, `auth_cookies.py` | Token ngẫu nhiên, digest và cookie policy thống nhất |
| `core/origin.py`, `csrf.py` | So origin đã parse, signed double-submit gắn session |
| `core/login_throttle.py`, `auth_http.py` | Giới hạn attempts/state/body, deadline và cache headers |
| `dependencies/auth.py` | optional_auth, require_auth, require_roles, require_csrf |
| `services/auth.py`, `repositories/users.py`, `repositories/sessions.py` | Transaction, khóa user, truy vấn và thu hồi phiên |
| `domain/auth.py` | Snapshot bất biến, tránh ORM object expired sau transaction |

## Cấu hình

Tiếp tục dùng DATABASE_URL, APPLICATION_ORIGIN, CSRF_SECRET, COOKIE_SECRET và các timeout/pool
settings #4. Secrets phải không rỗng; cung cấp giá trị độc lập có ít nhất 32 byte ngẫu nhiên từ
secret manager cho demo/production. Không dùng giá trị placeholder trong `.env.example` làm secrets thật.
COOKIE_SECRET được giữ vì hợp đồng #4; bearer session không cần ký cookie, CSRF dùng CSRF_SECRET.

| Biến | Mặc định, đơn vị và giới hạn |
|---|---|
| SESSION_LIFETIME_SECONDS | 86400 giây; integer 1..2592000; absolute lifetime, không sliding |
| LOGIN_MAX_BODY_BYTES | 4096 byte; integer >0; byte thực tế trước JSON parser, kể cả chunked/no Content-Length |
| LOGIN_IP_ATTEMPTS | 30 attempts/cửa sổ; integer >0 |
| LOGIN_EMAIL_ATTEMPTS | 5 attempts/email canonical từ DB cho tài khoản đã biết, email chuẩn hóa đã gửi cho tài khoản chưa biết/cửa sổ; integer >0 |
| LOGIN_WINDOW_SECONDS | 60 giây; integer >0; fixed window tính từ attempt đầu |
| LOGIN_STATE_MAX_ENTRIES | 10000 mục mỗi loại IP/email; integer >0 |
| PASSWORD_MAX_CONCURRENCY | 2 tác vụ Argon2 chạy đồng thời mỗi process; integer >0 |
| COOKIE_SECURE | true mặc định; demo/production bắt buộc true và APPLICATION_ORIGIN HTTPS; local HTTP đặt false |
| COOKIE_SAMESITE | Chỉ lax; cấu hình strict/none bị từ chối trong phiên bản auth này |
| COOKIE_DOMAIN | Phải bỏ trống/không đặt; cookie host-only |

Tên cookies cố định `prl_session` và `prl_csrf`, cùng Path=/, SameSite=Lax, expiration theo
server-side session. Session cookie HttpOnly; CSRF cookie không HttpOnly để frontend đọc được ở
mọi trang. Max-Age là phần thời gian còn lại, không gia hạn khi bootstrap CSRF. Xóa cookie giữ
đúng tên/path/domain/security policy. Response auth, kể cả lỗi 500, là `private, no-store`.

Argon2id dùng argon2-cffi 25.1.0, bindings 26.1.0; memory 65536 KiB, time_cost=3,
parallelism=1. Mỗi hash có salt ngẫu nhiên của thư viện. Password giữ nguyên, kể cả khoảng trắng;
email trim/lower theo User model. `needs_rehash` có sẵn; login chưa tự ghi lại password hash.
Unknown account chạy dummy verify; inactive account cũng verify trước cùng phản hồi thất bại.

Hash/verify chạy trong thread với CapacityLimiter chung. Hủy request, kể cả hủy lặp, chờ worker
thực sự xong trước khi kết thúc tác vụ chờ; không giải phóng slot trong khi thread còn dùng RAM.
Request còn xếp hàng chờ admission slot có thể hủy ngay và không chạy hash sau đó;
chỉ công việc đã được admission mới được shield tới lúc native worker kết thúc.
Vì vậy cleanup Argon2 có thể kéo dài hơn deadline nếu thư viện/hash tốn thời gian bất thường.
Không diễn giải deadline 3 giây là cơ chế cưỡng bức dừng native Argon2 thread.

## Session, transaction và quyền

Token chứa 32 byte ngẫu nhiên mật mã, base64url canonical 43 ký tự. DB chỉ lưu SHA-256 của
chuỗi ASCII cookie thành VARBINARY(32), không hex và không bearer token gốc.

Service login kết thúc transaction đọc trước khi verify password. Transaction ghi lấy khóa User
FOR UPDATE, refresh trạng thái/password_hash, lấy UTC_TIMESTAMP(6) sau khóa, insert session và
commit rồi mới trả token cho router cấp cookie. Không giữ khóa DB lúc chạy Argon2.
Login lại tạo token mới và giữ các phiên khác còn hợp lệ. Logout chỉ thu hồi phiên hiện tại.

Authentication đọc session + user hiện tại từ DB, kiểm tra revoked_at, expires_at <= now và
is_active. Role không được lấy từ client hoặc cache trong token. `require_roles` chỉ cho phép
tập role tường minh; ADMIN không có bypass. Kiểm tra ownership/assignment của lịch, proposal,
notification vẫn phải được service nghiệp vụ thực hiện, kể cả sau dependency RBAC.

`revoke_user_sessions(db, user_id)` yêu cầu caller đã mở transaction, lấy cùng khóa User và
không tự commit/rollback. Account administration sau này phải vô hiệu hóa user và gọi helper
trong một transaction. Dùng một AsyncSession riêng cho mỗi request/coroutine; service thường
sở hữu begin/commit, repositories không sở hữu transaction.

Các transaction auth bị giới hạn bởi DB_QUERY_TIMEOUT_SECONDS; khóa bởi DB_LOCK_TIMEOUT_SECONDS;
middleware giới hạn toàn request auth bằng REQUEST_TIMEOUT_SECONDS. DB/timeout error trả 503,
rollback toàn transaction. Nếu kết quả commit chưa xác định, không trả login success/cookies;
có thể tồn tại session không đến được client và nó sẽ hết hạn bình thường. Không tự retry login
sau commit outcome không rõ, không hứa rollback được một commit đã được DB xác nhận.

## Origin, CSRF và throttle

Unsafe requests kiểm Origin; chỉ khi thiếu Origin mới dùng Referer. So parsed scheme, hostname
và effective port; không prefix/substring. localhost và 127.0.0.1 khác origin. Thiếu cả hai,
Origin null/sai/malformed/lặp bị từ chối 403. Login nhận application/json và chưa yêu cầu CSRF
gắn phiên. Không dùng forwarded headers làm nguồn origin/IP đáng tin cậy.

Token CSRF `v1.nonce.mac` có nonce 32 byte, HMAC-SHA256 trên domain prefix + session UUID bytes
+ nonce bytes. Không chứa raw session token. Cookie/header phải khớp, signature đúng và cùng
session; dùng compare_digest. Reissue không vô hiệu token CSRF cũ cùng phiên. Sau session revoke
hoặc expiry, các token CSRF đó cũng không dùng được vì authentication thất bại.

`GET /api/v1/auth/csrf` trả `{csrf_token, user_id}` từ cùng phiên đã xác thực.
Frontend đặt lịch đối chiếu `user_id` với chủ sở hữu intent, sau đó kiểm tra
`/auth/me` và POST bằng chính token bootstrap. Không lấy token mới sau xác minh;
đổi cookie phiên sẽ bị kiểm tra chữ ký CSRF từ chối. Triển khai backend có field
`user_id` trước frontend dùng contract này; client cũ chỉ đọc `csrf_token` vẫn dùng được.

Logout với session hợp lệ cần origin + CSRF. Không/invalid/expired/revoked/inactive session có thể
logout 204 và clear cookies sau origin validation; DB lookup lỗi vẫn 503, không giả báo thành công.

IP limit áp trước body/JSON; email limit áp sau validation và DB lookup, trước Argon2. Tài khoản
đã biết dùng `User.email` canonical nên các cách viết được collation MySQL xem là cùng tài khoản
chia sẻ một bucket; tài khoản chưa biết dùng email đã chuẩn hóa từ request. Đếm cả thành công;
429 kèm Retry-After nguyên dương. Counter fixed-window dùng monotonic clock và khóa để atomic;
mục hết hạn bị dọn khi truy cập, không tạo background worker. Khi capacity đầy không evict mục
đang chặn: từ chối key mới tới cửa sổ có chỗ. Email được lưu dưới dạng digest, không plaintext.

Giới hạn chỉ áp trong một process, reset khi restart, không chia sẻ giữa workers/replicas.
IP limit dùng immediate peer cho kết nối trực tiếp. Qua gateway, API chỉ nhận
một địa chỉ `X-Forwarded-For` canonical từ peer gateway được tin cậy; header do client
gửi bị Nginx thay thế. Thiếu hoặc sai header quay về peer socket. Hai dạng login
có hoặc không có slash dùng chung quota IP trong backend và cùng rate-limit zone
ở Nginx. Bộ đếm backend vẫn theo process, reset khi restart và không chia sẻ giữa
workers/replicas; khi tăng process phải đánh giá lại ngưỡng. Chưa thêm Redis.

## HTTP contract và ví dụ

API prefix `/api/v1`; OpenAPI tại `docs/api/openapi.yaml`. Session dùng cookie API key scheme
`prl_session`. Mã lỗi: INVALID_CREDENTIALS/INVALID_SESSION (401), FORBIDDEN_ROLE/INVALID_CSRF/
INVALID_ORIGIN (403), LOGIN_THROTTLED (429), PAYLOAD_TOO_LARGE (413), UNSUPPORTED_MEDIA_TYPE (415),
VALIDATION_ERROR (422). Các lỗi này thuộc input; DB/timeout là SERVICE_UNAVAILABLE (503)/service.
Error body request_id khớp X-Request-ID; giữ Retry-After và Allow. Không trả SQL, stack trace,
credential, hash hay bearer token trong JSON. CSRF bootstrap chủ ý trả token CSRF cho frontend.
Middleware và error handler so đường dẫn route theo cách Starlette xử lý `root_path`, vì vậy
body limit, IP throttle, request deadline và cache policy vẫn áp dụng khi API nằm dưới URL prefix.

Chạy Uvicorn theo README, dùng tài khoản đã được cấp với hash hợp lệ; Issue này không tạo tài
khoản demo. Ví dụ Python dùng httpx trong requirements-dev, nhập password qua getpass, không in
token/cookie ra terminal. Chạy từ môi trường có API/ứng dụng đúng APPLICATION_ORIGIN; API_BASE
có thể là URL backend local cho công cụ HTTP, APP_ORIGIN vẫn phải trùng cấu hình server:

```python
import getpass
import os
import httpx

base = os.environ.get("API_BASE", "http://localhost:8000")
origin = os.environ.get("APP_ORIGIN", "http://localhost:3000")
with httpx.Client(base_url=base, headers={"Origin": origin}) as client:
    response = client.post("/api/v1/auth/login", json={
        "email": input("Email: "), "password": getpass.getpass("Password: ")})
    response.raise_for_status()
    assert set(response.json()) == {"id", "email", "name", "role"}
    client.get("/api/v1/auth/me").raise_for_status()
    bootstrap = client.get("/api/v1/auth/csrf")
    bootstrap.raise_for_status()
    protected_headers = {"X-CSRF-Token": bootstrap.json()["csrf_token"]}
    # Cùng header/cookie pattern này bảo vệ các write endpoints sau này.
    response = client.post("/api/v1/auth/logout", headers=protected_headers)
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
```

## Kiểm chứng và giới hạn nghiệm thu

Các lệnh từ gốc repo, sau editable install và cung cấp MYSQL_TEST_URL riêng:

```text
python -m pip check
python -m black --check src/backend tests/backend tools/scripts/export_openapi.py
python -m ruff check src/backend tests/backend tools/scripts/export_openapi.py
python -m pyright
python -m pytest tests/backend -q --junitxml=build/backend-tests.xml
python tools/scripts/export_openapi.py
git diff --exit-code -- docs/api/openapi.yaml
```

Lần full suite trước final review: 247 passed, 0 failed, 0 skipped trên Python 3.11.4,
MySQL 8.4.11 Docker tmpfs, port loopback riêng 33073, schema-per-test qua Alembic; 84 warnings
deprecation Alembic/Starlette có từ nền #4. Black/Ruff đạt sau dùng Annotated cho dependencies;
Pyright 0 errors. Các tests bổ sung/fix sau mốc này phải được kiểm tra trong lần cuối bên dưới.
Không coi số liệu này là kết quả cuối hoặc CI của head sau review.

Tests bao phủ login/me/csrf/logout/replay, failure đồng nhất/dummy, cookie policy, 3×3 role matrix,
session invalid/expired/revoked/inactive, origin/CSRF sai và cross-session, limiter TTL/capacity,
chunked payload, DB errors, commit rollback, lock timeout, race login/deactivate hai thứ tự,
đổi hash giữa verify và lock, caller rollback và hủy worker lặp. Tests routes chỉ ở tests.
CI hiện có chạy toàn tests/backend, MySQL 8.4, Alembic smoke, zero-skips gate và OpenAPI diff,
nên không thêm workflow trùng lặp. Kết quả của từng commit xem trong PR #12.

Chưa kiểm chứng browser frontend thật, gateway HTTPS/TLS/cookie qua proxy, multi-process rate
limits, production provisioning hoặc tải demo. Cookie Secure được kiểm bằng HTTP headers/test
client; không suy ra đã kiểm tra HTTPS deployment. Seed #8 và tích hợp frontend #7 là việc khác.
Review/CI/merge vào develop là điều kiện đóng Issue sau này; Issue vẫn mở ở lượt bàn giao local.

Review độc lập trên `6e8d1e2` tìm được hai điểm Important: hash đã hủy khi còn trong queue vẫn
chạy, và thiếu kiểm chứng cancellation ở commit boundary. Điểm đầu được tái hiện RED rồi sửa
bằng cancellable admission trước shield; điểm sau được bổ sung test MySQL hủy sau INSERT/flush
và timeout tại dialect do_commit trước dispatch. Cả hai kiểm tra lại row bằng connection mới,
không cấp cookie và lấy được khóa user sau cleanup. Test không mô phỏng mất acknowledgement
sau khi COMMIT thực sự tới server; trường hợp đó vẫn là kết quả chưa xác định đã ghi ở trên.
Không có finding Minor bị hoãn. Các fix được xác minh bằng tests và full suite, chưa review lại
độc lập lần hai hoặc có CI approval; không dùng kết quả local thay review/CI của nhóm.

## Phối hợp

- #8 dùng PasswordService/PasswordPolicy trực tiếp, không dựng hash implementation khác.
- #7 lấy contract OpenAPI đã review, đọc cookie/CSRF bootstrap, xử lý 401/403/429 và Retry-After.
- Nhóm hạ tầng thống nhất APPLICATION_ORIGIN, HTTPS/Secure, gateway peer và IP/body limits.
- Account administration dùng caller-owned transaction và cùng khóa user; business services
  kiểm quyền trên từng đối tượng. Request-ID/audit-column mismatch của #4 không sửa trong #5.

## Kết quả trên commit 340bbe3 trước phản hồi PR #12 — 15/10/2026

| Kiểm tra | Kết quả |
|---|---|
| Full backend, JUnit, MySQL 8.4.11 | **260 passed, 0 failed, 0 skipped**, 88.34 giây |
| Black | 68 Python files không cần format lại |
| Ruff | All checks passed |
| Pyright 1.1.407 | 0 errors, 0 warnings |
| Editable package + pip check | Cài bằng build isolation chuẩn; No broken requirements found |
| OpenAPI export + git diff | Exit 0, artifact deterministic; đủ health và 4 auth paths |
| Uvicorn thực trên loopback | Readiness/origin/login/me/csrf/logout/replay/repeated logout đạt |
| Cleanup smoke | Uvicorn process kết thúc; schema smoke riêng được xóa trong finally |

Suite dùng Python 3.11.4 ngoài Windows sandbox (sandbox chặn runtime/async DLL); MySQL container
riêng dùng tmpfs, schema test qua Alembic. JUnit `build/backend-tests.xml` ghi zero skips. Có 89
warnings thuộc các loại deprecation Starlette TestClient/httpx và Alembic path_separator của
nền hiện có. Không nâng dependency ngoài Argon2 để xử lý các cảnh báo không thuộc Issue này.
Log kiểm chứng local nằm trong `build/session-auth/` (gitignored); kết quả trên không phải CI.
Commit này đã được push trong PR #12; backend và frontend CI trên commit 340bbe3 đều đạt.
Issue chưa merge/đóng. Kết quả mới cho bản sửa sau phản hồi PR sẽ được ghi riêng bên dưới.

Lượt rà soát diff cuối tái hiện việc Origin và APPLICATION_ORIGIN chấp nhận dấu `?`/`#` rỗng
ở cuối URL. Đã bổ sung regression tests và từ chối các chuỗi này; Referer có path/query hợp lệ
vẫn được chấp nhận làm fallback. Full suite 260 tests và Black/Ruff/Pyright đạt sau sửa;
OpenAPI export không đổi.

## Phản hồi PR #12

Nhóm trưởng xác nhận hai lỗi P2 trên 340bbe3: email khác chuỗi nhưng cùng user theo collation
MySQL có thể né email throttle, và ASGI `root_path` làm middleware auth bỏ qua các giới hạn HTTP.
Tests mới tái hiện cả hai lỗi trước sửa. Service hiện lấy email canonical của user sau DB lookup
trước khi đếm, giữ email chuẩn hóa cho tài khoản chưa biết; middleware/error handler dùng route
path tương ứng với Starlette. Regression HTTP chạy với cả root path rỗng và `/prefix`, gồm
body chunked, IP throttle, timeout, response cache thành công và lỗi. Test tích hợp MySQL kiểm
tra sáu cách viết cùng resolve một user, attempt thứ sáu nhận 429/Retry-After và không gọi
Argon2 lần thứ sáu. CI MySQL 8.4 trên `632250d` chạy 271 tests: 270 passed,
1 failed ở test timeout commit cũ do timeout 0,2 giây vẫn được giữ cho lần login kiểm tra phục hồi.
Test đã được sửa để trả timeout DB về giá trị gốc trước khi kiểm tra phục hồi; giới hạn 0,2 giây
vẫn áp dụng cho lần commit được cố ý làm treo. Kết quả CI của head cuối xem trực tiếp trong PR #12.

## Quyết định thực thi so với kế hoạch

- Dùng branch mới trong checkout đã được người dùng yêu cầu; hai file kế hoạch local giữ nguyên.
- Ledger/logs PowerShell trong build/session-auth thay bash-only Superpowers scratch helpers.
- Gộp các utilities liên quan vào một commit; tests tập trung theo module, HTTP/services dùng
  chung fixtures trong integration/conftest thay tạo nhiều bộ setup/schema dư thừa.
- Ví dụ HTTPX/getpass thay curl/cookie jar để không ghi password/token ra file; cần môi trường
  dev có httpx. Rủi ro của các điều chỉnh quy trình: không cách ly thư mục như worktree, ít điểm
  commit trung gian hơn và ledger PowerShell cần giữ thủ công; thay đổi local cũ vẫn giữ nguyên.
- Sửa bổ sung nhánh cache 500 và mô tả OpenAPI sau khi tests tái hiện fail; regression hủy lặp
  Argon2 được chứng minh RED→GREEN. Không thay scope/DB schema hoặc thêm tài khoản seed.
