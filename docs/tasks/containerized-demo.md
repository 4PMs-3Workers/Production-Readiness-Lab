# Hướng dẫn triển khai demo bằng Docker Compose

Hướng dẫn này chạy Next.js, FastAPI, worker xử lý đề nghị hết hạn, MySQL 8.4 và
Nginx trên một máy Linux. Compose chỉ công khai cổng Nginx. API, giao diện, worker
và MySQL dùng mạng nội bộ; MySQL ghi vào volume có tên.

Đây là nền tảng smoke cho triển khai demo, chưa phải nghiệm thu production hoặc
SLO. Tài liệu này không thiết lập registry, HA, backup/restore, giám sát ngoài máy
chủ hay quy trình khôi phục production. Dữ liệu trong seed là hư cấu, nhưng mật
khẩu demo vẫn cần được giữ bí mật và đổi khi cần.

## Điều kiện trước khi bắt đầu

- Máy Linux x86-64 có Docker Engine đang hoạt động và Docker Compose v2 mới đủ
  hỗ trợ `env_file.format: raw`, buildx/BuildKit, health-aware Compose startup và
  lệnh `docker compose up --wait`.
- Git checkout chứa commit muốn kiểm chứng. `tools/deploy/demo.sh` yêu cầu cây Git
  sạch và `HEAD` không đổi trong suốt thao tác; vì vậy hãy commit thay đổi trước.
- Với HTTPS: DNS/origin đã chọn, chứng thư hợp lệ cho hostname và private key được
  cấp ngoài repository. Quyền đọc key phải cho phép container Nginx UID/GID 101.
- Với HTTPS remote: giới hạn firewall tới cổng gateway và dùng một origin HTTPS
  khớp chính xác với chứng thư, cổng và cấu hình cookie.

Docker Desktop trên Windows/macOS có thể dùng cho phát triển, nhưng bài nghiệm thu
Linux CI chạy trên Docker Engine Linux. Host cần giải phóng subnet cố định
`172.30.34.0/29`; các stack dùng cùng overlay gateway không thể cùng chiếm subnet này.

## Tạo các tệp cấu hình và secret

Tạo bốn tệp raw env ngoài repository với quyền chỉ cho chủ sở hữu đọc. `PRL_*_ENV_FILE`
phải trỏ tới đường dẫn tuyệt đối. Một thư mục riêng trong home dùng được cho demo local:

```sh
umask 077
install -d -m 700 "$HOME/.config/prl-demo"
```

Tạo các tệp `db.env`, `runtime.env`, `migration.env`, `seed.env` trong thư mục đó;
thay mọi `<...>` bằng giá trị local mới. Dùng các password sinh ngẫu nhiên, ví dụ
`openssl rand -hex 32`. Password của database user được nhúng trong URL nên dùng
chuỗi URL-safe hoặc percent-encode password. Hai secret cookie phải độc lập.

`db.env` khai báo các khóa:

```dotenv
MYSQL_ROOT_PASSWORD=<random-root-password>
PRL_MIGRATION_PASSWORD=<random-migration-password>
PRL_RUNTIME_PASSWORD=<random-runtime-password>
PRL_SEED_PASSWORD=<random-seed-database-password>
```

`runtime.env` chứa cấu hình process API/worker. Bắt buộc đặt `DATABASE_URL`,
`APPLICATION_ORIGIN`, `COOKIE_SECRET`, `CSRF_SECRET` và
`ACCEPTED_SCHEMA_REVISIONS=20261019_1200_proposal_replay`. Với HTTP local, đặt
`APPLICATION_ORIGIN=http://localhost:8080`, `ENVIRONMENT=local` và
`COOKIE_SECURE=false`. URL dùng user `prl_runtime` cùng password trong `db.env`.
Giữ `COOKIE_DOMAIN` không đặt; mặc định cookie host-only. Các giới hạn thời gian,
pool và worker mặc định được ghi trong `.env.example`.

```dotenv
DATABASE_URL=mysql+asyncmy://prl_runtime:<database-password>@mysql/prl
APPLICATION_ORIGIN=http://localhost:8080
COOKIE_SECRET=<random-cookie-secret>
CSRF_SECRET=<different-random-csrf-secret>
ACCEPTED_SCHEMA_REVISIONS=20261019_1200_proposal_replay
ENVIRONMENT=local
COOKIE_SECURE=false
```

