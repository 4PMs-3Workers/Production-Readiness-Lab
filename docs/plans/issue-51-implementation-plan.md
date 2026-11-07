# Kế hoạch triển khai issue #51 — Admin portal và quản lý tài khoản

- **Issue:** [#51 — Build the guarded admin portal and account management screens](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/51).
- **Ngày lập:** 06/11/2026, Asia/Bangkok.
- **Trạng thái:** Kế hoạch triển khai; chưa triển khai tính năng hoặc chạy các kiểm thử tính năng được đề xuất dưới đây.
- **Baseline đã kiểm tra trên GitHub:** `develop` tại `0d5d1cc070de83b97758b8c97fe578b7f981de21`, merge [PR #60](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/60) cung cấp API #49. #51 đang mở, không có comment bổ sung tại thời điểm khảo sát.
- **Checkout khi lập kế hoạch:** `feature/immutable-release-artifacts` tại `a597e3a`; frontend và harness được đối chiếu với `develop`. Không dùng việc checkout local chưa có backend #49 để kết luận API còn thiếu. Không đổi branch chỉ để viết tài liệu này.
- **Khi bắt đầu triển khai:** cập nhật `develop`, kiểm tra phần thay đổi kể từ baseline và tạo `feature/admin-account-management` theo `docs/other/GITFLOW.md`. PR đích là `develop`, có review trước merge.

## 1. Kết quả cần bàn giao và giới hạn phạm vi

ADMIN đăng nhập bằng session thật, vào khu vực `/admin`, quản lý tài khoản bằng API #49 và nhìn thấy kết quả đã được backend xác nhận. Portal có guard, điều hướng, trạng thái tải/lỗi/rỗng/xung đột, bảo vệ dữ liệu khi thay đổi danh tính, và thao tác được bằng bàn phím trên desktop/mobile.

Phạm vi trực tiếp của #51:

1. Shared admin layout, session/role guard và navigation để các trang quản trị sau này có thể dùng lại.
2. Danh sách tài khoản, tìm kiếm/lọc/phân trang theo đúng query API.
3. Tạo tài khoản, sửa trường được phép, đổi role, bật/tắt hoạt động và reset mật khẩu nội bộ có xác nhận rõ ràng.
4. Hiển thị đúng hạn chế tài khoản cuối cùng, STAFF còn lịch giữ chỗ và tài khoản có tham chiếu lịch sử theo backend.
5. Xử lý pending, lỗi xác định, stale conflict, kết quả ghi chưa rõ; không tự gửi lại một mutation khác sau kết quả chưa rõ.
6. Dọn dữ liệu/draft/mật khẩu khi session hoặc danh tính thay đổi; xử lý revoked session và đăng nhập lại.
7. Component/service tests và Playwright với API/MySQL thật; kiểm tra account/audit đã lưu sau reload; giữ các luồng student/staff hiện tại.

Các phần sau không thuộc kế hoạch này:

- Màn hình danh mục tư vấn, giờ phục vụ và booking settings của #52; không tạo trang hoặc menu dẫn đến chức năng chưa có. Shared shell chỉ cung cấp chỗ ghép page/navigation hiện có để #52 mở rộng sau.
- Thay đổi policy/API account đã bàn giao bởi #49, schema migration hay backend account service mới.
- Xem nội dung tư vấn, lịch tư vấn dành cho ADMIN, giao diện xem audit, dashboard/thống kê quản trị.
- Xóa tài khoản, bulk actions, import/export, sort tùy chọn, SSO, email reset, gửi mật khẩu qua email, quy trình phân công lại lịch hoặc di chuyển lịch sử khi đổi role.
- Idempotency/replay endpoint, operation-status endpoint, endpoint đọc mật khẩu hoặc endpoint account detail mới.
- Thiết kế lại toàn bộ portal, thay thư viện UI/state/form, thêm runtime dependency khi các công cụ hiện có đủ dùng.

Các tên component/file và cách chia màn hình dưới đây là lựa chọn tổ chức triển khai, không phải yêu cầu sản phẩm mới.

## 2. Nguồn yêu cầu và thứ tự đối chiếu

| Nguồn đã đọc                                                                                                                | Vai trò trong kế hoạch                                                                                                  |
| --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Nội dung issue #51, mục Coding scope / Tests and acceptance / Boundary / Delivery requirements                              | Xác định deliverable và điều kiện đóng issue.                                                                           |
| `docs/api/openapi.yaml`; `src/backend/app/api/admin_accounts.py`; `src/backend/app/schemas/admin_accounts.py` trên baseline | HTTP method, URL, DTO, query, validation và error contract của #49.                                                     |
| `src/backend/app/services/admin_accounts.py`; `repositories/users.py`; `docs/tasks/admin-account-management.md`             | Policy, version, no-op, audit và session revocation đã thực sự được triển khai.                                         |
| `tests/backend/integration/test_admin_account_endpoints.py`                                                                 | Bằng chứng có sẵn cho backend policy, concurrency, transaction và deployed grants; dùng làm nền tảng cho UI acceptance. |
| `src/frontend/AGENTS.md`; `docs/other/CONVENTION.md`; `docs/other/GITFLOW.md`                                               | App Router, TypeScript strict, tên file, service boundary, check và quy trình PR.                                       |
| `docs/architecture/system-design.md`; `docs/architecture/database-design.md`                                                | Giữ ranh giới ADMIN và nội dung tư vấn, session/CSRF, transaction/deadline và data isolation hiện tại.                  |
| Frontend source, component/API E2E, `tools/scripts/frontend_e2e_backend.py`, workflow frontend hiện có                      | Xác định thành phần tái sử dụng và các chỗ phải tích hợp.                                                               |

Đã đọc tài liệu Next.js cài trong `node_modules/next/dist/docs/01-app/01-getting-started/03-layouts-and-pages.md` và `05-server-and-client-components.md`. Khi bắt đầu code phải đọc lại hướng dẫn liên quan nếu phiên bản dependency thay đổi, theo `src/frontend/AGENTS.md`.

## 3. Hợp đồng API #49 phải giữ nguyên

### 3.1. Endpoint và response

| Operation      | Request chính xác                                                                                                         | Success                                 |
| -------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| List           | `GET /api/v1/admin/accounts`; `page`, `page_size`, tùy chọn `role`, `is_active`, `search`                                 | `200 { items, page, page_size, total }` |
| Create         | `POST /api/v1/admin/accounts`; `email`, `name`, `role`, `password`; `is_active` mặc định `true`; student profile tùy chọn | `201 AccountItem`                       |
| Update         | `PATCH /api/v1/admin/accounts/{account_id}`; `expected_updated_at` và ít nhất một account field được phép                 | `200 AccountItem`                       |
| Reset password | `POST /api/v1/admin/accounts/{account_id}/password`; `expected_updated_at`, `password`                                    | `200 AccountItem`                       |

`AccountItem` chỉ gồm:

```text
id, email, name, role, is_active,
student_code, faculty, major, gender,
created_at, updated_at
```

- `role`: `STUDENT | STAFF | ADMIN`.
- `gender`: `MALE | FEMALE | OTHER | UNDISCLOSED | null`.
- Các trường student profile có thể `null`; response không chứa password/hash/session token.
- Không có GET account theo ID. Chọn account từ kết quả list; khi cần đọc lại dùng list/search/phân trang hiện có và nhận diện bằng `id`, không tự gọi một URL detail chưa tồn tại.
- List mặc định `page=1`, `page_size=20`; `page` trong `[1, 1_000_000]`, `page_size` trong `[1, 100]`.
- `search` là name/email substring, sau trim phải dài 1–100; bỏ query khi rỗng. `%`, `_`, `\` được backend xem như ký tự literal. Không diễn giải thành regex/wildcard ở UI.
- Thứ tự list do backend cố định theo email rồi ID. Không thêm sort UI hoặc client-side sort/filter làm sai paging/total.

Tất cả request cần session ADMIN đang hoạt động. Ba mutation phải có CSRF hợp lệ và same-origin. API hiện dùng `Origin`, hoặc `Referer` khi không có `Origin`; frontend giữ đường request same-origin hiện có, không giả mạo các header do trình duyệt quản lý. Response vẫn private/no-store, request deadline và body-size handling thuộc middleware hiện tại.

### 3.2. Field và validation

| Field              | Quy tắc từ backend                                                                                                | Cách thể hiện trên UI                                                                                               |
| ------------------ | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `email`            | Trim/lowercase; 1–254 ký tự sau chuẩn hóa, một `@`, không whitespace, local/domain không rỗng, domain có dấu chấm | Validate theo contract; response backend là giá trị chuẩn. Không tự mô phỏng collation Unicode để quyết định trùng. |
| `name`             | Trim, không rỗng, tối đa 150                                                                                      | Required, lỗi gắn đúng field.                                                                                       |
| `password`         | 1–1024 ký tự, giữ nguyên giá trị                                                                                  | Input password; không trim, không thêm quy tắc độ phức tạp/độ dài tối thiểu khác. Chỉ có trong create/reset.        |
| `role`             | Ba giá trị được liệt kê ở trên                                                                                    | Select có nhãn rõ ràng.                                                                                             |
| `is_active`        | Boolean                                                                                                           | Hiển thị hoạt động/không hoạt động; không đồng nhất inactive với xóa tài khoản.                                     |
| `student_code`     | Nullable, tối đa 50                                                                                               | Chỉ có dữ liệu khi effective role là STUDENT.                                                                       |
| `faculty`, `major` | Nullable, tối đa 150                                                                                              | Trim; chuỗi rỗng chuyển `null` theo contract.                                                                       |
| `gender`           | Enum hoặc `null`                                                                                                  | Có lựa chọn không khai báo.                                                                                         |

- PATCH: bỏ field nghĩa là giữ nguyên; `null` chỉ hợp lệ cho student profile. Không gửi `null` cho email/name/role/is_active.
- Student profile chỉ được populate khi effective role là STUDENT. Đổi khỏi STUDENT thì backend tự clear profile. UI nêu tác động này trong xác nhận và không gửi lại profile cũ cho role mới.
- Browser constraint không được vô tình tạo policy chặt hơn API. Error 422 từ server vẫn được xử lý, kể cả khi client validation đã qua.

### 3.3. Version, no-op và audit

1. Giữ **nguyên chuỗi** `updated_at` backend trả về để gửi thành `expected_updated_at`. Không đi qua `new Date(...).toISOString()` hoặc phép chuyển đổi làm mất microseconds; có thể parse bản sao để hiển thị.
2. Form sửa/reset gắn với snapshot `{accountId, originalUpdatedAt}` tại lúc mở/review. Không thay token bên dưới draft đang mở vì background reload.
3. Actual update tạo version mới. Stale version trả `409 ACCOUNT_VERSION_CONFLICT`; backend kiểm tra version trước no-op.
4. Exact PATCH no-op trả account/version hiện tại, không thêm audit và không revoke session. UI thông báo không có thay đổi, không tự bịa một lần cập nhật mới.
5. Create tạo `ACCOUNT_CREATED`; actual PATCH tạo `ACCOUNT_UPDATED`; reset tạo `ACCOUNT_PASSWORD_RESET`. Mutation và audit atomic.
6. Reset luôn là mutation, kể cả plaintext trùng mật khẩu cũ; không áp dụng no-op của PATCH cho reset.
7. Role/active-state/password thay đổi sẽ revoke toàn bộ session của target trong cùng transaction. Chỉ sửa name/email/student profile không có tác động revoke này.

### 3.4. Policy và lỗi nghiệp vụ phải thể hiện

| Code                                     | Ý nghĩa cần hiển thị                                                     | Hành vi UI                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `EMAIL_ALREADY_EXISTS` — 409             | Email đã có theo normalization/collation của DB                          | Gắn lỗi create/email edit; giữ dữ liệu không nhạy cảm để sửa; không báo tạo/sửa thành công. |
| `ACCOUNT_VERSION_CONFLICT` — 409         | Account đã thay đổi so với snapshot                                      | Chuyển sang review/reload; không tự lấy token mới rồi ghi lại draft cũ.                     |
| `LAST_ACTIVE_ADMIN` — 409                | Phải còn ít nhất một ADMIN active                                        | Hiển thị hạn chế; account và audit không bị thay đổi do lần bị từ chối.                     |
| `LAST_ACTIVE_STAFF` — 409                | Phải còn ít nhất một STAFF active                                        | Xử lý tương tự; đây cũng là policy đã bàn giao của #49.                                     |
| `STAFF_HAS_OCCUPYING_APPOINTMENTS` — 409 | STAFF còn lịch `UPCOMING` hoặc `CHANGE_PENDING` nên không thể deactivate | Giải thích hạn chế; không mở chức năng xem nội dung hay tự chuyển/hủy lịch.                 |
| `ROLE_CHANGE_REFERENCED` — 409           | Lịch sử/tham chiếu account không cho đổi role                            | Giải thích phải giữ ý nghĩa lịch sử; không thêm quy trình migrate/reassign.                 |
| `STUDENT_PROFILE_ROLE_REQUIRED` — 422    | Student profile không phù hợp effective role                             | Hiển thị lỗi field/form, yêu cầu sửa payload.                                               |
| `ACCOUNT_NOT_FOUND` — 404                | Target không còn được tìm thấy                                           | Bỏ editor đang dựa vào dữ liệu cũ; cho tải lại danh sách.                                   |

Không suy ra “last admin”, “last staff”, “đang giữ chỗ” hoặc “được đổi role” từ trang list hiện tại: response không cung cấp đủ thông tin đó. Backend vẫn quyết định cuối cùng dưới lock. UI có thể giải thích policy và hiển thị lỗi backend; không cần API quyền phụ trợ mới.

Các lỗi chung giữ cách xử lý hiện có: `INVALID_SESSION`, `FORBIDDEN_ROLE`, `INVALID_CSRF`, `INVALID_ORIGIN`, client identity mismatch `ACCOUNT_CHANGED`, validation, timeout/network và `SERVICE_UNAVAILABLE`. Mã hỗ trợ `X-Request-ID`/`Retry-After` được trình bày theo helper hiện tại, không hiển thị raw payload lỗi.

## 4. Tích hợp với frontend hiện tại

### 4.1. Các chỗ phải nối, không chỉ thêm page mới

| File hiện có                                       | Thay đổi tối thiểu cần thiết                                                                                                                                                                                               |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/frontend/components/application-provider.tsx` | Cho `/admin` và prefix `/admin/` đi vào admin guard riêng; hiện root protection chỉ exempt login, root và staff nên STUDENT gate sẽ chặn ADMIN. Match boundary chính xác, không exempt `/administrator` hoặc `/admin-...`. |
| `features/api/api-login.tsx`                       | ADMIN login thành công đi tới admin return path hợp lệ; authenticated ADMIN có CTA vào portal, thay thông báo chưa có cổng. Giữ STUDENT/STAFF routing.                                                                     |
| `features/api/api-account.tsx`                     | Link Trang chủ của ADMIN tới `/admin/accounts`; không đưa ADMIN về `/home`. Logout giữ lifecycle hiện có.                                                                                                                  |
| `components/portal-shell.tsx` và CSS liên quan     | Thêm audience ADMIN, nhãn/breadcrumb/header/home link/navigation accounts; tái sử dụng cấu trúc responsive/skip link hiện có. Không đưa các menu student/staff vào admin.                                                  |
| `services/api/client.ts`                           | Bổ sung typed admin methods; mở rộng shared write method cho `PATCH` và expected role `ADMIN` với cùng CSRF/identity/abort/deadline flow. Giữ behavior của các caller STUDENT/STAFF.                                       |
| `services/api/errors.ts`                           | Thêm thông điệp account-specific hoặc cho admin error helper dùng cùng error type; không đổi nghĩa error booking/proposal.                                                                                                 |
| Các API E2E đang coi ADMIN là unsupported          | Sửa assertion landing cho ADMIN; giữ assertion ADMIN bị từ chối khi vào student/staff route không dành cho họ.                                                                                                             |

### 4.2. Route và component đề xuất

```text
src/frontend/app/admin/layout.tsx
src/frontend/app/admin/page.tsx
src/frontend/app/admin/accounts/page.tsx

src/frontend/features/admin/admin-session-gate.tsx
src/frontend/features/admin/admin-layout.tsx
src/frontend/features/admin/accounts/admin-accounts.tsx
src/frontend/features/admin/accounts/account-form.tsx
src/frontend/features/admin/accounts/account-confirmation.tsx
src/frontend/features/admin/accounts/account-password-reset.tsx
src/frontend/features/admin/accounts/use-admin-account-mutation.ts
src/frontend/features/admin/accounts/admin-accounts.module.css

src/frontend/services/admin-contracts.ts
src/frontend/services/api/admin-account-mapping.ts
```

- `/admin` chỉ đưa tới `/admin/accounts`; không tạo dashboard riêng.
- Accounts page chứa list và form/panel create/edit/reset. Không cần detail route mới để hoàn thành issue. Editor luôn giữ ID/version gốc, không giữ row index làm định danh.
- `app/.../page.tsx` chỉ compose feature; layout bọc guard/shell dùng lại được. Client boundary đặt ở phần cần session/state/event; không chuyển toàn bộ app thành client component.
- `admin-contracts.ts` giữ account/query/create/patch/reset types và request options. Có thể đặt domain types ở `types/` nếu phù hợp cấu trúc lúc code; chỉ có một định nghĩa contract.
- Mapping wire snake_case sang domain theo pattern service hiện tại, validate response trước khi dùng; `updated_at` giữ chuỗi nguyên bản. Không giữ thêm secret field nếu response bất ngờ có trường thừa.
- Dùng `ApiFeedback`, `Pagination`, CSS tokens/layout hiện có khi phù hợp. Không refactor diện rộng các portal để phục vụ một màn hình.
- Admin shell chỉ có section accounts ở #51; page con và xác định active section có thể mở rộng khi #52 triển khai.

### 4.3. Typed service boundary

Các method dự kiến trên `ApiServices`:

```text
listAdminAccounts(query, accessOptions)
createAdminAccount(body, writeOptions)
updateAdminAccount(accountId, body, writeOptions)
resetAdminAccountPassword(accountId, body, writeOptions)
```

- Component không tự `fetch`, không xử lý wire DTO/CSRF cookie.
- Access/write options mang expected actor ID, signal và callback đánh dấu đã dispatch mutation khi cần phân loại outcome.
- Chụp payload và original version trước bất kỳ await nào. Việc người dùng sửa form/đổi selection sau đó không làm biến đổi request đang chuẩn bị.
- Giữ `credentials: same-origin`, `cache: no-store`, `redirect: error`, scope abort, timeout và session-invalid handler của client hiện tại.
- Mutation: lấy CSRF cùng user ID, xác minh `/auth/me` vẫn là expected ADMIN, rồi gửi mutation bằng chính token đã kiểm tra. Không lấy token khác sau khi cookie/danh tính đã đổi.
- List: dùng expected ADMIN + scope signal chụp một lần, kiểm tra identity trước/sau request theo pattern owner verification hiện có, và kiểm tra request key trước khi apply response. Không tiếp tục một operation cũ bằng scope mới sau `clearSession()`.
- Admin API chưa có server-side `X-Expected-*` fence cho list. Các kiểm tra trên là cô lập response/phòng stale ở client, không được mô tả như một bảo đảm actor binding atomic của backend.
- Parse lỗi/thành công thuộc đúng phase. Response 2xx nhưng DTO thiếu/sai, hoặc body bị cắt sau dispatch, không phải bằng chứng mutation bị rollback.
- Không gửi `Idempotency-Key` hoặc dùng `BookingIntentStore`/`ProposalIntentStore` để tuyên bố replay cho API account.

## 5. Session guard, điều hướng và thay đổi danh tính

### 5.1. Admin guard

`AdminSessionGate` dùng `useApi()` và cách xử lý của staff gate:

| Session state                      | Hành vi                                                                              |
| ---------------------------------- | ------------------------------------------------------------------------------------ |
| Loading                            | Loading status, chưa mount list/form hoặc phát admin request.                        |
| Anonymous                          | Chuyển `/login?returnTo=...` bằng admin return path đã kiểm tra.                     |
| Error kiểm tra session             | Error + thao tác kiểm tra lại phiên; không render dữ liệu admin cũ.                  |
| Authenticated STUDENT/STAFF        | Forbidden state có thông điệp và điều hướng phù hợp/đăng xuất; không mount children. |
| Authenticated ADMIN đang verifying | Ẩn và làm inert nội dung riêng tư, chặn thao tác cho tới xác minh xong.              |
| Authenticated ADMIN hợp lệ         | Render shell và page dưới identity/revision key.                                     |

`safeAdminReturnPath` chỉ nhận các route admin đã có (`/admin`, `/admin/accounts`, cùng query an toàn nếu dùng), từ chối external/protocol-relative path, backslash, control characters và prefix giả. Không mở return path sang các trang ngoài quyền ADMIN. Khi đang ở mock mode, admin layout thông báo cần API mode theo pattern staff hiện tại; không dựng mock admin implementation để thay acceptance thật.

### 5.2. Dữ liệu, reads và writes khi identity thay đổi

1. Mỗi resource/mutation gắn với actor ID, role ADMIN, provider revision và query/target ID tương ứng.
2. Đổi account, login lại, logout, invalid session hoặc thay role làm abort scope cũ và dọn account list, selected account, form draft, confirmation, password và feedback riêng tư. Dùng lifecycle hiện có của `ApiProvider`; chỉ thêm cleanup admin ở boundary cần thiết.
3. Response muộn không được render dữ liệu hoặc success/error của actor trước, kể cả response về sau lần login mới với cùng user ID nhưng revision khác.
4. Khi focus tab, giữ behavior revalidate hiện có. Lúc bắt đầu `session.verifying`, admin feature chặn submit mới và hủy controller read/preparing cũ; `hidden/inert` không tự ngăn continuation sau await. Hủy trước dispatch giữ draft, chờ xác minh rồi người dùng chủ động submit; không tự resume request đã hủy. Write đã dispatch vẫn là pending nếu còn chờ được receipt; nếu bị hủy/mất receipt thì chuyển unknown, không tự replay. Nếu `/me` xác nhận cùng identity hợp lệ, giữ draft/unknown marker và không tăng revision chỉ vì focus; nếu khác identity/role hoặc xác minh lỗi thì không để nội dung cũ tương tác.
5. Một mutation đã dispatch vẫn có thể commit dù browser abort. Dọn UI theo identity không có nghĩa undo server mutation; tài khoản mới không được nhận recovery draft hay thực hiện tiếp mutation của tài khoản trước.
6. Không thêm polling session, socket, cross-tab message protocol hoặc auth provider mới. Dùng focus, các request và invalid-session flow sẵn có.

### 5.3. Self mutation và session bị revoke

- Xác nhận phải nêu rõ role/active/password change sẽ kết thúc các session của target; nếu target là actor hiện tại thì nêu cần đăng nhập lại và có thể mất quyền ADMIN.
- Với receipt hợp lệ cho self reset/role/active change: ghi nhận kết quả đã được backend xác nhận, dọn admin data và revalidate/chuyển login. Không tiếp tục dùng admin UI dựa vào role cũ.
- Self deactivate và role change chỉ làm khi backend cho phép; có thể bị last-active/history restriction. Không disable toàn bộ self-action trái với API, cũng không thêm quyền bypass.
- Nếu self write đã dispatch nhưng không nhận được receipt rồi `/me` trả 401, vẫn là kết quả ghi chưa rõ. 401 không chứng minh reset/role/active change vừa thành công.
- Target ở browser context khác phải bị backend từ chối session cũ sau role/active/password mutation thật; E2E chứng minh điều này, không chỉ kiểm tra UI logout.

## 6. Luồng quản lý tài khoản

### 6.1. List, filter, search và pagination

1. Sau ADMIN guard, tải page 1, page size 20. Hiển thị name, email, role, active state và entry thao tác account. Các profile field còn lại chỉ cần trong editor.
2. Filter đúng contract: role, hoạt động/không hoạt động/tất cả, name/email search. Search submit rõ ràng; không cần debounce mới hoặc search-as-you-type.
3. Đổi filter/search reset page về 1. “Tất cả”/search rỗng được bỏ query thay vì gửi giá trị không hợp lệ.
4. `total`, page và khả năng next/previous dựa response server. Phân biệt chưa có account và không có kết quả phù hợp filter.
5. Resource key chứa identity/revision và query; abort query cũ khi thay filter/page. Response đến sai thứ tự không được đè list hiện tại.
6. Sau mutation được xác nhận, đọc lại list theo query đang dùng. Account có thể biến mất vì không còn hợp filter hoặc chuyển trang do email/sort; hiển thị thông điệp hợp lý, không coi đó là mutation thất bại.
7. Khi cần đọc lại target để xử lý conflict/unknown: dùng list/search/phân trang với ID ổn định. Nếu không tìm thấy trong một search hoặc một trang thì chưa kết luận account không tồn tại. Cho phép điều chỉnh/bỏ filter và chọn lại từ dữ liệu server; không quét toàn bộ DB tự động hoặc thêm detail API.

### 6.2. Create

- Form chứa email/name/role/password/active state và student profile khi role STUDENT.
- Validate theo §3.2; submit guard đồng bộ chống double click/Enter lặp trước khi React render lại.
- Password chỉ ở input/state tạm và request tức thời; xóa khỏi input khi dispatch, cancel/unmount hoặc session mất. Nếu validation lỗi trước dispatch có thể sửa ngay; nếu cần gửi lại sau dispatch phải nhập lại, không có password replay store.
- Disable submit khi pending. Chỉ thông báo đã tạo khi nhận/parse được `201 AccountItem` của operation hiện tại.
- Sau receipt hợp lệ, dùng response chuẩn hóa và refresh list; reload vẫn tìm thấy account. `EMAIL_ALREADY_EXISTS` không tự đổi thành success.
- Create không có version/idempotency để replay an toàn. Mất receipt chuyển sang recovery §7, không gửi lại POST tự động.

### 6.3. Edit profile, role và active state

- Mở editor từ account đã đọc, giữ baseline/version tách với draft.
- PATCH chỉ gửi trường được thay đổi hoặc một payload hợp lệ cần thiết; không gửi password qua PATCH. Để trống optional profile nghĩa là `null` khi người dùng chủ đích xóa. Nếu không có field thay đổi tại client, báo không có thay đổi và gửi **0 PATCH**; payload chỉ có `expected_updated_at` không hợp lệ. Khi kiểm thử exact no-op phía API, gửi ít nhất một field giữ nguyên giá trị cùng raw version hiện tại và kiểm tra version/audit không đổi.
- Chỉ đổi name/email/profile có thể submit trực tiếp sau validation. Nếu có role hoặc active-state change, chuyển bước xác nhận trước dispatch.
- Xác nhận hiển thị account target, giá trị role/state trước và sau, tác động revoke session, và việc xóa student profile nếu rời STUDENT. Không in payload kỹ thuật hay version token trong luồng sản phẩm.
- Nút hủy xác nhận gửi **0 mutation**. Khi đang pending, không cho một hành động khác trên cùng target chen vào.
- Success nhận `200 AccountItem`, cập nhật baseline từ response rồi refresh list. Exact no-op giữ version, hiển thị không có thay đổi; không gửi một mutation phụ để “làm mới”.
- Policy rejection giữ trạng thái chưa lưu và thông báo đúng code; không tự fallback từ role change sang deactivate hoặc mutation khác.

### 6.4. Reset password

- Tách rõ action reset khỏi sửa profile; chỉ nhận password mới và version account hiện tại.
- Hiển thị xác nhận target và việc revoke toàn bộ session, bao gồm chính actor nếu self reset. Password không xuất hiện trong confirmation summary, toast, URL, log hoặc evidence.
- Không thêm gửi email, tự sinh/hiển thị mật khẩu, xem mật khẩu hiện tại hoặc khôi phục mật khẩu tự phục vụ.
- Submit đúng `POST .../{id}/password`; clear input như create; khóa submit khi pending; chỉ báo thành công khi có receipt hợp lệ.
- Reset trả account chứ không trả mật khẩu. Reload list/version không kiểm chứng được plaintext đã được đặt. Khi thiếu receipt, đi đúng luồng unknown reset §7.3.

## 7. State ghi, conflict và unknown outcome

### 7.1. Model trạng thái tối thiểu

Dùng hook/context chuyên cho account mutation, đặt dưới admin guard/layout hoặc account feature ở boundary đủ giữ cảnh báo khi chuyển child UI. Chỉ ở bộ nhớ, không tạo queue/store chung cho toàn ứng dụng.

| State          | Điều kiện                                                    | Cho phép                                                     |
| -------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `idle/editing` | Chưa gửi                                                     | Sửa form hoặc chuẩn bị confirmation.                         |
| `confirming`   | Sensitive change đã validate                                 | Cancel hoặc submit đúng snapshot đã review.                  |
| `preparing`    | Đang lấy CSRF/xác minh actor, chưa dispatch account mutation | Chặn submit lặp; lỗi ở đây không phải unknown account write. |
| `pending`      | Đã dispatch account mutation                                 | Chờ; không gửi write thay thế trên target.                   |
| `confirmed`    | Receipt hợp lệ, đúng identity/operation                      | Hiển thị kết quả và refresh read.                            |
| `rejected`     | Response account operation từ chối rõ ràng                   | Hiển thị lỗi, sửa/review theo code.                          |
| `conflict`     | `ACCOUNT_VERSION_CONFLICT`                                   | Reload và review; không tự rebase draft.                     |
| `unknown`      | Đã dispatch nhưng không có receipt đáng tin cậy              | Cảnh báo chưa rõ, read-only recovery; không auto replay.     |

Metadata tối thiểu: actor/revision, operation kind, target ID hoặc thông tin create không chứa mật khẩu, original version, dispatch flag và phase. Nếu cần so sánh PATCH thì giữ riêng các thay đổi không phải secret trong bộ nhớ. Không đưa password vào metadata/context hoặc serialization.

- Snapshot request không thay đổi khi CSRF/identity check đang chạy.
- Network failure, timeout, 5xx/`SERVICE_UNAVAILABLE`, response thành công bị cắt/sai DTO sau dispatch là unknown. `AbortError` sau dispatch chỉ nói browser dừng đợi, không nói transaction rollback.
- Phân biệt lỗi preflight với lỗi của account mutation; không gắn mọi lỗi client thành “đã ghi” và không gắn mọi lỗi 4xx thành bằng chứng về một operation trước đang unknown.
- Read-only retry là GET, không có write ngầm; việc auto-refresh GET sau kết quả hợp lệ không được biến thành mutation retry.
- Cảnh báo unknown tồn tại trong vòng đời admin feature khi còn cùng identity. Full reload hoặc rời session không duy trì secret/draft để replay: tải lại dữ liệu authoritative; mọi write sau đó là thao tác mới có chủ đích. Không tuyên bố khôi phục transaction xuyên reload.

### 7.2. Stale conflict

1. Giữ nguyên operation bị conflict và không thông báo success.
2. Cho người dùng tải lại account qua list hiện có. Tách current server snapshot với draft chưa lưu để nhìn thấy thay đổi cần review.
3. Không tự thay `expected_updated_at` rồi submit lại; không merge field âm thầm.
4. Sau khi người dùng review, chọn sửa trên snapshot mới và xác nhận lại sensitive action nếu có. Đây là mutation mới dùng fresh version.
5. Hai admin cùng edit phải cho đúng một update được chấp nhận; UI còn lại hiển thị conflict, không làm mất cập nhật đầu tiên.

### 7.3. Recovery theo operation

| Operation chưa rõ | Cách kiểm tra được phép                                                                          | Giới hạn và bước tiếp theo                                                                                                                                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Create            | Read/search email đã chuẩn hóa, xem account server trả về, đối chiếu ID/thông tin không nhạy cảm | Một account có sẵn hoặc lỗi duplicate không chứng minh POST đầu đã tạo nó. Không auto tạo lại. Người dùng review hiện trạng trước mọi create mới.                                                                                                                                       |
| PATCH             | Đọc lại account cùng ID, xem fields và raw version hiện tại so với snapshot/draft                | Matching fields chỉ xác nhận trạng thái hiện tại; version mới không chứng minh ai đã ghi. Nếu cần thay đổi tiếp, người dùng review và submit mới với version mới.                                                                                                                       |
| Password reset    | Read-only xác minh session/account hiện tại, yêu cầu reauthentication khi session bị revoke      | List/version không đọc được password và không đủ chứng minh reset thành công. Giữ thông báo chưa xác định khi chưa có xác nhận phù hợp. Nếu người dùng chủ đích reset lại sau review thì nhập lại password, dùng version mới và xác nhận như **một reset mới**; không gọi đó là replay. |

Không tự thử đăng nhập vào account target để kiểm tra mật khẩu từ portal ADMIN, không dùng hash/password API và không lấy audit từ endpoint chưa tồn tại để giải quyết unknown. Test fixture có quyền kiểm tra DB cho acceptance, nhưng product UI chỉ dùng contract đã giao.

## 8. Accessibility, mobile và bảo vệ dữ liệu

- Giữ tiếng Việt và style/tokens portal hiện tại; header/nav/heading mô tả đúng khu vực quản trị.
- Dùng link cho điều hướng, button cho action; labels đầy đủ cho input/select; không chỉ dùng màu để phân biệt role/state/lỗi.
- Loading/pending/success dùng status phù hợp; lỗi có `role=alert`, focus tới lỗi hoặc field cần sửa khi hữu ích.
- Confirmation/panel có tên accessible, thứ tự focus rõ, cancel bằng bàn phím và trả focus về action mở; nếu dùng modal phải quản lý focus/escape/inert đúng pattern đã chọn. Không bắt buộc một modal framework mới.
- Trên mobile, list/form/action/navigation không tràn ngang làm mất nút; tab order và touch target sử dụng được. Tận dụng breakpoint portal sẵn có.
- Chỉ hiển thị account data khi ADMIN guard hợp lệ; không có consultation content trong admin screen/request.
- Password dùng input password, không trim, không persist vào localStorage/sessionStorage, URL, provider/store, log, request dump hoặc attachment. Clear input sau dispatch/cancel/session loss; mật khẩu đã gửi chỉ tồn tại trong phạm vi request cần thiết.
- Không persist account list/draft sang browser storage. CSRF/session cookie handling giữ nguyên; không đưa token vào React form/debug output.
- Không bật trace/screenshot tự động cho real API suite nếu có nguy cơ ghi password/token; evidence chỉ gồm dữ liệu giả và thông tin đã allowlist.

## 9. Kế hoạch kiểm thử theo lớp

### 9.1. Service/mapping và component tests

Thêm test trong `tests/frontend/unit/` và `tests/frontend/components/`, theo Vitest/React Testing Library hiện có. Tập trung hành vi quan sát được và boundary, không chỉ mirror component implementation.

| Nhóm                 | Case cần chứng minh                                                                                                                                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Contract/mapping     | Đúng 4 endpoint/method/query/body; literal search; bỏ filter rỗng; DTO enum/null; secret không vào model; giữ nguyên microsecond version.                                                                           |
| Write preflight      | CSRF owner + `/me` ADMIN đúng actor; switch identity hoặc focus-verifying trong lúc chờ CSRF/`/me` không để mutation dispatch; payload/version không thay trong lúc await; PATCH không đổi caller booking/staff cũ. |
| Guard/routing        | Loading/anonymous/error/verifying; STUDENT/STAFF bị chặn; ADMIN được vào; login/CTA/home link đúng; return path độc hại bị từ chối; `/admin-...` không bị exempt.                                                   |
| Read isolation       | Filter/page response đảo thứ tự; account A sang B; logout/login lại cùng ID khác revision; response cũ không cập nhật list/editor/feedback.                                                                         |
| List                 | Loading/error/retry GET/empty/no results; filter reset page; server total; mutation làm target không còn thuộc filter.                                                                                              |
| Validation           | Name/email/password bounds theo API; password không trim; student fields chỉ cho STUDENT; nullable/omitted PATCH khác nhau.                                                                                         |
| Confirmation/pending | Role/active/reset có confirmation; cancel 0 write; double click/Enter 1 write; không báo success trước receipt; input/payload snapshot không lệch target.                                                           |
| Backend errors       | Duplicate email, last admin/staff, occupied staff, referenced role, 422, not found, forbidden/CSRF/session. Thông điệp riêng, không fallback write khác.                                                            |
| Conflict/no-op       | Conflict không auto rebase/retry; fresh read + deliberate submit; PATCH no-op không hiển thị thay đổi giả.                                                                                                          |
| Unknown              | Preflight failure khác postdispatch failure; 5xx, timeout, lost/malformed receipt; GET review không phát write; create/patch/reset không dùng booking replay; reset không suy success từ version.                   |
| Session/secret       | Target/self revocation; late completion; clear password/draft/private state; cùng identity focus thành công không mất draft vô cớ; không lưu secret vào storage.                                                    |
| Accessibility        | Labels, keyboard/focus confirm/cancel, live feedback, nav active state và action disabled lúc pending.                                                                                                              |

Tên file có thể chia thành `admin-api.test.ts`, `admin-route-guard.test.tsx`, `admin-layout.test.tsx`, `admin-accounts.test.tsx`, `admin-account-mutation.test.tsx`, `admin-stale-responses.test.tsx` để mỗi suite tập trung một nhóm hành vi.

### 9.2. Bổ sung fixture cho API/MySQL thật

Tái sử dụng `tools/scripts/frontend_e2e_backend.py`, không thêm test-only endpoint vào ứng dụng.

1. **Giữ isolation:** schema `prl_frontend_e2e_<uuid>` do harness tạo, Alembic upgrade head, ownership regex trước use/cleanup, MySQL 8.4.x, không dùng shared DB. Thiếu `MYSQL_TEST_URL` hoặc quyền/schema/server cần thiết phải fail.
2. **Dữ liệu riêng cho admin:** actor ADMIN thứ hai cho conflict/self revoke, target không có role references cho role change hợp lệ, inactive account, target có session và dataset đủ chứng minh phân trang. Seed bằng action admin chuyên biệt nếu thêm vào seed chung làm đổi assumptions suite cũ.
3. **Policy fixtures:** last-active ADMIN/STAFF; STAFF có `UPCOMING` hoặc `CHANGE_PENDING`; referenced user. STAFF hiện có `StaffPurpose` nên không dùng nó làm target đổi role thành công.
4. **Reset đầy đủ:** reset hiện chỉ dọn business/session/audit và một phần catalog, chưa khôi phục user đã đổi. Bổ sung phục hồi email/name/role/active/student fields/password/version của fixture users và dọn account tạo thêm sau khi dọn dependent records. Chỉ trong schema owned; giữ danh sách fixture ID/resource thuộc test. Bài sau phải bắt đầu từ đúng seed, kể cả bài trước fail sau self deactivate/reset.
5. **Inspect account/audit/session:** thêm action harness đọc safe account state, version, audit action/actor/entity/count và trạng thái revoke cần assert. Kiểm tra metadata theo allowlist key/value: tên field `email`/`name` trong `changed_fields` là hợp lệ; giá trị email/name, password/hash/token/request payload thì không. Chỉ trả kết quả allowlist, không in hash hoặc toàn bộ session/audit row.
6. **Lost/held receipt:** mở rộng `ProbeCommittedResponse` hiện có theo method + path cho đúng POST create, PATCH account, POST password. Chỉ làm mất/giữ receipt 200/201 sau application thật đã commit; không dựng response success giả, không nuốt policy/validation rejection.
7. **Revocation:** action `expire --user` có thể dùng cho session-expiry case; bằng chứng revoke do account mutation phải đi qua API thật, không thay bằng fixture expire.
8. Giữ `finally`/teardown release held response và cleanup schema, kể cả khi assertion thất bại. Nếu thay Python harness, chạy formatter/lint và tests phù hợp cho nhánh ownership/reset/probe mới.

### 9.3. Playwright acceptance với real API/MySQL

Thêm `tests/frontend/api-e2e/admin-portal.spec.ts` hoặc chia spec theo nhóm nếu cần. Dùng hai project đã có: `api-desktop` và `api-mobile`; production Next server, FastAPI thật và MySQL thật. Không dùng mock response thay thế các assertion acceptance thành công/persisted/policy dưới đây.

| ID  | Kịch bản                                                                                    | Bằng chứng bắt buộc                                                                                                                                                                       |
| --- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E01 | ADMIN login → admin; reload; anonymous/STUDENT/STAFF vào trực tiếp `/admin/accounts`        | Landing/guard đúng; không có private admin UI với role sai; request admin API thật từ role sai bị từ chối.                                                                                |
| E02 | List/filter active/role/name-email/search, empty và qua trang                               | Dữ liệu/total từ server; filter/page không chỉ xử lý ở client; hoạt động trên mobile.                                                                                                     |
| E03 | Tạo account hợp lệ, reload tìm lại                                                          | Account đúng fields/normalization trong DB; đúng 1 `ACCOUNT_CREATED`; receipt không chứa secret.                                                                                          |
| E04 | Tạo hoặc sửa email trùng normalization/collation                                            | Đúng lỗi; không thêm account hoặc audit/mutation một phần.                                                                                                                                |
| E05 | Sửa profile/email/student fields rồi reload; submit form không có thay đổi; API exact no-op | UI, API read và DB khớp; actual change có audit đúng; form không đổi gửi 0 PATCH. Request exact no-op qua service/API thật có field giữ nguyên + raw version thì không đổi version/audit. |
| E06 | Role change hợp lệ trên unreferenced target; activate/deactivate                            | Confirm cancel không write; submit một write; state/audit đúng; old target session bị revoke khi có thay đổi role/state.                                                                  |
| E07 | Last ADMIN, last STAFF, STAFF giữ chỗ ở cả hai status, referenced-role rejection            | Code/thông điệp đúng; target và audit không đổi; không phát fallback mutation.                                                                                                            |
| E08 | Hai ADMIN contexts cùng edit version; A lưu trước, B lưu sau                                | B nhận 409, không overwrite A; reload/review B rồi deliberate submit version mới hoạt động.                                                                                               |
| E09 | Reset password qua UI                                                                       | Target `/auth/me` trước reset 200, session cũ sau reset 401; old password login fail, new password login pass trong test; đúng 1 audit; input clear/reload không có password.             |
| E10 | Self reset hoặc self role/active change hợp lệ                                              | Receipt + actual revoke; admin data bị clear; reauthentication/routing đúng; dùng đủ active admins để test không vô tình vướng last-admin policy.                                         |
| E11 | Giữ receipt sau commit, logout/login danh tính khác rồi thả receipt                         | Không render stale account/feedback hoặc tiếp tục mutation của identity cũ.                                                                                                               |
| E12 | Commit create/update/reset thật rồi mất receipt                                             | UI unknown, không auto gửi mutation khác; read-only review không write; DB/audit đúng một lần. Reset không bị UI báo success chỉ vì version đổi.                                          |
| E13 | Expired/revoked session khi đang đọc/form/chuẩn bị submit                                   | Chặn thao tác, clear riêng tư, login lại; stale response không ghi vào session mới.                                                                                                       |
| E14 | Bàn phím, focus confirm/cancel/error, nav/form/list trên desktop/mobile                     | Các tác vụ chính dùng được không cần chuột; không mất action vì layout/overflow.                                                                                                          |

Các request kiểm tra password trong E09 chỉ thuộc test tự động với dữ liệu giả; không đưa chức năng thử mật khẩu target vào sản phẩm. Với E12, read-back xác nhận state và DB audit cho test, không nâng nó thành bằng chứng product UI có operation receipt.

### 9.4. Regression bắt buộc

- Chạy toàn bộ frontend unit/component suite, mock E2E và real API E2E student/staff/admin; không chỉ chạy spec mới rồi kết luận pass.
- Cập nhật assertion ADMIN unsupported trong `portal.spec.ts`, `staff-portal.spec.ts`, `notification-inbox.spec.ts`, `change-proposals.spec.ts` theo landing mới. Vẫn kiểm tra ADMIN không vào student/staff business route; STUDENT/STAFF không vào admin.
- Shared `write()` thay method/role phải có regression CSRF, expected user, deadline/abort của booking/staff/proposal/notifications hiện tại.
- Chạy backend account integration và các unit contract liên quan khi harness/contract integration cần xác minh, giữ nguyên backend behavior #49. Không viết lại business rules ở frontend hay thay tests backend để hợp một UI sai.
- Thiếu prerequisites integration là fail rõ ràng, không `test.skip`, không mock-only fallback hoặc báo xanh bằng suite rỗng.

## 10. Trình tự triển khai và điều kiện qua từng bước

### Bước 1 — Xác nhận baseline và contract

- Tạo nhánh feature từ `develop` mới nhất theo Git Flow; đọc lại convention/AGENTS/version-matched Next docs cần dùng.
- Đối chiếu 4 endpoint, response/error, profile policy, raw timestamp và runtime grant với §3. Nếu develop đổi contract kể từ baseline thì cập nhật plan/test trước khi wire UI.
- Xác định ADMIN landing `/admin/accounts`; chốt list + editor/panel thay vì phát sinh các route/API mới.
- **Qua bước khi:** request/response types và danh sách policy/unknown handling đã khớp source #49; không có quyết định API tự đặt.

### Bước 2 — Types, mapping và service methods

- Thêm admin contracts/mapping, methods và hỗ trợ `PATCH`/`ADMIN` cho helper hiện có.
- Thêm account error mapping; chụp payload/version và theo dõi dispatch để phân loại kết quả.
- Viết unit/service tests cho method/body/query, timestamp, CSRF owner, role, abort và malformed receipt.
- **Qua bước khi:** typed services dùng API thật hiện có; không còn component phải tự dựng request; shared caller regressions pass.

### Bước 3 — Guard, shell và entry points

- Thêm `/admin` layout/index/accounts composition, admin gate và safe return path.
- Nối root `ProtectedContent`, login/CTA, account home link và audience ADMIN của shared shell.
- Thêm guard/layout tests và giữ student/staff denial/routing.
- **Qua bước khi:** ADMIN thật vào shell đúng; role sai chưa mount account feature hoặc phát admin data requests; mock mode không giả vờ cung cấp admin feature.

### Bước 4 — Reads và account list

- Implement typed list resource, filters/search/pagination và loading/error/empty/forbidden.
- Thêm selection/editor baseline theo ID/version, query/identity invalidation.
- Bổ sung component tests cho stale reads và paging/filter behavior.
- **Qua bước khi:** dữ liệu authoritative, response cũ không đè query/identity mới và không persist account data.

### Bước 5 — Create/edit/role/activation/reset và recovery

- Implement forms đúng field/validation, sensitive confirmation, synchronous submit guard và clear password.
- Implement mutation states §7 trước khi mở submit trong UI: conflict/unknown không đi qua generic retry button.
- Xử lý policy, no-op, self revoke, refresh read sau receipt và identity cleanup.
- Thêm component tests pending/rejected/conflict/unknown/session switching; không chỉ happy path.
- **Qua bước khi:** mỗi operation có xác nhận backend đúng, không auto replay, không thay original version, không giữ password trong recovery state.

### Bước 6 — Real fixtures và acceptance

- Sửa harness reset, thêm admin fixtures/inspect, mở rộng committed-response probe có giới hạn.
- Thêm real API spec E01–E14; sửa các ADMIN landing assertions cũ.
- Chạy desktop/mobile trên DB owned, đối chiếu DB/account/audit/session sau reload và mất receipt.
- **Qua bước khi:** real behavior pass, không skip; cleanup không làm nhiễm các suite cũ; audit/assertion output secret-safe.

### Bước 7 — Checks, tài liệu sử dụng và PR

- Chạy checks ở §11 trên source cuối cùng và ghi đúng command/result.
- Bổ sung hướng dẫn ngắn vào `docs/tasks/admin-account-management.md` cho route, account flow, policy messages, conflict/unknown recovery và cách chạy E2E mới. Không biến phần này thành runbook hạ tầng ngoài #51.
- PR mô tả behavior, source SHA, build/config identity, test results và giới hạn còn thực tế. Review/merge vào `develop`; chỉ đóng #51 khi acceptance và review/merge đã đạt.

## 11. Lệnh xác minh và môi trường

Các lệnh dưới đây là checklist **sẽ chạy khi triển khai**, không phải kết quả kiểm thử của tài liệu này. Chạy từ repo root; dùng Node theo `.nvmrc`, npm theo `packageManager`, Python theo dự án.

### 11.1. Frontend checks và mock regressions

```powershell
npm ci
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test

$env:NEXT_PUBLIC_APP_MODE = 'mock'
npm --workspace src/frontend run build
npx --no-install playwright install chromium
npm --workspace src/frontend run test:e2e
```

Trên Linux CI dùng bước cài browser/system dependencies hiện có: `npx --no-install playwright install --with-deps chromium`. Không thay đổi lockfile/version dependency chỉ để thực hiện #51.

### 11.2. API/MySQL desktop và mobile

Prerequisites:

- MySQL 8.4.x truy cập được; `MYSQL_TEST_URL` cấp quyền tạo/dọn schema riêng của harness. Giá trị do môi trường cấp, không ghi credential thật vào plan/PR/artifact.
- Python backend dependencies đã cài bằng `requirements-dev.txt` và editable app; `PYTHONPATH=src/backend`.
- API mode build có `API_UPSTREAM_URL=http://127.0.0.1:8101`; không tái sử dụng build mock.
- Playwright Chromium khả dụng; các port 3101/8101 có thể được harness sử dụng. Có thể chọn Python interpreter bằng `FRONTEND_E2E_PYTHON` theo config hiện có.

```powershell
python -m pip install -r requirements-dev.txt
python -m pip install -e .
python -m black --check tools/scripts/frontend_e2e_backend.py
python -m ruff check tools/scripts/frontend_e2e_backend.py

$env:PYTHONPATH = 'src/backend'
$env:NEXT_PUBLIC_APP_MODE = 'api'
$env:API_UPSTREAM_URL = 'http://127.0.0.1:8101'
# MYSQL_TEST_URL phải được cấu hình từ môi trường trước khi chạy.
npm --workspace src/frontend run build
npm --workspace src/frontend run test:e2e:api
```

`playwright.api.config.ts` đã chạy `api-desktop` và `api-mobile`, start backend thật + Next production, workers=1/retries=0. Spec mới được discover tự động; không cần workflow hoặc Playwright config mới. Giữ job frontend và frontend-api hiện có; chỉ sửa CI nếu cần để kiểm thử thay đổi thực sự được thực thi.

Backend regression tập trung, với cùng MySQL prerequisite và cấu hình backend test của repo:

```powershell
python -m pytest --require-mysql tests/backend/integration/test_admin_account_endpoints.py
```

Nếu sửa thêm Python test/helper thì mở rộng Black/Ruff/typecheck và tests theo file ảnh hưởng và workflow hiện tại. Frontend không được tự mở rộng runtime grant: môi trường triển khai phải dùng grants #49 hiện tại, bao gồm quyền column-level `UPDATE(id)` trên singleton `service_settings` để locking read hoạt động. Thiếu grant được báo là lỗi prerequisite/integration.

## 12. Ma trận truy vết yêu cầu #51

| Yêu cầu issue                                                                   | Phần triển khai   | Bằng chứng nghiệm thu                                                                            |
| ------------------------------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------ |
| `/admin` layout/guard/navigation/service/type boundaries                        | §4–5, bước 2–3    | Guard/service/layout tests; E01, E14; reviewed PR.                                               |
| List/filter/pagination/create/permitted updates/role/activation/password reset  | §3, §6            | Service/forms tests; E02–E06, E09; persisted account sau reload.                                 |
| Last-admin/occupied-staff restrictions và confirmation                          | §3.4, §6.3–6.4    | Policy/confirm tests; E06–E07; no mutation khi cancel hoặc rejected.                             |
| Loading/empty/error/forbidden/stale-conflict/unknown; không retry mutation khác | §6.1, §7          | Component state/dispatch tests; E08, E12–E13; mutation/audit count.                              |
| Identity/session change, stale reads/writes, clear data, revoked session/reauth | §5, §7            | Stale response/session tests; E09–E11, E13.                                                      |
| Keyboard + desktop/mobile, không persist password hoặc lộ hash/token            | §8                | Accessibility tests, E09/E14, secret-safe fixtures/evidence, cả hai Playwright projects.         |
| Component tests guard/stale/pending/failed/unknown/validation/confirmation      | §9.1              | Vitest/RTL suite pass.                                                                           |
| Real API/MySQL ADMIN, revoked sessions, denied STUDENT/STAFF                    | §9.2–9.3          | E01, E06, E09–E13 chạy thật, không skip.                                                         |
| Persisted account/audit sau reload, duplicate email, concurrent update          | §3.3, §9.3        | E03–E09, DB fixture assertions; response/state/audit count khớp.                                 |
| Student/staff regressions và format/lint/typecheck/build                        | §9.4, §11         | Full frontend checks, mock E2E và real API E2E hiện có đều pass.                                 |
| Bắt đầu từ develop, reviewed PR, preserve contracts và evidence                 | §1–2, §10–11, §13 | Source/build/config identity và kết quả thật trong PR; review/merge/acceptance trước đóng issue. |

## 13. Điều kiện hoàn thành và evidence trong PR

- [ ] Branch bắt đầu từ `develop` đã cập nhật; diff giữ đúng phạm vi #51 và theo convention/Git Flow/AGENTS.
- [ ] ADMIN truy cập portal bằng session thật; STUDENT/STAFF bị chặn cả UI và API; entry points/return path đúng.
- [ ] Tất cả thao tác account dùng đúng API #49, bao gồm raw version, student profile policy, no-op và session revocation.
- [ ] Sensitive changes có confirmation; không báo success trước backend receipt; double submit không tạo write lặp.
- [ ] Loading/empty/error/forbidden/conflict/unknown đầy đủ; không auto replay/rebase hoặc gửi mutation thay thế sau uncertain outcome.
- [ ] Dữ liệu và password được clear theo identity/session lifecycle; response cũ không lọt vào identity mới.
- [ ] Desktop/mobile/keyboard đạt các kịch bản chính; không đưa account/password/session payload vào logs/evidence.
- [ ] Real API/MySQL chứng minh account/audit persisted, duplicate/stale conflict và revoke; missing prerequisite fail rõ, không skip.
- [ ] Frontend checks và student/staff regressions pass trên source cuối; harness formatting/lint và regression liên quan pass.
- [ ] PR ghi source SHA đã test; build mode và public config identity cần tái chạy (app mode/upstream test origin, version runtime/DB); commands, pass/fail/skipped counts và liên kết CI. Nếu check dùng image/artifact, ghi đúng identity của artifact đó; không tạo release/publish workflow chỉ để phục vụ issue UI.
- [ ] Evidence chỉ gồm fictional data và thông tin allowlist. Thử nghiệm local/CI được mô tả đúng là automated lab proof, không được gọi là measurement triển khai thực tế.
- [ ] API #49 đã merged và được integrate thực; PR #51 được review/merge vào `develop`; acceptance criteria đã đạt trước khi đóng issue.

Không còn API policy phải tự suy đoán để bắt đầu #51: các quyết định last-active, referenced role, stale version và revoke đã có ở #49. Những chỗ contract không cung cấp bằng chứng, đặc biệt unknown password reset, phải thể hiện đúng giới hạn trong UI/test thay vì thêm tính năng backend ngoài yêu cầu.
