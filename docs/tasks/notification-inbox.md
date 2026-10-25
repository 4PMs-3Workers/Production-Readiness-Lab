# Hộp thư thông báo cá nhân

Tài liệu này mô tả phần đọc của notification đã được tạo cùng transaction đặt/hủy lịch,
ghi kết quả buổi hẹn và chuyển trạng thái đề xuất. Hộp thư không tạo lại notification,
không thay đổi lịch sử và không cho ADMIN quyền đọc tư vấn.

## API

- `GET /api/v1/notifications?page=1&page_size=20&unread_only=false`
- `POST /api/v1/notifications/{notification_id}/read` với body JSON rỗng `{}`.

Danh sách trả `items`, `page`, `page_size` và `total` theo hợp đồng phân trang dùng chung.
Mục thông báo trả `id`, `event_type`, `summary`, `created_at`, `read_at` và dữ liệu lịch tùy
chọn (`appointment_id`, `appointment_starts_at`, `appointment_status`). Danh sách sắp theo
`created_at DESC, id DESC`; `page_size` từ 1 đến 100. `unread_only=true` chỉ lấy mục chưa đọc.
POST trả `{id, read_at}` với thời điểm đã lưu. Gọi lại giữ nguyên `read_at`.

Giao diện gửi cả `X-Expected-User-ID` và `X-Expected-Role` khi đọc danh sách hoặc đánh dấu
đã đọc. API so sánh cặp này với session xác thực **trong chính request đó** trước khi truy
vấn hoặc ghi notification. Nếu khác, API trả `409 ACCOUNT_CHANGED` để client xóa trạng thái
riêng tư; cặp thiếu hoặc sai định dạng trả `422 VALIDATION_ERROR`. API client không gửi cả
hai header vẫn đọc theo session của chính mình. Kiểm tra `/auth/me` trước/sau GET tiếp tục
phát hiện đổi cookie sau request, nhưng không được dùng để chứng minh chủ sở hữu của GET.

`recipient_id` luôn lấy từ session. Chỉ STUDENT và STAFF được đọc hộp thư; STUDENT chỉ nhận
liên kết lịch thuộc về mình và STAFF chỉ nhận liên kết lịch được giao. ADMIN không có quyền
ngầm định. ID không tồn tại và ID của người khác trả cùng `404 NOT_FOUND`. POST cần session,
same-origin và signed CSRF. Response API có `Cache-Control: private, no-store`, kể cả lỗi.

Backend chỉ dựng summary từ event type đã biết:

| Event | Summary |
|---|---|
| `APPOINTMENT_CREATED` | Lịch hẹn đã được tạo |
| `APPOINTMENT_CANCELLED` | Lịch hẹn đã bị hủy |
| `APPOINTMENT_OUTCOME_RECORDED` | Kết quả lịch hẹn đã được cập nhật |
| `CHANGE_PROPOSAL_CREATED` | Có đề xuất thay đổi lịch hẹn |
| `CHANGE_PROPOSAL_REPLACED` | Đề xuất thay đổi lịch hẹn đã được cập nhật |
| `CHANGE_PROPOSAL_ACCEPTED` | Đề xuất thay đổi lịch hẹn đã được chấp nhận |
| `CHANGE_PROPOSAL_DECLINED` | Đề xuất thay đổi lịch hẹn đã bị từ chối |
| `CHANGE_PROPOSAL_EXPIRED` | Đề xuất thay đổi lịch hẹn đã hết hạn |

Event type lạ hoặc lịch không còn được người nhận phép xem sẽ hiển thị summary chung và không
có dữ liệu/link lịch. Consultation text, event payload nội bộ, replay key/hash và dữ liệu SQL
không thuộc DTO.

## Trạng thái đã đọc

Repository cập nhật có điều kiện khi `read_at IS NULL`, cùng phạm vi notification ID và người
nhận. Service sở hữu transaction; request cạnh tranh thứ hai đọc lại timestamp đã commit. Khi
kết quả commit chưa rõ, service mở transaction đọc mới để tìm trạng thái đã lưu; chỉ thử lại
một lần nếu bản ghi vẫn chưa được đánh dấu. Thao tác không ghi audit hoặc notification mới,
không đổi trạng thái lịch hay đề xuất.

## Giao diện

- Sinh viên mở `/notifications`; nhân viên mở `/staff/notifications`.
- Cả hai dùng role shell/session hiện có, trang phân trang, lọc chưa đọc, làm mới thủ công,
  hiển thị loading/empty/error, trạng thái đọc và link do backend cấp quyền.
- Lịch hiển thị theo timezone nghiệp vụ nhận từ booking/staff context. Nếu không tải được
  context, giao diện giữ timezone mặc định `Asia/Ho_Chi_Minh`.
- Request list bị hủy khi thay đổi trang/bộ lọc hoặc unmount. Logout, hết hạn session và đổi
  tài khoản/role dùng cơ chế clear-session hiện có để hủy request và bỏ private UI cũ. Trước và
  sau khi tải danh sách, client đối chiếu session hiện tại với ID/role đang hiển thị; thao tác
  đánh dấu đã đọc cũng kiểm tra cùng danh tính trước khi ghi. Vì vậy đổi cookie từ tab khác
  khiến provider xóa trạng thái riêng tư thay vì nhận dữ liệu của tài khoản mới vào shell cũ.
  Nếu cookie đổi A → B → A trong lúc tải, backend từ chối ngay request xác thực bằng B nhờ
  cặp header kỳ vọng A; hai lần kiểm tra `/auth/me` nhìn thấy A không thể chấp nhận dữ liệu B.
