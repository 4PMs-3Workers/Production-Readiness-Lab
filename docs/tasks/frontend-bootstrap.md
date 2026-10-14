# Frontend bootstrap — mốc 1 + 2, issue #7

## Phạm vi bàn giao chung

- Issue: [#7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
- Nhánh: `feature/frontend-bootstrap`, bắt đầu từ `develop` (`00426bb`).
- Mốc 1 + 2 được gộp thành một bản bàn giao: Next.js/npm workspace, kiểm tra chất
  lượng và CI, DTO, fixtures, mock services, 13 preset, portal đặt lịch bốn bước
  và kết quả trả về sau xác nhận thành công.
- Frontend chạy độc lập, không yêu cầu FastAPI, MySQL hay biến môi trường.

Mở trực tiếp `/booking` để chọn nhu cầu → cố vấn → ngày/giờ → xác nhận, không cần
đăng nhập. `/booking/result` hiển thị kết quả mock mới nhất trong phiên. `/login`
vẫn là trang khung; form đăng nhập giả lập và Home là phần tiếp theo. **Issue #7
tiếp tục mở**, chưa nghiệm thu toàn bộ acceptance criteria.

Xem [hợp đồng mock services và các kịch bản](frontend-mock-services.md) và
[bộ screenshots hiện tại](../screenshots/frontend-bootstrap/README.md).

## Môi trường và dependencies

| Công cụ                            | Phiên bản                        |
| ---------------------------------- | -------------------------------- |
| Node.js                            | 24.19.0 (`.nvmrc`; nhánh LTS 24) |
| npm                                | 12.2.0 (`packageManager`)        |
| Next.js / @next/eslint-plugin-next | 16.4.0                           |
| React / React DOM                  | 19.3.0                           |
| TypeScript                         | 5.9.3                            |
| ESLint / typescript-eslint         | 10.12.0 / 8.71.2                 |
| Prettier                           | 3.9.10                           |
| Vitest / Vite                      | 5.0.4 / 8.3.4                    |
| Playwright                         | 1.64.0                           |

Dependencies trực tiếp pin phiên bản chính xác; `package-lock.json` ở gốc khóa
toàn bộ cây dependencies. TypeScript 5.9 nằm trong khoảng phiên bản được bộ
`typescript-eslint` hỗ trợ. `next build` không tự chạy lint; cần chạy lệnh lint riêng.
Flat config ở gốc sử dụng plugin Next.js chính thức, TypeScript và React Hooks;
không dùng `eslint-config-next` vì các plugin React/import bắc cầu hiện chưa hỗ trợ
ESLint 10. `tools/scripts/lint-frontend.mjs` chạy ESLint với cwd ở gốc để kiểm tra
cả frontend và tests bên ngoài workspace; lỗi hoặc warning đều trả mã thoát khác 0.

`package.json` ở gốc là npm workspace private, chỉ chứa `src/frontend`, đồng thời
pin bộ công cụ lint/format/tests/TypeScript dùng cho frontend và tests ở gốc.
Dependencies chạy ứng dụng nằm trong package frontend. Package Python/backend
tiếp tục được quản lý bằng các file hiện có. Node/npm phải đáp ứng
`engines`; `.npmrc` bật kiểm tra engines và lưu dependencies bằng phiên bản chính xác.
`.gitattributes` giữ LF cho frontend và các file công cụ để checkout trên Windows
vẫn qua cùng kiểm tra format như Linux CI.

Nếu máy có `node` nhưng không tìm được `npm`, cấu hình Node.js/npm đầy đủ trên PATH
trước khi chạy các lệnh bên dưới. Không commit đường dẫn runtime riêng của máy.

Runtime Node do Codex cung cấp có thể chỉ nằm trên PATH của tiến trình Codex,
không có trên PATH terminal PowerShell bên ngoài. Python `.venv` không cung cấp
Node.js hoặc npm.

## Cài đặt và chạy

Chạy từ gốc repository, không chạy `npm install` trong một thư mục frontend riêng:

```bash
node --version
npm --version
npm ci
npm --workspace src/frontend run dev
```

Dev server: `http://127.0.0.1:3000`. Nếu cổng 3000 đang được dùng, xem URL do server
in ra hoặc chọn cổng bằng `npm --workspace src/frontend run dev -- --port 3001`.

### PowerShell chưa có Node/npm trên PATH

Nếu dependencies đã được cài trong workspace hiện tại, chạy:

```powershell
.\tools\scripts\start-frontend.ps1
# Chọn cổng khác khi cần:
.\tools\scripts\start-frontend.ps1 -Port 3001
```

Script tìm `node.exe` trên PATH, trong thư mục Node.js chuẩn hoặc runtime Codex
dưới USERPROFILE. Script kiểm tra phiên bản Node, thêm thư mục Node vào PATH chỉ
trong lúc chạy server, rồi phục hồi PATH và thư mục hiện tại. Script gọi Next.js
đã cài trực tiếp, không phụ thuộc bản npm tạm trong `build`.

Nếu chưa có Node hoặc dependencies, cài Node.js 24 LTS/npm, mở terminal mới và
chạy `npm ci` từ gốc. Script không tự tải hoặc cài phần mềm.

Chạy production local:

```bash
npm --workspace src/frontend run build
npm --workspace src/frontend run start
```

Không có `.env` bắt buộc trong bản demo. Các file `.env` local, `node_modules`, `.next`,
TypeScript cache và test reports nằm ngoài Git.

## Routes và cấu trúc

| Route             | Nội dung hiện tại                                                                            |
| ----------------- | -------------------------------------------------------------------------------------------- |
| `/`               | Chuyển tới `/login`.                                                                         |
| `/login`          | Giới thiệu và đường xem trước đặt lịch; chưa nhận credentials.                               |
| `/booking`        | Portal bốn bước với dữ liệu mock, kiểm tra lựa chọn, loading, empty, lỗi, retry và xung đột. |
| `/booking/result` | Kết quả mock sau xác nhận thành công; trạng thái rỗng khi chưa có kết quả trong phiên.       |

- `src/frontend/app`: layout/pages, metadata tiếng Việt, CSS và provider demo ở root.
- `src/frontend/components`: app shell cho login, portal shell cho booking, navigation,
  demo disclosure và page wrapper.
- `src/frontend/features/booking`: wizard, các bước, result và resource hook.
- `src/frontend/features/demo`: state phiên, preset và reset dữ liệu demo.
- `src/frontend/services`: hợp đồng và mock adapters; UI không import fixtures.
- `src/frontend/types`: DTO booking và cấu hình demo.
- `src/frontend/lib/business-time.ts`: lịch và định dạng giờ `Asia/Ho_Chi_Minh`.
- `tests/frontend/components`: Vitest/React Testing Library.
- `tests/frontend/unit`: contracts, dữ liệu, thời gian, lỗi, hủy và reset services.
- `tests/frontend/e2e`: Playwright.

Kết quả gần nhất được giữ trong bộ nhớ qua điều hướng, kể cả quay qua `/login`
rồi chọn **Kết quả**. **Đặt lịch khác** bắt đầu draft mới nhưng giữ kết quả cũ
cho đến khi có lần xác nhận thành công tiếp theo hoặc **Đặt lại demo**.
Refresh hoặc mở tab mới bắt đầu phiên mới. Chưa dùng storage, cookie hay token.

TypeScript strict và alias `@/*` trỏ tới `src/frontend/*`. Vitest root là repository
để đọc tests ở gốc. ESLint và typecheck phải kiểm tra cả frontend lẫn tests/frontend.
Turbopack root được xác định rõ theo npm workspace; dùng font hệ thống để build
không phụ thuộc tải font ngoài.

Logo HUS ở đầu trang được lưu tại `src/frontend/public/branding/hus-logo.png`,
lấy nguyên file đang dùng trên [website chính thức HUS](https://hus.vnu.edu.vn/):
[`logo.png`](https://hus.vnu.edu.vn/static/hus/img/core-img/logo.png).
Hiển thị qua `next/image`, giữ tỉ lệ 661:99 và màu gốc; desktop đặt cạnh tên cổng,
mobile dùng bố cục gọn để không tràn ngang. File logo nằm trong repo nên trang
không phải tải ảnh từ website trường khi sử dụng.

`src/frontend/AGENTS.md` được `next dev` tạo để hướng dẫn đọc tài liệu đi kèm đúng
phiên bản Next.js trước khi sửa code. Giữ file này trong Git; loại khỏi formatting
vì phần hướng dẫn được framework tự quản lý. `next-env.d.ts` cũng do Next tạo,
nhưng nằm ngoài Git cùng các type files sinh trong `.next`.

## Kiểm tra

```bash
npm --workspace src/frontend run format:check
npm --workspace src/frontend run lint
npm --workspace src/frontend run typecheck
npm --workspace src/frontend run test
npm --workspace src/frontend run build
npm --workspace src/frontend exec -- playwright install chromium
npm --workspace src/frontend run test:e2e
```

`format` là script sửa định dạng; `format:check` chỉ kiểm tra. `test` chạy một lần,
không watch. Playwright tự mở production server cổng 3100, kiểm tra desktop và
mobile Chromium, rồi dừng server. Cổng 3100 cần trống. Cài browser một lần trước E2E;
trên Linux/CI dùng `playwright install --with-deps chromium`.

`typecheck` chạy `next typegen && tsc --noEmit` để tạo `next-env.d.ts` và route
types trước khi kiểm tra TypeScript; không cần mở dev server hoặc build trước.
Generated files tiếp tục nằm ngoài Git. `format:check` bao gồm cả workflow frontend.

Tests kiểm tra app shell, skip link, root redirect, ngôn ngữ `vi`, demo disclosure,
runtime errors và không tràn ngang. Unit tests kiểm tra dữ liệu, thời gian, hợp đồng
và vòng đời mock services. E2E kiểm tra luồng đặt lịch thành công, lựa chọn phụ
thuộc, OTHER, retry, booking failure, conflict recovery, stale responses và giữ
kết quả qua menu; reset hoặc refresh xóa kết quả đúng phạm vi phiên.

Không cần tạo database hoặc khởi động backend để chạy các kiểm tra frontend.
Workflow `.github/workflows/frontend.yml` chạy ngay từ mốc 1 trên pull request vào
`develop`/`main` và push vào các nhánh phát triển được cấu hình. Mỗi job checkout
sạch, dùng Node từ `.nvmrc` và npm 12.2.0, chạy `npm ci`, format, lint, typecheck,
unit/component tests và production build. Sau build, workflow cài Chromium cùng system
dependencies bằng Playwright từ lockfile rồi chạy E2E trên desktop/mobile.
Cache chỉ giữ các gói npm tải về; `node_modules` được cài lại bằng `npm ci`.
Khi job lỗi, workflow lưu `test-results/frontend/`, gồm trace E2E khi có, trong
7 ngày. Workflow backend hiện có được giữ nguyên. Tests tiếp tục mở rộng cùng
các chức năng còn lại của issue #7.

## Phần còn lại của issue #7

- Thêm login form giả lập và điều hướng vào Home; từ sidebar mở Đặt lịch.
- Kết nối login UI với `loginDemo`, bao gồm pending, lỗi credentials và lỗi dịch vụ.
- Rà soát toàn bộ acceptance criteria, trạng thái giao diện và bằng chứng review.
- Khi tích hợp backend, đối chiếu DTO/mock adapter với OpenAPI được review của #4,
  auth của #5 và API catalog/availability/appointments tương ứng.

Wizard và quản lý async state đã nằm trong bản gộp này. Kết quả hiện tại vẫn là
giả lập; chưa tạo lịch thật, xác thực thật hoặc triển khai thuật toán availability
và transaction của backend.

## Kết quả kiểm chứng

Kiểm chứng bản gộp ngày **14/10/2026** trên Windows, Node.js 24.19.0 và npm 12.2.0.
Xuất `HEAD` bằng `git archive`, chép toàn bộ file thay đổi và file mới, áp dụng
các file đã xóa; không mang theo `node_modules`, `.next` hoặc generated types.
Đã đối chiếu SHA-256 với working tree trước khi ghi kết quả.

| Kiểm tra                                | Kết quả                                                                   |
| --------------------------------------- | ------------------------------------------------------------------------- |
| Cài dependencies và kiểm tra chất lượng | Đạt: `npm ci`, format, lint và typecheck, với `next typegen` trước `tsc`. |
| Vitest / React Testing Library          | 39 unit/component tests đạt cho nền tảng và mock services.                |
| Production build                        | Đạt; bao gồm `/login`, `/booking` và `/booking/result`.                   |
| Playwright                              | 20 E2E tests đạt trên desktop và mobile Chromium.                         |
| Visual QA                               | Đạt; 14 ảnh hiện tại, không tràn ngang ở hai viewport đã chụp.            |

`npm ci` cài mới 269 packages từ lockfile. Typecheck chạy trước production build;
39 unit/component tests thuộc 4 files. Cả 20 E2E tests đạt ngay lần đầu, dùng
Chromium khớp với Playwright 1.64.0 và production server riêng, không dùng lại
preview server. Kết quả CI cũ tại `c06b45d` chỉ xác nhận scaffold trước đây.

Toàn bộ ảnh review nằm trong
[`docs/screenshots/frontend-bootstrap`](../screenshots/frontend-bootstrap/README.md).
Bộ ảnh gồm login, cả bốn bước đặt lịch, kết quả thành công và trạng thái rỗng;
mỗi trạng thái có ảnh desktop (viewport 1440 × 900) và mobile (viewport 390 × 844),
chụp toàn trang từ production build. Đây là bộ ảnh chung thay thế ảnh scaffold.
Các ảnh cũ vẫn xem được trong lịch sử Git tại `c06b45d`.

Kết quả trên được thu thập local. Mỗi commit được đẩy cần chạy lại CI; không dùng
CI của `c06b45d` làm bằng chứng cho code mới. Mô tả PR dùng phạm vi mốc 1 + 2 và
nhúng bộ ảnh mới bằng URL theo commit được review. Không chạy lại MySQL/backend
tests vì bản gộp không sửa backend; workflow backend hiện có giữ nguyên.

### Cảnh báo dependencies hiện tại

`npm audit --omit=dev` báo **0 vulnerabilities** cho dependencies production.
Audit toàn bộ cây báo **4 high-severity entries** qua chuỗi công cụ lint:
`@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces`.
Đây là các entry liên quan cùng advisory
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm),
không phải bốn lỗi độc lập trong source frontend. `braces` mới nhất trên registry
khi kiểm tra vẫn là 3.0.3, nằm trong khoảng bị advisory đánh dấu. Không áp dụng
`npm audit fix --force` để hạ phiên bản Next/plugin trái với cấu hình đã kiểm chứng.
Theo dõi bản vá upstream để cập nhật lockfile khi có bản tương thích.

Các kiểm tra xác nhận phạm vi nền tảng và mock booking đã gộp; chưa phải bằng chứng
cho đăng nhập UI, backend thật hoặc hoàn thành toàn bộ issue #7.
