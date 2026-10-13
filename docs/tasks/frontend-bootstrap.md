# Frontend bootstrap — issue #7

## Mốc 1: nền tảng và các trang khung

- Issue: [#7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
- Nhánh: `feature/frontend-bootstrap`, bắt đầu từ `develop` (`00426bb`).
- Phạm vi hiện tại: thiết lập Next.js, npm workspace, kiểm tra chất lượng mã,
  cấu hình component/E2E tests và ba route khung.
- Frontend chạy độc lập, không yêu cầu FastAPI, MySQL hay biến môi trường.

Đăng nhập giả lập, services/fixtures, booking wizard và kết quả booking thực hiện
ở những mốc sau. Issue #7 chưa hoàn tất tại mốc 1. Trang kết quả hiện chỉ hiển thị
trạng thái chưa có lịch hẹn.

## Môi trường và dependencies

| Công cụ | Phiên bản |
|---|---|
| Node.js | 24.19.0 (`.nvmrc`; nhánh LTS 24) |
| npm | 12.2.0 (`packageManager`) |
| Next.js / @next/eslint-plugin-next | 16.4.0 |
| React / React DOM | 19.3.0 |
| TypeScript | 5.9.3 |
| ESLint / typescript-eslint | 10.12.0 / 8.71.2 |
| Prettier | 3.9.10 |
| Vitest / Vite | 5.0.4 / 8.3.4 |
| Playwright | 1.64.0 |

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

Chạy production local:

```bash
npm --workspace src/frontend run build
npm --workspace src/frontend run start
```

Không có `.env` bắt buộc ở mốc 1. Các file `.env` local, `node_modules`, `.next`,
TypeScript cache và test reports nằm ngoài Git.

## Routes và cấu trúc

| Route | Nội dung hiện tại |
|---|---|
| `/` | Chuyển tới `/login`. |
| `/login` | Giới thiệu và đường xem trước khung đặt lịch; chưa nhận credentials. |
| `/booking` | Khung bốn bước nhu cầu → nhân viên → ngày/giờ → xác nhận. |
| `/booking/result` | Trạng thái chưa có kết quả, liên kết quay lại đặt lịch. |

- `src/frontend/app`: layout/pages Server Components, metadata tiếng Việt và CSS.
- `src/frontend/components`: app shell, navigation, demo disclosure và page wrapper.
- `features`, `services`, `types`, `lib`, `hooks`: giữ vị trí theo scaffold repository,
  dành cho những mốc tiếp theo.
- `tests/frontend/components`: Vitest/React Testing Library.
- `tests/frontend/e2e`: Playwright.

TypeScript strict và alias `@/*` trỏ tới `src/frontend/*`. Vitest root là repository
để đọc tests ở gốc. ESLint và typecheck phải kiểm tra cả frontend lẫn tests/frontend.
Turbopack root được xác định rõ theo npm workspace; dùng font hệ thống để build
không phụ thuộc tải font ngoài.

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

Smoke tests kiểm tra root redirect, ngôn ngữ `vi`, demo disclosure, điều hướng qua
ba trang, direct navigation/refresh trang result, runtime errors, keyboard skip link
và không tràn ngang trên mobile. Component test kiểm tra app shell và skip target.

Không cần tạo database hoặc khởi động backend để chạy các kiểm tra frontend.
Workflow frontend đầy đủ và tests nghiệp vụ sẽ được thêm trong mốc 6; workflow backend
hiện có tiếp tục giữ nguyên ở mốc 1.

## Bàn giao cho mốc tiếp theo

Mốc 2 thêm DTO, fixtures và mock services có scenarios tái hiện được. Mốc 3 thêm
login form và fake student context; mốc 4 thêm wizard và quản lý async state.
Sau này DTO/mock adapter cần đối chiếu OpenAPI được review của #4, auth của #5 và
API catalog/availability/appointments tương ứng.

## Kết quả kiểm chứng

Đã kiểm chứng ngày **13/10/2026** trên Windows, Node.js 24.19.0 và npm 12.2.0:

| Kiểm tra | Kết quả |
|---|---|
| `npm ci` với lockfile cuối trên bản sao sạch | Đạt; bản sao không mang theo `.next`, `node_modules` hay generated types. |
| Format, lint và typecheck trên bản sao sạch | Đạt; lint không có warning. |
| Vitest / React Testing Library | 1 component test đạt. |
| Production build trên bản sao sạch | Đạt; `/login`, `/booking`, `/booking/result` được prerender. |
| Playwright trên production build sạch | 4 smoke tests đạt: hai ca chạy trên desktop và mobile Chromium. |
| Dev server | Khởi động thành công, `/login` trả HTTP 200. |
| Visual QA | Đã xem ảnh login desktop và booking mobile; không tràn ngang hoặc cắt nội dung. |
| `git diff --check` | Đạt. |

Bản sao sạch và ảnh QA local nằm trong `build/frontend-bootstrap`, được gitignore.
Windows sandbox chặn socket local, một số thao tác ghi output và liên kết workspace;
những kiểm tra bị ảnh hưởng đã chạy lại ngoài sandbox. Không cần đổi source để né
các giới hạn đó. Không chạy lại MySQL/backend tests vì mốc này không sửa backend;
workflow backend hiện có giữ nguyên. Chưa có kết quả frontend CI remote ở mốc 1.

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

Các tests nền tảng này xác nhận mốc 1; chưa phải bằng chứng hoàn thành luồng
nghiệp vụ hoặc toàn bộ issue #7.
