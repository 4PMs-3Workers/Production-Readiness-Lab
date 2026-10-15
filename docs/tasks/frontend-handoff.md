# Mốc 7 — Bàn giao và nghiệm thu frontend

Đối chiếu [issue #7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7)
ngày **15/10/2026**. Người dùng đã duyệt bước commit/push các thay đổi mốc 3–7.
PR mới và việc upload ảnh vẫn chờ thực hiện; chưa xác nhận CI, approval hoặc merge
cho các thay đổi mới.

## Phạm vi và các quyết định đã thống nhất

- Ba màn hình của issue: `/login`, `/booking`, `/booking/result`; frontend chạy
  độc lập với backend, dùng dữ liệu giả qua services.
- Theo yêu cầu của người dùng, đăng nhập thành công vào `/home`; từ sidebar mở
  Đặt lịch. Đây là thay đổi so với đường chuyển thẳng tới Booking trong issue.
- `/booking` vẫn xem và thao tác trực tiếp được mà không đăng nhập.
- Theo yêu cầu bổ sung, sidebar có **Lịch đã đặt** tại `/appointments`, mở chi tiết
  tại `/appointments/[id]`. Lịch sử nằm ngoài scope ban đầu của issue nhưng chỉ là
  dữ liệu trong phiên mock, không có API lịch sử, phân trang, hủy hay đổi lịch.
- Cùng sinh viên không đặt được hai khoảng giao nhau trong phiên, kể cả khác
  nhu cầu/cố vấn. Hai khoảng tiếp giáp được phép. Đây là hành vi mock bổ sung,
  không chứng minh tính đúng đắn của giao dịch hoặc khả dụng backend.
- Giữ màu trắng–xanh dương theo logo HUS, thẻ cố vấn đơn giản, danh sách ngày và
  bốn bước hiện tại. Không thêm ảnh cố vấn, sao đánh giá hoặc lịch tháng.
- Đặt lịch khác mở draft mới và giữ lịch đã xác nhận. Refresh, reset/đổi kịch bản
  hoặc đăng xuất xóa danh sách; reset giữ sinh viên, logout xóa cả sinh viên.

## Đối chiếu acceptance criteria

| Tiêu chí của issue #7                                  | Bằng chứng và trạng thái                                                                                                                                                   |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cài và chạy từ source sạch bằng lệnh trong tài liệu    | **Đạt local**: `npm ci` trên bản sao không có dependencies/build/cache, cài mới 269 packages; hướng dẫn Node 24.19.0/npm 12.2.0 trong README.                              |
| Review ba màn hình và các trạng thái không cần backend | **Đạt local**: 13 preset; luồng Login → Home → Booking → Result và direct Booking. Thay đổi đường đi qua Home cần được reviewer ghi nhận.                                  |
| Desktop/mobile và bàn phím                             | **Đạt local**: 66 E2E, gồm skip link, focus lỗi, Tab/Shift+Tab/Space/Enter; 20 ảnh production ở 1440×900 và 390×844.                                                       |
| Đổi lựa chọn không submit được staff/slot cũ           | **Đạt local**: reducer, request ownership, hồi quy A → B → A và response đến muộn.                                                                                         |
| Hiển thị đúng múi giờ nghiệp vụ                        | **Đạt local**: ISO có offset/Z, giờ `Asia/Ho_Chi_Minh`; E2E browser UTC/Los_Angeles, unit clock gần biên ngày và D+1..D+7.                                                 |
| Không gửi xác nhận lặp khi pending                     | **Đạt local**: component spy kiểm tra số lời gọi; E2E khóa nút/controls và hủy khi rời trang.                                                                              |
| Conflict cho chọn và gửi giờ khác                      | **Đạt local**: `conflict-once`, xóa slot/tải lại, giữ lựa chọn còn lại, chọn giờ khác thành công.                                                                          |
| Mock data sau ranh giới services                       | **Đạt local**: UI không import fixtures; contracts/DTO riêng, factory theo phiên, không gọi FastAPI/MySQL.                                                                 |
| Format/lint/typecheck/tests/build đạt trong CI         | **Đạt local với `CI=true`**: 70 unit/component tests, 66 E2E không retry, production build; **chờ CI GitHub của commit mới**, cả frontend và backend.                      |
| Tài liệu có bằng chứng và phụ thuộc tích hợp           | **Đã chuẩn bị local**: setup, routes, presets, lifecycle, ma trận tests, API handoff và bản mô tả PR tiếng Anh. Ảnh phải được upload và nhúng vào PR trước khi xin review. |

Xem chi tiết và lệnh kiểm tra trong [mốc 6](frontend-testing.md). Source/runtime,
tests, configs và lockfile không đổi sau lần kiểm chứng sạch; mốc 7 bổ sung tài
liệu và bằng chứng hình ảnh, không thay hành vi ứng dụng.

## Hướng dẫn reviewer tái hiện

Chạy `npm ci`, `npm --workspace src/frontend run dev` từ gốc. Mặc định dev mở
`http://127.0.0.1:3000`; URL preview local hiện tại có thể khác. Chỉ dùng thông tin giả.
Demo clock là 13/10/2026; ngày đặt **14–20/10/2026** không đi theo đồng hồ máy.

Với tình huống Login hoặc dùng link header **Đăng nhập** trong lúc gửi Booking,
bắt đầu bằng phiên chưa đăng nhập (refresh trang đầu của tình huống hoặc Đăng xuất).
Reset/đổi preset giữ sinh viên đã đăng nhập, nên không thay thế bước này.

| Tình huống             | Thao tác                                                                                                                                         | Kết quả cần thấy                                                                       |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| Luồng đầy đủ           | `/login`, email `student@example.test`, mật khẩu minh họa bất kỳ không rỗng → Home → Đặt lịch → Nghề nghiệp → Demo 01 → 14/10, 09:00 → Xác nhận. | Mã DEMO-0001, 09:00–09:30, thông báo chưa tạo lịch thật.                               |
| Không đăng nhập        | Mở `/booking` trong phiên mới và đặt như trên.                                                                                                   | Dùng sinh viên giả, không tạo phiên xác thực.                                          |
| Login sai hoặc lỗi     | Trong Thiết lập bản demo ở Login, chọn **Sai thông tin đăng nhập** hoặc **Lỗi dịch vụ đăng nhập**, điền dữ liệu giả và gửi.                      | Ở lại form, focus lỗi, mật khẩu được xóa. Đổi Thành công rồi gửi lại để tiếp tục.      |
| OTHER                  | Chọn Nhu cầu khác, thử để trống/khoảng trắng rồi nhập nội dung hợp lệ.                                                                           | Chặn Tiếp tục khi trống; giữ nội dung ở review/result/chi tiết.                        |
| Loading/empty/retry    | Chọn Phản hồi chậm; hoặc các preset Chưa có nhu cầu/nhân viên/giờ trống, Lỗi tải … một lần.                                                      | Loading/empty rõ ràng; lỗi có Thử lại đúng nguồn hiện tại.                             |
| Gửi lỗi                | Preset Lỗi xác nhận lịch, hoàn thành draft và xác nhận.                                                                                          | Giữ draft và lỗi có focus; không hiện success hoặc thêm lịch.                          |
| Conflict               | Preset Xung đột giờ một lần, chọn 09:00 rồi xác nhận.                                                                                            | Trở lại ngày/giờ, 09:00 bị loại; chọn 09:15, tiếp tục và xác nhận thành công.          |
| Response cũ            | Preset Phản hồi cũ đến sau, đổi nhu cầu/cố vấn/ngày khi request còn chờ.                                                                         | Dữ liệu cũ không thay danh sách mới; không submit lựa chọn phụ thuộc cũ.               |
| Rời trang khi gửi      | Preset Phản hồi chậm, gửi rồi bấm Đăng nhập khi đang xác nhận; quay lại Booking bằng link.                                                       | Các nút/controls dùng được, đặt draft mới được; response cũ không mở khóa request mới. |
| Nhiều lịch hợp lệ      | Đặt Nghề nghiệp 09:00, Đặt lịch khác, cùng ngày chọn 09:30. Mở Lịch đã đặt rồi chi tiết DEMO-0001.                                               | Hai lịch riêng, mới nhất trước; 09:15 bị ẩn, mở lịch cũ không đổi latest.              |
| Trùng giờ như feedback | Phiên mới: đặt Khóa luận/Demo 02 ngày 14/10, 09:00–10:00; đặt Cố vấn học tập/Demo 04 cùng ngày.                                                  | Các giờ 09:00, 09:15, 09:30 bị ẩn. Chọn ngày 15/10 để đặt lịch khác.                   |
| Vòng đời phiên         | Refresh Result/History; hoặc Đặt lại demo/đổi preset; hoặc Đăng xuất.                                                                            | Không dựng success giả; danh sách xóa đúng phạm vi đã nêu.                             |

Đổi preset reset draft và toàn bộ lịch trong phiên; chỉ thử từng tình huống sau
khi đã ghi nhận kết quả tình huống trước. Preset `stale-response` làm response
đầu chậm hơn; hồi quy quay lại A → B → A được kiểm tra riêng với preset Phản hồi chậm.

## Bàn giao tích hợp API

OpenAPI đang commit trong `docs/api/openapi.yaml` mới có các route health; chưa
có hợp đồng nghiệp vụ để nối Login/catalog/availability/booking thật.

1. Đối chiếu DTO trong `types/booking.ts` và `services/contracts.ts` với OpenAPI
   được review của #4 và các API nghiệp vụ tương ứng. Không coi ID fixture hoặc
   `DemoServiceError.code` là hợp đồng backend đã chốt.
2. Viết adapter HTTP cho `BookingServices`, truyền `AbortSignal` và chuyển lỗi
   backend sang thông báo UI theo mã cụ thể; không đổi mọi HTTP 409 thành lỗi slot.
3. Tách API xác thực/phiên của #5 khỏi cơ chế demo. `DemoProvider` hiện dùng
   `MockBookingServices` với clock/reset/getDemoStudent; không thể thay bằng
   fetch đơn thuần mà giữ fallback danh tính hoặc controls demo trong chế độ thật.
4. Lấy danh tính và kết quả được phép đọc từ server. Bỏ giả định direct booking
   có sinh viên giả; xử lý hết phiên, cookie/CSRF và quyền theo hợp đồng thật.
5. Backend chịu trách nhiệm tính khả dụng, quota, giao khoảng, transaction và
   idempotency. Kiểm tra trùng giờ trong bộ nhớ chỉ phục vụ một phiên mock.
6. Nếu đưa Lịch đã đặt vào phạm vi API, cần endpoint theo sinh viên có kiểm tra
   quyền, trạng thái và phân trang; lịch sử trong provider không thay nguồn dữ liệu thật.
7. Bổ sung contract/integration tests cho adapter, lỗi/hết phiên/CSRF và các quy
   tắc ghi thực tế. 70 unit/component và 66 E2E hiện tại chỉ kiểm chứng bản demo.

## Bằng chứng hình ảnh và PR

Bộ mới chụp ngày 15/10/2026 từ production: **20 ảnh**, mỗi trạng thái có desktop
1440×900 và mobile 390×844: Login, Home, bốn bước Booking, Result thành công,
Lịch đã đặt, chi tiết lịch cũ và Result rỗng. Ảnh toàn trang có thể cao hơn viewport.
Các ô mật khẩu trống; dữ liệu sinh viên/cuộc hẹn đều giả.

Ảnh, gallery và manifest được giữ ngoài Git trong `build/frontend-handoff-20261015/`;
không tạo lại `docs/screenshots`. Đường dẫn local không thay thế bằng chứng review
trên GitHub: cần upload ảnh và nhúng URL trực tiếp trong mô tả PR, có route và
viewport trong caption. `PR_DESCRIPTION.md` là bản mô tả tiếng Anh chờ upload ảnh,
được ignore để dùng cho GitHub UI, không phải tài liệu commit.

## Trạng thái GitHub và bước xuất bản còn lại

- Issue #7 còn mở khi đọc lại GitHub API ngày 15/10/2026.
- [PR #10](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/10) đã
  squash-merge ngày 14/10/2026 vào `develop` tại `e416ffd978dc15e3f96008dac8f7485a2553d6d5`.
  Nó chỉ bàn giao mốc 1 + 2; cần PR mới cho mốc 3–7.
- Nhánh `feature/frontend-bootstrap` đã merge `origin/develop` tại `e416ffd`
  trước khi commit mốc 3–7 để đồng bộ lịch sử sau squash-merge của PR #10.
  Hai cây mã nguồn tại `fe97af9` và `e416ffd` giống nhau; bước đồng bộ không
  thay đổi mã nguồn đã kiểm chứng.
- CI xanh của `fe97af9` là bằng chứng lịch sử, không xác nhận code mới:
  [Frontend checks](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38550187011/job/115805959402),
  [backend models](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38550186957/job/115805960525).
- Sau commit/push, cần tạo PR mới vào `develop`, ghi SHA được review, nhúng ảnh
  đã upload và đối chiếu CI frontend/backend trên đúng SHA đó. Tạo PR và upload
  ảnh chưa nằm trong bước commit/push được người dùng duyệt.
- Chờ reviewer chấp thuận, xử lý feedback và merge theo GitFlow. Giữ #7 mở đến
  khi merge và mọi acceptance criteria đạt. Không tự đánh dấu issue hoàn tất
  chỉ dựa vào kết quả local hoặc closing keyword.