Với HTTPS, overlay ghi đè `APPLICATION_ORIGIN` và `COOKIE_SECURE` ở runtime theo
`PRL_HTTPS_ORIGIN`; cookie vẫn host-only.

`migration.env` chỉ cấp URL user migration; `seed.env` chỉ cấp user seed và các mật
khẩu tài khoản demo:

```dotenv
# migration.env
DATABASE_URL=mysql+asyncmy://prl_migration:<database-password>@mysql/prl
```

```dotenv
# seed.env
DATABASE_URL=mysql+asyncmy://prl_seed:<database-password>@mysql/prl
ENVIRONMENT=local
DEMO_SEED_DATABASE=prl
DEMO_ADMIN_PASSWORD=<random-demo-admin-password>
DEMO_STAFF_PASSWORD=<random-demo-staff-password>
DEMO_STUDENT_PASSWORD=<random-demo-student-password>
```

Mỗi password tài khoản demo cần dài ít nhất 12 ký tự. Không đưa secret thật vào
shell history, Git, log, URL web hay file Compose.

Tạo `paths.env` cùng thư mục để dễ nạp lại biến đường dẫn vào shell:

```sh
cat > "$HOME/.config/prl-demo/paths.env" <<'EOF'
export PRL_DB_ENV_FILE="$HOME/.config/prl-demo/db.env"
export PRL_RUNTIME_ENV_FILE="$HOME/.config/prl-demo/runtime.env"
export PRL_MIGRATION_ENV_FILE="$HOME/.config/prl-demo/migration.env"
export PRL_SEED_ENV_FILE="$HOME/.config/prl-demo/seed.env"
export PRL_HTTP_PORT=8080
EOF
chmod 600 "$HOME/.config/prl-demo"/*.env
```

Khi source `paths.env`, shell mở rộng `$HOME` trong các dòng `export` và đưa
đường dẫn tuyệt đối vào môi trường của shell. Không truyền `paths.env` trực tiếp
cho Docker Compose; hãy truyền các env file mà những biến này trỏ tới. Nạp các
biến trước khi gọi script:

```sh
set -a
. "$HOME/.config/prl-demo/paths.env"
set +a
```

Docker Compose đọc bốn env file riêng ở định dạng raw. Tệp `paths.env` chỉ chứa đường
dẫn và port, không chứa credential. `compose config --quiet` chỉ xác thực cấu hình;
không chạy `docker compose config` không có `--quiet` hoặc `--no-env-resolution`, vì
đầu ra đã render có thể chứa secret. Các gán biến trong `paths.env` mở rộng `$HOME`
thành đường dẫn tuyệt đối khi file được nạp bằng shell như bên dưới.

## Build, migrate và chạy HTTP local

Thực hiện từ gốc repository trên commit đã chọn:

```sh
tools/deploy/demo.sh config --overlay local
tools/deploy/demo.sh build --overlay local
tools/deploy/demo.sh migrate --overlay local
```

`migrate` chạy migration bằng tài khoản DDL riêng, áp lại grant theo danh sách quyền
đã định nghĩa và kiểm tra schema. API/worker không tự migrate. Seed dữ liệu hư cấu là
thao tác tách biệt và opt-in:

```sh
tools/deploy/demo.sh seed --overlay local
```

Nếu không muốn cấp demo accounts, bỏ qua seed. Sau migration (và tùy chọn seed),
khởi động và xác minh gateway, frontend, API, schema cùng heartbeat/tiến triển worker:

```sh
tools/deploy/demo.sh up --overlay local
tools/deploy/demo.sh status --overlay local
tools/deploy/demo.sh evidence --overlay local
```

Mở `http://localhost:8080/login`. Tài khoản hư cấu và các luồng đặt/hủy lịch được
mô tả trong [hướng dẫn seed](demo-seed.md). Dùng `tools/deploy/demo.sh stop --overlay local`
để dừng container mà giữ nguyên database. `tools/deploy/demo.sh down --overlay local`
cũng gỡ container/network nhưng giữ nguyên volume dữ liệu; `up` sau đó dùng lại volume.

