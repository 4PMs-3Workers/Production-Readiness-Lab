# Kế hoạch triển khai issue #52 — Danh mục tư vấn và cấu hình đặt lịch

- **Issue:** [#52 — Add consultation catalog and booking configuration screens](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/52).
- **Ngày lập:** 09/11/2026, Asia/Bangkok.
- **Trạng thái:** Kế hoạch trước triển khai. Chưa triển khai tính năng hoặc chạy các kiểm thử tính năng được đề xuất trong tài liệu này.
- **Baseline GitHub đã đối chiếu:** `develop` tại `92d9157533c806ce5a1e1ac25b431cb65b4e19b4`.
- **Dependencies:** #50 đã được merge qua [PR #64](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/64); #51 đã được merge qua [PR #61](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/61). Cả hai issue đã đóng. #52 đang mở và chưa tìm thấy PR tham chiếu tại thời điểm khảo sát.
- **Checkout khi lập kế hoạch:** `feature/admin-account-management` tại `71eff79236c5cbd34b44b77eaae803d4cdb0b6e0`. Checkout có frontend #51 nhưng chưa có backend #50; contract #50 được đọc trực tiếp từ baseline GitHub, không suy ra API còn thiếu từ checkout local.
- **Thay đổi của bước lập kế hoạch:** chỉ thêm tài liệu này; giữ nguyên branch và mã tính năng.
- **Khi bắt đầu triển khai:** cập nhật `develop`, kiểm tra thay đổi kể từ baseline, rồi tạo `feature/admin-consultation-configuration` theo `docs/other/GITFLOW.md`. PR đích là `develop`, có review trước merge.

## 1. Kết quả bàn giao và giới hạn phạm vi

ADMIN dùng session thật để quản lý danh mục nhu cầu tư vấn, giờ phục vụ theo tuần và quy định đặt lịch trong portal chung. Mỗi lần lưu chỉ được báo thành công khi có response hợp lệ từ backend hoặc đã đối chiếu được trạng thái hiện tại với yêu cầu đã gửi.

Ba trang mới:

| Route                     | Chức năng                                                                             |
| ------------------------- | ------------------------------------------------------------------------------------- |
| `/admin/purposes`         | Danh sách/lọc/phân trang, tạo, sửa các trường được phép, ngừng/kích hoạt lại purpose. |
| `/admin/service-hours`    | Xem đủ bảy ngày, thêm/xóa nhiều khoảng phục vụ trong ngày, hiển thị ngày đóng cửa.    |
| `/admin/booking-settings` | Chỉnh timezone, bước bắt đầu slot và booking horizon; xem tác động trước khi lưu.     |

Hai trang cuối là hai cách chỉnh **cùng một tài nguyên cấu hình**, dùng chung snapshot, draft và revision; API lưu policy và toàn bộ weekly windows trong một transaction.

Phạm vi gồm service/types/mapping, validation, loading/error/empty/forbidden, review xung đột, khôi phục kết quả ghi chưa rõ, keyboard/mobile, component tests và real API/MySQL E2E. Cần mở rộng test harness và kiểm tra bắt buộc để các ca này thực sự được chạy.

Không thêm business endpoint, migration, runtime grant, rescheduling, nhiều trường hoặc trang duyệt nội dung tư vấn. Không sửa thời gian/thời lượng lịch đã xác nhận. Chỉ sửa thêm frontend sinh viên/nhân viên nếu kiểm thử tích hợp chứng minh một lỗi trực tiếp khiến cấu hình đã lưu không được áp dụng đúng.

## 2. Nguồn hợp đồng và nguyên tắc tích hợp

| Nguồn đã đối chiếu                                                                                                                                                            | Nội dung dùng trong triển khai                                                                                    |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| [Runbook #50 tại baseline](https://github.com/4PMs-3Workers/Production-Readiness-Lab/blob/92d9157533c806ce5a1e1ac25b431cb65b4e19b4/docs/tasks/admin-booking-configuration.md) | HTTP, validation, no-op/replay, concurrency, audit và deployment prerequisite.                                    |
| [OpenAPI tại baseline](https://github.com/4PMs-3Workers/Production-Readiness-Lab/blob/92d9157533c806ce5a1e1ac25b431cb65b4e19b4/docs/api/openapi.yaml)                         | Endpoint, request/response và error contract chính xác; Git blob `b33fcb6bd4c6222cbfefe0ae33c2ffcbe4ebe9ac`.      |
| Backend `api/admin_booking_configuration.py`, `schemas/admin_purposes.py`, `schemas/admin_booking_configuration.py`, `schemas/common.py`                                      | Mutable fields, strict integer/boolean, timezone được chấp nhận, timestamp và weekly-window constraints.          |
| Backend `services/admin_purposes.py`, `admin_booking_configuration.py`, `admin_writes.py`                                                                                     | Matching-state được kiểm tra trước stale-version; atomic policy/windows; retry chỉ khi biết transaction đã abort. |
| `features/admin/`, `features/api/api-provider.tsx`, `services/api/client.ts`                                                                                                  | Shared ADMIN guard, session generation, CSRF, abort scope, request và mutation lifecycle.                         |
| `tests/frontend/api-e2e/admin-portal.spec.ts`, `tools/scripts/frontend_e2e_backend.py`, `playwright.api.config.ts`                                                            | Session thật, hai ADMIN, MySQL fixture, fault sau commit và desktop/mobile.                                       |
| `docs/architecture/system-design.md`, `database-design.md`, `docs/tasks/catalog-availability.md`                                                                              | Singleton configuration lock, future eligibility, stored appointment instants/duration và data isolation.         |
| `src/frontend/AGENTS.md`, `docs/other/CONVENTION.md`, `GITFLOW.md`                                                                                                            | App Router, strict TypeScript, service boundary, convention và reviewed PR.                                       |

Đã đọc hướng dẫn Next.js 16.4.1 cài tại `node_modules/next/dist/docs/01-app/01-getting-started/03-layouts-and-pages.md` và `05-server-and-client-components.md`. Trước khi code, đọc lại tài liệu liên quan nếu dependency/baseline thay đổi. Page giữ vai trò composition; form và resource state thuộc Client Components.

Nếu tài liệu kiến trúc cũ mô tả giá trị mặc định bảy ngày hoặc giờ Việt Nam, dùng contract runtime #50 và booking context để xác định giá trị thật. Không cố định UI vào các default này.

## 3. Hợp đồng API phải giữ nguyên

### 3.1. Endpoint và dữ liệu

Tất cả path có tiền tố `/api/v1`, yêu cầu ADMIN active. GET không cần CSRF; mutation dùng JSON và CSRF hiện hành qua transport chung.

| Operation             | Method/path                        | Input                                                                                                   | Success                                                               |
| --------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| List purposes         | `GET /admin/purposes`              | `page >= 1`, `page_size` 1–100; tùy chọn `is_active`.                                                   | `200 { items, page, page_size, total }`, theo code order của backend. |
| Create purpose        | `POST /admin/purposes`             | `code`, `name`, `duration_minutes`, `requires_free_text`, `is_active`.                                  | `201 AdminPurpose`, kể cả exact matching replay.                      |
| Read purpose          | `GET /admin/purposes/{purpose_id}` | UUID, không query.                                                                                      | `200 AdminPurpose`, gồm cả inactive.                                  |
| Replace purpose       | `PUT /admin/purposes/{purpose_id}` | `expected_updated_at` và **đủ bốn mutable fields**; không gửi `code`.                                   | `200 AdminPurpose`.                                                   |
| Read configuration    | `GET /admin/booking-configuration` | Không query.                                                                                            | `200 BookingConfiguration`.                                           |
| Replace configuration | `PUT /admin/booking-configuration` | `expected_updated_at`, `timezone`, `start_step_minutes`, `booking_horizon_days`, **toàn bộ `windows`**. | `200 BookingConfiguration`.                                           |

`AdminPurpose` gồm `id`, `code`, `name`, `duration_minutes`, `requires_free_text`, `is_active`, `updated_at`.

`BookingConfiguration` gồm `timezone`, `start_step_minutes`, `booking_horizon_days`, `windows`, `updated_at`. Mỗi window chỉ gồm `weekday`, `opens_at`, `closes_at`; không có ID window trong API UI.

Không có search/code query, sort tùy chọn, DELETE purpose hoặc PATCH riêng cho giờ phục vụ. Không tự thêm endpoint từ nhu cầu của form. List mặc định page 1/page size 20; trang vượt dữ liệu có thể trả `items: []`, phải xử lý theo `total`.

### 3.2. Quy tắc validation và controls

| Trường/quy tắc                    | Contract                                                                            | Cách thể hiện trong UI                                                                                            |
| --------------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Purpose code                      | `^[A-Z][A-Z0-9_]{0,49}$`.                                                           | Required khi tạo; hiển thị bất biến khi sửa; không đưa vào PUT. Không tự đổi code trong bước recovery.            |
| Purpose name                      | Trim, 1–150 ký tự.                                                                  | Đếm Unicode code points theo backend; không cắt theo UTF-16 gây sai giới hạn.                                     |
| Duration / step / horizon         | JSON integer strict, 1–32.767.                                                      | Nhãn kèm đơn vị phút/ngày, kiểm tra integer và range; không dùng `parseInt` làm tròn input sai.                   |
| `requires_free_text`, `is_active` | JSON boolean strict.                                                                | Checkbox/switch có label; không serialize chuỗi `"false"`.                                                        |
| Timezone                          | IANA location keys từ backend `tzdata` zone table; thêm `UTC`, `Etc/UTC`; dài 1–64. | Text/combobox có gợi ý, cho nhập giá trị hợp lệ ngoài danh sách gợi ý. Backend quyết định membership cuối cùng.   |
| Weekday                           | Integer ISO 1–7.                                                                    | Bảy nhóm từ thứ Hai đến Chủ nhật, luôn hiện kể cả không có window.                                                |
| Wall time                         | `HH:MM:SS`, không timezone/microsecond, `00:00:00`–`23:59:59`.                      | `type="time"` với `step=1` hoặc control tương đương; giữ phần giây, chuẩn hóa `HH:MM` thành `HH:MM:00` trước gửi. |
| Một window                        | `opens_at < closes_at` trong cùng ngày.                                             | Lỗi gắn đúng hàng; `24:00`, giờ bằng nhau và overnight đều không hợp lệ.                                          |
| Nhiều window cùng ngày            | Không duplicate/overlap; adjacent hợp lệ và giữ riêng.                              | Sort bản sao để kiểm tra, đánh dấu các hàng xung đột; không tự merge hoặc làm tròn.                               |
| Ngày không có window              | Đóng cửa; `windows: []` đóng cả tuần hợp lệ.                                        | Nhãn “Đóng cửa”, không tự thêm giờ mặc định khi tải; đóng cả tuần có review tác động rõ.                          |

Validation chéo chỉ áp các ràng buộc API đã giao. Không tự bắt duration chia hết step, step nhỏ hơn duration hoặc duration vừa mọi window. Cấu hình không có slot phù hợp có thể vẫn hợp lệ: UI giải thích tác động, không invent policy mới. Một buổi gặp phải vừa trong một window; hai khoảng tiếp giáp không cộng thành một window dài hơn.

Không suy ra timezone hợp lệ chỉ từ `Intl.supportedValuesOf()` hoặc offset UTC. Browser và backend có thể khác phiên bản timezone data; danh sách gợi ý không phải allowlist. Nếu browser không format được một timezone, hiển thị key rõ ràng và lỗi hỗ trợ phù hợp, không âm thầm thay timezone khác.

Backend trả `422 VALIDATION_ERROR` đã sanitize, không cung cấp contract field-error/overlap detail có cấu trúc. UI tự highlight lỗi local; khi backend vẫn từ chối thì hiện thông báo kiểm tra timezone, giá trị và các khoảng giờ kèm request ID. Không giả định response có danh sách field errors.

### 3.3. Version, no-op, replay và audit

1. Giữ nguyên chuỗi `updated_at` làm `expected_updated_at`, bao gồm mọi chữ số microsecond và offset. Không chuyển token qua `Date.toISOString()`.
2. Mỗi draft gắn snapshot gốc và revision lúc bắt đầu chỉnh; background GET không thay token bên dưới draft đang bẩn.
3. Purpose/config PUT kiểm tra **matching-state trước version**. Body có state giống hiện tại thành công kể cả token cũ; revision và audit không tăng. Đây là khác biệt với account PATCH #51.
4. State khác + token cũ trả `PURPOSE_VERSION_CONFLICT` hoặc `BOOKING_CONFIGURATION_VERSION_CONFLICT`. Không tự thay token rồi submit draft cũ.
5. Configuration thay settings-only hoặc windows-only đều tăng revision chung. Một PUT thay cả hai nhóm chỉ tạo một `BOOKING_CONFIGURATION_UPDATED`.
6. Purpose POST với cùng code và cùng values trả purpose hiện hữu với 201, không tạo audit mới. Cùng code nhưng khác values trả `PURPOSE_CODE_CONFLICT`.
7. Actual create/update purpose có `PURPOSE_CREATED` / `PURPOSE_UPDATED`; matching no-op/replay không tạo effect/audit lặp. Không dùng revision tăng đơn thuần để kết luận request của mình đã thành công.
8. Không thêm `Idempotency-Key` cho các endpoint này. Khôi phục dựa trên exact body, immutable code/resource ID và version contract đã giao.

### 3.4. Error và hành vi giao diện

| Error/status                                                                | Hành vi                                                                                                                                 |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `PURPOSE_CODE_CONFLICT` — 409                                               | Hiện xung đột code; giữ draft để xem xét. Nếu đang khôi phục POST cũ, không xem lỗi này là bằng chứng request ban đầu chưa từng commit. |
| `PURPOSE_VERSION_CONFLICT` / `BOOKING_CONFIGURATION_VERSION_CONFLICT` — 409 | Khóa lưu lại; đọc current, hiển thị current so với submitted/draft và yêu cầu review.                                                   |
| `PURPOSE_NOT_FOUND` — 404                                                   | Bỏ khả năng save dựa trên snapshot không còn tồn tại; cho tải lại catalog.                                                              |
| `VALIDATION_ERROR` — 422                                                    | Hiện lỗi field local/form đúng ngữ cảnh admin; giữ input để sửa nếu không có intent cũ chưa rõ.                                         |
| `INVALID_SESSION` — 401                                                     | Theo provider/gate hiện tại, dọn owner cũ, chuyển reauthentication; không nhận response cũ.                                             |
| `FORBIDDEN_ROLE` — 403                                                      | Khóa thao tác, xác minh lại session; không hiện dữ liệu admin cho role khác.                                                            |
| `INVALID_CSRF`, `INVALID_ORIGIN` — 403                                      | Giải thích lỗi xác minh; không retry tự động. Lần thử lại chủ động vẫn đi qua transport lấy CSRF mới.                                   |
| 413 / 415                                                                   | Thông báo input/payload không hợp lệ; không đổi sang method hoặc content type khác.                                                     |
| Network/timeout, 5xx, malformed success sau dispatch, abort sau dispatch    | Kết quả ghi chưa rõ; chuyển recovery, không thông báo rollback hoặc success.                                                            |

Giữ `X-Request-ID`, `Retry-After` và helper hiển thị lỗi an toàn. Không show raw response, SQL, stack trace hoặc token. Một lần retry/recovery-read bị từ chối không tự xóa trạng thái chưa rõ của request trước đó.

## 4. Kiến trúc frontend và cấu trúc file

Tên file mới dưới đây là dự kiến; giữ phân tách trách nhiệm khi code thay vì tạo nhiều abstraction không dùng.

| Khu vực           | Tệp dự kiến/thay đổi                                                                                                              | Trách nhiệm                                                                                                                              |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Routes            | `app/admin/purposes/page.tsx`, `service-hours/page.tsx`, `booking-settings/page.tsx`                                              | Composition vào feature tương ứng; giữ `/admin` redirect accounts.                                                                       |
| Shell             | `features/admin/admin-layout.tsx`, `components/portal-shell.tsx`                                                                  | Section có kiểu rõ ràng, nav/breadcrumb/active state đúng route; sidebar text đúng ngữ cảnh.                                             |
| Guard/return path | `features/admin/admin-session-gate.tsx`; caller `features/api/api-login.tsx`                                                      | Mở rộng exact allowlist cho ba route mới; deep link sau login đúng trang.                                                                |
| Contracts         | `services/admin-catalog-contracts.ts`, `admin-configuration-contracts.ts`                                                         | DTO, create/full replace/query và options dùng lại `AdminAccessOptions`/`AdminWriteOptions`.                                             |
| Mapping           | `services/api/admin-catalog-mapping.ts`, `admin-configuration-mapping.ts`                                                         | Parse `unknown`, allowlist fields, strict type/range/time/revision và canonical comparison.                                              |
| API               | `services/api/client.ts`, `services/api/errors.ts`                                                                                | Sáu typed methods và error messages đúng contract, transport/session/CSRF chung.                                                         |
| Admin workspace   | `features/admin/admin-workspace.tsx`                                                                                              | State chỉ thuộc owner hiện tại; giữ config base/draft và mutation/recovery qua điều hướng cùng portal. Load từng resource khi route cần. |
| Purpose UI        | `features/admin/purposes/`                                                                                                        | List/filter/paging, form, confirmation, validation và recovery purpose.                                                                  |
| Configuration UI  | `features/admin/booking-configuration/`                                                                                           | Shared full draft, general form, weekly windows, review diff, mutation/recovery và CSS.                                                  |
| Unit/component    | `tests/frontend/unit/admin-catalog-api.test.ts`, `admin-configuration-api.test.ts`; component files tương ứng                     | DTO/service/validation và hành vi UI/session/version.                                                                                    |
| Real E2E          | `tests/frontend/api-e2e/admin-catalog.spec.ts`, `admin-booking-configuration.spec.ts`                                             | Persist, future availability, stored appointments, races và unknown outcomes thật.                                                       |
| Harness/evidence  | `tools/scripts/frontend_e2e_backend.py`, backend probe/reset tests, `tests/frontend/safe-reporter.ts` và validator CI nhỏ nếu cần | Owned fixtures/reset, PUT probes và required zero-skip evidence.                                                                         |
| Bàn giao          | `docs/tasks/admin-consultation-configuration.md`                                                                                  | Cách sử dụng, commands, evidence thực tế và giới hạn sau triển khai.                                                                     |

`AdminWorkspace` đặt bên trong subtree được `AdminSessionGate` key theo owner, nhưng bên trên các page để cùng session đổi route không mất pending/recovery. Không tạo auth provider mới. `components/application-provider.tsx` đã nhận diện `/admin/*`; chỉ sửa nếu baseline mới chứng minh cần thiết.

Không dùng nguyên `useAdminAccountMutation`/`AccountRecovery`: chúng hardcode account, password, self-revocation và list-based recovery. Purpose/config có direct GET và replay/no-op khác. Chỉ trích lifecycle chung nếu giữ được account behavior và có regression phù hợp.

### 4.1. Typed services và parser

Methods dự kiến: `listAdminPurposes`, `getAdminPurpose`, `createAdminPurpose`, `replaceAdminPurpose`, `getAdminBookingConfiguration`, `replaceAdminBookingConfiguration`.

- Admin GET dùng pattern `verifyAdmin` trước/sau read và signal kết hợp caller với service scope; stale response không được tới owner mới.
- Writes dùng `write` hiện có với role `ADMIN`, expected user/session generation, đúng method và `onDispatch`; giữ same-origin, no-store, redirect:error, timeout và invalid-session handler.
- Tạo payload snapshot **trước mọi await**; copy từng window và array, không chỉ spread object ngoài. Thay đổi form trong lúc chờ CSRF không được đổi body đang chuẩn bị gửi.
- Parser kiểm tra identity resource/UUID, required scalar types, strict boolean/integer, metadata paging, timestamp có offset, local times và window invariants; chỉ map các fields đã công bố.
- Receipt phải khớp target ID/code và desired mutable state sau normalization contract. Receipt malformed hoặc lệch target/state sau dispatch là unknown, không phải validation rejection trước ghi.
- Comparison configuration sort canonical windows theo weekday/open/close; không phụ thuộc row key của UI hoặc thứ tự nhập. Không gộp adjacent windows.

## 5. Luồng giao diện và tác động nghiệp vụ

### 5.1. Catalog purpose

List hiện code, tên, thời lượng, yêu cầu nội dung tự do và trạng thái. Filter mặc định xem tất cả; chọn active/inactive gửi `is_active` đúng API. Đổi filter quay về page 1, không lọc/sort lại một trang client để giả full result. Phân biệt catalog rỗng với page out of range; dùng total để refetch page hợp lệ cuối khi cần.

Form create yêu cầu đủ năm fields. Form edit GET theo stable ID, hiện code bất biến và gửi đủ bốn mutable fields cùng revision. Không lấy revision mới từ background list để ghi đè draft cũ. Cancel không gửi request.

Ngừng hoạt động có bước review: purpose ngừng dùng cho booking/proposal acceptance mới; UUID/code, staff links và lịch sử được giữ. Không có nút xóa. Kích hoạt lại không đồng nghĩa mọi staff tự nhận purpose; eligibility vẫn theo active staff và liên kết hiện hành. UI không suy ra purpose có/không có tham chiếu từ list vì API không cung cấp thông tin đó.

### 5.2. Một draft cho service-hours và booking-settings

Shared workspace giữ `base`, `draft`, `baseRevision`, dirty fields và trạng thái mutation. Khi GET đầu tiên thành công, hai trang cùng nhìn một snapshot. Không có default policy thay cho GET lỗi hoặc singleton thiếu.

Trang settings chỉnh timezone/step/horizon; trang hours chỉnh windows. Hai trang giữ draft khi chuyển qua lại trong cùng owner. Nút “Lưu cấu hình” luôn mở review **toàn bộ diff**, gồm thay đổi ở trang kia nếu có; thông báo rõ các thay đổi này được lưu cùng nhau. “Bỏ thay đổi” reset toàn bộ draft sau xác nhận khi dirty. Không âm thầm gửi những field chưa được review.

Mỗi Save deep-snapshot toàn bộ policy/windows và token từ cùng base. Nếu chỉ sửa hours, các policy field giữ giá trị từ base/draft đã review; nếu chỉ sửa settings, giữ toàn bộ windows. Không gửi mảng rỗng để đại diện cho phần form đang ẩn.

Reload khi draft sạch có thể thay base. Khi dirty, chỉ đọc current riêng để review; không overwrite draft hoặc lén nâng revision. Nếu current thay đổi, người dùng chọn tải bản mới và sửa lại, hoặc áp dụng có chủ đích các field đã chọn vào draft mới rồi review lần nữa. Không có auto merge/rebase/submit.

### 5.3. Weekly-window editor và timezone

Bảy nhóm ngày có label rõ ràng, thao tác thêm/xóa window bằng keyboard. Mỗi hàng có stable UI key, label cho giờ mở/đóng và nút xóa có tên ngày/hàng. Hiện lỗi duplicate/overlap sát hàng, error summary dẫn focus về trường liên quan. Window trống chưa hoàn chỉnh là lỗi draft; ngày không có hàng là đóng cửa hợp lệ.

Giờ nhập là wall time trong timezone cấu hình, không chuyển qua timezone máy/browser. Khi đổi timezone, giữ wall times đang nhập và review tác động; không tự dịch giờ để giữ UTC. Hiện timezone key cạnh editor và summary, không suy ra một offset cố định cho timezone có DST.

### 5.4. Giải thích tác động và trạng thái xác nhận

Review nêu cụ thể duration/step/horizon/timezone/windows nào thay đổi và ngày nào đóng. Giải thích: cấu hình mới áp dụng khi tính/xác minh eligibility đặt lịch tương lai; danh sách slot chỉ là preview và backend kiểm tra lại khi ghi.

Thông điệp phải có: “Lịch đã xác nhận giữ nguyên thời điểm và thời lượng đã lưu.” Đổi timezone có thể đổi cách hiển thị giờ local của cùng instant; không gọi đó là thay đổi thời điểm lịch. Không hứa số slot hoặc số lịch bị ảnh hưởng vì API không có impact-preview/count.

Trong preparing/pending, khóa Save, confirmation và các thao tác làm đổi intent. Dùng active ref để chặn cả hai click/Enter trước React rerender. Chỉ receipt hợp lệ mới hiện “Đã lưu”; matching-state/no-op không được quảng bá là một lần tạo thay đổi/audit mới.

## 6. Session, conflict và khôi phục kết quả chưa rõ

### 6.1. Owner và lifecycle

Owner gồm `{user.id, sessionGeneration, revision}` như #51. Mọi read/write continuation kiểm tra owner và signal trước cập nhật UI. Gate `hidden/inert` khi verifying không thay cho kiểm tra continuation sau await.

Focus cùng session giữ draft, nhưng khóa submit trong lúc kiểm tra; pre-dispatch bị hủy không tự resume. Replacement session cùng ADMIN, đổi account/role, logout hoặc session loss dọn draft/confirmation/workspace của owner cũ và abort scope. Response về muộn không được hiện hoặc xóa state của owner mới.

Pending/recovery giữ trong memory khi điều hướng trong cùng portal/owner. Không lưu body vào URL, localStorage, sessionStorage hoặc logs. Full reload hoặc reauthentication bắt đầu bằng GET authoritative mới; không tuyên bố đã khôi phục lịch sử transaction qua reload hoặc rằng abort đã rollback request trước. Với PUT, version preconditions tiếp tục bảo vệ lần lưu mới trước request cũ còn chạy. Purpose POST chưa có revision; immutable code cùng matching replay/code conflict bảo vệ khỏi tạo trùng cùng code.

### 6.2. State machine

| State          | Được phép làm gì                                               | Chuyển trạng thái                                                                         |
| -------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `idle/editing` | Edit và validate; review desired changes.                      | Save → `preparing`.                                                                       |
| `preparing`    | CSRF/identity preflight; chưa dispatch mutation.               | `onDispatch` → `pending`; cancellation trước dispatch → trở lại editing.                  |
| `pending`      | Khóa intent khác cùng resource; chưa báo thành công.           | Valid receipt → confirmed; definitive rejection → rejected/conflict; uncertain → unknown. |
| `confirmed`    | Dùng canonical receipt làm base mới, rồi refresh list khi cần. | Mở editor mới với current revision.                                                       |
| `rejected`     | Hiện lỗi xác định và sửa draft khi an toàn.                    | Review/save mới có chủ đích.                                                              |
| `conflict`     | Read current, so sánh và review; khóa save draft cũ.           | Tải current/sửa lại/review, không auto submit.                                            |
| `unknown`      | Chỉ kiểm tra resource hoặc replay đúng intent cũ có chủ đích.  | Exact match/receipt → reconciled; khác state → review/conflict.                           |

### 6.3. Recovery theo resource

- **Purpose PUT:** GET trực tiếp target ID; so code/ID và toàn bộ submitted mutable fields, không chỉ active/name hoặc revision.
- **Configuration PUT:** GET singleton; so timezone, step, horizon và canonical full windows. Read failure không mở khóa intent khác.
- **Purpose POST chưa biết ID:** ưu tiên nút deliberate replay exact body đã snapshot vì contract hỗ trợ cùng code/values. Nếu có chức năng tìm trong list thì phải đọc toàn bộ pages ở chế độ tất cả, đối chiếu exact code; không kết luận không tồn tại từ một page hoặc active-only filter. Không thêm search API.
- **Exact match:** ghi nhận trạng thái hiện tại khớp yêu cầu, cho người dùng tiếp tục từ current receipt/snapshot; không nói đã chứng minh chính request nào tạo ra state đó.
- **State khác:** không khẳng định request ban đầu thất bại; có thể đã được người khác sửa tiếp. Review current/draft và xác nhận thao tác mới với snapshot mới.
- **Replay:** giữ nguyên endpoint, target, body và expected revision đã gửi; fresh CSRF/owner verification được phép. Không đổi token hoặc payload để vượt conflict. Không auto retry sau timeout/5xx.

Nếu một GET diễn ra trước khi request cũ commit thì mismatch không phải bằng chứng rollback. Exact replay với original precondition hoặc quy trình review explicit xử lý sự không chắc chắn này. Một resource chỉ có một mutation lane trong workspace, dùng operation version để continuation cũ không clear hoặc ghi đè intent mới. Purpose lane dùng immutable code với mapping sang UUID khi có receipt/GET, để unknown POST và PUT của cùng purpose không đi hai lane khác nhau. Configuration dùng một singleton lane chung cho cả hai trang.

## 7. Test harness, fixture và evidence

### 7.1. MySQL fixture và reset

Giữ schema riêng `prl_frontend_e2e_<uuid>` với ownership validation, MySQL 8.4, Alembic head và teardown hiện có. Thiếu `MYSQL_TEST_URL`, sai DB version, schema/grant/seed không đủ hoặc server không start phải fail rõ, không `test.skip`/fallback mock.

Reset hiện chỉ phục hồi một phần purpose, chưa đủ khi test sửa catalog/policy. Trước feature E2E, bổ sung reset baseline đầy đủ trong schema do harness sở hữu: tất cả mutable fields/revisions của purpose, extra purposes và staff links, policy và weekly windows; dọn dữ liệu phụ thuộc đúng FK order theo reset hiện hành. Không xóa schema/data không do harness tạo.

Snapshot dùng cho service outage không được thay cho baseline reset thông thường. Test reset hai lần liên tiếp, sau đổi timezone/đóng cả tuần/tạo purpose, và xác minh ca sau trở về cùng baseline. Giữ các tests account/student/staff hiện hữu chạy được với reset mới.

Fixture bổ sung ADMIN thứ hai, purpose có staff link và appointment reference, các window nhiều khoảng/adjacent/seconds, cùng ngày tương lai theo DB clock/business timezone. Tận dụng `seed-admin` và helper hiện có; thêm CLI seed/inspect tập trung khi cần, không thêm endpoint test vào application runtime.

Inspect phục vụ assertions chỉ trả allowlisted fixture state, counts/booleans hoặc các scalar cần so sánh. Không dump appointment descriptions, account/session payloads, passwords, cookies hoặc full audit rows ra stdout/artifacts. Chứng minh preserved appointment bằng so sánh trong helper và trả booleans khi phù hợp.

### 7.2. Fault sau commit thật

`ProbeCommittedResponse` hiện chỉ nhận POST/PATCH. Mở rộng allowlist chính xác cho `POST /api/v1/admin/purposes`, `PUT /api/v1/admin/purposes/{uuid}` và `PUT /api/v1/admin/booking-configuration`.

Giữ cách cho app thật thực hiện transaction thành công rồi mới giữ/truncate response. Không fabricate success để thay bằng chứng MySQL. Unit tests phải bao gồm method/path không hợp lệ, UUID sai, non-success đi nguyên trạng, held/lost response, và existing account/booking probes vẫn đúng.

### 7.3. Required checks và public evidence

Giữ desktop/mobile projects, workers=1, retries=0, browser timezone `America/Los_Angeles` và job API timeout 35 phút. Không tăng timeout mặc định để che race hoặc test chậm; dùng fixture reset gọn và suite timing thực tế để quản lý budget.

Safe reporter hiện chỉ ghi kết quả, chưa enforce zero skipped/discovery coverage. Bổ sung gate bắt buộc cho required real-API suite: lấy tập stable case ID/project expected từ suite của cùng invocation trong `onBegin` hoặc discovery đã sanitize; executed attempts phải bao phủ tập đó, status đạt, không skip/interrupted/failed/unexpected retry và globalErrors=0. Không hardcode tổng số tests từ baseline; subset chạy local có tập expected riêng. Gate đọc evidence đã sanitize; không publish output `--list`, raw HTML/JSON hoặc titles chứa payload.

Có thể bổ sung metadata allowlist cho discovered counts/case IDs và validator nhỏ. Phải có regression fail-probe cho skipped/missing case/worker error và test redaction để failure diagnostics không lọt vào evidence. Giữ nguyên exit failure gốc nếu Playwright hoặc validator fail.

CI chỉ upload allowlisted safe summary theo pattern hiện có. Không upload HTML, trace, screenshot, attachments, stdout/stderr, test title hoặc raw `test-results`. Evidence nêu source SHA, mode/config/artifact identity công khai và counts; không chứa payload nhạy cảm. MySQL frontend harness dùng quyền tạo schema nên không được gọi kết quả này là proof runtime-grant của môi trường deploy; proof đó thuộc backend #50.

## 8. Ma trận kiểm thử

### 8.1. Unit/service/component

| ID  | Kịch bản                                                                                 | Assertion chính                                                                                                      |
| --- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| U01 | DTO/service contract cho sáu methods                                                     | Đúng method/path/query/full body; immutable code không vào PUT; không có query/field tự chế.                         |
| U02 | Parser response sai scalar/UUID/page/time/revision, target hoặc desired state không khớp | Read fail an toàn; malformed success sau dispatch thành unknown, không success.                                      |
| U03 | Revision microseconds/offset và nested snapshot trong CSRF wait                          | Token nguyên văn; sửa draft không đổi body đã snapshot.                                                              |
| U04 | Integer/name/code boundaries                                                             | 0/âm/thập phân/NaN/out-of-range sai; 1/32767 và tên Unicode sát biên đúng.                                           |
| U05 | Wall time/weekday/cross-field                                                            | Seconds được giữ; equal/overnight/24:00/duplicate/overlap sai; adjacent đúng.                                        |
| U06 | Closed day/all-week-closed/order                                                         | Ngày thiếu window hiện đóng, empty array hợp lệ, canonical order không merge rows.                                   |
| U07 | Timezone control                                                                         | UTC/location key, giá trị ngoài suggestions và server rejection; không chuyển wall time bằng timezone browser.       |
| U08 | Loading/empty/out-of-range/error/forbidden và stale GET                                  | Error không thành list rỗng/default; total/paging đúng; late read không đè editor/query mới.                         |
| U09 | Double click/Enter, preparing cancellation và receipt held                               | Chỉ một dispatch, không success trước receipt, không auto resume.                                                    |
| U10 | Version conflict/background reload                                                       | Draft và base token không bị thay ngầm; reload/review cần explicit action, không auto PUT.                           |
| U11 | No-op/matching-state với revision cũ                                                     | Receipt được chấp nhận đúng #50; UI không tạo thông điệp effect/audit mới.                                           |
| U12 | Shared config giữa hai route                                                             | Giữ draft, review full diff; settings-only giữ windows, hours-only giữ policy; một resource lane.                    |
| U13 | Unknown/recovery/read failure/exact replay                                               | So toàn bộ state; giữ original body/token; mismatch không coi rollback; retry rejection không xóa uncertainty cũ.    |
| U14 | Focus cùng session, same ADMIN replacement, khác account/role, expiry/logout             | Giữ hoặc clear đúng #51; stale continuation không dispatch/render/clear intent của owner mới.                        |
| U15 | Deep links/nav/return-path                                                               | Đúng active/breadcrumb; allowlist exact route; chặn external URL, prefix giả, backslash/control chars.               |
| U16 | Keyboard/mobile semantics                                                                | Labels/fieldset, Tab/Enter/Space, focus error/review/recovery, không horizontal overflow gây mất control.            |
| U17 | Harness probe/reset và required evidence gate                                            | PUT thật được fault; reset sạch; missing prerequisites/skip/missing case/error fail; không lộ sensitive diagnostics. |

Unit/component có thể dùng mock transport có kiểm soát. Các test dưới đây phải dùng backend/MySQL thật để nghiệm thu persistence và effects.

### 8.2. Real API/MySQL E2E trên cả desktop và mobile

| ID  | Luồng                                                                                | Bằng chứng cần đạt                                                                                                             |
| --- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| E01 | Anonymous rồi login deep link; STUDENT/STAFF mở từng route và gọi API                | Anonymous reauth đúng trang; non-ADMIN bị chặn UI và backend; không tải dữ liệu admin.                                         |
| E02 | Create/edit purpose, reload                                                          | Fields/UUID/revision persisted; code bất biến, PUT đủ mutable fields; đúng audit.                                              |
| E03 | Cùng code matching replay và khác values                                             | Replay trả cùng purpose, một create/audit; conflict không tạo purpose mới.                                                     |
| E04 | Purpose có staff link và appointment reference: deactivate/reactivate                | Record/link/history giữ nguyên; inactive mất khỏi student booking; active trở lại vẫn tuân staff eligibility.                  |
| E05 | Edit service hours nhiều window, có seconds/adjacent và closed days                  | Reload đúng; day closed không có slot; adjacent không bị cộng thành window.                                                    |
| E06 | Edit settings/timezone/step/horizon, đổi qua lại hai trang rồi save                  | Full policy/windows persisted atomically; fields không sửa được giữ; student context/date range/slot interval cập nhật từ API. |
| E07 | Đóng cả tuần rồi mở lại                                                              | Empty windows được lưu đúng, student thấy không có giờ; mở lại có slot theo policy. Không nhầm 503 với không có slot.          |
| E08 | Tạo confirmed appointment qua student booking thật rồi đổi duration/timezone/windows | DB và student appointment detail giữ `starts_at`, `ends_at`, stored `duration_minutes`; future booking dùng rule mới.          |
| E09 | Hai ADMIN sửa cùng purpose từ cùng revision                                          | Winner persist; loser conflict; không overwrite winner; explicit reload/review trước write mới.                                |
| E10 | ADMIN A sửa hours, ADMIN B sửa settings từ cùng revision                             | Một winner, revision chung chặn lost update; loser không reset phần dữ liệu winner; một audit cho winner.                      |
| E11 | No-op và exact PUT replay sau revision cũ                                            | Không đổi revision/audit; windows-only actual change có revision mới.                                                          |
| E12 | Lose response sau commit thật cho create purpose và PUT purpose/config               | UI unknown; deliberate read/replay nhận đúng state; không duplicate resource/audit, không tự đổi token/body.                   |
| E13 | Unknown rồi ADMIN khác sửa tiếp, hoặc recovery GET thất bại                          | Không xác nhận sai; không unlock new mutation từ read thất bại; explicit review khi current khác submitted.                    |
| E14 | Hold response, đổi/revoke session trước hoặc sau dispatch                            | Không stale success/data vào session mới; trước dispatch không write, sau dispatch UI không tuyên bố rollback.                 |
| E15 | Invalid CSRF gửi bằng `route.continue`, request validation trực tiếp                 | Backend thật từ chối, state/audit giữ nguyên; local validation không che server enforcement.                                   |
| E16 | Keyboard, viewport hẹp và điều hướng account/purpose/config                          | Luồng chính thao tác được; shell/account portal không regression; assertions chạy trong cả hai projects.                       |

E08 lưu snapshot appointment trước mutation; so sánh chính xác UTC instants và stored duration, không chỉ text giờ local. Test future duration/step cần assert slot hoặc booking mới thực tế, không chỉ giá trị settings. Test horizon lớn hơn bảy ngày phải dùng API booking UI có paging theo context; không lấy mock `getBookingDates()` cố định bảy ngày làm oracle.

E09/E10 là concurrent-edit UI proof. Các race giữa configuration và booking/proposal đã được #50 chứng minh dưới lock; chạy lại backend race suites khi tích hợp, không tự tuyên bố rằng test UI hai tab đã thay thế proof transaction đó.

Regression cuối gồm toàn bộ Vitest, mock E2E và real API E2E hiện có: account, student booking/profile/history/cancel, staff portal, proposal và notification. Chỉ mở rộng tests khi có thay đổi/failure/rủi ro chưa giải quyết; không chạy lặp toàn suite khi không có thay đổi mới.

## 9. Thứ tự triển khai và các mốc kiểm chứng

| Bước | Công việc                                                                                                   | Kết quả kiểm chứng trước bước tiếp                                                                                    |
| ---- | ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 0    | Đồng bộ develop, kiểm tra dependencies/AGENTS/contract, tạo feature branch; ghi source và OpenAPI identity. | Không còn suy đoán endpoint/policy; MySQL/API build prerequisite được xác nhận.                                       |
| 1    | Viết contracts/parsers/canonical comparison và sáu service methods; errors và deep payload snapshot.        | U01–U07, raw revision và identity/CSRF tests pass.                                                                    |
| 2    | Mở rộng shell/nav/return path, thêm route composition và workspace owner lifecycle.                         | U12/U14/U15; account/student/staff shell regressions pass.                                                            |
| 3    | Xây purpose list/form/deactivation review và mutation/recovery.                                             | U08–U11/U13; create/edit/conflict/unknown component tests pass.                                                       |
| 4    | Xây shared configuration draft, settings form, seven-day windows, full review và recovery.                  | U05–U14; bảo toàn hidden fields/revision và keyboard.                                                                 |
| 5    | Mở rộng owned fixture/reset, PUT commit probe và evidence zero-skip gate.                                   | U17; test reset độc lập và unknown probe thật hoạt động trước feature E2E. Có thể làm trước bước 3–4 để kiểm tra sớm. |
| 6    | Thêm real E2E theo E01–E16, chạy targeted rồi full regressions.                                             | Persistence, student availability, confirmed appointment preservation, real denial/conflicts/recovery đều có proof.   |
| 7    | Format/lint/typecheck/build và checks bị ảnh hưởng; rà soát keyboard/desktop/mobile, contract và evidence.  | Kết quả trên source cuối; không skip; ghi rõ check chưa chạy nếu prerequisite lỗi.                                    |
| 8    | Cập nhật task runbook/verification, PR review vào develop.                                                  | PR có source/config/artifact identity, commands/results và acceptance mapping; đóng #52 sau review/merge/acceptance.  |

Tách commit theo service contract, shell/workspace, catalog, configuration, harness/E2E và documentation khi hữu ích. Khi review làm đổi implementation, chạy lại nhóm tests liên quan rồi hoàn thành checks theo diff cuối; không giữ số pass từ source cũ làm kết quả source mới.

## 10. Lệnh kiểm chứng dự kiến

Các lệnh dưới đây **chưa được chạy ở bước lập kế hoạch**. Tên test mới phải khớp file thực tế sau triển khai. Chạy từ repo root bằng interpreter đã cài backend dependencies.

### 10.1. Prerequisites

- Node/npm đúng `.nvmrc`, package engines và lockfile; `npm ci`.
- Python dependencies từ `requirements-dev.txt`, editable install; dùng `FRONTEND_E2E_PYTHON` nếu cần chỉ định interpreter.
- Playwright Chromium và system dependencies đã cài. CI tiếp tục dùng installer hiện có.
- MySQL 8.4.11 isolated có quyền tạo/dọn schema fixture; `MYSQL_TEST_URL` lấy từ môi trường, không ghi credentials thật vào plan/PR/output.
- Ports 8101/3101 sẵn sàng cho harness; API-mode build có upstream đúng. Không tái dùng build mock cho API E2E.
- Môi trường triển khai dùng runtime grants #50 đã giao; không thêm grant để chữa lỗi UI. Missing grant là integration prerequisite failure.

### 10.2. Targeted và frontend checks

```powershell
npm --workspace src/frontend run test -- tests/frontend/unit/admin-catalog-api.test.ts tests/frontend/unit/admin-configuration-api.test.ts tests/frontend/components/admin-purposes.test.tsx tests/frontend/components/admin-booking-configuration.test.tsx
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test
```

Thêm targeted shell/session/recovery/evidence tests theo file thực tế. `format:check` frontend hiện không quét docs/plans; tài liệu Markdown mới phải được kiểm tra trực tiếp:

```powershell
npx prettier --check docs/plans/issue-52-implementation-plan.md docs/tasks/admin-consultation-configuration.md
```

### 10.3. Harness/backend foundation và API E2E

```powershell
$env:PYTHONPATH = 'src/backend'
python -m black --check tools/scripts/frontend_e2e_backend.py tests/backend/unit/test_frontend_admin_probe.py
python -m ruff check tools/scripts/frontend_e2e_backend.py tests/backend/unit/test_frontend_admin_probe.py
python -m pyright
python -m pyright tools/scripts/frontend_e2e_backend.py tests/backend/unit/test_frontend_admin_probe.py
python -m pytest tests/backend/unit/test_frontend_admin_probe.py -q
python -m pytest tests/backend/integration/test_admin_booking_configuration.py tests/backend/integration/test_admin_policy_transactions.py tests/backend/integration/test_admin_policy_races.py tests/backend/integration/test_admin_account_endpoints.py -q --require-mysql

$env:NEXT_PUBLIC_APP_MODE = 'api'
$env:API_UPSTREAM_URL = 'http://127.0.0.1:8101'
npm --workspace src/frontend run build
npm --workspace src/frontend run test:e2e:api -- admin-catalog.spec.ts admin-booking-configuration.spec.ts
npm --workspace src/frontend run test:e2e:api
```

Đưa reset tests/validator tests mới vào commands tương ứng. Gate zero-skip phải chạy bắt buộc trong job API, và có cách chạy local cùng safe summary; chốt tên script/lệnh chính xác khi triển khai gate. Required suite fail nếu prerequisite thiếu, không xem skipped là passed.

### 10.4. Mock/student/staff regressions và contract stability

```powershell
$env:NEXT_PUBLIC_APP_MODE = 'mock'
npm --workspace src/frontend run build
npm --workspace src/frontend run test:e2e
python -m pytest tests/backend/unit/test_openapi_export.py -q
python tools/infra/generate_gateway_routes.py --check
git diff --exit-code -- docs/api/openapi.yaml tools/deploy/nginx/route-map.conf tools/deploy/mysql/grants.sql
git diff --check
```

Issue UI dự kiến không đổi OpenAPI/gateway/grants; nếu phát hiện contract defect, tách rõ quyết định backend và review phạm vi trước khi dùng contract mới. Không sửa generated artifact để làm UI request sai trở thành hợp lệ.

## 11. Truy vết yêu cầu #52 và điều kiện hoàn thành

| Yêu cầu issue                                                              | Phần kế hoạch  | Bằng chứng nghiệm thu                                                                  |
| -------------------------------------------------------------------------- | -------------- | -------------------------------------------------------------------------------------- |
| Extend shared admin layout/guard, ba nhóm trang                            | §1, §4–5       | U12/U15/U16, E01/E16; không regression accounts.                                       |
| Typed services, immutable code, referenced deactivation                    | §3–4, §5.1     | U01–U04, E02–E04.                                                                      |
| Duration/step/horizon/timezone/windows, cross-field, closed day/no overlap | §3.2, §5.2–5.3 | U04–U07, E05–E07/E15.                                                                  |
| Version preconditions và stale review/reload                               | §3.3–3.4, §6   | U03/U10–U13, E09–E13.                                                                  |
| Future availability và giữ confirmed appointment                           | §5.4, §8       | E04–E08, backend policy race regression.                                               |
| Identity/loading/error/empty/CSRF/unknown, keyboard/mobile                 | §4–7           | U08/U09/U13–U17, E01/E12–E16.                                                          |
| Real API/MySQL persistence, denial, conflicting edits                      | §7–8           | E01–E15 trên cả desktop/mobile, không skip.                                            |
| Relevant checks và portal regressions                                      | §8–10          | Full frontend suites/build và backend checks bị ảnh hưởng trên final source.           |
| Reviewed PR/dependency integration/evidence                                | §2, §9–11      | Source/config/artifact identity, actual commands/results; review và merge vào develop. |

Checklist trước khi đóng issue:

- [ ] Đúng baseline develop và reviewed dependencies; routes/services/state được integrate với backend #50 và shell #51 thật.
- [ ] Ba trang mới dùng đúng HTTP contract, giữ raw revision và atomic full configuration; code không sửa/xóa purpose đã có.
- [ ] Loading/empty/error/forbidden/pending/conflict/unknown đầy đủ; không auto replay/rebase hoặc xác nhận trước receipt/reconciliation.
- [ ] Draft/confirmation và stale continuations tuân owner/session lifecycle; hai trang cấu hình không làm mất dữ liệu của nhau.
- [ ] Cấu hình thay đổi future availability đúng, confirmed appointment instants/duration giữ nguyên với proof DB/API/UI.
- [ ] Validation, deactivation, concurrent edits, no-op/replay, invalid CSRF và unknown post-commit được chứng minh bằng tests phù hợp.
- [ ] Keyboard/desktop/mobile đạt; full student/staff/admin regressions và required checks pass, zero skipped/global errors.
- [ ] Harness reset/probe/evidence gate được kiểm chứng; fixtures chỉ trong schema owned và public artifacts không có payload nhạy cảm.
- [ ] PR ghi final source SHA, build mode, public config/DB/runtime identity, artifact identity nếu có, commands và pass/fail/skipped counts cùng CI links.
- [ ] Ghi rõ đây là automated lab proof trên dữ liệu fixture, không phải actual deployment measurement. Không lấy CI của dependencies hoặc head cũ làm kết quả của #52.
- [ ] Runbook/verification cập nhật sau khi chạy thật; PR được review và merge vào `develop`; acceptance criteria đạt trước khi đóng #52.

Các lựa chọn sản phẩm trong plan (route names, shared draft và review full diff) có thể được điều chỉnh khi review kế hoạch, nhưng HTTP/policy/session/replay contracts ở §3 và §6 phải được giữ. Không có dependency API đang thiếu chặn bắt đầu triển khai tại baseline đã kiểm tra.
