# Sinh viên tự kiểm chứng BT3 — 02/11/2026

Người thực thi: sinh viên, theo terminal output gửi trong chat. Codex đối chiếu
file trên máy, đóng gói và biên tập báo cáo; không chạy lại các test để thay thế
lần thực hành này. Source15526cbac4b48ae8c6f2430e8cd50931fb78872b, branch local
codex/student-recheck-20261102-083514. Hai lượt dùng cùng host Windows và cùng
MySQL8.4 local; mỗi fixture tạo schema UUID riêng.

| Kiểm chứng | Kết quả | Nguồn |
|---|---|---|
| Export + OpenAPI |41passed/45.39s|captured functional.txt trong ZIP|
| TC18 riêng |1passed/4.09s|captured tc18.txt trong ZIP|
| Coverage run |41passed/69.38s|terminal sinh viên cung cấp; JSON đối chiếu riêng|
| Export service |34/34 statements,8/8arcs|coverage-full.json trong ZIP|
| Diff Python coverage |50/50executable changed lines,10/10arcs|diff-coverage.json|
| Mutation M1–M5 |4KILLED/1SURVIVED; M3 bảo vệ dư thừa|mutants.json và raw logs|
| Restore |source diff trống;5mutants không là ancestor source15526cb|Git/local metadata đã đối chiếu|
| Pool5+5/queue0.5s |312/560HTTP503;248success; acceptanceFAIL/10.63s|raw JSON + pytest log trong ZIP|
| OpenAPI/gateway |OpenAPI diff trống;31business operations|terminal sinh viên cung cấp|

Pool timestamp2026-11-02T01:44:45Z, tức08:44:45 Asia/Bangkok. Mỗi phase80requests:

| Phase | Lỗi503 | p95 success(s) | Auth pool queue timeouts |
|---|---:|---:|---:|
|cold|49|0.702|49|
|warmup80|45|0.834|45|
|warm1|45|0.774|45|
|warm2|42|0.729|42|
|warm3|41|0.781|41|
|warm4|45|0.653|45|
|warm5|45|0.619|45|

Cả312lỗi đều tại auth trước export, leaf module sqlalchemy.exc và isinstance
TimeoutError của SQLAlchemy được logger xác nhận. Mọi response thành công có đúng
100rows và exact owned ID set. Pool_size5/max_overflow5, queue0.5s,
connect/read/request budgets1/1/3s. NFR-03 vẫnFAIL. Đây là một lượt mới, không phải
một phép đo cải thiện so với Codex302/560; không đổi setting/code để ép PASS.
Đây là phép lặp trên cùng môi trường, không phải xác nhận chéo bằng môi trường
độc lập. Trong mỗi lượt,80client chung một phiên đăng nhập thật; fixture của lượt
khác đăng nhập mới và tạo schema UUID mới, không tái dùng một cookie qua mọi lượt.

### Tên nhánh và đường dẫn tra cứu