- Khi đánh dấu đã đọc thành công, client tải lại danh sách ở scope hiện tại, kể cả khi một lần
  làm mới thủ công đang chạy. Nếu tổng mục chưa đọc giảm khiến trang hiện tại vượt giới hạn,
  giao diện chuyển về trang cuối còn hợp lệ và tải trang đó.
- Không có fallback fixture khi API thất bại. Nếu kết quả mark-read không rõ, UI báo chưa thể
  xác nhận và cho tải lại trạng thái.

## Kiểm chứng

Kiểm thử schema/OpenAPI và service adapter chạy độc lập với MySQL. Acceptance transaction dùng
MySQL 8.4 với schema Alembic riêng. Frontend component test kiểm tra loading/empty/error, link
được cấp quyền và cập nhật read state; API E2E cần dùng FastAPI + MySQL thật, không mock response
nghiệp vụ và không dùng demo seed.

Chạy từ gốc repo sau khi cài các dependency theo README. Trên Windows, Python cần gói
`tzdata` đúng phiên bản trong `pyproject.toml` để đọc múi giờ IANA.

```powershell
python -m pytest tests/backend/unit tests/backend/integration/test_http_body_limits.py -q
$env:MYSQL_TEST_URL='mysql+asyncmy://test_user:test_password@127.0.0.1:3306'
python -m pytest tests/backend/integration/test_notifications.py -q
python -m pytest tests/backend/integration/test_notifications.py::test_recipient_query_plans_and_concurrent_read_with_large_history -q -s --require-mysql
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test
```

Để chạy E2E desktop/mobile bằng API thật, cấu hình thêm `FRONTEND_E2E_PYTHON`,
`NEXT_PUBLIC_APP_MODE=api` và `API_UPSTREAM_URL=http://127.0.0.1:8101`, build frontend,
rồi chạy `npm --workspace src/frontend run test:e2e:api`. Lệnh và quyền MySQL cần thiết
được mô tả tại [frontend-api-integration.md](frontend-api-integration.md#kiểm-chứng).
Thử tay: đăng nhập STUDENT, tạo hoặc hủy lịch, mở `/notifications`, lọc chưa đọc và đánh
dấu đã đọc; reload để xác nhận trạng thái. Đăng nhập STAFF được gán lịch và mở
`/staff/notifications`; tài khoản khác không thấy thông báo hoặc link lịch của người trước.

Ngày 24/10/2026, bộ regression frontend thêm tình huống đổi cookie ở tab khác sau lần kiểm
tra focus, hai thứ tự hoàn thành của POST/GET và trang cuối của bộ lọc chưa đọc. Local và
[Frontend CI](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/39997364215)
đã chạy **254 unit/component tests**; CI cũng chạy **66 mock-mode browser E2E tests**.
Trong cùng workflow, **60 desktop/mobile real-API E2E tests** qua FastAPI và MySQL 8.4
đã đạt, gồm luồng hộp thư dùng dữ liệu tạo từ nghiệp vụ và expiry từ worker.
Backend unit/OpenAPI tại local chạy được phần không cần timezone; toàn bộ suite Windows
không chạy xong vì môi trường Python thiếu `tzdata==2026.5` và không tải được package.
MySQL 8.4 không sẵn có trên máy này; backend integration và real-API E2E được chạy trong CI.
[Backend CI](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/39997364247)
đạt **1.292 tests** không skip, cùng Black, Ruff, Pyright, migration và OpenAPI artifact.

Ca MySQL tải 10.000 thông báo tổng hợp (5.000 cho mỗi người nhận, 80% đã đọc), chạy
`ANALYZE TABLE`, ghi `EXPLAIN` cho lịch sử và bộ lọc chưa đọc cùng truy vấn đếm, đo 10
lần truy vấn trang cho mỗi chế độ, rồi gửi 8 request đánh dấu cùng một mục đồng thời.
Đây là dữ liệu đại diện theo kịch bản tải của lab, không phải số liệu production. Handoff
cần đối chiếu lại nếu nhóm có phân bố dữ liệu hoặc tải thực khác. Không có migration hoặc
chỉ mục mới trong branch này.

Trên MySQL 8.4 của CI, cả truy vấn trang lịch sử và trang chưa đọc dùng
`ix_notifications_recipient_history` (`type=ref`, ước lượng 4.837 hàng mỗi người nhận);
truy vấn đếm cũng dùng cùng chỉ mục. `EXPLAIN` của truy vấn trang còn báo
`Using temporary; Using filesort`; vì vậy kết quả này chứng minh chỉ mục được dùng và
thời gian ở cỡ dữ liệu thử nghiệm, không chứng minh kế hoạch không cần sắp xếp bổ sung.
Tám POST đồng thời cùng một ID trả HTTP 200 với cùng timestamp; không tạo thêm audit hoặc
notification và không đổi trạng thái lịch. Chi tiết kế hoạch và số đo có trong log của bước
`Notification query-plan and concurrent-read evidence` thuộc Backend CI ở trên.
