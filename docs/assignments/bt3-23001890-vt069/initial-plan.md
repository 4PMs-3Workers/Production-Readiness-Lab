# Initial AI Implementation Plan - VT-069

> Bản kế hoạch ban đầu được ghi trước khi thêm test hoặc sửa implementation. Kết quả review sau khảo sát chi tiết nằm ở cuối file; không xóa các nhận định ban đầu.

**Goal:** Chuyển hạn mức lịch chiếm chỗ của sinh viên từ literal trong booking service sang policy đặt lịch có thể thay đổi bằng admin API.

**Architecture:** Thêm một trường vào singleton `service_settings` và resource admin booking configuration có sẵn. Booking đọc trường đã khóa trong transaction hiện tại và giữ nguyên response/side-effect semantics.

**Tech stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2 async, Alembic, pytest, MySQL 8.4.

**Spec:** [`spec.md`](spec.md)

## Global constraints

- Baseline là `12a74f7308550ccc294a783edde79db267a526d1` trên `origin/develop`.
- Branch bài `feature/bt3-23001890-vt069`; không thay đổi `develop/main`.
- Giữ default hiện tại 3; không cho exception DB trở thành default hợp lệ.
- Contract testing phải assert outcome nghiệp vụ và state/side effects.
- Không ghi nhận test, coverage, tải hay review khi chưa chạy/quan sát.
- Không chứa deliberate mutation trong branch cuối.
- Tải mục tiêu: 60 tác vụ đồng thời; p95 <= 2.0 giây, không suy rộng thành SLO production.

## Review focus

- Hạn mức phải được đọc trong transaction có khóa hiện có để không vượt quota khi cạnh tranh.
- Giảm quota dưới lượng lịch hiện tại không được làm mất dữ liệu hoặc tự hủy lịch.
- Invalid/missing config và lỗi đọc DB phải có outcome xác định, không bị fallback che giấu.
- Admin contract cần giữ version/audit semantics khi thay trường mới.
- Migration/deployment grants/fixtures phải cùng hiểu schema mới.

## Initial file map

- Modify `src/backend/app/models/service_setting.py`: persisted setting and DB invariant.
- Modify `src/backend/app/schemas/admin_booking_configuration.py`: validated input/output contract.
- Modify `src/backend/app/services/admin_booking_configuration.py`: snapshot and replace/audit field list.
- Modify `src/backend/app/services/booking.py`: compare count against stored policy.
- Create Alembic migration under `src/backend/migrations/versions/` with backfill/default.
- Test `tests/backend/integration/test_admin_booking_configuration.py` and booking integration tests.
- Refresh generated `docs/api/openapi.yaml` and relevant task/architecture docs.

## Initial steps and order

1. Add an HTTP-level test changing the policy, then create bookings and assert quota outcome and DB side effects; add invalid input and read-failure cases.
2. Run focused MySQL tests to capture RED caused by missing field/behavior.
3. Add the schema field and forward migration with default 3; keep seed compatibility.
4. Extend admin schema/service and use its value in booking transaction.
5. Run focused tests, regression/static checks, OpenAPI consistency and migration checks.
6. Run an isolated exception-swallowing mutation against the failure-path test; restore and prove clean source.
7. Measure available coverage, repeat/order-related checks, and run a real or explicitly synthetic 60-concurrency harness.
8. Record traceability/AI usage/reflection, create Vietnamese report and PDF, render and inspect every page.
9. Review all branch changes; commit in logical slices; push feature branch and open Draft PR if GitHub access permits.

## Expected evidence and assumptions

- Evidence: complete baseline SHA, RED/GREEN commands with output/exit status; migration and contract behavior; mutation diff/result; coverage and repeated tests; 60-concurrency p95; PDF page/render/text checks; git/remote/PR/CI state.
- Assumptions: reuse the existing admin policy API and singleton row; default 3; changed value applies after commit; test target is MySQL 8.4. No hot reload is needed because each transaction reads the row under the existing lock.
- Proposed commits: (1) persisted policy and API contract + booking outcome; (2) supporting deployment/seed/OpenAPI/docs; (3) assignment report/evidence/PDF. Actual commit boundaries may change when diff and repository conventions are reviewed.

## Repository review: evidence-based plan changes

### 1. Add deployment grant and demo fixture consumers to the file map

- **Initial AI assessment:** The main implementation path was the model, schema, service, booking code, migration and tests; the original file map did not name the runtime grant or demo/load data consumers.
- **Repository evidence:** `tools/deploy/mysql/grants.sql` grants column-level `UPDATE` on `service_settings` and enumerates the three existing policy fields. `src/backend/app/demo/data.py` and `dataset.json` define the seed contract. `tools/load/fixtures.py` and rollout/seed tools instantiate or project policy fields.
- **Why the plan changed:** A database column could migrate and work in a privileged test account while the least-privilege runtime account cannot update it, or fixture/restore scripts can silently diverge.
- **Replacement decision:** Include those consumers in impact review and update only those that semantically own booking configuration; add regression checks for grants, demo seed and generated API contract.

### 2. Make the contract test prove business result and atomic state

- **Initial AI assessment:** The initial sequence called for an HTTP contract test that changes configuration and checks booking response, but did not explicitly require database state assertions in every branch.
- **Repository evidence:** `src/backend/app/services/booking.py` writes appointment, `AuditEvent` and two `Notification` rows through `add_creation_bundle`; `docs/tasks/appointment-booking.md` documents atomicity. Existing admin configuration tests mostly check HTTP validation and round-trip; booking tests cover transaction effects separately.
- **Why the plan changed:** Status/JSON schema checks alone can pass while a rejected request still leaves partial effects, or while an accepted booking ignores the changed value.
- **Replacement decision:** A real MySQL contract test will use the admin API as config provider and booking API as consumer, then assert accepted/rejected business outcomes, quota code, appointment count, audit count and notification count from a fresh DB session.

### 3. Update exact schema-head gates across API, worker, seed and deployment artifacts

- **Initial AI assessment:** The initial plan named a forward migration and expected ordinary Alembic checks to cover schema rollout.
- **Repository evidence:** The current release pins an exact revision in `.env.example`, `tools/deploy/compose.yaml`, `tools/deploy/release_manifest.py`, `tools/deploy/evidence.py`, `src/backend/app/worker/resources.py`, and `src/backend/app/demo/loader.py`; repo tests verify these pins.
- **Why the plan changed:** A correct migration alone would leave readiness, background processing, seed and release attestation rejecting the new schema or claiming the former head.
- **Replacement decision:** Advance those source-of-truth gates and their relevant tests/docs with the migration. Keep older revision references in historical evidence unchanged.

## Traceability and commit plan

Will be completed after implementation and real evidence exist. Initial plan commit grouping is provisional; final mapping uses actual commit SHAs and test evidence without self-reference.
