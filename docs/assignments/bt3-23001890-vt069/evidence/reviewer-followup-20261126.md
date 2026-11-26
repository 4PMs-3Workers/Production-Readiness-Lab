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
