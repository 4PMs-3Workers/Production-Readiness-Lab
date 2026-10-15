# Frontend mock services và UI đặt lịch

## Phạm vi hiện tại

DTO, fixtures, mock services và các kịch bản có thể chạy lại được bàn giao cùng
nền tảng trong [bản gộp mốc 1 + 2](frontend-bootstrap.md) của
[issue #7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
**UI đặt lịch hoàn chỉnh** gồm header HUS, sidebar có tab Đặt lịch tư vấn,
form bốn bước và phần tóm tắt cuộc hẹn.
Mốc 5 thêm tab **Lịch đã đặt**, danh sách `/appointments` và chi tiết từng lịch;
xem [kết quả và vòng đời danh sách](frontend-results.md).

Mở trực tiếp `/booking`, không cần đăng nhập. Các thao tác chọn nhu cầu → cố vấn →
ngày/giờ → xác nhận đều sử dụng mock services. Sau xác nhận thành công, trang
`/booking/result` hiển thị dữ liệu trả về từ service. Khi refresh hoặc mở trực tiếp
trang kết quả trong phiên mới, màn hình hiển thị trạng thái chưa có kết quả.

Mốc 3 nối `/login` với `loginDemo`: sau đăng nhập giả lập vào `/home`, từ sidebar
mở Đặt lịch. Chi tiết form, lỗi và phiên trong bộ nhớ nằm trong
[tài liệu đăng nhập demo và Home](frontend-login.md). Chưa bổ sung xác thực thật.
Mốc 4 chuẩn hóa draft và vòng đời request; xem
[đối chiếu yêu cầu và kiểm chứng wizard](frontend-booking.md).

## Cấu trúc và hợp đồng

- `types/booking.ts`: `StudentContext`, `Purpose`, `Staff`, `AvailableSlot`,
  `BookingDraft` và `BookingResult`.
- `types/demo.ts`: cấu hình phiên và kiểu preset.
- `services/contracts.ts`: giao diện Promise và tùy chọn `AbortSignal`.
- `services/index.ts`: điểm truy cập của UI; không export fixtures.
- `services/mock`: factory, fixtures và định nghĩa preset.
- `lib/business-time.ts`: ngày nghiệp vụ và định dạng giờ Việt Nam.
- `features/demo`: provider ở root layout giữ một instance services, sinh viên và các lịch đã đặt
  trong bộ nhớ qua điều hướng giữa các route.
- `features/booking`: wizard, các bước chọn dữ liệu, result, history và resource hook.
- `components/portal-shell`: layout portal dùng chung cho Home và nhóm route đặt lịch.

UI đọc dữ liệu qua `services`, không import fixtures. DTO và lỗi là hợp đồng
frontend tạm thời; cần đối chiếu OpenAPI được review của #4 và các API nghiệp vụ
khi tích hợp thật. `getDemoStudent()` chỉ cấp danh tính giả để xem trước màn đặt
lịch, không thiết lập phiên hoặc quyền truy cập.

| Service                               | Kết quả                                      |
| ------------------------------------- | -------------------------------------------- |
| `loginDemo(credentials, options?)`    | Sinh viên giả hoặc lỗi theo preset.          |
| `listPurposes(options?)`              | Danh mục nhu cầu.                            |
| `listStaff(purposeId, options?)`      | Nhân viên tiếp nhận đúng nhu cầu.            |
| `listAvailableSlots(query, options?)` | Slots cho đúng nhu cầu, nhân viên và ngày.   |
| `createBooking(draft, options?)`      | Kết quả demo chỉ sau khi request thành công. |

Các request hỗ trợ `{ signal }`. Lỗi nghiệp vụ demo dùng `DemoServiceError.code`:
`INVALID_CREDENTIALS`, `SERVICE_UNAVAILABLE`, `BOOKING_CONFLICT`, `INVALID_REQUEST`.
Request bị hủy hoặc bị reset reject với `AbortError`; dùng `isRequestCancelled`
để không hiển thị như lỗi dịch vụ.

## Fixtures và đồng hồ

- Một sinh viên giả (`student@example.test`), bốn cố vấn có tên Demo 01–04.
- Đủ năm mã nhu cầu: `CAREER_INTERNSHIP`, `THESIS_RESEARCH`, `ACADEMIC_ADVISOR`,
  `GENERAL_ADVICE`, `OTHER`; thời lượng lần lượt 30, 60, 30, 45, 30 phút.
- `OTHER` yêu cầu nội dung không rỗng sau khi loại khoảng trắng đầu/cuối.
- Quan hệ nhân viên–nhu cầu được khai báo tường minh. Mỗi nhu cầu có ít nhất một
  nhân viên phù hợp; slots có cả địa điểm xác định và `location: null`.
- Clock mặc định: **2026-10-13T09:00:00+07:00**. UI hiện nhãn ngày demo 13/10/2026.
- Ngày có thể chọn: **14–20/10/2026**, tức D+1..D+7 theo `Asia/Ho_Chi_Minh`.
- Slots lấy từ các mẫu hẹn đã khai báo theo day offset; phút bắt đầu nằm trên
  bước 15 phút, `endsAt` khớp thời lượng nhu cầu. Timestamps trả về dùng UTC `Z`.
- Không dùng clock máy hoặc ngẫu nhiên để quyết định dữ liệu/kết quả. Có thể đổi
  clock bằng tùy chọn `now` có `Z` hoặc offset khi tạo/reset services.

Đây là lịch demo được khai báo, không phải thuật toán availability từ giờ phục
vụ, ngày nghỉ, quota hoặc lịch backend. Thành công ghi nhận khoảng giờ đã đặt
của sinh viên demo; danh sách giờ trống loại mọi khoảng giao nhau, kể cả khác
nhu cầu/cố vấn. `createBooking` kiểm tra lại trước khi tạo kết quả và trả
`BOOKING_CONFLICT` nếu trùng. So sánh toàn bộ khoảng `[startsAt, endsAt)`, nên
hai lịch tiếp giáp được phép. Fixtures có thêm giờ 09:30 ngày D+1 để thử biên này.
Kịch bản conflict-once chỉ loại slot ID bị tranh, không tạo lịch của sinh viên.
Reset xóa cả các khoảng đã đặt. Không mô phỏng khóa/giao dịch MySQL, idempotency
hay xác thực; mỗi factory chỉ có một sinh viên giả.

## Preset và reset

Trong `/booking`, mở **Thiết lập bản demo** ở cuối trang để chọn kịch bản hoặc
**Đặt lại demo**. Panel tách khỏi các trường đặt lịch và bị khóa khi đang gửi.
Cùng 13 preset được chọn từ login hoặc booking; hai preset lỗi đăng nhập chỉ
ảnh hưởng `loginDemo`. Kịch bản đã chọn được giữ qua điều hướng client.

| Preset           | Hành vi                                                                             |
| ---------------- | ----------------------------------------------------------------------------------- |
| `success`        | Các request thành công; delay mặc định 200 ms.                                      |
| `slow`           | Mỗi request chờ 1.500 ms để quan sát loading/pending.                               |
| `invalid-login`  | Đăng nhập luôn trả `INVALID_CREDENTIALS`.                                           |
| `login-error`    | Đăng nhập luôn trả `SERVICE_UNAVAILABLE`.                                           |
| `empty-purposes` | Danh mục nhu cầu rỗng.                                                              |
| `empty-staff`    | Danh sách cố vấn rỗng.                                                              |
| `empty-slots`    | Danh sách giờ trống rỗng.                                                           |
| `purposes-error` | Lần tải nhu cầu hoàn tất đầu tiên lỗi, retry thành công.                            |
| `staff-error`    | Lần tải cố vấn hoàn tất đầu tiên lỗi, retry thành công.                             |
| `slots-error`    | Lần tải giờ trống hoàn tất đầu tiên lỗi, retry thành công.                          |
| `booking-error`  | Xác nhận luôn lỗi, giữ lựa chọn và không hiển thị thành công.                       |
| `conflict-once`  | Xác nhận đầu báo conflict, loại slot đó; chọn slot khác để thành công.              |
| `stale-response` | Request nhân viên đầu và request slots đầu chờ 1.550 ms; các request sau chờ 50 ms. |

```ts
import { createMockServices } from '@/services';

const services = createMockServices({ scenario: 'conflict-once' });
const dates = services.getConfiguration().bookingDates;
const purposes = await services.listPurposes();
const staff = await services.listStaff(purposes[0].id);
const controller = new AbortController();
const slots = await services.listAvailableSlots(
  { purposeId: purposes[0].id, staffId: staff[0].id, date: dates[0] },
  { signal: controller.signal },
);
// createBooking với slots[0] sẽ báo conflict lần đầu; tải lại slots và chọn giờ khác.

services.reset(); // Hủy request cũ, phát lại cùng cấu hình và clock.
services.reset({ scenario: 'success', delayMs: 0 }); // Thay cấu hình; các mục bỏ qua dùng mặc định.
```

Mỗi factory có state riêng. Reset khôi phục slots, fail-once, conflict-once,
counter request và mã `DEMO-0001`; request cũ bị hủy trước khi có thể tạo kết quả.
Request bị hủy không tiêu thụ lỗi một lần hoặc slot. Dữ liệu trả về là bản sao.
UI dùng AbortController cùng key/active guard để response của lựa chọn cũ không
ghi đè lựa chọn mới; đổi nhu cầu/cố vấn/ngày xóa các trường phụ thuộc.
Từ mốc 4, guard so sánh đối tượng request theo query/attempt/loader. Quay lại
query A sau A → B → A vẫn phải chờ request mới, không hiển thị lại kết quả A cũ.

Trạng thái bận khi xác nhận gắn với AbortController của từng submission trong
provider. Unmount wizard hủy request và giải phóng trạng thái ngay; `finally`
cũng giải phóng cả khi request bị hủy. Chỉ controller đang sở hữu trạng thái
được xóa nó, nên cleanup/response muộn của request cũ không mở khóa request mới.

Provider lưu từng kết quả thành công theo ID, mới nhất trước, bỏ qua ID trùng.
`result` được suy ra từ phần tử đầu; xem lại lịch cũ không đổi kết quả gần nhất.
Chuyển qua `/login` rồi bấm **Kết quả** vẫn xem được cuộc hẹn vừa xác nhận.
**Đặt lịch khác** bắt đầu draft mới, giữ toàn bộ lịch đã đặt. **Đặt lại demo** hoặc
đổi kịch bản xóa danh sách nhưng giữ sinh viên nếu đã đăng nhập. Đăng xuất reset
services về `success`, xóa sinh viên và danh sách. Refresh hoặc mở tab mới bắt đầu
phiên mới, không có lịch. Không dùng localStorage, sessionStorage, cookie hay token.
`loginDemo` không lưu hoặc log mật khẩu và chỉ
trả danh tính giả; không đối chiếu một mật khẩu thật/hardcode nào.

## Kiểm chứng ngày 14/10/2026

Phần dưới là kiểm chứng lịch sử mốc 1 + 2. Kết quả mốc 5 nằm trong
[tài liệu kết quả và lịch đã đặt](frontend-results.md); kiểm chứng mới nhất với
dependencies cài sạch nằm trong [mốc 6: kiểm thử và CI](frontend-testing.md).

Chạy các scripts frontend từ README, với Node.js 24.19.0 và npm 12.2.0:

- Format, lint và typecheck đạt.
- **40 unit/component tests**: catalog/associations, timestamps/duration/window,
  biên ngày, OTHER, login outcomes, empty/fail-once/conflict, reset, hủy request,
  input snapshot, đảo thứ tự response, cách ly state giữa các factory và cleanup
  submission cũ hoàn tất sau khi submission mới bắt đầu.
- Production build đạt.
- **22 E2E tests** trên desktop/mobile Chromium: các route, skip link, happy path
  không cần login, reset lựa chọn phụ thuộc, OTHER, retry, booking failure,
  conflict recovery, stale responses, refresh result và giữ kết quả qua menu
  điều hướng/đặt lịch khác; reset demo xóa kết quả. Có hồi quy cho rời trang khi
  đang xác nhận chậm, quay lại không refresh, gửi lịch mới và dùng selector/reset.
- Đã chụp 14 ảnh: login, 4 bước đặt lịch, kết quả thành công và kết quả rỗng
  trên desktop 1440×900/mobile 390×844; không tràn ngang ở các trạng thái đã chụp.
  Bộ ảnh và chú thích route/viewport được nhúng trong mô tả
  [PR #10](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/10).
  Bộ ảnh này thay thế ảnh scaffold trước đây. Ảnh kết quả thành công được chụp sau khi
  đi qua menu để kiểm chứng kết quả vẫn được giữ lại.

Workflow frontend kiểm tra toàn bộ nền tảng và mock booking khi commit được đẩy.
Kết quả trên là kiểm chứng local; trạng thái CI của từng commit xem trong checks
của PR.
Backend, dependencies và lockfile không thay đổi. Issue #7 tiếp tục mở cho các
phần còn lại và review toàn bộ acceptance criteria.
