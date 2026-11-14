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

### 4. Preserve the reviewed release predecessor when updating the runtime grant

- **Initial AI assessment:** Advancing the exact current schema pin and runtime grant looked sufficient for the rollout artifacts touched by the migration.
- **Repository evidence:** After the grant added `max_active_appointments`, `tests/backend/unit/test_release_manifest.py::test_reviewed_predecessor_requires_opt_in_and_exact_configuration` failed because `previous_configuration()` compared the live grants hash to a fixed reviewed “after” hash. All other reviewed transition files still matched; the current grants SHA was `bdede7d278ee16b114ed2da85ac9e350cfdacec1aa0b4f125237b2765229f3a3`.
- **Why the plan changed:** Without updating the exact transition identity, release tooling could not construct the previously accepted predecessor configuration, breaking its explicit compatibility review path.
- **Replacement decision:** Update only the reviewed current grants hash while retaining the original predecessor hash. The predecessor, exact-file tamper rejection, and rollout schema tests then passed (75 tests across release manifest, rollout schema and preflight modules).

### 5. Generate rollback fixture manifests from the selected source tree's schema pin

- **Initial AI assessment:** Updating the current exact schema pin and reviewed transition was expected to cover rollout consumers and historical release fixtures.
- **Repository/CI evidence:** PR #66's serialized rollback job failed `tests/rollout/test_configuration_transition.py::test_reviewed_configuration_upgrade_and_rollback_preserve_the_existing_host` at fixture startup with `Fixture schema failed` (1 failed, 3 passed). The fixture builds a `BASE` commit as the predecessor but `Artifacts.manifest(root=base)` populated `accepted_schema_revisions` from the current module-level `SCHEMA`. A regression test with a synthetic predecessor source returned the current `20261113_1200_booking_quota` instead of the predecessor's `20261019_1200_proposal_replay`.
- **Why the plan changed:** The predecessor image validates the database against its own source contract; using the head revision made the old host reject its own schema before the upgrade rehearsal began.
- **Replacement decision (refined after a second CI run):** Read the exact schema pin from the selected source tree throughout fixture manifest construction, compatibility validation, and release rendering. The first correction fixed manifest construction but CI then exposed a second module-global comparison in `require_compatible()`. The test now exercises both manifest and rendering; focused release/schema/preflight/fixture tests are green (78 passed), and the full backend suite plus another remote rollback run are pending after the follow-up patch.

### 6. Use the selected source tree's schema pin during release validation and rendering

- **Initial AI assessment:** The first fixture fix made predecessor manifests carry their own schema revision, which appeared to resolve the rollback failure.
- **Repository/CI evidence:** The next serialized rollback run progressed past fixture startup but failed in `tools/deploy/release.py::render` → `release_manifest.require_compatible` with `ReleaseError("incompatible-schema")`; that function still compared the predecessor manifest against current module-global `SCHEMA`. The original rollback test failed at `RolloutStack(..., root=base)`, specifically while rendering the archived predecessor configuration.
- **Why the plan changed:** A manifest is validated and rendered relative to the source root that produced it. Correcting only the fixture data left production release tooling unable to render that valid historical config in the rehearsal.
- **Replacement decision:** Add a shared source-root-aware `schema_revision(root)` reader and use it in compatibility validation and generated Compose environment; the fixture uses the same helper. Extend the RED test through `render()` and assert emitted accepted revision. Targeted group: 78 passed; remote CI remains to be rerun on the follow-up commit.

### 7. Preserve the reviewed predecessor's schema through additive upgrade and rollback

- **Initial AI assessment:** Reading the predecessor's schema pin in its fixture manifest and renderer appeared sufficient to make the rollback rehearsal independent of the current schema.
- **Repository/CI evidence:** CI head `43e9942` still failed before the predecessor host became stable: `rollout_schema.require_schema()` rejected revision `20261019_1200_proposal_replay` against current module constant `20261113_1200_booking_quota`. The baseline model at `724169a` has no `max_active_appointments` field. The release transition migrates that database forward and later rehearses rollback to the predecessor without downgrading the database.
- **Why the plan changed:** Schema validation must understand the exact old catalog before migration, current catalog afterward, and the forward-compatible current catalog while the reviewed old application is restored. Treating every pin as current prevents startup, while allowing any arbitrary revision would weaken rollback checks.
- **Replacement decision:** Permit only the current revision and the one reviewed predecessor revision. Validate the exact predecessor catalog (the current catalog minus the newly added quota column), require the exact current catalog for the delivered revision, and permit the predecessor release on the exact current catalog only when the release consumer is explicitly preparing an expected prior selection. Pre-migration validation uses the prior stable manifest's pin; post-migration validation remains current-only. Focused coverage: 89 passed; full backend suite: 1,799 passed, 646 skipped, 1 warning. Remote Linux rollback is pending on the pushed fix.

