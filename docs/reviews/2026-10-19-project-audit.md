# Rà soát toàn dự án — 19/10/2026

Commit được rà soát: `f5fed91c1be2fdba916e514ffadb0cb115e3522e`.

Phạm vi gồm mã backend/frontend, hợp đồng HTTP/OpenAPI, mô hình và migration MySQL, xác thực/phân quyền, transaction và cạnh tranh, trạng thái UI, khả năng truy cập, dependencies, kiểm thử, CI, tài liệu và thành phần vận hành đang có trong repository. Không sửa mã ứng dụng hoặc dữ liệu dùng chung. Các ca chẩn đoán nằm trong `build/audit/`; MySQL kiểm thử chạy trong container tạm, không gắn volume của dự án.

Đánh giá: các luồng đã triển khai có nền tảng transaction, idempotency, CSRF và kiểm thử tương đối chặt chẽ. Có bảy vấn đề cụ thể dưới đây và một nhóm rủi ro dependency cần xử lý. Chưa có bằng chứng về lỗi P0/P1 trong các luồng đã triển khai. Repository hiện chưa cung cấp đủ thành phần phát hành, giám sát và phục hồi để xác nhận sẵn sàng production.

P2: nên xử lý trong đợt phát triển tiếp theo, trước khi mở rộng sử dụng thực tế. P3: mức độ ảnh hưởng thấp hơn, vẫn cần đưa vào backlog. Mức độ của advisory thư viện được giữ riêng với mức độ ảnh hưởng đã chứng minh trên ứng dụng.

## Phát hiện

### F1 — P2: JSON của API nghiệp vụ chưa có giới hạn kích thước trước khi parse

Vị trí: [catalog_http.py:38](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/core/catalog_http.py:38), đối chiếu [auth_http.py:41](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/core/auth_http.py:41).

Middleware nghiệp vụ truyền nguyên `receive` xuống FastAPI. Chỉ endpoint login có bộ đếm byte trước khi parse. FastAPI đọc/parse JSON trước khi chạy dependency xác thực; giới hạn độ dài trường trong DTO được áp dụng sau đó. Deadline asyncio không giới hạn được bộ nhớ đã cấp hoặc ngắt phần parse JSON đồng bộ.

Đã tái hiện với request chưa đăng nhập gửi JSON **262.163 byte**: `/api/v1/appointments` gọi `Request.json()` với toàn bộ body rồi mới trả 401. Cùng body gửi đến login trả 413 trước khi gọi JSON parser. Đây là bằng chứng thiếu giới hạn body; không thực hiện bài gây cạn bộ nhớ.

Ảnh hưởng: nếu không có giới hạn ở upstream, client chưa xác thực có thể tiêu thụ bộ nhớ và CPU bằng body lớn. Repository hiện chưa có cấu hình gateway để bảo đảm giới hạn đó.

Hướng xử lý: áp dụng giới hạn byte ở lớp ASGI cho mọi request ghi có body, đếm dữ liệu streaming/chunked thay vì chỉ tin `Content-Length`; trả 413 và giữ hợp đồng lỗi/cache/request ID. Bổ sung giới hạn tại gateway khi triển khai. Kiểm thử cả trường hợp chưa có session, chunked và header độ dài giả.

### F2 — P2: API cấu hình đặt lịch hỏng làm mất cả hai đường xác minh kết quả đã commit

Vị trí: [api-booking-flow.tsx:29](C:/WorkSpace/Production-Readiness-Lab/src/frontend/features/api/api-booking-flow.tsx:29), [api-appointments.tsx:68](C:/WorkSpace/Production-Readiness-Lab/src/frontend/features/api/api-appointments.tsx:68), cùng mẫu ở dòng 188.

`ApiBookingFlow` chỉ render wizard chứa phần phục hồi intent sau khi `booking-context` tải thành công. Trang lịch sử và chi tiết lại gộp request lịch hẹn với context bằng `Promise.all`.

