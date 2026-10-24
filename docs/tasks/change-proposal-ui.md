# Giao diện đề xuất đổi lịch — issue #32

Nhánh triển khai: `feature/change-proposal-ui`, từ `develop` tại
`f683310da6c6be86045444c20beb418d5c2c607f`. Các phụ thuộc #25/PR #29,
#27/PR #37 và #26/PR #38 đã được merge vào base. Giữ issue mở đến khi PR
được review, merge vào `develop` và nghiệm thu.

## Luồng sử dụng

- Staff mở `/staff/appointments/[id]`, chọn **Đề xuất đổi lịch** hoặc
  **Thay thế đề xuất**, nhập ngày/giờ và cách xử lý địa điểm, xem lại hai
  phương án rồi xác nhận gửi. Không áp dụng cửa sổ đặt lịch của sinh viên
  làm giới hạn cho biểu mẫu staff; backend kiểm tra chính sách thực tế.
- Sinh viên mở `/appointments/[id]` (cả deep link từ thông báo), đối chiếu
  lịch gốc và đề xuất, hạn phản hồi, thời lượng đã đặt rồi xác nhận chấp nhận
  hoặc từ chối. Từ chối hủy toàn bộ cuộc hẹn. Trong khi chờ, lịch gốc vẫn giữ chỗ.
- Lịch sử phân trang hiển thị cả trạng thái `PENDING`, `ACCEPTED`, `DECLINED`,
  `EXPIRED`, `SUPERSEDED`, `CANCELLED`, thời điểm tạo/hạn phản hồi/xử lý.
- Chi tiết hai portal dịch lý do hủy do từ chối/hết hạn sang tiếng Việt; lý do
  do người dùng nhập được giữ nguyên.
- Refresh mỗi 10 giây khi trang đang hiển thị và khi cửa sổ lấy lại focus.
  Không hủy một lần đọc còn đang chạy chỉ vì đến nhịp polling tiếp theo.
  Thay đổi context sẽ tải lại chi tiết lịch, kể cả accept quay về `UPCOMING`.
  Chỉ backend/worker xác định trạng thái hết hạn; client không giả lập chuyển trạng thái.

Múi giờ staff lấy từ `/staff/me/context`; sinh viên dùng múi giờ của
`/booking-context`. Không mở thao tác đề xuất khi chưa lấy được múi giờ dịch vụ.
Chuyển đổi ngày/giờ không phụ thuộc timezone trình duyệt, từ chối giờ DST không
tồn tại hoặc mơ hồ. Khi ngày/giờ hiển thị không đổi, giữ nguyên timestamp gốc
(kể cả offset/microseconds) để gửi thay đổi chỉ về địa điểm.

## API và ánh xạ

| Dịch vụ frontend                                     | API                                                                    | Hợp đồng quan trọng                                                               |
| ---------------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `proposalHistory(id, page, options)` / STUDENT       | `GET /api/v1/appointments/{id}/change-proposals`                       | `page_size=20`; context vẫn có trên trang rỗng/trang sau.                         |
| `proposalHistory(id, page, options)` / STAFF         | `GET /api/v1/staff/me/appointments/{id}/change-proposals`              | Chỉ lịch được giao; xác minh account/role trước và sau đọc.                       |
| `createProposal(id, body, key, options)`             | `POST /api/v1/staff/me/appointments/{id}/change-proposals`             | Bốn trường bắt buộc và `Idempotency-Key`; response 200/201 là biên nhận bất biến. |
| `decideProposal(id, proposalId, 'accept', options)`  | `POST /api/v1/appointments/{id}/change-proposals/{proposalId}/accept`  | Body chính xác `{}`; retry cùng proposal/action.                                  |
| `decideProposal(id, proposalId, 'decline', options)` | `POST /api/v1/appointments/{id}/change-proposals/{proposalId}/decline` | Body chính xác `{}`; decline hủy lịch.                                            |

DTO response chuyển sang camelCase trong `services/api/proposal-mapping.ts`;
body tạo giữ đúng bốn khóa snake_case: `expected_proposal_version`,
`expected_pending_proposal_id`, `proposed_starts_at`, `proposed_location`.
Phiên, cookie, signed CSRF, account/role preflight, AbortSignal, timeout 15 giây,
`private/no-store` và thông báo lỗi/request ID dùng HTTP foundation hiện có.
POST không được middleware tự động phát lại.

Context được đọc từ `response.context`, không suy ra từ item đầu tiên. Khi
pending ID không ở trang đang xem, đọc trang đầu và tìm đúng ID; nếu cần,
tìm tiếp tối đa 50 trang. Context đổi giữa các lần đọc hoặc không tìm được ID
sẽ chặn thao tác và yêu cầu refresh. Không tự coi trang rỗng là không có pending.