### 8. Include the schema Compose pin in exact predecessor reconstruction

- **Initial AI assessment:** The accepted schema revision was validated by release manifests and the rollout schema gate; the prior release configuration inventory appeared unaffected by the application policy change.
- **Repository/CI evidence:** On head `4a3b007`, the Linux rollback test passed migration and reached `assert upgraded.transition.previous_release == old.configuration`, where the reconstructed prior configuration SHA differed. `tools/deploy/compose.yaml` is part of the immutable configuration inventory and its accepted schema pin changed. `git diff` shows only that assignment changed; SHA-256 of the base blob is `1f9c939f…ca15833e` and current file is `af71a4d2…37179d28`. The regression assertion first failed with current hash retained for the predecessor.
- **Why the plan changed:** The reviewed transition reconstructs the full hashed configuration, so changing the schema pin also requires a reviewed before/after identity for this Compose file. Leaving it out causes a legitimate old host to be refused at the configuration continuity check.
- **Replacement decision:** Add only the measured predecessor/current hash pair for `tools/deploy/compose.yaml` to `ADMIN_POLICY_TRANSITION`; retain the existing configuration allowlist and unknown-hash rejection. The release/rollout focused group then passed 89 tests. Full suite and remote rerun on `4437a5e` are pending.

### 9. Carry the stable schema revision through the final pre-migration check

- **Initial AI assessment:** Once configuration transition and predecessor preflight accepted the stable release's schema, the migration workflow appeared to keep that revision until migration.
- **Repository/CI evidence:** The Linux run on `4437a5e` passed predecessor startup and configuration reconstruction, then the upgrade ended with `schema-incompatible`. `RolloutController.deploy()` has a second direct `require_schema()` after entering the migration phase and before `runtime.migrate()`; it still passed the candidate's new revision. The source's in-memory controller test reproduced this failure with an exact predecessor catalog.
- **Why the plan changed:** Preflight validation alone did not protect the next guard at the migration boundary. The database remains at the stable revision until DDL succeeds.
- **Replacement decision:** Use the same `_migration_input_schema_revisions(state, prepared)` for the final pre-migration guard, and keep all post-migration guards on the candidate's current revision. `test_upgrade_checks_predecessor_schema_until_migration_completes` fails before this patch and passes afterward; 90 focused tests and the final 1,800-test backend suite pass. GitHub rollback CI on `7a80437` is the remaining remote validation.

### 10. Refuse rollback when the immutable predecessor cannot read the migrated schema

- **Initial AI assessment:** Because the reviewed predecessor release could be selected against the recognized current schema catalog, its old application image could serve traffic after the additive migration.
- **Repository/CI evidence:** GitHub run `43455866441` reached the actual rollback smoke check after migration. The new backend returned 200; the immutable predecessor backend returned 503 `SERVICE_UNAVAILABLE` because its source supports `20261019_1200_proposal_replay` and lacks the newly required quota column. The run failed semantically at the expected `rolled_back` phase, not during fixture startup.
- **Why the plan changed:** The schema catalog being structurally valid does not mean an old binary can consume it. Continuing rollback would replace a healthy current backend with one that cannot serve requests.
- **Replacement decision:** Require exact equality between the selected release pin and delivered DB revision. The controller records a failed rollback with `schema-incompatible` before runtime rollout; the Linux regression asserts current images, database identity, and committed appointment remain unchanged. The schema unit regression was RED (`DID NOT RAISE`) before the gate fix and passes afterward. This verifies safe refusal, not successful rollback; supporting rollback across this migration needs a separate compatibility design. Local full backend suite: 1,800 passed, 646 skipped, 1 warning. The first CI attempt on `654137c` exposed a stale intermediate assertion; `8e5d686` aligns it with the earlier schema gate, and the follow-up CI run passed all 4 rollout tests in 368.61 seconds.

## Traceability and commit plan

Will be completed after implementation and real evidence exist. Initial plan commit grouping is provisional; final mapping uses actual commit SHAs and test evidence without self-reference.
