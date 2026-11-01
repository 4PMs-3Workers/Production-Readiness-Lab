# BT3 - 23001952 - VT-071

Date checked: 2026-11-01 (Asia/Bangkok). Official repository:
https://github.com/4PMs-3Workers/Production-Readiness-Lab
Default branch: develop. Baseline: aa8c5b0be057754f9b3409c8699537d4aeb887b9.
Working branch: codex/bt3-23001952-vt071. No merge authorized.

## Problem statement

Sinh viên cần tải lịch sử lịch hẹn để sử dụng ngoài ứng dụng. Nếu endpoint xuất dữ liệu
không áp dụng quyền và owner scope như luồng đọc hiện có, người gọi có thể tải lịch hẹn
của sinh viên khác hoặc nhận dữ liệu nội bộ không thuộc hợp đồng công khai.

## Acceptance criteria (new contract; subject to team review)

- AC-01: Given STUDENT có phiên hợp lệ, when GET /api/v1/appointments/export,
  then nhận CSV UTF-8, attachment appointments.csv, private/no-store, cột allowlist
  cố định, timestamp UTC và thứ tự starts_at DESC, id DESC; chuỗi công thức được prefix '.
- AC-02: Given thiếu/không có phiên hoặc role STAFF/ADMIN, when export,
  then 401 hoặc 403 tương ứng; không trả CSV hoặc dữ liệu lịch hẹn.
- AC-03: Given dữ liệu của nhiều sinh viên, when export (có thể lọc status hợp lệ),
  then chỉ có owner là subject trong session; unknown/duplicate query bị 422.
  Không chấp nhận student_id, page hoặc page_size do caller gửi.
- AC-04: Given lịch sử rỗng, DB lỗi/timeout hoặc vượt 1000 dòng, when export,
  then lần lượt header-only CSV, sanitized 503 hoặc 409 EXPORT_TOO_LARGE;
  không trả file thiếu dòng như thể export thành công.
- AC-05: Given create booking rồi retry cùng key/payload (kể cả sau cancellation),
  when đọc export, then receipt được replay, DB và CSV chỉ có một lịch tương ứng.
  Đây là invariant của nguồn dữ liệu, không phải guard mới cho GET.

## Constraints and invariant

- Security: giữ require_student + owner SQL predicate; không xuất description,
  creation_request_key/hash, email hoặc thông tin phiên; query không chọn owner.
- Reliability/resource: một SELECT có LIMIT 1001; tối đa 1000 dòng; đọc trong
  read_transaction và buffer trước response; không thêm dependency runtime.
- Performance experiment: 80 tasks đồng thời, p95 nearest-rank <= 2.5 s;
  chỉ kết luận cho môi trường/fixture được đo, không suy rộng production.
- Invariant: mọi row CSV thuộc user_id được xác thực, chỉ chứa cột được cho phép;
  retry tạo lịch không làm nhân bản dữ liệu nguồn.

## Source guidance read

- Lecture 06: all 27 slides. Applied slides 9-14, 16-23 (loop, context, checkpoints,
  diff review, independent evidence, traceability/ownership).
- Lecture 07: all 28 slides. Applied slides 7-12, 17-25 (risk portfolio, real boundary,
  API contract, assertions, coverage, doubles, flaky risks, mutation, quality gates).
- Decision table is required by VT-071; not attributed to a nonexistent slide.

## Scope / assumptions

CSV format, 1000-row bound and STUDENT-only export are proposed policy consistent
with existing history access, not a stakeholder-approved export permission system.
No UI or job queue added. Browser E2E is not required for this API-only slice;
real-session HTTP workflow is the critical journey. Team review remains outstanding.