**Địa điểm:** giữ nguyên gửi lại chính xác chuỗi/null trên lịch gốc; đổi gửi
chuỗi người dùng nhập, không trim/normalize; bỏ gửi `null`. Thời lượng và end
dùng duration đã lưu trên appointment, không lấy duration mới của purpose.
Biên nhận tạo không được áp dụng làm appointment/pending hiện tại; sau response
luôn đọc lại history và detail.

## Phục hồi và vòng đời phiên

`ProposalIntentStore` giữ mỗi thao tác theo `(account, role, appointment)` trong
`sessionStorage` của tab, trước khi phát POST. Staff lưu key ngẫu nhiên, nguyên
body và cả hai precondition; sinh viên lưu proposal ID/action. Không lưu
password, token, cookie hay nội dung tư vấn. Chuỗi địa điểm là một phần của body
phải giữ nguyên để replay. Đóng tab hoặc xóa browser storage sẽ làm mất khả năng
replay thao tác staff theo key; cần đối chiếu trạng thái/lịch sử trước ý định mới.

| Trạng thái lưu               | Giao diện và bước tiếp theo                                                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `ready` / `submitting`       | Lưu bền trước dispatch, chặn nhấn lặp và thao tác hủy/ghi kết quả cạnh tranh trong portal. Sau reload chuyển thành `unknown`.               |
| `unknown`                    | Hiển thị thao tác cũ và **Thử lại thao tác đã gửi**; giữ nguyên key/body/precondition hoặc proposal/action. Không sửa ý định đang phục hồi. |
| `confirmed`                  | Chỉ cần đọc lại history + detail. Nếu đọc lỗi hoặc reload giữa chừng, tiếp tục đọc; không áp dụng receipt như trạng thái hiện tại.          |
| `conflict`                   | `IDEMPOTENCY_CONFLICT`: dừng và đối chiếu lịch sử/hỗ trợ, không tự đổi key.                                                                 |
| Xác nhận xong cả hai lần đọc | Xóa thao tác đã giải quyết; ý định mới cần người dùng mở biểu mẫu/xác nhận lại và key mới.                                                  |

Đăng xuất, hết phiên, đổi account/role và tháo provider hủy request, vô hiệu callback
cũ, ẩn dữ liệu của account trước; giữ bản phục hồi để chỉ cùng account/role lấy lại
sau đăng nhập. Mỗi tab có ý định riêng, không ghi đè request của tab khác.
Request đọc cũ bị abort/loại khi đổi trang lịch sử, route hoặc account.

Storage bị chặn/hết quota hoặc bản lưu sai schema sẽ chặn POST mới. Giao diện
cho đọc trạng thái và đối chiếu lịch sử trước khi xóa bản phục hồi hỏng bằng
xác nhận riêng; không gửi tự động yêu cầu thay thế cho bản đã mất key.

### Lỗi nghiệp vụ

- `PROPOSAL_PRECONDITION_FAILED`: refresh trạng thái; người dùng xác nhận một
  ý định mới. Không tự refresh precondition rồi replay request cũ.
- `PROPOSAL_STATE_CONFLICT`, `APPOINTMENT_STATE_CONFLICT`, `PROPOSAL_EXPIRED`:
  đọc trạng thái thực tế; không chuyển quyết định sang proposal khác.
- `STAFF_UNAVAILABLE`, `STUDENT_UNAVAILABLE`, `BOOKING_DATE_INVALID`,
  `PROPOSAL_NO_CHANGE`: giải thích theo code thực, refresh và sửa ý định.
- `NOT_FOUND` dùng chung cho ngoài quyền, tham chiếu không tồn tại, participant/
  purpose/acceptance không còn đủ điều kiện; không suy đoán hay tiết lộ nguyên nhân riêng.
- Hết phiên/CSRF/404/422 ở lần retry không chứng minh request cũ rollback.
  Một thao tác đã `unknown` chỉ được giải quyết bởi response thành công hoặc
  lỗi nghiệp vụ có thứ tự kiểm tra replay trước lỗi (`ProposalRejection`).
- Timeout, mất kết nối, 5xx và response 2xx sai DTO vẫn là kết quả chưa rõ.

## Kiểm thử và tái hiện

Yêu cầu Node 24.15+ và npm phù hợp lockfile, backend editable/dependencies đã
cài, Chromium Playwright và MySQL **8.4** riêng cho test. `MYSQL_TEST_URL` phải
có quyền tạo schema; harness tạo tên ngẫu nhiên, migrate bằng Alembic và chỉ
dọn schema do chính harness sở hữu. Không phụ thuộc demo seed #8.

