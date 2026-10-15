# Mốc 6 — Kiểm thử hành vi và CI

Hoàn thiện kiểm chứng frontend cho
[issue #7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
Workflow đã được thêm từ mốc 1 theo yêu cầu review. Mốc 6 bổ sung báo cáo,
chẩn đoán lỗi và kiểm chứng lại toàn bộ trên dependencies cài sạch.

## Thay đổi

- Playwright sinh báo cáo HTML tại `playwright-report/frontend/`, không tự mở trình duyệt.
- Khi test lỗi, lưu ảnh tại thời điểm lỗi và trace; tất cả E2E dùng fixture chung
  để phát hiện lỗi JavaScript không được xử lý, kể cả khi các assertion UI đã đạt.
- Trong CI, dùng 2 workers, chặn `test.only` và coi test flaky là lỗi. Một lần retry
  vẫn được giữ để chẩn đoán, nhưng lần retry thành công không làm job xanh.
- Vitest cũng chặn `only` trong CI. Các bài kiểm tra hiện tại không có `skip`,
  `fixme` hoặc ca chỉ chạy riêng.
- Thêm regression test: khi đã có kết quả gần nhất, ID lịch không tồn tại vẫn
  hiện trạng thái không tìm thấy, không thay bằng dữ liệu của lịch khác.

Frontend workflow giữ thứ tự `npm ci` → format → lint → typecheck → unit/component
tests → production build → cài Chromium/system dependencies → E2E. Chromium lấy
theo Playwright trong lockfile; E2E tự mở production server riêng tại cổng 3100.

Sau bước E2E, GitHub Actions lưu báo cáo cả khi đạt và khi lỗi trong artifact
`frontend-e2e-<run_id>-<run_attempt>`, giữ 7 ngày. Khi lỗi, artifact có thêm ảnh
và trace. Nếu chưa chạy đến E2E hoặc workflow bị hủy, không xuất báo cáo E2E.
Dependencies, lockfile và workflow backend được giữ nguyên.

## Đối chiếu các ca kiểm thử

Các đường dẫn dưới đây tính từ gốc repo trong `tests/frontend/`.

| Hành vi                                                   | Bằng chứng                                                                                                  |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Login → Home → Booking → Result                           | `e2e/login-flow.spec.ts`; component tests kiểm tra chỉ gọi service một lần.                                 |
| Invalid login/lỗi dịch vụ và retry                        | `components/login-flow.test.tsx`, `e2e/login-flow.spec.ts`.                                                 |
| Đổi nhu cầu/cố vấn/ngày xóa lựa chọn phụ thuộc            | `unit/booking-state.test.ts`, `e2e/booking-flow.spec.ts`, `e2e/booking-states.spec.ts`.                     |
| Response cũ và A → B → A                                  | `components/booking-resource.test.tsx`, `unit/mock-service-requests.test.ts`, `e2e/booking-states.spec.ts`. |
| OTHER bắt buộc, giữ nội dung nhiều dòng                   | Reducer tests; `e2e/booking-flow.spec.ts`, `e2e/appointments.spec.ts`.                                      |
| Gửi trùng, pending, rời trang khi đang gửi                | `components/login-flow.test.tsx`, `components/booking-submission.test.tsx`; E2E login/booking.              |
| Loading/empty/error/retry từng nguồn                      | `e2e/booking-states.spec.ts`; retry nhu cầu trong `e2e/booking-flow.spec.ts`.                               |
| Booking lỗi giữ draft, không dựng success                 | `components/booking-submission.test.tsx`, `e2e/booking-flow.spec.ts`.                                       |
| Conflict giữ phần còn lại và chọn giờ mới                 | Reducer tests và `e2e/booking-flow.spec.ts`.                                                                |
| Múi giờ, clock gần biên ngày, D+1 và D+7                  | `unit/business-time.test.ts`, `unit/mock-services.test.ts`; E2E browser UTC và America/Los_Angeles.         |
| Result trực tiếp/refresh, ID hết phiên hoặc sai           | `e2e/frontend-smoke.spec.ts`, `e2e/appointments.spec.ts`, `components/demo-history.test.tsx`.               |
| Địa điểm null ở review/result                             | `e2e/booking-states.spec.ts`, `e2e/booking-flow.spec.ts`.                                                   |
| Mobile, skip link, Tab/Shift+Tab/Space/Enter, focus lỗi   | `e2e/frontend-smoke.spec.ts`, `e2e/appointments.spec.ts`, E2E loading errors.                               |
| Lưu nhiều lịch, mở chi tiết cũ, reset/logout              | `components/demo-history.test.tsx`, `e2e/appointments.spec.ts`.                                             |
| Chặn giao khoảng dù khác nhu cầu/cố vấn; tiếp giáp hợp lệ | `unit/booking-overlap.test.ts`, `e2e/booking-overlap.spec.ts`, `e2e/appointments.spec.ts`.                  |

## Kiểm chứng source sạch ngày 15/10/2026

Bản kiểm tra là working tree chưa commit, dựa trên `fe97af9`, gồm thay đổi mốc 3–6.
Sao chép 159 file tracked/untracked không bị ignore sang thư mục tạm; đối chiếu
SHA-256 từng file với workspace. Không chép `node_modules`, `.next`, generated
types, cache, `.env` local hoặc reports. Sau đó mới chạy `npm ci` từ lockfile.

Môi trường Windows, Node.js **24.19.0**, npm **12.2.0**, `CI=true`. Đây là kiểm
chứng local với cấu hình CI, không thay thế lần chạy GitHub Actions trên Linux.

| Kiểm tra                 | Kết quả                                                                             |
| ------------------------ | ----------------------------------------------------------------------------------- |
| Cài dependencies sạch    | `npm ci` đạt; cài mới 269 packages.                                                 |
| Format, lint, typecheck  | Đạt; typegen chạy trước TypeScript.                                                 |
| Unit/component           | 70 tests trong 10 files đạt.                                                        |
| Production build         | Đạt, gồm Home, Booking, Result, Lịch đã đặt và route chi tiết động.                 |
| Chromium                 | Lệnh install từ Playwright của bản cài sạch đạt; dùng browser cache khớp phiên bản. |
| E2E production           | 66 tests đạt trên desktop/mobile, 2 workers, không retry; 1,8 phút.                 |
| Báo cáo và chẩn đoán lỗi | Đã xác minh HTML report, ảnh lỗi và trace bằng ca lỗi chủ động riêng.               |

Ca kiểm tra chẩn đoán riêng cố ý phát sinh một lỗi JavaScript ở lần đầu, rồi
thành công khi retry. Fixture đã phát hiện lỗi, ảnh/trace/HTML được tạo, và CLI
vẫn trả exit code 1 do `failOnFlakyTests`. Ca này nằm trong thư mục tạm, không
thuộc 66 E2E của ứng dụng và không đưa vào bộ tests được commit.
Báo cáo HTML của 66 ca đạt được giữ trong `playwright-report/frontend/` ngoài Git.
Đã dọn bản sao source, dependencies cài thử và ca chẩn đoán tạm. Các thay đổi
runtime/tests/config được đối chiếu lại SHA-256 với bản đã kiểm tra trước khi dọn.

Không chạy lại backend/MySQL tests trong mốc này. Đã kiểm tra workflow
`.github/workflows/backend-models.yml` và lockfile không có thay đổi. Các kiểm
tra backend hiện có tiếp tục chạy độc lập khi push/PR. Issue #7 giữ mở để review
và nghiệm thu; chưa commit, push hoặc sửa mô tả PR trong mốc này.

## Chạy lại và xem báo cáo

Từ gốc repository:

```bash
npm ci
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test
npm --workspace src/frontend run build
npx --no-install playwright install chromium
npm --workspace src/frontend run test:e2e
npx --no-install playwright show-report playwright-report/frontend
```

Trên Linux CI, cài browser bằng `playwright install --with-deps chromium`.
Để kiểm tra không dựa vào retry, thêm `-- --retries=0` sau `run test:e2e`.
Để mở trace của ca lỗi, dùng `npx --no-install playwright show-trace` rồi đường
dẫn `trace.zip` mà output E2E in ra. Reports/ảnh/trace đều nằm ngoài Git;
không tạo lại `docs/screenshots`.
