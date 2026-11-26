# AI review follow-up — 26/11/2026

Source before changes: `54bb94c9d31708dc8ea78d98a812bb8451b78bc8`. Review: https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/66#pullrequestreview-6041610768 . Người thực hiện và review: Codex AI; chưa có xác nhận sinh viên đã review.

## Contract và sửa đổi

Resume cùng immutable candidate sau migration phải hoạt động từ state trên đĩa và runtime mới của CLI. Runtime phải chọn manifest/Compose overlay trước mọi schema probe, kể cả probe được gọi khi Python tính argument cho preflight. Chỉ candidate catalog và revision đầy đủ mới hợp lệ; không nới schema allowlist.

Rollback bị từ chối ở bước read-only phải giữ operation deploy, target, history và yêu cầu recovery để resume được khi lỗi hết. Đưa database identity check lên trước `_begin`, chuyển identity đã đọc vào `_restore`; các kiểm tra bảo toàn identity sau rollout vẫn giữ nguyên.

State v1 trước migration phải giữ wire encoding cũ: serializer bỏ `migration_started` khi false; true vẫn được lưu cho retry sau migration. Không tuyên bố reader cũ đọc được state sau migration có marker true. Không thay version hoặc bỏ marker cần thiết.

Malformed JSON test giữ nguyên status 201/409 để decoder thực sự được gọi. `.gitattributes` quy định LF cho hai Dockerfile và images lock; không thay hashing production hay thêm accepted hash.

## RED → patch → GREEN

Working directory: `D:/Production-Readiness-Lab-bt3-23001890-vt069`; Python 3.11.4, isolated `.venv`. Test-first trên source trước thay đổi: `python -m pytest tests/backend/unit/test_rollout_controller.py tests/load/test_vt069_quota_load.py -q --tb=short`: **4 failed, 11 passed**, exit 1. Hai resume failures kết thúc `invalid-manifest`; database identity refusal không có refusal và mất pending deploy; JSON có field `migration_started: false`. Đây là failure hành vi, không phải import/setup. Đổi status malformed JSON không làm current code fail vì implementation accounting trước đó đã đúng; test trước đó thiếu khả năng phát hiện bug.

Sau patch: tất cả `test_rollout*.py` cộng `test_release_manifest.py`, `test_backup_evidence.py`, `tests/load/test_vt069_quota_load.py`: **251 passed in 42.87s**, exit 0. Lệnh PowerShell: `$cases=@(Get-ChildItem tests/backend/unit/test_rollout*.py | ForEach-Object {$_.FullName}); .venv/Scripts/python.exe -m pytest @cases tests/backend/unit/test_release_manifest.py tests/backend/unit/test_backup_evidence.py tests/load/test_vt069_quota_load.py -q --tb=short`. Sau sửa fixture scratch directory và Optional assertions, targeted controller/load: **15 passed in 4.79s**, exit 0.

Black check và Ruff trên 4 Python files thay đổi: pass; Pyright 1.1.407: **0 errors, 0 warnings, 0 informations**, exit 0. `git diff --check`: pass. AI review phát hiện fixture runtime mới dùng root thay scratch, tạo overlay trong repo; đã sửa dùng scratch và xóa đúng artifact do test tạo. Không stage `tmp/` hoặc thay đổi workspace chính.

## Giới hạn

Đây là deterministic controller regression với effects giả; không chứng minh live Docker resume. Không chạy lại thí nghiệm quota/performance vì patch chỉ đổi orchestration/tests. PDF và evidence cũ giữ nguyên snapshot. CI trên SHA mới phải được đọc riêng sau push; không dùng xanh của SHA cũ thay thế. GitHub review báo conflict với develop; chưa tuyên bố PR mergeable hay được phép merge.

AI Usage Log bổ sung: prompt thực tế của người dùng yêu cầu thực hiện review mới và tránh lỗi CI cũ; dùng output code/tests/ghi chú này. Bác bỏ assumption runtime reuse đủ cho CLI resume và default field đồng nghĩa backward reader compatibility, dựa vào RED ở trên. Nguyên tắc đã bổ sung trong root AGENTS.md.

