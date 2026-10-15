# Frontend — nền tảng và luồng đặt lịch demo, issue #7

## Phạm vi hiện tại và các mốc

- Issue: [#7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
- Nhánh: `feature/frontend-bootstrap`, bắt đầu từ `develop` (`00426bb`).
- Mốc 1 + 2 được gộp thành một bản bàn giao: Next.js/npm workspace, kiểm tra chất
  lượng và CI, DTO, fixtures, mock services, 13 preset, portal đặt lịch bốn bước
  và kết quả trả về sau xác nhận thành công.
- Frontend chạy độc lập, không yêu cầu FastAPI, MySQL hay biến môi trường.
- Mốc 3 bổ sung form đăng nhập demo, Home và đăng xuất trên cùng nền tảng;
  xem [luồng đăng nhập và vòng đời phiên](frontend-login.md).
- Mốc 4 chuẩn hóa reducer cho draft và sửa request A → B → A;
  xem [booking wizard và kiểm chứng state](frontend-booking.md).
- Mốc 5 thêm Lịch đã đặt, chi tiết từng lịch và hoàn thiện focus bàn phím;
  xem [kết quả và lịch sử trong phiên](frontend-results.md).
- Mốc 6 hoàn thiện báo cáo E2E, guard CI và kiểm chứng từ dependencies cài sạch;
  xem [ma trận kiểm thử và CI](frontend-testing.md).
- Mốc 7 đối chiếu acceptance criteria, bổ sung hướng dẫn reviewer và API handoff;
  xem [bàn giao và nghiệm thu](frontend-handoff.md).

Mở trực tiếp `/booking` để chọn nhu cầu → cố vấn → ngày/giờ → xác nhận, không cần
đăng nhập. `/booking/result` hiển thị kết quả mock mới nhất trong phiên. `/login`
gọi `loginDemo` rồi chuyển vào `/home`; từ sidebar mở Đặt lịch tư vấn. **Issue #7
tiếp tục mở**, chưa nghiệm thu toàn bộ acceptance criteria.

Xem [hợp đồng mock services và các kịch bản](frontend-mock-services.md).
[PR #10](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/10) đã
squash-merge phần mốc 1 + 2. Bộ ảnh trong PR đó là bằng chứng lịch sử;
bộ ảnh và mô tả PR mới cho mốc 3–7 đang được chuẩn bị theo [bàn giao](frontend-handoff.md).

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
| `/login`          | Form đăng nhập demo, validation, pending, lỗi và đường xem trước đặt lịch.                   |
| `/home`           | Thông tin sinh viên giả, dịch vụ đặt lịch; trạng thái chưa đăng nhập sau refresh.             |
| `/booking`        | Portal bốn bước với dữ liệu mock, kiểm tra lựa chọn, loading, empty, lỗi, retry và xung đột. |
| `/booking/result` | Kết quả mock sau xác nhận thành công; trạng thái rỗng khi chưa có kết quả trong phiên.       |
| `/appointments`   | Tất cả lịch đã xác nhận trong phiên, mới nhất trước; có trạng thái danh sách rỗng.            |
| `/appointments/[id]` | Chi tiết đúng lịch đã chọn; giải thích khi ID không có trong phiên.                         |

- `src/frontend/app`: layout/pages, metadata tiếng Việt, CSS và provider demo ở root.
- `src/frontend/components`: app shell cho login, portal shell cho booking, navigation,
  demo disclosure và page wrapper.
- `src/frontend/features/booking`: wizard, các bước, result, history và resource hook.
  `booking-state.ts` giữ quy tắc reset/chuyển bước; resource hook chỉ hiện kết quả
  của request hiện tại, kể cả khi quay lại query đã chọn trước đó.
- `src/frontend/features/login`, `features/home`: form đăng nhập và trang chủ demo.
- `src/frontend/features/demo`: state phiên, preset và reset dữ liệu demo.
- `src/frontend/services`: hợp đồng và mock adapters; UI không import fixtures.
- `src/frontend/types`: DTO booking và cấu hình demo.
- `src/frontend/lib/business-time.ts`: lịch và định dạng giờ `Asia/Ho_Chi_Minh`.
- `tests/frontend/components`: Vitest/React Testing Library.
- `tests/frontend/unit`: contracts, dữ liệu, thời gian, lỗi, hủy và reset services.
- `tests/frontend/e2e`: Playwright.

Mọi lịch xác nhận thành công được giữ trong bộ nhớ qua điều hướng. Kết quả gần
nhất lấy từ đầu danh sách; mở một lịch cũ không thay đổi kết quả này. **Đặt lịch
khác** bắt đầu draft mới và giữ cả danh sách lẫn kết quả gần nhất.
Refresh hoặc mở tab mới bắt đầu phiên mới. Chưa dùng storage, cookie hay token.
Đăng xuất xóa cả sinh viên demo và lịch đã đặt; đổi kịch bản hoặc Đặt lại demo chỉ
reset dữ liệu đặt lịch, giữ danh tính nếu đã đăng nhập.

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
kết quả qua menu; reset hoặc refresh xóa kết quả đúng phạm vi phiên. Hồi quy P2
kiểm tra rời trang khi đang xác nhận chậm, quay lại bằng client navigation,
đặt lịch mới và tiếp tục sử dụng bộ chọn kịch bản/reset demo.

Không cần tạo database hoặc khởi động backend để chạy các kiểm tra frontend.
Workflow `.github/workflows/frontend.yml` chạy ngay từ mốc 1 trên pull request vào
`develop`/`main` và push vào các nhánh phát triển được cấu hình. Mỗi job checkout
sạch, dùng Node từ `.nvmrc` và npm 12.2.0, chạy `npm ci`, format, lint, typecheck,
unit/component tests và production build. Sau build, workflow cài Chromium cùng system
dependencies bằng Playwright từ lockfile rồi chạy E2E trên desktop/mobile.
Cache chỉ giữ các gói npm tải về; `node_modules` được cài lại bằng `npm ci`.
Sau khi E2E chạy, workflow lưu HTML report tại `playwright-report/frontend/` cả
khi đạt và khi lỗi; khi lỗi có thêm ảnh/trace trong `test-results/frontend/`.
Artifact giữ 7 ngày. CI chặn `only`, dùng 2 workers và báo lỗi khi test flaky dù
retry thành công. Workflow backend hiện có được giữ nguyên. Tests tiếp tục mở rộng cùng
các chức năng còn lại của issue #7.

## Phần còn lại của issue #7

- Đã rà acceptance criteria local trong [bàn giao mốc 7](frontend-handoff.md);
  còn xuất bản PR mới, nhúng ảnh, kiểm tra CI trên SHA mới, approval và merge.
- Khi tích hợp backend, đối chiếu DTO/mock adapter với OpenAPI được review của #4,
  auth của #5 và API catalog/availability/appointments tương ứng.

Wizard và quản lý async state đã nằm trong bản gộp này. Kết quả hiện tại vẫn là
giả lập; chưa tạo lịch thật, xác thực thật hoặc triển khai thuật toán availability
và transaction của backend.

## Kết quả kiểm chứng

Kết quả bên dưới ghi nhận bản gộp mốc 1 + 2 và sửa P2 ngày 14/10/2026.
Kiểm chứng cho mốc 3 nằm trong [tài liệu đăng nhập demo và Home](frontend-login.md).
Kiểm chứng mốc 4 nằm trong [tài liệu booking wizard và state](frontend-booking.md).
Kiểm chứng mốc 5 nằm trong [tài liệu kết quả và lịch đã đặt](frontend-results.md).
Kiểm chứng mới nhất từ clean install của mốc 6 nằm trong [tài liệu kiểm thử và CI](frontend-testing.md).

Kiểm chứng ngày **14/10/2026** trên Windows, Node.js 24.19.0 và npm 12.2.0.
Bản gộp ban đầu được kiểm tra trên source sạch: xuất `HEAD` bằng `git archive`,
chép file thay đổi/mới và áp dụng file đã xóa; không mang theo `node_modules`,
`.next` hoặc generated types, rồi đối chiếu SHA-256 với working tree.
Bản sửa P2 được kiểm tra lại trên workspace với cùng dependencies và lockfile;
bảng dưới ghi kết quả mới nhất.

| Kiểm tra                       | Kết quả                                                                     |
| ------------------------------ | --------------------------------------------------------------------------- |
| Cài dependencies               | `npm ci` đạt trên bản sao sạch ban đầu; P2 không đổi dependencies/lockfile. |
| Format, lint và typecheck      | Đạt sau sửa P2; `next typegen` chạy trước `tsc`.                            |
| Vitest / React Testing Library | 40 unit/component tests đạt, gồm cleanup muộn của submission cũ.            |
| Production build               | Đạt; bao gồm `/login`, `/booking` và `/booking/result`.                     |
| Playwright                     | 22 E2E tests đạt trên desktop và mobile Chromium.                           |
| Visual QA                      | Đạt; đã chụp 14 ảnh, không tràn ngang ở hai viewport đã kiểm tra.           |

`npm ci` ban đầu cài mới 269 packages từ lockfile. Typecheck chạy trước production
build; 40 unit/component tests thuộc 5 files. Cả 22 E2E tests đạt không cần retry, dùng
Chromium khớp với Playwright 1.64.0 và production server riêng, không dùng lại
preview server. Kết quả CI cũ tại `c06b45d` chỉ xác nhận scaffold trước đây.

Test điều hướng trong khi xác nhận chậm đã thất bại trên cả desktop/mobile ở
production build `94fc319`, rồi đạt sau sửa P2. Wizard hủy request và giải phóng
trạng thái bận khi unmount; `finally` cũng giải phóng khi bị hủy. Provider chỉ
cho request hiện đang sở hữu trạng thái bận xóa nó. Component test cố ý cho
request cũ báo hủy sau khi request mới bắt đầu, để xác nhận request mới vẫn khóa
nút gửi, Back, selector và reset cho đến khi hoàn tất.

Ảnh review được nhúng trực tiếp trong mô tả
[PR #10](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/10).
Bộ ảnh đã chụp gồm login, cả bốn bước đặt lịch, kết quả thành công và trạng thái rỗng;
mỗi trạng thái có ảnh desktop (viewport 1440 × 900) và mobile (viewport 390 × 844),
chụp toàn trang từ production build. Thư mục screenshot đã được xóa khỏi cây mã
nguồn hiện tại; bộ ảnh vẫn xem được tại
[commit `7505019`](https://github.com/4PMs-3Workers/Production-Readiness-Lab/tree/750501984778a4de3d4a16b117aa727973954bbd/docs/screenshots/frontend-bootstrap).

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

Các kiểm tra lịch sử ở trên xác nhận phạm vi nền tảng và mock booking đã gộp;
chưa phải bằng chứng cho backend thật hoặc hoàn thành toàn bộ issue #7.