Tình huống: POST đã commit nhưng receipt bị mất; sau đó riêng `/booking-context` trả 503. Nút gửi lại cùng key không xuất hiện. Đi theo hướng dẫn kiểm tra lịch sử cũng không xem được lịch, mặc dù API đọc lịch đang trả 200. Backend có thể replay receipt trước khi đọc booking policy tại [booking.py:312](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/services/booking.py:312); các GET của sinh viên cũng không cần cấu hình cửa sổ đặt.

Ba ca component chẩn đoán đã xác nhận: history và detail che dữ liệu đọc thành công khi context lỗi; intent `unknown` vẫn còn nhưng nút replay bị che.

Hướng xử lý: hiển thị phần phục hồi intent độc lập với booking context. Tách cấu hình cửa sổ đặt khỏi việc đọc lịch đã lưu; cung cấp timezone cần thiết qua hợp đồng đọc lịch hoặc nguồn cấu hình độc lập. Không tự đoán timezone khi nguồn cấu hình lỗi.

### F3 — P2: Nhấn “Thử lại” sau lỗi giờ trống làm mất draft

Vị trí: [api-booking-fields.tsx:210](C:/WorkSpace/Production-Readiness-Lab/src/frontend/features/api/api-booking-fields.tsx:210), liên quan [api-booking-flow.tsx:29](C:/WorkSpace/Production-Readiness-Lab/src/frontend/features/api/api-booking-flow.tsx:29).

Retry availability gọi cả `retry()` lẫn `onContextRefresh()`. Context chuyển sang loading khiến parent tháo `ApiWizard`; reducer lưu bước, nhu cầu, cố vấn, ngày và mô tả nằm trong wizard nên bị mất. Context trả lại cùng dữ liệu cũng không cứu được draft đã bị tháo.

Đã tái hiện: chọn nhu cầu yêu cầu mô tả, nhập nội dung, chọn cố vấn/ngày; availability trả 503; nhấn “Thử lại”. Giao diện về bước đầu, radio nhu cầu bỏ chọn, phần mô tả đã nhập biến mất.

Hướng xử lý: retry lỗi mạng/503 ngay ở resource availability; giữ wizard/draft trong lúc refresh context. Nếu cửa sổ hoặc danh mục thực sự đổi, chỉ vô hiệu các lựa chọn không còn hợp lệ và giải thích thay đổi cho người dùng.

### F4 — P2: Số trang hợp lệ về schema có thể gây HTTP 500

Vị trí: [appointments.py:77](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/repositories/appointments.py:77), [common.py:20](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/schemas/common.py:20).

`PaginationParams.page` không có giới hạn trên. Repository lịch sử sinh viên luôn đưa `(page - 1) * page_size` vào SQL OFFSET, trong khi repository staff/catalog đã trả trang rỗng khi offset vượt total.

Đã tái hiện trên MySQL 8.4.11 với session sinh viên hợp lệ:

```text
GET /api/v1/appointments?page=99&page_size=100
→ 200, trang rỗng

GET /api/v1/appointments?page=1000000000000000000&page_size=100
→ 500, INTERNAL_SERVER_ERROR
```

OFFSET thứ hai là `99999999999999999900`, vượt miền số nguyên MySQL/driver hỗ trợ. Không cần có nhiều dữ liệu để gây lỗi.

Hướng xử lý: dùng cùng kiểm tra `offset >= total` như repository staff; nếu chọn giới hạn page ở schema thì công bố giới hạn trong OpenAPI. Bổ sung kiểm thử trang rỗng với số nguyên rất lớn.

### F5 — P2: Hướng dẫn schema revision đã cũ

Vị trí: [README.md:230](C:/WorkSpace/Production-Readiness-Lab/README.md:230), [fastapi-bootstrap.md:46](C:/WorkSpace/Production-Readiness-Lab/docs/tasks/fastapi-bootstrap.md:46).