```sh
npm ci
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test
NEXT_PUBLIC_APP_MODE=api API_UPSTREAM_URL=http://127.0.0.1:8101 npm --workspace src/frontend run build
MYSQL_TEST_URL='<local isolated MySQL URL>' NEXT_PUBLIC_APP_MODE=api API_UPSTREAM_URL=http://127.0.0.1:8101 npm --workspace src/frontend run test:e2e:api
python -m black --check tools/scripts/frontend_e2e_backend.py
python -m ruff check tools/scripts/frontend_e2e_backend.py
```

Trên Windows dùng biến môi trường PowerShell; nếu npm không ở PATH, gọi các CLI
đã cài bằng Node (`prettier`, `tools/scripts/lint-frontend.mjs`, `next typegen`,
`typescript/bin/tsc`, `vitest/vitest.mjs`, `@playwright/test/cli.js`). Không thay đổi
lockfile/dependencies để khắc phục PATH. Missing MySQL/browser không được skip.

Các test riêng bao phủ mapping/context, body và location chính xác, retry/lost
response, session/account, storage lỗi/tampering, timezone/DST, conflict và
keyboard focus. E2E dùng Next.js → proxy cùng origin → FastAPI → MySQL thật;
probe mất response chỉ cắt phản hồi **sau** khi backend đã xử lý và commit.
Không intercept business response trong trình duyệt.

Harness bổ sung fixture appointment có duration 30 phút trong khi purpose hiện
tại 60 phút; kiểm tra counts proposal/audit/notifications, hai browser có context
cũ, worker process mới xử lý backlog đã lưu và trang mở tự cập nhật trạng thái.

## Bằng chứng nghiệm thu

Môi trường local: Windows, Node 24.22.1, Next.js **16.4.1** đúng lockfile,
Python 3.12.14, MySQL 8.4.11, Playwright Chromium. Browser E2E chạy timezone
`America/Los_Angeles` để kiểm tra giờ nghiệp vụ Việt Nam độc lập browser.
Dependencies cục bộ đã được đối chiếu với manifest/lockfile; không thay đổi pins.

| Kiểm tra                         | Kết quả local                                                          |
| -------------------------------- | ---------------------------------------------------------------------- |
| Frontend format                  | Pass toàn bộ phạm vi của script `format:check`.                        |
| Lint                             | Pass với ESLint/Next plugin đúng lockfile.                             |
| Typecheck + production API build | Pass với Next.js 16.4.1.                                               |
| Unit/component                   | **343 passed, 0 failed** / 34 files; `--maxWorkers=4`.                 |
| Bộ mới cho #32                   | 37 service tests, 31 recovery/time tests, 17 component tests.          |
| Harness Python                   | Black và Ruff pass.                                                    |
| npm audit toàn lockfile          | **0 vulnerabilities**.                                                 |
| Real API desktop/mobile E2E      | **74 passed, 0 failed, 0 skipped** (13,5 phút); gồm 14 ca mới cho #32. |

Lượt review source độc lập và audit acceptance không còn finding cần sửa sau
các điều chỉnh. Kết quả local không thay GitHub CI hoặc approval của maintainer.

Ảnh dùng hoàn toàn fixture giả, không chứa password, cookie, token hay nội dung
tư vấn. Bộ ảnh được commit cùng source để reviewer mở được từ PR; báo cáo đầy đủ
của Playwright tiếp tục là CI artifact. Bộ 14 ca mới cũng được chạy lại trên
production build cuối. Issue vẫn chờ review/merge/nghiệm thu; reviewer đối chiếu
CI trên đúng SHA head của PR.

| Trạng thái                                                      | Desktop                                                       | Mobile                                                       |
| --------------------------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------ |
| Staff: đã thay đề xuất, lịch gốc còn giữ chỗ                    | [Ảnh](images/change-proposal-ui/staff-desktop.png)            | [Ảnh](images/change-proposal-ui/staff-mobile.png)            |
| Sinh viên: đối chiếu lịch gốc và đề xuất đang chờ               | [Ảnh](images/change-proposal-ui/student-pending-desktop.png)  | [Ảnh](images/change-proposal-ui/student-pending-mobile.png)  |
| Sinh viên: đã chấp nhận, giờ và địa điểm đã cập nhật            | [Ảnh](images/change-proposal-ui/student-accepted-desktop.png) | [Ảnh](images/change-proposal-ui/student-accepted-mobile.png) |
| Worker: proposal hết hạn, appointment đã hủy trên trang đang mở | [Ảnh](images/change-proposal-ui/worker-expired-desktop.png)   | [Ảnh](images/change-proposal-ui/worker-expired-mobile.png)   |