Script tuần tự hóa thao tác bằng lock local, kiểm tra source sạch và ghi manifest tại
`build/deploy/<source-sha>/manifest.json`. Manifest lưu commit, image ID/digest,
overlay, schema revision và kết quả lệnh; không ghi credentials. `build/` bị Gitignore.
Giữ manifest đã khử thông tin nhạy cảm làm bằng chứng cùng commit và kết quả smoke.

## Chạy HTTPS với chứng thư ngoài

Đặt biến đường dẫn tuyệt đối `PRL_TLS_CERT_FILE` và `PRL_TLS_KEY_FILE`, cùng
`PRL_HTTPS_ORIGIN=https://<hostname>[:port]`; origin phải đúng với URL người dùng sẽ
mở. Chứng thư phải có chuỗi tin cậy của trình duyệt và SAN đúng hostname; key phải
được đọc bởi UID/GID 101 trong Nginx. Bảo vệ key trên host, không copy vào checkout.
HTTPS bật cookie Secure, host-only, SameSite=Lax. Giữ `PRL_HTTPS_BIND=127.0.0.1`
nếu chỉ truy cập tại host; để demo remote phải cấu hình bind/firewall có chủ ý.

Sau khi export biến, thay `--overlay local` bằng `--overlay https` cho từng lệnh:

```sh
tools/deploy/demo.sh config --overlay https
tools/deploy/demo.sh build --overlay https
tools/deploy/demo.sh migrate --overlay https
tools/deploy/demo.sh seed --overlay https  # tùy chọn
tools/deploy/demo.sh up --overlay https
```

HTTPS bind mặc định `127.0.0.1:8443`. Cần origin/port trùng với public address nếu
đổi `PRL_HTTPS_PORT`; gateway chứng minh liveness, còn API readiness được kiểm tra riêng.
Kiểm tra TLS từ máy client dùng chứng thư được hệ thống tin cậy trước khi công bố link.

## Chẩn đoán và dữ liệu bền vững

`/health/gateway` kiểm tra Nginx, `/health/ready` kiểm tra kết nối DB và schema đã
được chấp nhận, `/login` kiểm tra trang frontend. `status` hiển thị trạng thái/health
của các service. Nếu API không ready, kiểm tra migration và schema head; nếu worker
không có tiến triển, kiểm tra service riêng `worker` và quyền DB. Nếu database tạm
thời dừng, giữ nguyên volume và khởi động lại MySQL; readiness phải trở lại khi DB
phục hồi. Xem [gateway contract](../../tools/deploy/nginx/README.md) để biết subnet,
request ID, cookie và log policy.

Volume tên `prl-demo_mysql_data` do Docker quản lý. `stop` và `down` không xóa volume.
Không chạy `docker compose down -v`, `docker volume prune`, `docker system prune` hoặc
`docker volume rm` trên môi trường chứa dữ liệu cần giữ. Docker volume không phải
backup; quy trình backup/restore phải được thiết kế và kiểm thử riêng trước khi dùng
dữ liệu quan trọng. Đổi giá trị env sau khi MySQL volume đã được khởi tạo không tự
đổi credential hiện có trong MySQL.

## Smoke acceptance và giới hạn

GitHub Actions workflow `Containerized demo` chạy trên Ubuntu với timeout 45 phút,
xác thực formatting YAML, cài Chromium, build cả hai application image và chạy
`tests/containers/test_compose_acceptance.py`. Bài test tạo project, tag image, tài
khoản và volume UUID riêng; nó kiểm tra student/staff browser flow, restart/persistence,
schema failure, database outage/recovery và worker expiry. Teardown chỉ nhận tài
nguyên đã xác minh thuộc project test và đối chiếu snapshot `prl-demo`.

Đây là nền tảng smoke cho đường triển khai Compose, không xác nhận production readiness
hoặc SLO. CI không push/publish images và không dùng credentials thật. Hệ thống này
chưa có quy trình phát hành registry, backup/restore, HA, quan sát đa host, tải production
hay diễn tập fault injection.
