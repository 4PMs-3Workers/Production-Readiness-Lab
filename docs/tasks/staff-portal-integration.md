# Bàn giao cổng STAFF

Cổng STAFF dùng phiên đăng nhập và API same-origin hiện có. Tài khoản vai trò
`STAFF` được chuyển tới `/staff/appointments`; các route được hỗ trợ là:

- `/staff/appointments`: lịch được giao, lọc theo trạng thái/ngày và phân trang.
- `/staff/appointments/[id]`: chi tiết lịch được giao, hủy có xác nhận và ghi
  nhận `COMPLETED` hoặc `NO_SHOW` sau xác nhận riêng khi API cho phép.
- `/staff/preferences`: bật/tắt nhận lịch theo nhu cầu được phân công.
- `/staff/unavailability`: xem, lọc theo khoảng thời gian, thêm và xóa khoảng thời
  gian không nhận lịch.

Backend tiếp tục là nguồn quyết định vai trò, quyền sở hữu, trạng thái, thời hạn,
xung đột và múi giờ nghiệp vụ. Frontend lấy timezone từ
`GET /api/v1/staff/me/context`; giờ nhập được chuyển thành timestamp có offset
theo timezone đó. Summary của lịch không hiển thị nội dung trao đổi; nội dung chỉ
có ở trang chi tiết được API cho phép.

Tài khoản `STUDENT` vẫn dùng luồng cũ. `ADMIN` chưa có giao diện trong phạm vi
cổng này. Đề nghị đổi lịch, hộp thư thông báo, lịch nghỉ lặp và quản trị vẫn nằm
ngoài phạm vi.

## Hợp đồng và cách xử lý lỗi

`src/frontend/services/api/client.ts` ánh xạ dữ liệu STAFF từ các endpoint
`/api/v1/staff/me/*`. Các lệnh ghi lấy CSRF và xác minh lại `/auth/me` là đúng
STAFF đang xem trước khi gửi. Server vẫn kiểm tra role, assignment và ownership
cho từng request. Danh sách và bộ lọc lấy từ API, không lưu dữ liệu riêng tư vào
browser storage.

Nếu `/auth/me` cho biết người đang đăng nhập đã đổi trước một lệnh ghi, provider
xóa dữ liệu STAFF đang hiển thị và chuyển về luồng đăng nhập. Khi kết quả hủy/ghi
kết quả lịch chưa rõ, chỉ cho đọc lại trạng thái hoặc thử lại đúng thao tác ban đầu;
không cho chọn kết quả trái ngược cho đến khi đọc được chi tiết mới. Nếu server
từ chối do xung đột trạng thái, giao diện tải lại chi tiết trước khi cho thao tác
tiếp. Chi tiết lịch hiển thị thời điểm và người thực hiện hủy/ghi kết quả khi DTO
có các trường đó.

| Thao tác               | Khi kết quả không rõ                                                                                                                                       |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hủy lịch, ghi kết quả  | Đọc lại chi tiết; chỉ báo thành công khi trạng thái đã đúng. Nếu đọc lại lỗi, giữ đúng thao tác ban đầu để xác minh hoặc thử lại.                          |
| Đổi nhu cầu tiếp nhận  | Tải lại danh sách server; trạng thái hiển thị lấy từ phản hồi mới.                                                                                         |
| Thêm khoảng không nhận | Giữ nguyên khoảng đã đổi sang timestamp có offset để thử lại đúng payload. Nếu replay bị server từ chối dứt khoát, mở lại trường nhập với giá trị đã điền. |
| Xóa khoảng không nhận  | Tải lại danh sách và báo chưa xác nhận; một trang kết quả thiếu item không đủ chứng minh đã xóa.                                                           |

Trường giờ nhập và bộ lọc `from`/`to` theo timezone của `/staff/me/context`. Giờ không tồn tại hoặc
mơ hồ khi đổi giờ mùa hè bị từ chối để người dùng chọn lại. Danh sách hiển thị
cả ngày và giờ theo timezone nghiệp vụ, kể cả khi trình duyệt ở múi giờ khác.

