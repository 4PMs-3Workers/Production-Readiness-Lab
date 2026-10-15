# Mốc 3 — Đăng nhập demo và Home

Phần mở rộng của [issue #7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7),
trên nền tảng và mock booking của mốc 1 + 2. Luồng hiện tại:

`/login` → `/home` → sidebar **Đặt lịch tư vấn** → `/booking` → `/booking/result`.

## Trải nghiệm

- Form có nhãn Email/Mật khẩu, thông báo lỗi theo từng trường, focus trường lỗi
  đầu tiên và hỗ trợ gửi bằng Enter. Email phải đúng định dạng; mật khẩu minh họa
  chỉ cần không rỗng, không bị trim hay đối chiếu mật khẩu hardcode.
- Dùng `student@example.test` và mật khẩu minh họa bất kỳ, ví dụ `demo`.
  Không dùng thông tin tài khoản thật. `loginDemo` trả về danh tính giả cố định
  **Sinh viên Demo HUS**, không sử dụng email nhập vào để giả danh tài khoản thật.
- Khi đang gửi, form và thiết lập demo bị khóa; header vẫn cho phép chuyển trang.
  Mật khẩu được xóa khỏi ô ngay khi gửi; lỗi dịch vụ giữ email để thử lại.
- **Thiết lập bản demo** có thành công, phản hồi chậm, sai thông tin và lỗi dịch vụ
  đăng nhập; nhóm kịch bản đặt lịch được giữ cùng phiên để thử tiếp sau khi login.
- Home dùng khung portal HUS, thông tin sinh viên giả, dịch vụ tư vấn và sidebar
  có tab Đặt lịch. Khi có kết quả trong phiên, Home có link xem kết quả gần nhất.
- Header portal có **Trang chủ** và **Đăng xuất** sau đăng nhập. Trang login của
  phiên đã mở có link **Vào trang chủ**.
- Truy cập trực tiếp `/booking` vẫn được hỗ trợ, không yêu cầu đăng nhập.
  `/home` khi chưa có phiên hiển thị thông báo và link đăng nhập/xem demo đặt lịch.

## Giao diện portal

Home, Đặt lịch và Kết quả dùng tông trắng–xanh dương đồng bộ với logo HUS:
header, sidebar và thẻ nền trắng, vùng nội dung xanh nhạt, nút chính và lựa chọn
đang chọn dùng xanh dương. Logo giữ nguyên màu gốc; Login giữ giao diện sáng.

Các phần giao diện dùng CSS Modules riêng cho portal, Home và booking. Focus
bàn phím, lựa chọn đang chọn, nút disabled, lỗi và trạng thái thành công giữ
cách hiển thị của giao diện sáng.

Giữ nguyên wizard bốn bước, sidebar Đặt lịch, thẻ cố vấn đơn giản và danh sách
ngày. Không bổ sung ảnh cố vấn, đánh giá sao, khoa/chuyên môn hoặc lịch tháng.
Desktop giữ vùng nội dung và tóm tắt; mobile xếp dọc như luồng hiện có.

## State và hủy request

`DemoProvider` ở root giữ `StudentContext` trong bộ nhớ. Chỉ kết quả `loginDemo`
thành công của request còn hoạt động mới được ghi vào context và chuyển Home.
UI đặt lịch dùng sinh viên từ context, hoặc `getDemoStudent()` khi xem trực tiếp.

Form giữ AbortController riêng và hủy khi unmount. Kiểm tra controller và signal
trước khi ghi state hoặc điều hướng ngăn phản hồi thành công cũ mở lại phiên.
Ref pending ngăn gửi trùng ngay cả trước lần render tiếp theo; busy của login
thuộc form nên không khóa wizard đặt lịch sau điều hướng.

Đổi kịch bản/Đặt lại demo hủy request cũ, reset draft và kết quả nhưng giữ sinh viên.
Đăng xuất hủy submission đang có, reset mock services về `success`, xóa sinh viên,
draft và kết quả, rồi về `/login`. Refresh hoặc tab mới cũng bắt đầu phiên trống.
Không dùng localStorage, sessionStorage, cookie, token hoặc lưu/log mật khẩu.

Các ngày 14–20/10/2026 và ngày demo 13/10/2026 trong wizard được cố định bởi mock
clock; không thay đổi theo đồng hồ máy. Đây vẫn là dữ liệu và lịch hẹn minh họa.

## Cấu trúc

- `features/login/login-flow.tsx`: form, validation, pending, lỗi và cancellation.
- `features/home/home-view.tsx`: Home có phiên và trạng thái phiên trống.
- `features/demo/demo-provider.tsx`: sinh viên, kết quả và reset/logout trong bộ nhớ.
- `components/portal-account.tsx`: header tài khoản và thông tin sidebar tương tác.
- `app/home`: page metadata và layout dùng lại PortalShell.
- `tests/frontend/components/login-flow.test.tsx`: validation, lỗi, gửi trùng và
  phản hồi login cũ hoàn tất sau khi form mới đã gửi.
- `tests/frontend/e2e/login-flow.spec.ts`: login → Home → booking, retry, hủy khi
  chuyển trang, refresh và logout trên desktop/mobile.

## Kiểm chứng ngày 15/10/2026

Bảng dưới ghi nhận bản mốc 3. Kiểm chứng mốc 4 nằm trong
[tài liệu booking wizard và quản lý state](frontend-booking.md); kết quả mới nhất
nằm trong [mốc 6: kiểm thử và CI](frontend-testing.md).

Trên Windows, Node.js 24.19.0 và npm 12.2.0, với dependencies hiện có từ lockfile:

| Kiểm tra                       | Kết quả                                                                                      |
| ------------------------------ | -------------------------------------------------------------------------------------------- |
| Format, lint, typecheck        | Đạt; tạo route types trước `tsc`.                                                            |
| Vitest / React Testing Library | 44 tests trong 6 files đạt.                                                                  |
| Production build               | Đạt, gồm route mới `/home`.                                                                  |
| Playwright                     | 32 tests đạt trên desktop/mobile Chromium, không retry.                                      |
| Visual QA                      | Đã xem 6 trạng thái ở desktop 1440×900 và mobile 390×844; không tràn ngang hoặc lỗi runtime. |

Component tests mới kiểm tra validation/focus, hai lỗi login, mật khẩu được xóa,
ngăn gửi trùng và request cũ trả thành công sau khi đã rời trang/gửi request mới.
E2E mới kiểm tra gửi bằng Enter, Home → booking → result, giữ sinh viên khi đổi
preset, logout xóa kết quả, retry lỗi login, rời trang lúc login chậm và refresh
kết thúc phiên. Bộ kiểm tra cũ vẫn đạt, gồm hồi quy P2 rời trang khi xác nhận
đặt lịch chậm. Kết quả này là kiểm chứng local cho thay đổi chưa commit.

Chưa chạy lại `npm ci` hoặc backend tests vì không thay dependencies/lockfile
hay mã backend. CI sẽ kiểm tra bản checkout sạch khi thay đổi được đẩy lên GitHub.

Backend, dependencies, lockfile và workflow CI giữ nguyên. Issue #7 tiếp tục mở
cho review toàn bộ acceptance criteria và bằng chứng giao diện mới.
Thư mục `docs/screenshots` đã được xóa theo yêu cầu; không thêm lại ảnh vào repo.
Ảnh cũ nhúng trong PR #10 chỉ phản ánh mốc 1 + 2, chưa phải bằng chứng UI mốc 3.