## CI phát hiện và sửa thứ tự validation

Run [45753103992](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/45753103992) trên `95d93e4` thất bại: `python -m pytest tests/rollout -q --tb=short --junitxml=build/rollback-tests.xml`, 1 failed, 3 passed in 267.53s. Assertion path: rehearsal `preflight_refusals` gọi deploy manifest sai cấu hình; `runtime.select` gọi renderer trước `consumer.verify`, ném `ReleaseError: incompatible-configuration` ra ngoài thay vì ghi refusal đúng contract. Đây là regression do patch đầu của AI, không phải lỗi môi trường. Unit fixture không render cấu hình sai nên chưa phát hiện.

Sửa: truyền state/resume vào `_preflight`, tính schema revisions sau `consumer.verify`, runtime select, plan và vendor images. Không chọn runtime ngoài preflight. Test mới `test_invalid_configuration_is_verified_before_select` yêu cầu verify từ chối trước select và state giữ đúng reason. Giữ cả hai tests fresh-runtime resume. Suite sau sửa: **252 passed in 44.23s**, exit 0; Black/Ruff pass, Pyright **0 errors/warnings/informations**, exit 0. Chưa suy ra live rehearsal pass từ unit suite này; cần run CI mới. Đây là ví dụ AI-generated patch hợp lý nhưng cần kiểm tra cả negative manifest path. Không tăng timeout, skip hoặc nới allowlist.

## Bổ sung kiểm tra policy trước rollback

Nguồn trước bản sửa này là `fb26cc12911e03854ddf0822daba0ab2145d449a`. Codex AI thực hiện bản sửa sau yêu cầu của người dùng. Nhánh policy guard vẫn có thể trả `rollback-incompatible` trước khi dừng API nhưng sau khi operation deploy đã bị thay bằng rollback. Regression mới đi qua guard thật với các thao tác Docker được thay bằng effects xác định, kiểm tra state trên đĩa và resume bằng runtime mới.

Ba trường hợp test mới đã thất bại trên nguồn cũ ở các assertion về operation, refusal và thứ tự kiểm tra. Bản sửa chuẩn bị manifest, định danh DB và policy snapshot trước khi ghi operation rollback. Guard nhận lại snapshot đã kiểm tra, dừng API sau khi ý định rollback đã được lưu, kiểm tra lại policy và khởi động API cũ nếu policy đổi trong khoảng drain. Test bao phủ refusal rồi resume, rollback thành công và policy đổi sau drain.

Review độc lập phát hiện ngân sách khởi động lại API cần được kiểm tra lần nữa ngay trước stop, vì việc lưu state có thể tiêu tốn thời gian sau preflight. Test đồng hồ xác định giảm thời gian còn lại từ 86 xuống 84 giây đã thất bại trước khi bổ sung recheck. Khi thiếu ngân sách, guard không phát lệnh stop hoặc start.

Nhóm `test_rollout*.py`, release manifest, backup evidence và regression load đã qua **256 tests**, exit 0. Black và Ruff trên bốn tệp Python đã sửa đều qua. Pyright với môi trường dependency của repository báo **0 errors, 0 warnings, 0 informations**. Test runtime/controller dùng effects giả và không chứng minh live Docker rollback. Kết quả CI phải được kiểm tra trên commit mới sau push.

Lượt pytest rộng đầu tiên dừng ở collection do môi trường local thiếu `boto3`, `botocore` và package `offhost_probe`. Các dependency được cài theo pin hiện có rồi chạy lại. Mã ứng dụng và dependency pins của repository không đổi trong bước thiết lập môi trường này.

Lượt đầy đủ sau thiết lập đã qua **1.881 tests**, **648 skipped** theo điều kiện môi trường, một cảnh báo deprecation của Starlette, exit 0 trong 227,26 giây. Các skip local không được tính là bằng chứng MySQL/Linux đã chạy. Workflows CI trên commit mới cung cấp phần kiểm chứng đó.