Tài liệu yêu cầu `ACCEPTED_SCHEMA_REVISIONS=20261016_1200_audit_reqid128` và nói giá trị đó theo `.env.example`. Trong thực tế, [`.env.example:30`](C:/WorkSpace/Production-Readiness-Lab/.env.example:30) và `alembic heads` đều dùng `20261017_2350_student_profile`.

Nếu migration lên head rồi làm theo giá trị cũ, readiness trả 503. Nếu giữ database ở revision cũ và chấp nhận revision cũ thì readiness chỉ kiểm tra tên revision nên có thể thành công, trong khi ORM hiện tại cần các cột hồ sơ mới. Đây là hướng dẫn vận hành không nhất quán, không phải lỗi của `.env.example` hiện tại.

Hướng xử lý: đồng bộ tài liệu với release/schema hiện tại, mô tả rõ danh sách tương thích; kiểm tra tự động các revision được nêu như cấu hình hiện hành trong tài liệu triển khai. Giữ tài liệu migration ban đầu được đánh dấu là tài liệu lịch sử.

### F6 — P3: Throttle đăng nhập tiết lộ email có tài khoản hay không

Vị trí: [auth.py:59](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/services/auth.py:59), [login_throttle.py:44](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/core/login_throttle.py:44).

Email tồn tại được tính throttle bằng email chuẩn từ DB; email không tồn tại được tính bằng chuỗi người dùng gửi. Collation MySQL của email bỏ qua U+200B, còn chuẩn hóa Python giữ ký tự này.

Đã tái hiện với ngưỡng email mặc định 5: gửi năm lần sai mật khẩu cho email ứng viên, rồi gửi lần thứ sáu chèn U+200B trước `@`. Nếu tài khoản tồn tại, alias trúng cùng bucket và trả 429. Nếu không tồn tại, alias có bucket mới và trả 401. Sáu lần vẫn nằm dưới hạn IP mặc định 30.

Ảnh hưởng là dò sự tồn tại của tài khoản; không chứng minh vượt xác thực hoặc đọc dữ liệu người khác.

Hướng xử lý: thống nhất chính sách định danh email và khóa throttle cho cả hai nhánh tồn tại/không tồn tại; giữ chống bypass bằng alias. Thêm ca đối xứng known/unknown thay vì chỉ kiểm tra alias của tài khoản thật.

### F7 — P3: Chữ hướng dẫn không đủ độ tương phản

Vị trí: [booking.module.css:301](C:/WorkSpace/Production-Readiness-Lab/src/frontend/features/booking/booking.module.css:301), [booking.module.css:534](C:/WorkSpace/Production-Readiness-Lab/src/frontend/features/booking/booking.module.css:534).

