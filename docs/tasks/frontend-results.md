# Mốc 5 — Kết quả và lịch đã đặt

Hoàn thiện màn kết quả và bổ sung lịch sử trong phiên cho
[issue #7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
Giữ giao diện trắng–xanh dương, logo HUS và bốn bước đặt lịch hiện tại.
Login không đổi giao diện; `/booking` vẫn cho trải nghiệm trực tiếp không cần đăng nhập.

## Các màn hình và điều hướng

| Route                | Hành vi                                                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `/booking/result`    | Kết quả xác nhận thành công gần nhất, thông tin sinh viên, nhu cầu, cố vấn, ngày/giờ Việt Nam, thời lượng, địa điểm và mô tả OTHER nếu có. |
| `/appointments`      | Mục **Lịch đã đặt** riêng trên sidebar; mọi lịch thành công trong phiên, lần đặt mới nhất trước.                                           |
| `/appointments/[id]` | Chi tiết đúng lịch được chọn, gồm cả lịch cũ; không thay kết quả gần nhất.                                                                 |

Mỗi thẻ có mã `DEMO-xxxx`, trạng thái xác nhận demo, nhu cầu, cố vấn, ngày/giờ,
thời lượng, địa điểm và liên kết **Xem chi tiết** có mã lịch trong accessible name.
Địa điểm chưa xác định hiển thị **Địa điểm sẽ được thông báo sau**.
Home giữ liên kết kết quả gần nhất và thêm liên kết xem tất cả lịch trong phiên.

**Đặt lịch khác** mở draft mới nhưng giữ các lịch đã xác nhận. Lỗi hoặc request
bị hủy không tạo lịch trong danh sách. Khi chưa có kết quả, danh sách rỗng hoặc ID
không tồn tại trong phiên, trang giải thích rõ và cung cấp đường quay lại đặt lịch.
ID không tìm thấy không được thay bằng lịch gần nhất.

## Vòng đời dữ liệu

`DemoProvider` giữ `readonly BookingResult[]`; `recordBooking` thêm kết quả thành
công vào đầu danh sách bằng functional update. ID trùng bị bỏ qua, không thêm
thẻ hoặc đổi thứ tự. `result` được suy ra từ phần tử đầu, tránh hai nguồn state
cho kết quả gần nhất.

| Thao tác                                                     | Kết quả                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------ |
| Điều hướng client giữa Home, Booking, Kết quả và Lịch đã đặt | Giữ danh sách.                                                           |
| Mở chi tiết một lịch cũ                                      | Giữ nguyên danh sách và kết quả gần nhất.                                |
| Đặt lịch khác                                                | Xóa draft của wizard, giữ danh sách.                                     |
| Đặt lại demo hoặc đổi kịch bản                               | Hủy request, xóa danh sách/draft; giữ sinh viên demo nếu đã đăng nhập.   |
| Đăng xuất                                                    | Hủy request, xóa sinh viên/danh sách/draft, reset services về `success`. |
| Tải lại trang hoặc mở tab mới                                | Bắt đầu phiên mới, danh sách rỗng.                                       |

Không ghi localStorage, sessionStorage, cookie hoặc token; chưa gọi backend và
chưa tạo lịch hẹn thật. Mã lịch có thể dùng lại từ `DEMO-0001` sau reset vì đây là
dữ liệu giả của phiên mới. Màn danh sách nêu rõ phạm vi phiên.
Guard AbortController của submission vẫn bảo đảm cleanup cũ không giải phóng
trạng thái bận của request mới.

## Chặn lịch trùng trong phiên

Mock services lưu khoảng giờ của từng lần đặt thành công. Danh sách giờ trống
lọc theo toàn bộ khoảng, không chỉ ID slot, nên đổi nhu cầu hoặc cố vấn vẫn
không cho sinh viên đặt hai lịch giao nhau. Khi xác nhận, service kiểm tra lại
trước khi ghi, kể cả draft đã lấy từ trước hoặc hai yêu cầu chờ hoàn tất cùng lúc.
Lỗi `BOOKING_CONFLICT` đưa wizard về bước ngày/giờ để chọn lại và không thêm lịch.

Ví dụ 09:00–10:00 chặn 09:00–09:30, 09:15–09:45 và 09:30–10:00.
Hai lịch 09:00–09:30 và 09:30–10:00 được phép vì khoảng dùng biên `[start, end)`.
Reset/đăng xuất khôi phục giờ trống. Đây là kiểm tra trong một phiên mock;
khóa dữ liệu và chống tranh chấp giữa các phiên/người dùng thuộc backend thật.

## Bàn phím và phản hồi

- Skip link dẫn tới main trên tất cả route, gồm danh sách và chi tiết chưa có dữ liệu.
- Chuyển bước đưa focus tới tiêu đề bước; các radio dùng bàn phím chuẩn của trình duyệt.
- Lỗi tải danh mục/cố vấn/giờ trống và lỗi xác nhận nhận focus, có `role="alert"`
  và viền focus. Cùng một lỗi xuất hiện lại sau retry vẫn nhận focus.
- Kết quả thành công và chi tiết có dữ liệu đưa focus tới tiêu đề để tiếp tục bằng Tab.
- Loading/empty và số lịch dùng `role="status"`; mô tả OTHER nối với hướng dẫn
  qua `aria-describedby`. Nội dung nhiều dòng được giữ khi review và xem chi tiết.

## Kiểm chứng ngày 15/10/2026

Các số liệu dưới đây ghi nhận mốc 5 và bản sửa trùng lịch. Kiểm chứng mới nhất
với dependencies cài sạch nằm trong [mốc 6: kiểm thử và CI](frontend-testing.md).

Kiểm tra local trên Windows, Node.js 24.19.0, npm 12.2.0, dependencies hiện có từ lockfile:

- Format, lint, typecheck và production build đạt; build gồm `/appointments` và route động `/appointments/[id]`.
- **69 unit/component tests trong 10 files** đạt. Tests mới kiểm tra lưu nhiều lịch,
  không trùng ID, giữ latest khi ghi lại lịch cũ, reset/logout, focus khi lặp lại lỗi,
  giao khoảng giữa các nhu cầu/cố vấn, draft cũ, submit đồng thời và hai biên tiếp giáp.
- Bảy ca hồi quy trùng lịch đã thất bại trước sửa và đạt sau sửa.
- **66 E2E tests** trên desktop/mobile Chromium đạt, không retry. Trong đó 14 ca
  mốc 5 kiểm tra nhiều lịch, chi tiết cũ/mới, latest, logout, refresh, reset/đổi preset,
  nội dung OTHER, toàn bộ luồng bằng Tab, Shift+Tab, Space, Enter và tình huống
  đặt 09:00–10:00 rồi chọn nhu cầu/cố vấn khác cùng ngày. Giờ trùng bị ẩn, chọn
  ngày khác đặt thành công; hai lịch tiếp giáp vẫn được lưu và xem lại độc lập.
- Các hồi quy cũ vẫn đạt, gồm rời trang khi xác nhận chậm, response cũ, A → B → A,
  conflict và giờ Việt Nam khi browser dùng UTC/America/Los_Angeles.
- Visual QA: đã xem kết quả, danh sách hai lịch, chi tiết lịch cũ có nội dung nhiều dòng,
  Home, danh sách rỗng và link hết phiên ở desktop **1440×900** và mobile **390×844**.
  Không tràn ngang hoặc lỗi runtime ở các trạng thái này; ảnh QA tạm đã được dọn.

Chạy bằng các scripts trong [hướng dẫn frontend](frontend-bootstrap.md).
Đây là kiểm chứng working tree chưa commit, chưa phải CI của commit mới.
Không đổi backend, dependencies, lockfile hoặc workflows; không chạy lại `npm ci`
hoặc backend tests. Không tạo lại `docs/screenshots`.

Issue #7 tiếp tục mở cho review và nghiệm thu toàn bộ acceptance criteria.