## Kiểm thử API thật

`tests/frontend/api-e2e/staff-portal.spec.ts` kiểm tra thao tác STAFF qua Next.js,
FastAPI và MySQL 8.4. Cần cấu hình `MYSQL_TEST_URL` tới MySQL dùng riêng cho test
và `FRONTEND_E2E_PYTHON` tới Python có dependencies backend. Harness tạo schema
ngẫu nhiên có tiền tố `prl_frontend_e2e_`, tạo fixture STAFF/STUDENT/ADMIN giả,
rồi xóa schema khi cleanup. Không dùng URL database dùng chung hoặc production.

Chạy từ gốc repo sau khi frontend production đã build ở chế độ API:

```powershell
$env:NEXT_PUBLIC_APP_MODE='api'
$env:API_UPSTREAM_URL='http://127.0.0.1:8101'
# MYSQL_TEST_URL: URL tài khoản MySQL test có quyền tạo/xóa schema riêng.
# FRONTEND_E2E_PYTHON: Python đã cài requirements-dev.txt.
npm --workspace src/frontend run build
npm --workspace src/frontend run test:e2e:api -- --grep "staff can|another staff|expired staff|purpose preferences|staff absence"
```

Suite chạy trên desktop và mobile. Case STAFF chụp ảnh danh sách, chi tiết,
preferences và unavailability bằng dữ liệu giả; ảnh đính kèm Playwright report
được CI lưu trong artifact `frontend-api-e2e-*`. E2E kiểm tra thay đổi khả dụng
cho sinh viên sau khi thêm/xóa khoảng không nhận, lịch có sẵn giữ nguyên, conflict
không ghi dở, replay chỉ tạo một khoảng và một audit. Thiếu MySQL là lỗi
prerequisite; suite không đánh dấu thành công bằng cách skip.

Kiểm tra bằng tay: đăng nhập STAFF, mở bốn route trên bằng menu; lọc lịch và mở
chi tiết; thử hủy/ghi kết quả rồi reload; đổi nhu cầu tiếp nhận và kiểm tra catalog
sinh viên; thêm khoảng không nhận qua nửa đêm, reload và xóa. Mở cùng các route
bằng STUDENT/ADMIN và bằng STAFF khác để kiểm tra giới hạn truy cập. Dùng dữ liệu
giả khi chụp màn hình; kiểm tra cả desktop/mobile, không đưa cookie, token hay nội
dung trao đổi riêng vào ảnh/log bàn giao.

## Kết quả lịch sử tại 20/10/2026

- Frontend Vitest: 25 file, 217 test đạt; lint, TypeScript và Prettier đạt.
- Backend OpenAPI và private response policy: 5 test đạt; build Next.js đạt.
- Playwright nhận diện 48 test API desktop/mobile, gồm 8 trường hợp STAFF. Suite
  MySQL thật và kiểm tra trực quan chưa chạy tại máy này vì chưa có
  `MYSQL_TEST_URL` và Docker daemon không hoạt động. Cần QA/CI chạy suite này
  và bổ sung bằng chứng trước khi merge.

Phối hợp: chủ sở hữu #20 xác nhận session/proxy, #21 xác nhận hợp đồng lịch hẹn,
#22 xác nhận preferences/unavailability/replay; reviewer backend kiểm tra endpoint
context mới, QA cấp MySQL test cô lập và xác nhận desktop/mobile.

## Bổ sung theo review ngày 21/10/2026

Thêm hồi quy cho đổi tài khoản trước lệnh ghi, xử lý kết quả ghi chưa rõ và xung
đột trạng thái, replay khoảng nghỉ bị từ chối, liên kết Home, focus dialog, bộ lọc
`from`/`to` và audit detail. Bổ sung integration test cho staff context với quyền,
múi giờ từ database, cấu hình lỗi và timeout. Kết quả chạy mới được ghi trong PR
#37; số liệu 20/10 ở trên được giữ nguyên làm mốc lịch sử.