Chữ mô tả 10px `#8b99a6` trên nền trắng có tương phản **2,915:1**. `.requiredNote` 10px `#97a5b1` trên nền trắng đạt **2,520:1**. Các nội dung này gồm hướng dẫn và giới hạn đặt lịch, cần đọc được. Chúng thấp hơn ngưỡng 4,5:1 của chữ thường theo [WCAG 2.2, SC 1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

Hướng xử lý: tăng độ đậm của màu chữ hỗ trợ và xem lại cỡ chữ; đo lại trên các nền thường/selected/hover. Đây là phép đo từ CSS hiện tại, không phải kết quả kiểm định toàn bộ khả năng truy cập bằng công cụ đọc màn hình.

### F8 — P2: Các phiên bản dependency đã khóa có advisory bảo mật

Bằng chứng quét lưu ở [npm-audit.json](C:/WorkSpace/Production-Readiness-Lab/build/audit/npm-audit.json) và [python-advisories.json](C:/WorkSpace/Production-Readiness-Lab/build/audit/python-advisories.json).

`npm audit` trả exit 1: bốn mục mức high là `braces`, `micromatch`, `fast-glob`, `@next/eslint-plugin-next`. Chúng nối về cùng advisory [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) của `braces@3.0.3`, không phải bốn lỗ hổng độc lập. Chuỗi nằm trong công cụ ESLint dùng khi phát triển/CI; chưa có bằng chứng đường vào từ request production. Advisory hiện không công bố bản braces đã vá. Không áp dụng mù đề xuất downgrade Next ESLint plugin từ npm audit.

Đã đọc metadata advisory chính thức của PyPI cho **44 package được khóa** trong hai requirements, không có query lỗi. Có năm package được đánh dấu:

| Package | Phiên bản | Phạm vi và điều kiện cần xem xét |
|---|---|---|
| `asyncmy` | 0.2.10 | [CVE-2025-65896](https://github.com/advisories/GHSA-qhqw-rrw9-25rm): dict key được chế tạo. Chưa xác nhận input của API đi vào đường này; SQL hiện dùng SQLAlchemy binding. |
| `cryptography` | 46.0.3 | Nhiều advisory về SECT, X.509, buffer và OpenSSL trong wheel. Ví dụ [advisory SECT của maintainer](https://github.com/pyca/cryptography/security/advisories/GHSA-r6ph-v2qm-q3c2). Chưa chứng minh đường khai thác qua luồng hiện có. |
| `Mako` | 1.3.10 | [Traversal trên Windows](https://github.com/sqlalchemy/mako/security/advisories/GHSA-2h4p-vjrc-8xpq) với template URI không tin cậy; dự án hiện dùng cho migration. |
| `black` | 25.1.0 | Công cụ phát triển; điều kiện liên quan tùy chọn cache/Action. CI hiện gọi CLI, không dùng Black Action `use_pyproject`. |
| `pytest` | 8.4.2 | Công cụ kiểm thử; advisory liên quan thư mục tạm trên máy UNIX có người dùng cục bộ không tin cậy. |

Các bản ghi GHSA/PYSEC trùng alias được xem là cùng một vấn đề. Bản quét PyPI này đối chiếu đúng các pin đang khai báo, không thay thế kiểm kê SBOM toàn image hoặc kiểm tra dependency ẩn ngoài requirements.

Hướng xử lý: triage khả năng đi tới mã bị ảnh hưởng, cập nhật/thay thế bản phù hợp, chạy lại ma trận kiểm thử và lưu quyết định cho advisory chưa có bản vá. Thêm quét npm/Python vào CI; hai workflow hiện chưa chạy các bước này. Chưa thay đổi package hoặc lockfile trong lượt rà soát.

## Phần còn thiếu để vận hành production

Các mục dưới đây là khoảng trống so với thiết kế/lộ trình, không gắn nhãn regression cho chức năng chưa được triển khai.

| Mảng | Hiện trạng trong repository | Việc cần có trước khi nghiệm thu |
|---|---|---|
| Đóng gói/phát hành | `tools/infra` và `tools/deploy` chỉ có `.gitkeep`; CI kiểm tra nhưng chưa đóng gói/phát hành artifact. | Dockerfile/Compose/gateway, runtime không root, manifest digest/schema, serialize deploy/rollback và smoke qua gateway. |
| Quan sát/cảnh báo | Có request ID và health. Không thấy instrumentation metrics/traces, dashboard, exporter hoặc probe ngoài máy chủ. [factory.py:55](C:/WorkSpace/Production-Readiness-Lab/src/backend/app/factory.py:55) chỉ log loại exception. | Log an toàn có request ID/route/duration, SLI/SLO có dữ liệu, cảnh báo và bằng chứng phát hiện outage. Giữ việc lọc secret/nội dung tư vấn. |
| Backup/restore | Thiết kế đã nêu nhưng chưa có công cụ/lịch chạy và bằng chứng restore trong mã hiện tại. | Backup schema/dữ liệu/revision, restore vào DB riêng, đối chiếu ràng buộc và đo RPO/RTO. |
| Tải và sự cố | Có test cạnh tranh/timeout trong backend; chưa thấy bộ tải 20 phiên/10 phút hoặc công cụ diễn tập có TTL như thiết kế. | Đo độ trễ ở gateway với seed đủ lớn; diễn tập DB/worker/telemetry/host và rollback. Không suy ra SLO từ unit/integration tests. |
| Vòng đời phiên | Login tạo phiên, logout/revoke cập nhật trạng thái; chưa có tác vụ dọn phiên hết hạn/đã thu hồi. | Chính sách lưu giữ và cleanup theo batch có giới hạn; theo dõi dung lượng/index và không xóa audit nghiệp vụ tùy tiện. |
| Phạm vi nghiệp vụ | Backend có đặt/hủy/đọc lịch và quản lý lịch được giao/ghi outcome cho staff; frontend hiện là cổng sinh viên. Chưa thấy API/UI quản trị đầy đủ, đề nghị đổi lịch/phản hồi, worker hết hạn hoặc hộp thông báo. | Lập ma trận nghiệm thu theo tính năng; triển khai phần thiếu trước khi tuyên bố hoàn thành MVP được mô tả trong README/thiết kế. |

## Kiểm chứng

Kết quả cuối đã đọc từ output và artifact của lượt chạy này:

- Backend: **716/716 đạt, 0 lỗi, 0 skip**; gồm 433 ca integration và 283 ca unit. Thời gian 17 phút 18 giây. Bằng chứng: [JUnit XML](C:/WorkSpace/Production-Readiness-Lab/build/audit-backend-mysql.xml).
- Frontend unit/component hiện hữu: **145/145 đạt**. Chạy kèm bốn ca chẩn đoán: **149/149 đạt**; bốn ca mới kiểm tra hành vi lỗi hiện có, không phải test chứng minh đã sửa.
- Backend probes trên MySQL 8.4.11: **3/3 tái hiện đạt**, log ghi body 262.163 byte, phân trang 500 và known/unknown alias 429/401.
- E2E API thật: **32/32 đạt** trên desktop/mobile với MySQL 8.4.11, gồm mất response sau commit, retry cùng key, account switch, ownership, hủy và reload.
- E2E mock: **66/66 đạt** trên desktop/mobile, chạy theo cấu hình CI, gồm bàn phím, trạng thái lỗi/rỗng và timezone.
- Build production chế độ API đạt. Lần đầu E2E gặp artifact thiếu `preview-props.json`; tái tạo build trong cùng môi trường chạy E2E giải quyết được lỗi, sau đó toàn bộ 32 ca đạt. Không quy kết lỗi artifact ban đầu thành lỗi mã dự án.
- Black: 138 file đạt; Ruff và ESLint đạt.
- Pyright: 0 lỗi, 0 cảnh báo. Frontend typecheck và Prettier đạt.
- OpenAPI xuất lại trùng byte với artifact đã commit; Alembic có đúng một head hiện tại.
- `pip check`: không có dependency bị thiếu/xung đột. Điều này không đồng nghĩa không có advisory bảo mật.
- Kiểm tra mẫu khóa riêng/token thông dụng trên cây làm việc không tìm thấy kết quả; file môi trường được track chỉ là hai `.env.example`. Đây không phải kiểm định toàn bộ lịch sử Git hoặc secret ngoài repository.

Bộ backend phát 286 warning, chủ yếu thiếu `path_separator` trong Alembic config và API TestClient dùng HTTPX đã được thông báo deprecate. Chưa gây thất bại hiện tại; cần xử lý trước đợt nâng dependency liên quan. Artifact frontend cuối cùng đã được build lại về chế độ API, upstream local mặc định `http://127.0.0.1:8000`.

Ca chẩn đoán và bằng chứng chi tiết: [backend-probes.log](C:/WorkSpace/Production-Readiness-Lab/build/audit/backend-probes.log), [frontend-probes.json](C:/WorkSpace/Production-Readiness-Lab/build/audit/frontend-probes.json), [api-e2e.log](C:/WorkSpace/Production-Readiness-Lab/build/audit/api-e2e.log), [mock-e2e.log](C:/WorkSpace/Production-Readiness-Lab/build/audit/mock-e2e.log). Container MySQL tạm đã dừng và tự xóa sau kiểm thử.

Giới hạn: chưa đo bài tải/SLO, chưa kiểm chứng restore/rollback trên môi trường demo, không kiểm định hạ tầng hoặc secret ngoài repository; kiểm tra khả năng truy cập mới tập trung mã nguồn, tương phản và kiểm thử UI sẵn có. Runtime kiểm chứng local là Python 3.12.2, Node 24.17.0, npm 11.13.0; CI khai báo Python 3.11 và npm 12.2.0 nên vẫn cần CI xác nhận trên ma trận đó.

Thứ tự khuyến nghị: giới hạn body và tách phục hồi khỏi context; sửa mất draft và OFFSET; đồng bộ revision tài liệu; triage dependencies; sửa oracle throttle và tương phản; hoàn thiện các hạng mục vận hành theo tiêu chí phát hành.

## Remediation status on bugfix/audit-findings-remediation (2026-10-20)

Findings F1-F8 in this report were addressed on a branch created from `develop` at `f5fed91c1be2fdba916e514ffadb0cb115e3522e`:

| Finding | Change and regression coverage | Commit |
|---|---|---|
| F1 | Bound JSON request bodies before parsing; cover chunked/oversized bodies, disconnects, and auth endpoints. | `d6c8641` |
| F2 | Decouple saved appointment reads from booking context; show explicit UTC fallback and timezone retry; cover in API E2E. | `47516b5` |
| F3 | Preserve booking intent and draft through context/slot retries, 422, unknown results, and account switches; cover in component/API E2E. | `deaa252` |
| F4 | Short-circuit student-history queries beyond the result count; cover repository and MySQL tests. | `75ddefc` |
| F5 | Align current schema revision guidance with the Alembic head; add a consistency test. | `2f8f734` |
| F6 | Derive email throttle identity using MySQL collation; cover known/unknown accounts and U+200B aliases. | `6fc7c17` |
| F7 | Raise helper text to 14px, improve contrast, and allow notes to wrap; verify contrast and browser layouts at 320px, Pixel 7, desktop, 200% reflow, and keyboard focus. | `e7d76b6` |
| F8 | Upgrade vulnerable pinned Python dependencies; add npm/Python advisory gate with an exact, scoped, owned, expiring `braces==3.0.3` exception. Checker tests validate malformed/inconsistent reports and the dependency graph. | `d7f8a48`, `e59a8ca` |

### Branch acceptance

- Backend: **801/801 passed, 0 skipped** on MySQL 8.4.11; JUnit summary is in `build/backend-tests-final.xml` in the worktree.
- Frontend unit/component: **170/170 passed**; mock desktop/mobile E2E **66/66**; API desktop/mobile E2E **42/42**.
- Mock and API production builds passed. Frontend format/lint/typecheck, backend Ruff/Pyright/Black, and workflow Prettier/YAML checks passed.
- OpenAPI export matches the committed artifact; Alembic has one head, `20261017_2350_student_profile`; no migration was added. `pip check` is clean, and `pip-audit 2.10.1` found no known advisories after the pin updates.
- Advisory checker: **19/19 tests passed**. The saved npm audit report from the initial audit, checked against the current lockfile, is approved with exactly one finding: braces 3.0.3, development, GHSA-vfj7-8cjw-p6xm; the exception expires `2026-11-18T23:59:59+07:00`.
- A fresh live `npm audit` was not run during final acceptance. Automatic review rejected registry egress because it could transmit private project dependency metadata; the saved report validates parser/policy behavior, and CI will collect a fresh scan.

All commits are local on the branch; nothing has been pushed or merged.
