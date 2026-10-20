# Demo seed — issue #8

## Dependencies and authorship review

Prepared on 19 October 2026 from `develop` at `f5fed91`.
Migration PR [#9](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/9)
and authentication PR [#12](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/12)
were merged into `develop`; their backend CI checks passed before implementation began.
The loader requires the exact reviewed Alembic head `20261019_1200_proposal_replay`,
which includes the initial migration and subsequent audit/profile/proposal migrations.
Changing this allowlist requires schema compatibility review.

The agent individually authored all identities, consultation values, associations,
and service windows in `src/backend/app/demo/dataset.json`. No Faker, random identity
generator, name-combination template, numbered profiles, or runtime content synthesis
was used. Names describe fictional Vietnamese people; no real personal data was copied.
Emails use the reserved `.invalid` domain. Optional student profile fields remain NULL;
gender and personal details are not inferred from names.

Review covered every record: 42 distinct names/emails, normalized email spelling,
2 ADMIN / 10 STAFF / 30 STUDENT accounts, all initially active; five distinct purpose
codes with positive durations; all ten staff assigned deliberately with every purpose
covered; 16 unique associations; correct singleton settings; ten non-overlapping
weekday windows and no weekend service. Machine validation supplements this content
review. Dataset changes require a new review and explicit data migration if needed;
rerunning this command neither resets accounts nor applies configuration changes.

## Full account manifest

All addresses below are complete login emails. All accounts start with `is_active=true`.
The JSON is the canonical list; this manifest contains no credentials.

| Name | Email | Role |
|---|---|---|
| Nguyễn Minh Khuê | khue.nguyen@demo.example.invalid | ADMIN |
| Trần Quốc Bảo | bao.tran@demo.example.invalid | ADMIN |
| Lê Thu Hà | ha.le@demo.example.invalid | STAFF |
| Phạm Đức Huy | huy.pham@demo.example.invalid | STAFF |
| Võ Ngọc Lan | lan.vo@demo.example.invalid | STAFF |
| Đặng Hoàng Nam | nam.dang@demo.example.invalid | STAFF |
| Bùi Thanh Trúc | truc.bui@demo.example.invalid | STAFF |
| Hoàng Gia Khánh | khanh.hoang@demo.example.invalid | STAFF |
| Đỗ Mai Anh | anh.do@demo.example.invalid | STAFF |
| Dương Tuấn Kiệt | kiet.duong@demo.example.invalid | STAFF |
| Hồ Bích Ngọc | ngoc.ho@demo.example.invalid | STAFF |
| Ngô Hải Đăng | dang.ngo@demo.example.invalid | STAFF |
| Nguyễn An Nhiên | nhien.nguyen@demo.example.invalid | STUDENT |
| Trần Khánh Linh | linh.tran@demo.example.invalid | STUDENT |
| Lê Nhật Minh | minh.le@demo.example.invalid | STUDENT |
| Phạm Thảo Vy | vy.pham@demo.example.invalid | STUDENT |
| Võ Quang Hưng | hung.vo@demo.example.invalid | STUDENT |
| Đặng Phương Chi | chi.dang@demo.example.invalid | STUDENT |
| Bùi Hữu Phước | phuoc.bui@demo.example.invalid | STUDENT |
| Hoàng Yến Nhi | nhi.hoang@demo.example.invalid | STUDENT |
| Đỗ Thành Đạt | dat.do@demo.example.invalid | STUDENT |
| Dương Diệu Hương | huong.duong@demo.example.invalid | STUDENT |
| Hồ Thiên Ân | an.ho@demo.example.invalid | STUDENT |
| Ngô Trọng Nghĩa | nghia.ngo@demo.example.invalid | STUDENT |
| Đinh Hà My | my.dinh@demo.example.invalid | STUDENT |
| Vũ Đức Long | long.vu@demo.example.invalid | STUDENT |
| Mai Tú Uyên | uyen.mai@demo.example.invalid | STUDENT |
| Lý Hoài An | an.ly@demo.example.invalid | STUDENT |
| Phan Minh Quân | quan.phan@demo.example.invalid | STUDENT |
| Trịnh Ngọc Diệp | diep.trinh@demo.example.invalid | STUDENT |
| Tạ Anh Dũng | dung.ta@demo.example.invalid | STUDENT |
| Cao Thuỳ Dương | duong.cao@demo.example.invalid | STUDENT |
| Hà Khánh Duy | duy.ha@demo.example.invalid | STUDENT |
| Lâm Kim Ngân | ngan.lam@demo.example.invalid | STUDENT |
| Đoàn Bảo Châu | chau.doan@demo.example.invalid | STUDENT |
| Chu Việt Hoàng | hoang.chu@demo.example.invalid | STUDENT |
| Tô Nhã Phương | phuong.to@demo.example.invalid | STUDENT |
| Kiều Quốc Việt | viet.kieu@demo.example.invalid | STUDENT |
| Đào Thanh Tâm | tam.dao@demo.example.invalid | STUDENT |
| Tăng Gia Hân | han.tang@demo.example.invalid | STUDENT |
| Quách Hồng Sơn | son.quach@demo.example.invalid | STUDENT |
| La Hải Yến | yen.la@demo.example.invalid | STUDENT |

## Consultation manifest

| Code | Vietnamese display name | Minutes | requires_free_text | is_active |
|---|---|---:|---|---|
| CAREER_INTERNSHIP | Định hướng nghề nghiệp và thực tập | 45 | false | true |
| THESIS_RESEARCH | Khóa luận và phương pháp nghiên cứu | 60 | false | true |
| ACADEMIC_ADVISOR | Kế hoạch học tập và tiến độ tốt nghiệp | 30 | false | true |
| GENERAL_ADVICE | Hỗ trợ đời sống và kỹ năng học đại học | 30 | false | true |
| OTHER | Nhu cầu tư vấn khác (vui lòng mô tả) | 45 | true | true |

## Complete staff-purpose matrix

Each check mark is a stored association with `is_accepting=true`; a dash means no row.
These fictional specialties explain the deliberate choices, not additional database fields.

| Staff | CAREER_INTERNSHIP | THESIS_RESEARCH | ACADEMIC_ADVISOR | GENERAL_ADVICE | OTHER | Assignment rationale |
|---|:---:|:---:|:---:|:---:|:---:|---|
| Lê Thu Hà | ✓ | — | — | ✓ | — | Career transition and student workplace skills. |
| Phạm Đức Huy | ✓ | — | — | — | — | Internship preparation and employer expectations. |
| Võ Ngọc Lan | — | ✓ | — | — | — | Research methods and thesis scoping. |
| Đặng Hoàng Nam | — | ✓ | ✓ | — | — | Thesis milestones within the graduation study plan. |
| Bùi Thanh Trúc | — | — | ✓ | — | — | Course selection and academic progress. |
| Hoàng Gia Khánh | ✓ | — | ✓ | — | — | Align course choices with career goals. |
| Đỗ Mai Anh | — | — | — | ✓ | — | Campus adjustment and study skills. |
| Dương Tuấn Kiệt | ✓ | ✓ | — | — | — | Connect applied research with industry internships. |
| Hồ Bích Ngọc | — | — | — | ✓ | ✓ | Student support and initial triage of uncommon requests. |
| Ngô Hải Đăng | — | — | ✓ | — | ✓ | Academic administration and requests needing clarification. |

## Booking settings and weekly schedule

`service_settings`: `id=1`, `timezone=Asia/Ho_Chi_Minh`, `start_step_minutes=15`,
`booking_horizon_days=7`. This is demo configuration, not production policy.
Times are local wall-clock values; timestamps are stored in UTC through the shared engine.

| ISO weekday | Morning | Afternoon |
|---|---|---|
| 1 — Monday | 08:00–11:30 | 13:30–17:00 |
| 2 — Tuesday | 08:00–11:30 | 13:30–17:00 |
| 3 — Wednesday | 08:00–11:30 | 13:30–16:30 |
| 4 — Thursday | 08:00–11:30 | 13:30–17:00 |
| 5 — Friday | 08:00–11:00 | 13:30–16:00 |
| 6 — Saturday | No service | No service |
| 7 — Sunday | No service | No service |

## Installation and execution

Use Python 3.11+ and MySQL 8.4/InnoDB. From the repository root:

```sh
python -m venv .venv
# POSIX: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

Copy `.env.example` to an untracked `.env`, set the following values locally, and have
a migration operator run `alembic upgrade head` before using the DML-only seed account:

| Variable | Required configuration |
|---|---|
| ENVIRONMENT | Explicit `local`, `development`, or `demo`; production/test/unset rejected. |
| DATABASE_URL | `mysql+asyncmy` URL with host and the already created, migrated database. |
| DEMO_SEED_DATABASE | Explicit target acknowledgment; must exactly match URL and `SELECT DATABASE()`. |
| DEMO_ADMIN_PASSWORD | Locally supplied initial password for the two newly inserted administrators. |
| DEMO_STAFF_PASSWORD | Locally supplied initial password for the ten newly inserted staff members. |
| DEMO_STUDENT_PASSWORD | Locally supplied initial password for the thirty newly inserted students. |

All three password variables are required even on a no-op rerun. Use 12–1024 characters;
blank strings, control characters and documented placeholder prefixes are rejected.
Do not trim passwords: the supplied value is used exactly. Store them only in the
local environment/untracked `.env`; never place them in command arguments or Git.
New accounts use separate salted hashes through #5's `PasswordService` and its shared
`PasswordPolicy` (Argon2id, 65536 KiB, time cost 3, parallelism 1).
This is a small demo using one initial password per role; production provisioning and
password rotation are outside scope.

Before API/worker traffic starts, run:

```sh
python tools/scripts/seed_demo.py
```

The standalone command needs no API server, cookie/CSRF secrets, or application startup.
It reads `.env` with process environment taking precedence. First-run output:

```text
users: inserted=42 existing=0
purposes: inserted=5 existing=0
staff_purposes: inserted=16 existing=0
service_settings: inserted=1 existing=0
service_hours: inserted=10 existing=0
```

A second run reports `inserted=0` and existing counts `42, 5, 16, 1, 10` respectively.
Counts describe seed records, not total database population. No other table is populated;
in particular, sessions are created only by subsequent authentication, not by this loader.

## Stable identity and conflict policy

UUIDs are allocated using UUIDv5 namespace `9a15c7ab-9dec-498d-b37a-ad8d777d840e`.
The name is `table:natural-key`: users use complete normalized email; purposes use code;
service hours use `weekday/HH:MM:SS/HH:MM:SS` (for example
`service_hours:1/08:00:00/11:30:00`). Allocation is technical processing of explicit records.
Staff-purpose identity is the pair of these user/purpose UUIDs. Settings use ID 1.

| Table | Required existing match | Fields preserved |
|---|---|---|
| users | Both deterministic ID and email, exact name and role | Existing password hash, active/disabled state, optional profile fields, timestamps, ID |
| purposes | Both deterministic ID and code, name, duration, free-text and active flags | All fields and timestamps |
| staff_purposes | Staff/purpose pair and accepting flag | All fields and timestamps |
| service_settings | ID 1 and all three booking values | All fields and updated_at |
| service_hours | Both deterministic ID and exact interval | All fields and timestamps |

Email lookup uses MySQL's `utf8mb4_0900_as_ci` column comparison rather than Python
case folding, including collation-equivalent strings containing ignorable characters.
The stored ID and exact email/name/role must still match the manifest; collation
equivalence does not establish seed ownership or permit adoption of another account.

An email/code/interval found under an unrelated UUID is a conflict, even if its other
values look identical. ID ownership is checked independently of natural keys; split
ID/key matches also fail. Matching singleton configuration can be reused since it is
global rather than an account-owned record. Missing seed records are inserted; matching
ones are reused. Unrelated records and business history are untouched. Extra service
windows are preserved only when they do not overlap any reviewed interval.
Disabled seed users stay disabled. Changed roles/names or configuration require manual
review and cause failure; changed hashes and student profile fields are preserved.

The loader validates the whole dataset, database selection, MySQL version, exact migration
revision, InnoDB tables and all existing-data conflicts before insertion. It writes the
five tables in one transaction; failures roll back all changes from that run. Every
engine created by the standalone path is disposed in `finally`. It never creates a
database, migrates/stamps schema, or resets/truncates data. Run only once at a time while
API/worker traffic is stopped; concurrent loading is outside scope.

## Permissions and troubleshooting

The runtime account needs SELECT on `alembic_version` and SELECT/INSERT on only the five
seed tables. Metadata visibility for those tables is needed in `information_schema`.
No CREATE, ALTER, DROP, UPDATE or DELETE permission is required by the loader.
Use a separate migration/test operator for DDL and isolated integration schemas.

Configuration, schema, conflicts and database failures return exit code 1 with sanitized
diagnostics. Successful loading returns 0. Never log raw exceptions, hashes, passwords,
tokens or credential-bearing connection URLs when investigating a failure.

- Configuration failure: check variable names, placeholder replacement, database spelling,
  driver and explicit environment. System schemas and missing database names are rejected.
- Schema failure: ask the migration operator to verify `alembic current` reports
  `20261019_1200_proposal_replay`; missing/old/unknown states fail closed.
- Conflict: inspect the reported table/field with an authorized database operator.
  The command does not overwrite existing configuration, adopt users, or repair roles.
- Generic database failure: check connectivity and DML/metadata permissions using a
  database client without exposing connection details in issue comments or logs.

## Verification

```sh
python -m black --check src/backend tests/backend tools/scripts/export_openapi.py tools/scripts/seed_demo.py
python -m ruff check src/backend tests/backend tools/scripts/export_openapi.py tools/scripts/seed_demo.py
pyright
# MYSQL_TEST_URL must point at a dedicated MySQL 8.4 test server with schema DDL permissions.
python -m pytest tests/backend -q --junitxml=build/backend-tests.xml
```

CI retains existing model, migration and authentication checks, checks both standalone
scripts, runs the full backend suite against isolated Alembic-migrated schemas and fails
if any required test is skipped. Seed tests cover exact content, role counts, hash
verification with #5, root CLI twice, stable IDs/hashes/timestamps, unrelated records,
disabled/changed accounts, conflicts, final-table failure rollback, target/schema guards,
sanitized output and actual seeded-student login through `/api/v1/auth/login`.

Local verification on Windows / Python 3.12.14 / MySQL 8.4.11:

- Black 25.1.0: all 143 backend/test/script files pass.
- Ruff 0.14.0: all checks pass.
- Pyright 1.1.407: 0 errors and 0 warnings.
- Dedicated seed integration suite: 15 passed, 0 skipped (67.89 seconds), including
  root CLI twice, final-table rollback and real authentication.
- The full backend suite is also required by the PR's CI on Python 3.11; its JUnit
  skip guard rejects any skipped integration test. See the PR checks for the final run.

JUnit reports are local ignored build artifacts; no credentials or hash values are
included in this handoff. Merge remains subject to passing CI and reviewer approval.

### Review follow-up — 20 October 2026

Synchronized the feature branch with `develop` at `f13730a`. The Pyright include list
retains both `tools/scripts/check_dependency_advisories.py` from develop and
`tools/scripts/seed_demo.py` from this feature, along with dataset package resources.

Reproduced the reviewer's collation regression on MySQL 8.4.11: the original loader
passed the exact-email control but failed for an unrelated UUID with
`yen.la\u200b@demo.example.invalid`. The updated loader performs both email and ID
lookup in SQL, checks deterministic ownership and exact required fields, and rejects
the conflict with a sanitized `SeedError` during preparation. Both control and U+200B
cases now assert zero attempted INSERT statements and unchanged snapshots of all eleven
application tables. The recorder retains statement text only, never SQL parameters.

Local checks with the updated develop dependencies: Black 26.3.1 passes all 149 files,
Ruff passes, Pyright reports 0 errors/0 warnings, all 360 unit tests pass, and all 46
seed tests pass (29 unit and 17 MySQL integration tests). The full required checks are
rerun on the updated PR head; use the current PR CI results rather than the original
760-test run as evidence for integration with the newer develop branch.

### Integration with current develop — 20 October 2026

Synchronized with `develop` at `b3f2384`, including the merged change-proposal feature
and its audit collector fix. The Python audit now explicitly enumerates both pinned
requirements files with `--no-deps --disable-pip`; a real local audit reports all 47
locked packages, including `packaging==26.3`. Inventory/version checks and advisory
exceptions remain enforced by the existing policy checker. Additional regressions
reject missing packages, wrong versions, unexpected audit-tool packages and unhandled
advisories.

Reviewed migration `20261019_1200_proposal_replay`: it adds durable proposal replay
fields to `appointments`/`change_proposals` and leaves the five seed tables unchanged.
The seed loader now requires this current head. A unit guard compares its accepted
revision to the sole Alembic head so a future schema change requires compatibility
review. MySQL coverage also rejects the previously accepted student-profile revision
before writes, while fresh/current-schema loading, CLI reruns and seeded login use
the full latest migration chain.

Local acceptance on this base: 388 backend unit tests and all 18 MySQL seed integration
tests passed with no skips. Black passed all 162 checked files, Ruff and Pyright passed,
and the exported OpenAPI matched the committed artifact. Current full backend,
frontend and dependency-security results are recorded in the PR's latest checks.
