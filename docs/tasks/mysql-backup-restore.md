# MySQL backup và isolated restore (Issue #36)

Công cụ `python -m app.backup` tạo snapshot logic MySQL 8.4/InnoDB và phục hồi
vào database **có tên khác, đang rỗng**. Một artifact hoàn tất mới chứng minh được
integrity; khả năng phục hồi ứng dụng chỉ được chấp nhận sau diễn tập có login,
read, book, cancel, replay và logout. Dùng [recovery runbook](mysql-recovery-runbook.md)
để chuẩn bị storage, quyền, lịch chạy và xử lý lỗi.

## Hợp đồng snapshot và migration

- Backup giữ chung advisory lock theo database với `app.deploy migrate` và Alembic
  online. Mọi caller cung cấp connection cho Alembic phải dùng
  `app.core.schema_lifecycle.migration_connection`; env.py kiểm tra ownership.
  Guard bao trùm cả commit version table vì DDL MySQL có implicit commit.
- Trong guard, `LOCK INSTANCE FOR BACKUP` ngăn DDL không tương thích; ordinary DML
  vẫn được phép. Tất cả bảng được đọc trong **một** transaction `REPEATABLE READ`,
  `WITH CONSISTENT SNAPSHOT, READ ONLY`. Không dùng live counts sau backup.
- Source phải đúng head hiện tại của migration tree trong release đang chạy,
  đúng tập bảng/cột được hỗ trợ, InnoDB, UTC và utf8mb4. Không hardcode một revision
  cũ trong công cụ. View, trigger, routine, event và schema chưa biết bị từ chối.
  Tài khoản phải có direct grants cho phép nhìn đủ metadata; role-only hoặc partial
  revokes chưa được hỗ trợ. Không suy luận “không có object” từ catalog bị lọc quyền.
- DDL thủ công ngoài lifecycle không phải đường deploy được hỗ trợ. Guard không
  tự sửa schema đã drift; không tự downgrade, seed hay migrate khi restore.