| Tên nhánh | Phạm vi | Dùng để tra cứu |
|---|---|---|
| `codex/student-recheck-20261102-083514` | Local, chưa push | Bản sinh viên thực hành tại source [15526cb](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/15526cbac4b48ae8c6f2430e8cd50931fb78872b); không tạo link GitHub cho tên local |
| `codex/bt3-23001952-vt071-mutants-verified` | Tên local do mutation helper tạo | Tên gốc còn trong metadata thô, không phải nhánh công khai để bấm vào |
| `codex/bt3-23001952-vt071-student-mutants-20261102` | Nhánh thí nghiệm đã công bố | [Mở nhánh công khai](https://github.com/4PMs-3Workers/Production-Readiness-Lab/tree/codex/bt3-23001952-vt071-student-mutants-20261102), gồm các commit mutant và commit restore |

[mutants.json](mutants.json) là bản chỉ mục đã chuẩn hóa tên nhánh công khai;
`local_experimental_branch` giữ tên local ban đầu. [summary.json](summary.json)
dùng `student_local_branch` và ghi rõ phạm vi local, kèm link source và nhánh đã
công bố. Bản gốc `mutants/review-mutants.json` bên trong ZIP vẫn giữ nguyên tên
local và bytes tại thời điểm chạy; manifest kiểm tra bản thô đó, không phải chỉ mục
được chuẩn hóa. Commit IDs, kết quả test và raw archive không thay đổi.

### Mutation của lần sinh viên chạy

Các commit mới được giữ trên branch thí nghiệm riêng:
[student-mutants-20261102](https://github.com/4PMs-3Workers/Production-Readiness-Lab/tree/codex/bt3-23001952-vt071-student-mutants-20261102).
HEAD721ffc04cf51fb80ed9a9312b2a7ffdc66392995 đã restore source; không merge vào nhánh nộp.

| Mutant | Commit local được công bố | Outcome |
|---|---|---|
|owner|[0203961](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/0203961808aea43b475c5f1fed6f63e5ca7d412e)|KILLED|
|overflow|[cda5c4b](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/cda5c4b7d67d70eb2dae6f898f75e074adfd5225)|KILLED|
|role_primary|[db9d1ef](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/db9d1eff35ee5651aee83a71d2a7537df3fc3374)|SURVIVED|
|role_all|[10c0fb4](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/10c0fb4b220849d3875a22131332824afc026f51)|KILLED|
|source_and_unique|[f70e9d5](https://github.com/4PMs-3Workers/Production-Readiness-Lab/commit/f70e9d5c36994621f81e9e1a18244f1e5e2d7612)|KILLED|

M5 vẫn bắt cardinality2≠1, không chỉ status; M3 sống vì query guard vẫn kiểm tra
STUDENT. DDL bỏ unique index chỉ có trong fixture-owned UUID schema của mutant.
So từng commit với source15526cb, không chỉ parent của commit nối tiếp.

### Raw evidence và kiểm tra archive

[raw-evidence.zip](raw-evidence.zip) giữ nguyên19file, tổng2041090bytes trước nén;
ZIP168192bytes. [manifest.json](manifest.json) có SHA256/size của từng file và ZIP.
Một số log Tee-Object là UTF-16 có BOM; giữ nguyên bytes, không tái tạo output.
[terminal-excerpts.txt](terminal-excerpts.txt) là trích hai captured log, không phải
phiên terminal mới do Codex chạy. [summary.json](summary.json) chỉ là dữ liệu dẫn xuất.

```powershell
Get-FileHash docs/assignments/VT-071/evidence/student-20261102/raw-evidence.zip -Algorithm SHA256
Expand-Archive -LiteralPath docs/assignments/VT-071/evidence/student-20261102/raw-evidence.zip -DestinationPath build/student-evidence-review
```

### Attribution và giới hạn

Người học đã tự chạy41tests, TC18, coverage, mutation helper và pool production,
rồi kiểm tra OpenAPI/gateway. Chưa có bằng chứng họ tự chạy historical red/M0,
NullPool, reverse order, full backend, frontend, Docker hoặc dựng PDF. Các log ban
đầu, thiết kế/patch/review và PDF vẫn ghi AI hỗ trợ. Reflection được biên tập dựa
trên việc đã làm. Ngày03/11/2026, sinh viên xác nhận đã đọc diff bốn file
production và trả lời xong bộ câu hỏi vấn đáp; xem
[checklist đã xác nhận](../../student-diff-review-guide.md). Đây là cập nhật
xác nhận hoạt động đọc/trả lời, không phải một lượt test hoặc chấm vấn đáp mới.
Người học đã cung cấp [bảy nhận xét review cá nhân](../../student-diff-review-notes.md)
về bốn file, sentinel1001 và lo ngại tải503; đồng ý với cách triển khai nhưng muốn
kiểm tra/xử lý thêm hiệu năng trước merge. Review nhóm và tách PR vẫn là bước
trước khi merge.

CI của source15526cb đã completed/success ở cả6workflow events, theo GitHub API
đối chiếu ngày02/11: [snapshot](ci-source-15526cb.json). Không gán trạng thái đó cho
commit mới chỉ lưu tài liệu/evidence.
