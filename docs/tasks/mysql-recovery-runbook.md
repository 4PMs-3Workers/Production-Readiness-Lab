# Runbook backup và diễn tập recovery MySQL

Phạm vi hỗ trợ: Linux Docker Engine + Compose có `env_file.format: raw` và
`!override` (bài diễn tập local dùng Compose 5.1.3), backend image đã review,
MySQL 8.4/InnoDB và filesystem local. Xem [hợp đồng và evidence](mysql-backup-restore.md).
Backup/restore chạy độc lập với observability. Các lệnh dưới đây chạy tại root repo;
secrets phải nằm trong external env, không đưa URL/mật khẩu vào arguments hay log.

## 1. Chuẩn bị source và storage

Hoàn tất [triển khai #34](containerized-demo.md), migrate bằng job riêng và apply
runtime grants. Không seed dữ liệu thật. Dùng cùng backend release cho operations,
API và worker; giữ bản release/migration tree tương ứng với artifact để recovery.

Tạo external `backup.env` theo [example](../../tools/backup/backup.env.example), quyền
0600 trong thư mục 0700, với URL percent-encode password. Source account `prl_backup`
cần direct grants `SELECT, TRIGGER, EVENT ON prl.*` và `BACKUP_ADMIN, SHOW_ROUTINE ON *.*`.
TRIGGER/EVENT/SHOW_ROUTINE cần cho metadata không bị lọc quyền: công cụ sẽ từ chối
trigger/event/routine hiện hữu. Các quyền metadata này cũng cho phép thao tác DDL
tương ứng, nên đây là credential vận hành được giữ private, không cấp cho runtime.
Không cấp INSERT/UPDATE/DELETE, FILE hay quyền user administration cho backup role.

Với database có `_`, đọc `SELECT @@global.partial_revokes` trước khi cấp quyền.
Khi giá trị là `OFF`/`0`, database-level grant cho tên literal `prl_app` dùng
``ON `prl\_app`.*``; `_` không escape là wildcard có thể cấp quyền sang database
khác. Khi `partial_revokes=ON`, dùng ``ON `prl_app`.*`` với tên literal, không thêm
backslash. Quy tắc này chỉ áp dụng scope database `db.*`: tên database trong
`CREATE DATABASE`, URL và các grant cho table cụ thể vẫn dùng identifier gốc.
Preflight đọc mode từ chính server đang kết nối và nhận diện scope đã escape;
không cần cấp thêm wildcard grant để backup hoặc restore chạy được. Xem
[MySQL GRANT — Object Quoting Guidelines](https://dev.mysql.com/doc/refman/8.4/en/grant.html).

Với volume **mới**, thêm `PRL_BACKUP_PASSWORD` vào external `db.env` trước lần bootstrap
đầu tiên. Với volume đang có, entrypoint không chạy lại. Cấp/rotate account bằng tác
vụ administrator rõ ràng, dùng script được review; nó chỉ thay quyền `prl_backup`:

```sh
# PRL_BACKUP_PASSWORD đã được secret manager cung cấp vào môi trường shell.
# Không dùng shell xtrace. dc được định nghĩa bên dưới.
dc exec -T -e PRL_BACKUP_PASSWORD mysql bash -s < tools/deploy/mysql/provision-backup.sh
```

Đồng bộ password trong external backup URL sau rotation. Root password được script
đọc bên trong MySQL container qua socket; không mở root remote. Với source database
khác `prl`, administrator phải review/cấp cùng quyền ở đúng database đó thay vì chạy
script cố định của demo.

Tạo backup volume **riêng**, không gắn `/var/lib/mysql`, không dùng volume MySQL:

```sh
docker volume create --label prl.backup.storage=1 prl-backup-storage
```

Chỉ tạo tên này khi chưa có; kiểm tra owner/purpose nếu tên đã tồn tại. Empty volume
được Docker khởi tạo ownership/mode từ `/backups` trong backend image (UID10001/0700).
Không chmod 0777 để chữa lỗi. Với volume cũ sai owner, administrator kiểm tra chính
xác volume trước khi sửa offline; công cụ không tự chown volume bất kỳ.
Named volume cùng host không bảo vệ khỏi mất host/disk. Bố trí bản sao mã hóa ngoài
host và kiểm tra lại restore theo chính sách vận hành; repository chưa cung cấp
object-store transport, HA hoặc PITR.

Khai báo các biến **không chứa secret** theo
[job env example](../../tools/backup/backup-job.env.example): project source, immutable
image tag, reviewed full source SHA, HTTP port, năm đường dẫn env tuyệt đối và tên
backup volume. Giữ secret files ngoài checkout. Kiểm tra image identity:

```sh
export PRL_BACKEND_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "prl-backend:$PRL_IMAGE_TAG")"
test "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "prl-backend:$PRL_IMAGE_TAG")" = "$PRL_SOURCE_SHA"
dc() {
  docker compose --project-name "$PRL_BACKUP_PROJECT" \
    -f tools/deploy/compose.yaml -f tools/deploy/compose.local.yaml \
    -f tools/deploy/compose.backup.yaml "$@"
}
```

`PRL_SOURCE_SHA` phải là release đã review; không lấy một tag tùy ý rồi mặc nhiên
chấp nhận label của nó. Kiểm tra API/worker thực tế dùng cùng inspected image ID.
Không chạy `compose config` có resolve env để chép output lên ticket; dùng
`dc --profile backup config --no-env-resolution --quiet` khi kiểm tra cấu hình.

## 2. Backup, status và retention

```sh
dc --profile backup run --rm --no-deps -T backup backup
dc --profile backup run --rm --no-deps -T backup status
dc --profile backup run --rm --no-deps -T backup inspect --backup-id <backup-id>
dc --profile backup run --rm --no-deps -T backup retain
# Supported scheduled invocation: inspect image/volume and bound the entire job.
python3 -m tools.backup.run
```

Shell `<...>` ở đây là placeholder, phải thay bằng ID cụ thể từ sanitized success
report. Exit 0 là thành công, 1 là operation failure, 2 là config/arguments invalid.
`scheduled` chạy backup rồi retention dưới cùng lease; backup success và retention
failure là hai kết quả riêng. `status` không chứng minh ứng dụng đã restore/smoke.
Theo dõi failure reason, latest ID, backup age, duration và integrity/rehearsal flags.
Output JSON có thể được #35 thu nhận sau này, không cần OTLP/Grafana để operation chạy.

Giới hạn cấu hình trong external `backup.env`: `BACKUP_MAX_SECONDS` 1–3600,
query 1–120, lifecycle lock 1–30, max bytes, min-free bytes, keep-count và max-age.
Mặc định 600s/30s/3s/512MiB/64MiB/7/30 ngày. Cấp dung lượng đủ cho retained artifacts
**cộng một staging artifact**, filesystem overhead và margin; JSONL có overhead so với
dữ liệu MySQL. Kiểm tra tuổi/size thực tế, không suy ra capacity từ số rows.

Retention tính tuổi từ `completed_at` host UTC: bản cũ hơn cutoff hoặc ngoài count
bị xóa, trừ bản thành công mới nhất của từng source UUID/database. Biên tuổi bằng
cutoff còn được giữ. Store chứa nhiều UUID có thể giữ hơn keep-count tổng.
Artifact hỏng, partial và unrelated entries bị bỏ qua, không tự xóa; do đó vẫn có
thể đầy disk dù retention chạy. Chúng không được tính là recovery point thành công.

## 3. Cài lịch Linux

Copy repository đã review đến `/opt/production-readiness-lab`; chuẩn bị
`/etc/prl/backup-job.env` theo example cùng các secret files 0600. Python invoker chỉ
dùng stdlib, chạy image đã build; không tự pull/build/migrate/seed. Tài khoản systemd
cần quyền Docker socket (đây là quyền quản trị host).

```sh
sudo install -m 644 tools/backup/systemd/prl-backup.service /etc/systemd/system/
sudo install -m 644 tools/backup/systemd/prl-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start prl-backup.service
sudo systemctl enable --now prl-backup.timer
systemctl list-timers prl-backup.timer
journalctl -u prl-backup.service --since today
```

Mặc định 02:00 UTC hằng ngày, randomized delay tối đa 5 phút; Persistent chạy bù
một lần bị lỡ khi host trở lại. Đổi lịch bằng `systemctl edit prl-backup.timer`:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 01,13:00:00 UTC
RandomizedDelaySec=2m
```

Service mặc định timeout 1400s, invoker 1300s (backup tối đa 600s + retention tối đa
600s + startup/cleanup). Nếu tăng budget, đổi **cả** `BACKUP_MAX_SECONDS`,
`PRL_BACKUP_JOB_SECONDS` và systemd `TimeoutStartSec`, chừa ít nhất 60s cho cleanup.
Timer không tạo job thứ hai khi service đang active; lease còn chặn manual runs.
Không flock lại `.operation.lock`. Compose giới hạn job 512MiB/1CPU/64PIDs, readonly
root, tmpfs16MiB, nonroot, cap-drop và bounded logs; service host 256MiB/50%CPU.
Timeout/SIGTERM dọn đúng job container qua UUID label, không stop MySQL/application.
Nếu Docker daemon không trả lời nên cleanup không hoàn tất, kiểm tra label/ID trước
khi dọn thủ công; DB backup lock tự giải phóng khi session đóng.

## 4. Diễn tập tự động được hỗ trợ

Trên checkout sạch, dependencies đã cài, Docker local/Compose sẵn sàng và hai subnet
`172.30.34.0/29`, `172.30.36.0/29` còn trống:

```sh
python -m tools.backup.rehearsal
# Hoặc required acceptance: thiếu prerequisites phải fail, không skip.
RUN_BACKUP_REHEARSAL=1 python -m pytest tests/recovery -q --tb=short
```

Harness tái sử dụng `tests/containers/test_compose_acceptance.py` của #34, tạo cả
source giả và target riêng, không sử dụng database thật. Chỉ source giả được explicit
migrate/seed; target chỉ restore. Nó kiểm tra existing-volume backup-role provisioning,
source/recovered image identity, snapshot, schema/rows/FK/generated/UTC, real gateway
smoke và source unchanged. JSON allowlist nằm tại `build/recovery-review/rehearsal.json`;
không upload scratch, env files, Docker inspect toàn bộ, logs hay backup payload.
Harness cleanup cả khi thất bại và kiểm tra labels trước khi xóa riêng các tài nguyên
của nó; không chạy prune/down-v trên môi trường đang dùng.

## 5. Restore thủ công vào môi trường riêng

Ưu tiên diễn tập tự động để có bằng chứng lặp lại. Quy trình manual sau cần operator
review identity trước mỗi bước. Giữ source đang chạy; dùng terminal/config riêng cho
recovery, project/port khác, cùng immutable application images và backup volume.

1. Tạo năm env files **riêng** cho recovery theo #34. Dùng secret DB/runtime mới;
   runtime `DATABASE_URL` trỏ `mysql/prl_recovery_demo`. Không chạy seed target.
   Backup env có `RESTORE_DATABASE_URL` dùng `prl_migration` trên database đó;
   `DATABASE_URL` vẫn mô tả tên nguồn `prl` để guard không chấp nhận ghi vào nguồn.
2. Khởi tạo biến không secret `PRL_RECOVERY_CONFIG` là thư mục tuyệt đối mới, mode
   0700; chạy renderer và export đúng paths/IP mà JSON trả về:

   ```sh
   python3 -m tools.backup.recovery_config --directory "$PRL_RECOVERY_CONFIG"
   export PRL_RECOVERY_SUBNET=172.30.36.0/29
   export PRL_RECOVERY_GATEWAY_IP=172.30.36.2
   export PRL_RECOVERY_API_IP=172.30.36.3
   export PRL_RECOVERY_NGINX_CONF="$PRL_RECOVERY_CONFIG/nginx.conf"
   export PRL_RECOVERY_NGINX_ROUTES="$PRL_RECOVERY_CONFIG/routes.conf"
   rdc() {
     docker compose --project-name "$PRL_RECOVERY_PROJECT" \
       -f tools/deploy/compose.yaml -f tools/deploy/compose.local.yaml \
       -f tools/deploy/compose.backup.yaml -f tools/deploy/compose.recovery.yaml "$@"
   }
   rdc up -d --wait mysql
   ```

   Overlay thay cả upstream trong routes lẫn trusted-gateway identity map; không chỉ
   đổi subnet. Không attach recovery vào data network của source.

3. Qua socket administrator của **recovery MySQL**, tạo tên đích khác và rỗng, charset/
   collation khớp manifest. Cấp DDL/import và metadata privileges chỉ cho restore role:

   Ví dụ dưới dùng `partial_revokes=OFF` mặc định của demo. Nếu server đang bật mode
   này, bỏ backslash trước `_` trong scope của `GRANT`, giữ nguyên các tên khác.

   ```sh
   rdc exec -T mysql bash -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot --connect-timeout=5' <<'SQL'
   CREATE DATABASE prl_recovery_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
   GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,DROP,INDEX,REFERENCES,TRIGGER,EVENT ON `prl\_recovery\_demo`.* TO 'prl_migration'@'%';
   GRANT SHOW_ROUTINE ON *.* TO 'prl_migration'@'%';
   SQL
   rdc --profile backup run --rm --no-deps -T backup restore --backup-id <id> --target-database prl_recovery_demo
   rdc --profile backup run --rm --no-deps -T backup verify --backup-id <id> --target-database prl_recovery_demo
   ```

4. Review bản sao `tools/deploy/mysql/grants.sql`, thay **chỉ** literal `prl.` thành
   `` `prl_recovery_demo`. ``, apply bằng socket admin của recovery. Không sửa grants
   của source. Runtime API/worker đều phải dùng env recovery với quyền DML #34.
   `rdc run --rm -T schema` phải pass; sau đó `rdc up -d --wait api worker frontend gateway`.
5. Qua đúng origin/port recovery, chạy login/history/detail/availability/book/replay/
   cancel/replay/logout. Xác minh một booking tạo một audit/hai notifications, replay
   không tăng; cancellation thêm một audit/hai notifications, replay không tăng.
   Kiểm tra worker heartbeat, source unchanged và marker sau backup không có ở target.
   Đừng xác nhận recovery nếu chỉ import pass.

Trước bước 2 ghi recovery-start UTC và monotonic clock; sau smoke cuối ghi smoke-passed
UTC/elapsed. Incident và snapshot phải dùng cùng DB UTC clock để tính RPO range.
Báo cáo release/schema/checksum/validation/commands đã sanitize; không chép ID tài
khoản, token, session/cookie, response hay nội dung tư vấn. Public evidence dùng
allowlist `tools/backup/evidence.py`. Không tự đổi source URL hoặc chuyển traffic thật
sang target: live cutover nằm ngoài quy trình này.

## 6. Chẩn đoán và cleanup

| Reason / tín hiệu                                    | Xử lý                                                                                                                   |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `CONFIGURATION_INVALID`                              | Kiểm tra required external env, driver, absolute store, SHA/image format và bounds. Không in URL để debug.              |
| `OPERATION_ALREADY_RUNNING`                          | Chờ job hiện tại; kiểm tra container/token ownership và lease owner. Không xóa lock file khi còn job.                   |
| `UNSUPPORTED_SOURCE_SCHEMA`                          | Kiểm tra MySQL 8.4/InnoDB/UTC, bảng/cột/objects và direct metadata grants; không bỏ guard.                              |
| `SCHEMA_OR_RELEASE_INCOMPATIBLE`                     | Dùng đúng release/migration tree của backup; không sửa manifest hoặc seed/migrate để ép hợp lệ.                         |
| `ARTIFACT_INVALID`                                   | Quarantine artifact hỏng/thiếu; kiểm tra storage/copy/COMPLETE/checksum. Chọn bản verified khác.                        |
| `UNSAFE_RESTORE_TARGET` / `RESTORE_TARGET_NOT_EMPTY` | Dừng; xác minh project, service, DB name và URL. Tạo target riêng rỗng, không ghi đè.                                   |
| `DATABASE_OPERATION_FAILED`                          | Kiểm tra connectivity, privileges, source backup lock và target constraints bằng kênh vận hành private.                 |
| `RESTORED_DATA_VALIDATION_FAILED`                    | Quarantine target, kiểm tra schema/hash/FK/UTC từ artifact; không nhận import là recovery.                              |
| `STORAGE_UNAVAILABLE_OR_CAPACITY_EXCEEDED`           | Kiểm tra UID10001/private mode, free space, size budget. Giải phóng chỉ artifact đã review, luôn giữ last success.      |
| `OPERATION_DEADLINE_EXCEEDED`                        | Kiểm tra tải/lock và resource limits; tăng budget có kiểm chứng, đồng bộ scheduler/service timeouts.                    |
| Smoke fail dù restore pass                           | Kiểm tra image SHA, target runtime grants/URLs, gateway subnet/maps, schema health, worker, origin/CSRF; không cutover. |

Failed staging do tool đang sống được dọn trong finally. Crash/kill có thể để partial;
chúng không restorable và không bị retention xóa. Khi chắc chắn không còn operation,
administrator kiểm tra exact private path/owner/ID rồi quarantine hoặc xóa riêng
partial đó; không glob-delete store. Không xóa `.operation.lock` để “unlock”: OS lease
đã tự nhả khi process chết, thay inode có thể làm mất mutual exclusion.

Manual cleanup chỉ sau khi ghi nhận kết quả: đối chiếu exact Compose project labels,
container IDs, network labels, mysql volume name/label và backup volume purpose. Dừng
**recovery project** bằng `rdc down` không `-v`; chỉ xóa đúng empty rehearsal volume
đã kiểm tra, không xóa shared backup volume/source data. Harness tự động cũng kiểm
tra labels và chỉ xóa volume backup riêng của bài giả đó. Không dùng prune.