Cơ sở MySQL: [backup lock](https://dev.mysql.com/doc/refman/8.4/en/lock-instance-for-backup.html),
[consistent snapshot](https://dev.mysql.com/doc/refman/8.4/en/commit.html),
[metadata privileges](https://dev.mysql.com/doc/refman/8.4/en/privileges-provided.html).

## Artifact và dữ liệu nhạy cảm

Format `prl-mysql-jsonl`, version 1, gồm native `SHOW CREATE TABLE`, typed canonical
JSONL cho từng bảng, `manifest.json` và marker `COMPLETE`. Kiểu binary, Decimal,
datetime UTC với microsecond, DATE, TIME, JSON và NULL được giữ nguyên. Generated
columns có trong checksum snapshot nhưng được MySQL tính lại khi insert.

Manifest chứa backup ID, UTC start/completion, khoảng UTC trước/sau lúc thiết lập
read view trên clock DB, server UUID/version, database, migration revision/digest,
release SHA/image ID khi được cung cấp, schema và số dòng/bytes/SHA-256 từng file.
`COMPLETE` là SHA-256 của manifest. Chỉ rename staging sang tên cuối sau khi fsync
và kiểm tra toàn bộ artifact; completion timestamp bao gồm bước kiểm tra đó.
Thư mục `.partial-*`, file thiếu, checksum sai và artifact bị sửa không được nhận
là backup thành công. Checksum bảo vệ integrity, không chứng thực người tạo.

Storage phải là filesystem local riêng, ngoài MySQL data volume, owner UID10001,
thư mục 0700/file 0600 trên Linux. Không hỗ trợ lease trên NFS/object store. Backup
chứa password hashes, session và nội dung nghiệp vụ: chỉ lưu private, mã hóa và
quản lý khóa theo môi trường; không upload payload/manifest vào CI, Git hay ticket.
Chỉ báo cáo allowlist đã sanitize được công bố. `.gitignore` và `.dockerignore`
loại các tên artifact chuẩn; vẫn phải đặt storage ngoài checkout.

Mặc định: artifact tối đa 512 MiB, record 1 MiB, chừa 64 MiB trống, backup/restore
600 giây, query 30 giây, lifecycle lock 3 giây, pool một connection. Record quá lớn,
thiếu dung lượng hoặc vượt deadline làm operation thất bại. Logical JSONL ưu tiên
tính kiểm chứng cho môi trường lab/demo, chưa có bằng chứng throughput production.

## Restore và xác minh

`restore --backup-id <id> --target-database <name>` bắt buộc có
`RESTORE_DATABASE_URL` trỏ đúng tên đích. Luôn từ chối tên source/current database,
kể cả trên server riêng; không có cờ force/overwrite. Đích phải rỗng, MySQL 8.4,
UTC và charset/collation khớp. Kiểm tra toàn bộ artifact, format, migration contract,
release SHA nếu có và identity đích trước DDL/DML.

Native DDL giữ index, FK, CHECK, UNIQUE và generated columns. Rows được nạp bằng
bound parameters trong một transaction; trước commit, công cụ đối chiếu mọi row
hash/count từ artifact, toàn bộ DDL (bỏ riêng bộ đếm AUTO_INCREMENT), cột/PK/generated,
orphan FK, charset/collation và UTC. CHECK/UNIQUE vẫn do MySQL enforce lúc insert.
Không nhận dữ liệu được tạo lại từ seed thay cho dữ liệu backup.

DDL không rollback được. Nếu import/validation lỗi, rows rollback nhưng database
đích có thể còn tables: **quarantine đích đó**, không tự xóa hoặc thử ghi đè.
`verify` chỉ đọc snapshot của đích trước khi chạy smoke; sau khi ứng dụng đặt/hủy
lịch hợp lệ, hash đích sẽ khác backup và việc verify nguyên trạng sẽ thất bại đúng
thiết kế.

## Lịch chạy, retention và status

Profile `backup` dùng cùng backend image của API/worker, không khởi động cùng app
và không cần telemetry. `tools/backup/run.py` kiểm tra image SHA/ID, external env,
backup-volume label, chạy `scheduled` có timeout và chỉ dọn one-off container có
đúng token ownership. [Service/timer Linux](../../tools/backup/systemd/) chạy hằng
ngày, cấu hình được bằng systemd drop-in. Xem runbook để điều chỉnh ngân sách đồng bộ.

Lease filesystem chống overlap giữa backup, restore, verify, status và retention,
giải phóng khi process chết. `scheduled` giữ lease xuyên backup và retention; không
bọc thêm flock trên cùng file. Retention mặc định giữ 7 bản trong 30 ngày, luôn giữ
bản thành công mới nhất **cho từng cặp server UUID/database**, dù quá tuổi. Chỉ xóa
artifact chuẩn đã verify lại; không xóa partial, symlink, file hỏng hoặc unrelated
paths. Lỗi retention sau backup có report backup success riêng và job failure;
không làm mất bản vừa hoàn tất.

JSON stdout gồm operation, success/failure reason, duration, backup ID/age, checksum
và validation flags. `status` đọc storage không kết nối DB hoặc telemetry; tuổi là
tuổi artifact hoàn tất theo clock host. Khi cùng store có nhiều server dùng tên DB
giống nhau, status tổng hợp theo tên; inspect manifest private để chọn đúng UUID.
Để đánh giá freshness từng môi trường, dùng store riêng. CLI integrity success để
`application_rehearsal_verified=false`; báo cáo diễn tập mới có `accepted=true`.

## Nghiệm thu và bằng chứng

Workflow [MySQL recovery](../../.github/workflows/mysql-recovery.yml) bắt buộc chạy
`tests/recovery` với Docker/Compose thật; thiếu prerequisite làm fail, không skip.
Nó build source của checkout, tái sử dụng môi trường đã review của #34, tạo data giả,
appointment/proposal/audit/notification, backup, tạo booking sau snapshot, rồi:

1. Tạo project, volume, database, gateway subnet và HTTP port recovery riêng.
2. Restore và verify trước khi chạy app; không seed/migrate target.
3. Dùng đúng image IDs của source cho API, worker và frontend; worker trỏ target.
4. Kiểm tra persisted history/detail, dữ liệu sau backup bị loại, availability,
   booking/replay, cancellation/replay, logout và side effects chính xác.
5. So sánh fingerprint mọi bảng source sau incident với sau recovery/smoke.
6. Dọn đúng resources UUID đã xác minh label; bảo toàn project `prl-demo` hiện có.

[Bằng chứng ngày 2026-10-28](../evidence/mysql-backup-restore/2026-10-28-rehearsal.json)
ghi nhận source `43f350ff470ea30025e0187e27249711c5a3b73d`, 12 bảng/86 rows,
14 FK và một generated column; schema/row hashes, UTC và toàn bộ smoke pass.
RPO quan sát **3.514220–3.516430 giây**, RTO tới service smoke **91.062 giây**.
Đây là số đo của lần diễn tập đó, không phải SLO hay chứng minh production readiness.
File lịch sử giữ nguyên source/date; CI tại PR head tạo evidence mới trong artifact
`mysql-recovery-<run>-<attempt>`. Với checkout merge của GitHub, `source_sha` là SHA
thực sự được build; `pr_head_sha` ghi riêng, không gán nhầm image cho PR head.

RPO dùng incident DB UTC trừ khoảng thiết lập snapshot DB UTC; không giả định độ
chính xác tuyệt đối của read-view instant. RTO dùng monotonic elapsed từ trước lúc
provision target đến khi **smoke ứng dụng** pass, gồm startup/import/grants/auth và
nghiệp vụ. UTC recovery-start/smoke-passed cũng được lưu. Không lấy SQL import
completion làm service recovery, không tự đặt mục tiêu RPO/RTO.

## Coverage và kiểm tra lại

Backend tests chạy MySQL 8.4 thật, migrations được review và schema UUID riêng:
concurrent cross-table transactions; source unchanged; typed round-trip; native DDL
lock; partial schema/revision; corrupt/truncated/missing/inconsistent manifests;
schema/release mismatch; wrong/nonempty target; CHECK/UNIQUE/FK violations; failed
backup/restore/deadline; missing metadata privileges; cross-process overlap;
retention age/count/last-success boundaries. Unit tests kiểm tra codec, manifest,
private storage, CLI redaction, scheduler ownership và evidence allowlist/RPO math.

```sh
python -m black --check src/backend tests/backend tools/backup tests/recovery
python -m ruff check src/backend tests/backend tools/backup tests/recovery
pyright
# Dedicated test-only MySQL server, schema creation rights; URL supplied externally.
python -m pytest tests/backend -q --require-mysql
RUN_BACKUP_REHEARSAL=1 python -m pytest tests/recovery -q --tb=short
```

Các CI cũ vẫn được giữ: backend/migrations, frontend mock/API, container business/
telemetry/lifecycle và dependency security. Workflow recovery bổ sung một gate riêng.
Kiểm tra local trước handoff: 899 unit tests và 79 MySQL tests thuộc backup/restore,
schema/migrations/models pass; hai proposal-replay migration tests cũng pass sau
khi chuyển caller sang lifecycle guard. Black, Ruff, Pyright và formatting các
workflow/Compose mới pass. Full backend và full service recovery tiếp tục được chạy
bắt buộc tại checkout của CI; không thay thế chúng bằng kết quả chọn lọc local.
Issue chỉ đóng sau review/merge và acceptance. Live cutover, PITR/binlogs, replication,
HA, release rollback/CD và fault-injection rộng hơn nằm ngoài phạm vi này.
