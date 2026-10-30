# Nền tảng quan sát hệ thống (Issue #35)

Tài liệu này mô tả cấu hình local/demo chạy cùng host bằng Docker Compose và bằng chứng nghiệm thu. Xem [runbook](observability-runbook.md) để xử lý tín hiệu, cảnh báo, lỗi exporter, worker stall và khoảng trống telemetry; [thiết kế hệ thống](../architecture/system-design.md) mô tả topology và các giới hạn vận hành.

[Backup/restore #36](mysql-backup-restore.md) cung cấp JSON status/outcomes đã sanitize
(success/failure, identity/age/duration, artifact validation và rehearsal riêng).
Core operations chạy không cần telemetry. Có thể tích hợp những tín hiệu đó sau;
dashboard/alert hiện tại chưa được coi là monitor backup freshness hay xác nhận
application restore. Evidence lịch sử bên dưới giữ nguyên phạm vi/source ban đầu.

## Thành phần và vận hành

- Overlay chính: [`tools/deploy/compose.observability.yaml`](../../tools/deploy/compose.observability.yaml). Image/config được kiểm tra bằng [`tools/infra/check_telemetry.py`](../../tools/infra/check_telemetry.py) và khóa image [`tools/infra/images.lock.json`](../../tools/infra/images.lock.json).
- API và worker gửi logs/traces qua OTLP đến Collector; Prometheus lấy metrics, Loki giữ logs, Tempo giữ traces, Grafana cung cấp dashboard và Alertmanager chuyển cảnh báo đến receiver local.
- Grafana chỉ dành cho vận hành qua hostname riêng và HTTPS theo cấu hình. Giữ credential/TLS secret ngoài Git; không đưa request/trace IDs, payload, header, cookie, SQL hay nội dung nghiệp vụ vào incident note.
- Các truy vấn, ngưỡng cảnh báo, hướng dẫn redaction và recovery nằm trong [runbook](observability-runbook.md).

## Kết quả nghiệm thu

Nguồn ứng dụng và acceptance được kiểm tra tại SHA `0576f21305eb4b76a6b9e2436011a4ab23087366`. Toàn bộ `tests/containers` chạy với cả năm opt-in; JUnit ghi nhận **144 passed, 0 failed, 0 errors, 0 skipped** trong 2460.93 giây (2026-10-26T13:19:37.7335109Z–2026-10-26T14:00:38.6611312Z UTC). Full-stack runtime acceptance cũng pass trong cùng run; không có tracked input nào thay đổi. Docker Engine 29.5.3, Compose 5.1.4; image digests và SHA-256 của image lock nằm trong [runtime evidence](../evidence/observability-foundation/runtime-acceptance-run3.json).

Real API/MySQL smoke dùng schema UUID riêng trên MySQL 8.4.11. Các lệnh `alembic upgrade head`, `alembic current`, `alembic heads` và lặp lại `alembic upgrade head` đều thành công ở revision `20261019_1200_proposal_replay`; schema và container tạm đã được xác minh xóa.

Acceptance xác nhận:

- Luồng nghiệp vụ healthy và khi Collector, Loki, Prometheus hoặc Tempo dừng: readiness/API gateway tiếp tục trả HTTP 200; booking/replay/cancel/proposal/expiry giữ các hiệu ứng audit/notification mong đợi.
- Gateway log, API request log và Tempo trace khớp cùng một giao dịch; trace có MySQL query span nhưng không có SQL statement hoặc parameter attributes.
- Khi Collector dừng, gateway vẫn ready. Sau recovery, 9 scrape target trở lại; bộ đếm drop/failed tăng lũy kế từ 12 lên 401 (delta 389).
- Worker cùng slot khởi động lại với generation mới, giữ series tiến độ, scan DB sau recovery, hết hạn proposal đã lưu và không lặp hiệu ứng.
- Sự kiện gateway cũ 738 giây vượt cửa sổ truy vấn gần 600 giây: truy vấn recent trả HTTP 200 nhưng không có entry; truy vấn lịch sử neo vào timestamp gốc khớp lại đúng event. Đây là bằng chứng chạy thật cho đường kiểm tra sau Collector restart.
- Các trạng thái firing/resolved và thời điểm receiver nhận được pipeline alert cùng GatewayFastSuccess alert được ghi trong [runtime evidence](../evidence/observability-foundation/runtime-acceptance-run3.json). Bản ghi TelemetryTargetDown có thời điểm quan sát firing/resolution; envelope không ghi trạng thái hoặc delivery của receiver cho bản resolve đó.

## Kích hoạt và rollback

Thiết lập host, secret và TLS theo [hướng dẫn containerized demo](containerized-demo.md). Khi manifest HTTPS đã cấu hình, kiểm tra và bật observability:

```sh
tools/deploy/demo.sh config --overlay https --observability
tools/deploy/demo.sh up --overlay https --observability
```


Để rollback cấu hình observability, chạy lần lượt:

```sh
tools/deploy/demo.sh down --overlay https --observability
tools/deploy/demo.sh config --overlay https
tools/deploy/demo.sh up --overlay https
```

Lệnh down giữ named volumes; không dùng down -v. Quy trình bỏ overlay telemetry khỏi deployment nhưng giữ dữ liệu MySQL và telemetry.

## Dashboard evidence

Ảnh được chụp ở vùng đầu dashboard 1600×760; các log panel phía dưới bị loại khỏi ảnh để không lộ correlation/run IDs. SHA-256 từng ảnh đã được đối chiếu với envelope nghiệm thu. Tên thư mục evidence đã bỏ hậu tố capture sinh tự động.

| Dashboard | UID / ảnh | SHA-256 |
|---|---|---|
| API errors | [prl-api-errors](../evidence/observability-foundation/screenshots/run3/prl-api-errors.png) | `18e0cf65e7e518514e4e9294b25a6b1c09bcd1fefc84271ef96e871265eaf146` |
| DB / pool | [prl-db-pool](../evidence/observability-foundation/screenshots/run3/prl-db-pool.png) | `99499957423587da48909bce55e09a26f2c7fd3b0c90afac19c157724f7f886e` |
| Gateway SLI | [prl-gateway](../evidence/observability-foundation/screenshots/run3/prl-gateway.png) | `a731169a2a0b2fae208712cffee22ca667216469442ac62e94bb0720f44f2e7c` |
| Telemetry loss | [prl-telemetry-loss](../evidence/observability-foundation/screenshots/run3/prl-telemetry-loss.png) | `53ab38447dc5013366b7adb72199f53bf722ede0070d54e4085413f6c2f8b68d` |
| Worker progress / backlog | [prl-worker-progress](../evidence/observability-foundation/screenshots/run3/prl-worker-progress.png) | `dbe46f64635ac0e98060356019980a9212a4d4f40945df8d268906a0da1a03a9` |

Trong ảnh API và DB, một số panel ghi “No data”. Đây là cửa sổ không có mẫu, không phải bằng chứng giá trị bằng 0; nội dung hướng dẫn trên dashboard phân biệt trường hợp thiếu telemetry family.

## Cấu hình và giới hạn

- Retention hiện cấu hình 168 giờ cho Loki/Tempo và 14 ngày cho Prometheus. Chưa có soak đủ thời lượng hoặc backup/restore proof.
- Envelope run3 ghi resource cap cho 13 service Compose, tổng 5,888 MiB. Một số test integration dùng operations probe tạm thời có cap 128 MiB trong cấu hình test; probe không thuộc tổng 13 service của envelope run3. Đây là giới hạn cấu hình, không phải mức tiêu thụ đo được. Hash SHA-256 các file Compose, gateway và telemetry configuration ở source SHA này được liệt kê trong provenance của [runtime evidence](../evidence/observability-foundation/runtime-acceptance-run3.json).
- Gateway spool 64 MiB là volatile và nằm trong cap của gateway. Named volumes giữ dữ liệu MySQL/telemetry theo cấu hình.

Giả định sizing để lập kế hoạch là host demo có 8 vCPU, 16 GiB RAM, ít nhất 50 GiB dung lượng trống và đồng hồ được đồng bộ; acceptance run không đo xác nhận các điều kiện này. Retention 168 giờ/14 ngày không đặt quota kích thước volume; Compose không giới hạn dung lượng từng named volume, nên cần theo dõi disk usage thực tế. Nghiệm thu này dùng dữ liệu hư cấu trên một Docker host. Nó không chứng minh production load, SLO đủ 7 ngày, probe/notification ngoài host, host outage, backup/restore hay trạng thái zero-vulnerability. Các mục tiêu đó cần kiểm tra riêng trên môi trường triển khai thực tế.
