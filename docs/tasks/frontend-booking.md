# Mốc 4 — Booking wizard và quản lý state

Phần hoàn thiện wizard của [issue #7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
Wizard và mock services đã có từ mốc 1 + 2; mốc 4 gom quy tắc draft vào reducer,
sửa vòng đời request và bổ sung bằng chứng cho các trạng thái còn thiếu.
Giao diện trắng–xanh dương, thẻ cố vấn đơn giản và danh sách ngày giữ nguyên.

## Quy tắc draft

`features/booking/booking-state.ts` quản lý selection và bước hiện tại:

| Thao tác                            | Kết quả                                                  |
| ----------------------------------- | -------------------------------------------------------- |
| Đổi nhu cầu                         | Xóa cố vấn, ngày, slot và mô tả OTHER cũ.                |
| Đổi cố vấn                          | Giữ nhu cầu/mô tả; xóa ngày và slot.                     |
| Đổi ngày                            | Giữ nhu cầu/cố vấn/mô tả; xóa slot.                      |
| Chọn lại cùng giá trị               | Giữ các lựa chọn phụ thuộc.                              |
| Quay lại rồi Tiếp tục               | Giữ những lựa chọn chưa bị thay đổi.                     |
| OTHER chỉ có khoảng trắng           | Chặn bước tiếp theo và xác nhận.                         |
| Slot không khớp nhu cầu/cố vấn/ngày | Bỏ qua và không coi là draft hoàn chỉnh.                 |
| Xung đột khi xác nhận               | Về bước ngày/giờ, xóa slot và tải lại; giữ phần còn lại. |

Các sự kiện chọn cố vấn/ngày mang ID của query nguồn, nên sự kiện cũ không thể
thay lựa chọn của query mới. Reducer kiểm tra điều kiện trước khi chuyển bước;
`isCompleteSelection` kiểm tra lại draft trước khi gọi `createBooking`.
Không bổ sung quy tắc quota, khóa giao dịch hay tính availability của backend.

## Vòng đời request và sửa lỗi A → B → A

Trước sửa, `useDemoResource` chỉ gắn kết quả với chuỗi query/attempt. Nếu A đã
tải xong, chuyển sang B rồi quay lại A trong lúc B còn chờ, kết quả A cũ có thể
hiện ngay dù request A mới chưa hoàn tất. Thay loader cho cùng query cũng có
thể để lại kết quả cũ trong lúc chờ.

Hook hiện dùng đối tượng request riêng cho mỗi bộ query/attempt/loader. Chỉ kết
quả có cùng đối tượng với request hiện tại mới được hiển thị; các trường hợp
còn lại hiện loading. Cleanup hủy AbortController và vô hiệu callback cũ, kể cả
khi adapter vẫn trả dữ liệu/lỗi sau khi bị hủy. Retry gọi loader của query hiện tại.

Hai component regression tests đã thất bại trước sửa và đạt sau sửa. E2E với
**Phản hồi chậm** kiểm tra chọn ngày 14 → 15 → 14: các slot cũ không được offer
trong lúc request mới đang chờ; sau tải xong có thể chọn và tiếp tục bình thường.

## Đối chiếu mốc 4

| Yêu cầu                                                | Bằng chứng                                                                    |
| ------------------------------------------------------ | ----------------------------------------------------------------------------- |
| Bốn bước, điều kiện tiến bước và Back giữ lựa chọn     | Reducer tests và E2E upstream changes.                                        |
| Reset khi đổi nhu cầu/cố vấn/ngày, xóa mô tả cũ        | Reducer tests; E2E đổi cố vấn/ngày và OTHER.                                  |
| Loading/empty/error/retry cho purposes, staff và slots | E2E kiểm tra từng nguồn, khóa Tiếp tục khi chưa chọn được dữ liệu.            |
| Hủy request và bỏ qua response cũ                      | Resource tests, E2E stale-response và hồi quy A → B → A.                      |
| OTHER bắt buộc, duration, review đủ thông tin          | Reducer tests và E2E review, gồm địa điểm null.                               |
| Giờ Việt Nam và ngày D+1..D+7                          | Unit business-time; E2E trong UTC và America/Los_Angeles.                     |
| Chặn submit trùng và khóa thao tác lúc pending         | Component test double click; hồi quy điều hướng khi đang xác nhận chậm.       |
| Chỉ lưu result/điều hướng sau thành công               | Component test lần đầu lỗi, retry cùng draft thành công; E2E failure/success. |
| Conflict giữ nhu cầu/cố vấn/ngày và chọn slot khác     | Reducer test và E2E conflict-once.                                            |

Kết quả gần nhất vẫn được giữ qua điều hướng như hành vi đã thống nhất ở mốc 1 + 2.
**Đặt lịch khác** mở draft mới; **Đặt lại demo** xóa draft/kết quả và giữ sinh viên
demo nếu đã đăng nhập. **Đăng xuất** xóa cả phiên và kết quả. `/booking` vẫn mở trực
tiếp được mà không cần đăng nhập. Không lưu state vào storage/cookie hay tạo token.

## Kiểm chứng ngày 15/10/2026

Các số liệu dưới đây ghi nhận bản mốc 4. Mốc 5 bổ sung danh sách lịch đã đặt,
chi tiết từng lịch và focus bàn phím. Kiểm chứng mới nhất với dependencies cài
sạch nằm trong [mốc 6: kiểm thử và CI](frontend-testing.md).

Kiểm tra local trên Windows, Node.js 24.19.0, npm 12.2.0 và dependencies hiện có
từ lockfile:

- Format, lint, typecheck và production build đạt.
- **56 unit/component tests trong 8 files** đạt, gồm 8 reducer tests, 3 resource
  tests và test gửi trùng/lỗi rồi retry cùng draft.
- **52 E2E tests** đạt trên desktop/mobile Chromium, không retry. Trong đó có
  kiểm tra browser dùng UTC và America/Los_Angeles, ngày 14 và 20/10/2026,
  review/result hiển thị đúng giờ Việt Nam và địa điểm null.

Chạy lại bằng các scripts trong [hướng dẫn frontend](frontend-bootstrap.md).
Đây là kiểm chứng working tree chưa commit, chưa phải kết quả CI của commit mới.
Không đổi backend, dependencies, lockfile hoặc workflow; không chạy lại `npm ci`
hay backend tests trong mốc này. Không tạo lại thư mục screenshot.

Issue #7 tiếp tục mở cho phần hoàn thiện tương tác/kết quả và nghiệm thu còn lại.
