# Implementation plan: xử lý NFR-03 cho export CSV

Ngày lập: 25/11/2026, Asia/Bangkok. Trạng thái: plan; chưa triển khai thay đổi runtime/harness, chưa đo tải mới trong lượt lập plan.

## 1. Mục tiêu và mốc nguồn

Mục tiêu là xác định vì sao export bị lỗi lấy connection ở bước auth, khắc phục nguyên nhân được đo, và chứng minh release candidate đạt cả tiêu chí lỗi lẫn latency ở workload đã công bố.

- PR tính năng: [PR #55](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/55).
- HEAD đã kiểm tra: `8dcfd1c824963ae343e1d87db658d3fd502ad0ac`; cả tám workflow PR đã SUCCESS. Đây là kết quả CI chức năng/recovery, chưa phải kết quả NFR-03.
- Base develop tại thời điểm lập plan: `aed6c68a4a081f558d01e9ba4c39cd031439af47`.
- Review nhắc release blocker: [comment 24/11](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/55#issuecomment-7132980502).
- Bản đo cũ: `e7392e3ebfea2da9fe20f381d909deef0e849dbb`, MySQL 8.4.13 trên Windows, ASGITransport + MySQL TCP, 80 client dùng chung một session thật.

Khi triển khai, tạo worktree `D:\SE\tmp\nfr03-export-pool`, nhánh `codex/nfr03-export-pool` từ HEAD #55 đã xác minh lại. Nhánh NFR có thể được phát triển trong lúc reviewer xem hai sửa recovery. Không push thay đổi NFR lên nhánh #55 đang review. PR mới cần ghi rõ dependency vào #55; nếu #55 chưa merge, dùng stacked PR base nhánh #55 để diff chỉ gồm NFR, rồi đổi base sang develop sau khi #55 merge và kiểm tra lại diff/CI. Không merge hay phát hành chỉ vì tạo xong plan.

PDF đã nộp và bằng chứng lịch sử giữ nguyên. Không rewrite lịch sử. Plan này chỉ là file local cho đến khi bắt đầu nhánh NFR.

## 2. Bằng chứng đang có và giới hạn kết luận

Lượt lập plan đã đọc `build/vt071/review-pool-production-performance.json`, `candidate-head.txt`, code auth/export, cấu hình engine và các harness tải. Các con số sau được trích từ báo cáo đã có, không phải một lượt benchmark mới:

| Phase cũ | HTTP 503 / 80 | p95 thành công, giây |
| --- | ---: | ---: |
| cold | 56 | 0,875 |
| warmup80 | 45 | 0,884 |
| warm1 | 45 | 0,909 |
| warm2 | 45 | 0,733 |
| warm3 | 45 | 0,729 |
| warm4 | 50 | 0,801 |
| warm5 | 55 | 0,743 |
| Tổng | 341 / 560 | không gộp percentile các phase |

Tất cả 341 lỗi được báo là `sqlalchemy.exc.TimeoutError` khi auth lấy connection. Mục tiêu p95 thành công ≤ 2,5 giây đạt ở từng phase, nhưng điều kiện zero errors không đạt.

Cấu hình mặc định trong `core/config.py`/`factory.py`: pool_size=5, max_overflow=5, pool_timeout=0,5 giây, connect timeout=1 giây, DB query timeout=1 giây, whole-request timeout=3 giây. Pool có tối đa pool_size + max_overflow connection cùng lúc theo [hợp đồng QueuePool của SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/pooling.html); đây là giới hạn mỗi engine/process, không phải toàn deployment.

Những điều chưa thể kết luận từ báo cáo cũ:

- Timer sub-stage hiện chủ yếu ghi cho `STAGE == export`; auth chỉ có timer tổng và loại lỗi execute. Chưa tách được auth acquisition, giữ connection, SELECT và transaction exit.
- p95 event-loop lag của từng burst khoảng 0,031–0,327 giây; host CPU chỉ là trung bình toàn máy 16 logical CPU. Không suy ra một core, API hay mysqld có dư tài nguyên.
- Một request dự kiến có transaction auth rồi transaction export, nhưng cần đếm checkout/checkin để xác minh lifecycle thực tế; không mặc định hai transaction đồng nghĩa hai connection vật lý mới.
- Phase tên `cold` đã qua migrations, seed, login và serial request. Nó không chứng minh cold start sau restart MySQL.
- Harness hiện không đưa warmup80 vào assertion cuối, dù tổng lỗi có cộng warmup. Protocol mới sẽ định nghĩa rõ việc này, không sửa số đo lịch sử.
- File JSON hiện bị ghi đè theo tên cố định. Thiếu run identity và immutable output sẽ làm so sánh thử nghiệm dễ nhầm.
- `tools/load` của nhóm chạy mixed workload 20 session, không phải bài export 80 request. PASS của nó không thay thế NFR-03.

## 3. Hợp đồng đo phải chốt trước khi sửa

Tạo protocol phiên bản `nfr03-export-v2`, tách khỏi harness lịch sử. Tất cả run dùng cùng version phải giữ dataset, concurrency, arrival pattern, phase order, pacing và verdict như nhau.

### 3.1. Workload bắt buộc

- Endpoint thật: `GET /api/v1/appointments/export`; auth/session/owner filtering thật, MySQL thật.
- Dataset so sánh lịch sử: 100 hàng thuộc owner và ít nhất một hàng foreign canary; expected cardinality, ID set và thứ tự được dựng từ fixture, không lấy response đầu tiên làm oracle duy nhất.
- 80 request phát cùng barrier, không tự retry. Ghi thời điểm barrier release, actual send, response body hoàn tất và CSV validation hoàn tất riêng.
- Latency end-to-end tính từ release/scheduling workload tới body nhận đủ; client validation CPU đo riêng. Báo send skew để nhận biết generator không thực sự tạo được burst như công bố.
- Giữ bảy burst: `first_burst`, `warmup80`, `warm1`…`warm5`. Hai request serial dùng xác minh fixture trước burst được ghi là setup và không đưa vào 560 request.
- Protocol v2 kiểm tra zero errors và p95 ≤ 2,5 giây cho **mọi burst**, kể cả warmup80, đồng thời đủ 560/560 request. Việc này chặt hơn assertion v1, phải ghi rõ khi so sánh.
- Response chỉ thành công khi HTTP 200, đúng định dạng CSV, cardinality/owner/order đúng. HTTP lỗi, timeout, network error, response sai hoặc thiếu mẫu đều là lỗi, không bị loại khỏi denominator.
- Dùng nearest-rank percentile; nếu không có success thì p95_success=null và verdict FAIL. Báo p50/p95/max cho success và tất cả response, cùng số lượng/error breakdown. Không dùng p95 tổng thấp do 503 trả nhanh để kết luận đạt.

### 3.2. Các kịch bản bổ sung

- Chạy lại shared-session như lịch sử; thêm nhiều session thật được chuẩn bị trước workload để xem kết quả có phụ thuộc session chung. Login nằm ngoài timed burst; tôn trọng login throttle, không tắt cơ chế bảo vệ để seed session nhanh.
- Case 0, 100, 1.000 hàng hợp lệ và 1.001 hàng trả 409 đúng nghiệp vụ. Chỉ case 100 hàng là denominator so sánh 560 lịch sử; 409 ở case overflow là negative functional check, không được gộp thành thành công của workload zero-error.
- Một lượt first-burst sau restart API/engine riêng. Cold MySQL chỉ chạy nếu có MySQL lab do run sở hữu và đã ghi chính xác thao tác restart; không restart MySQL dùng chung của người khác.
- Lượt sustained khoảng 10 phút với arrival rate/concurrency công bố riêng để xem hold/leak/drift và cạnh tranh với worker/mixed workload. Đây là evidence bổ sung, không thay đổi workload burst bắt buộc.

### 3.3. Tính hợp lệ của môi trường

Giữ MySQL version, CPU/memory limit, driver/dependencies, telemetry mode và worker topology cố định giữa baseline/candidate cùng một cặp so sánh. Đo qua HTTP/gateway trên topology dự kiến release; ASGI dùng chẩn đoán và regression, không tự đại diện cho gateway/TLS/container limits.

Đánh giá generator riêng với server khi chạy HTTP. Mượn tiêu chí saturation của `tools/load/runner.py`: p95 lag > 0,1 giây hoặc hơn 10% tick trễ > 0,1 giây, hoặc thiếu mẫu thì không chứng nhận run đạt. Chọn chu kỳ lấy lag cố định, công bố trước run và kiểm tra barrier/send skew. Với ASGI cùng event loop, lag cao chưa phân biệt được client/server; run đó chỉ dùng chẩn đoán, cần xác minh bằng client ngoài process.

## 4. Instrumentation: đo đúng phần đang thiếu

Ưu tiên hooks có phạm vi run trên đúng engine, tháo trong `finally`. Không đổi public API response hoặc ghi payload. Đặt helper đo/đọc báo cáo trong `tools/load/`; code benchmark trong test opt-in tái dùng helper này.

| Số đo | Cách thu dự kiến | Giới hạn phải ghi |
| --- | --- | --- |
| Auth/export tổng | Wrapper ở service entry/exit, correlation theo task/request | Bao scheduling, không đồng nghĩa thời gian DB |
| Acquisition | Bắt đầu trước lần DB operation cần connection, kết thúc khi checkout/connection acquisition hoàn tất | Bao queue/connect/pre-ping/scheduling; không gọi toàn bộ là pure queue wait |
| Hold time | Pool checkout → checkin; theo connection record, ghép stage lúc checkout | Có thể gồm DB/network, code Python và chờ event loop; xử lý invalidation không có checkin bình thường |
| SQL round trips | Statement begin/end, operation label hữu hạn; đếm theo stage | Buffered execute có thể gồm wire fetch; không ghi SQL text/bind values |
| Transaction exit | Đo quanh context exit/commit/rollback tương ứng | Phân biệt commit, rollback, cleanup; hook trước commit không đủ để đo commit hoàn tất |
| Pool pressure | checked_out, connects/closes/invalidates, peak và outstanding sau drain | Scrape 15 giây có thể bỏ sót burst dưới một giây; dùng event counters/peak nội bộ |
| Host/DB | CPU từng process/core khi có, memory, Threads_running/connected, Aborted_clients, DB-owned process state | Thiếu dữ liệu phải null/unavailable, không giả định bằng 0 |
| Client | Event-loop lag, send skew, HTTP connection-limit wait, body/CSV validation CPU | HTTP client phải cho ≥80 in-flight; không để pool client che pool server |

Không suy pure queue wait chỉ từ event checkout. Nếu cần explicit `session.connection()` để phân tách, đặt nó ở diagnostic mode có nhãn riêng vì có thể đổi vị trí checkout. Acceptance mode không monkeypatch service/transaction hay thêm checkout. So overhead bằng cặp diagnostics ON/OFF cùng source/config; báo cả hai, không chọn mode đẹp hơn sau khi thấy kết quả.

Kiểm tra checkout/checkin balance sau khi request và cleanup đã drain; không yêu cầu mọi physical connection đóng vì pool có connection idle hợp lệ. Đếm theo API engine; observer/fixture/worker có pool khác phải được ghi riêng.

## 5. Trình tự triển khai

### Bước A — Ghim baseline và tách môi trường

1. Kiểm tra PR55 HEAD/base, worktree sạch và các SHA lịch sử. Tạo nhánh/worktree NFR riêng, chép plan vào nhánh đó khi triển khai.
2. Dùng venv riêng với `requirements-dev.txt` và package candidate; giữ Python/dependency versions khớp runtime sẽ phát hành. Không dùng editable install đang trỏ sang worktree khác.
3. Chuẩn bị MySQL lab hoặc Docker project riêng. Schema dùng UUID và ownership marker; runtime dùng quyền tối thiểu, setup/cleanup dùng identity lab riêng.
4. Đọc runtime env theo allowlist setting cần đo, không xuất file env chứa secret. Xác minh số API process/replica, worker pools, MySQL max_connections và container limits thực tế.
5. Copy evidence cũ vào archive local có checksum trước khi chạy harness v1 đang ghi tên cố định. Nếu v1 ghi trùng tên, không chạy trực tiếp trên nơi giữ bản duy nhất.
6. Chạy baseline v1 một lượt để xem triệu chứng còn xuất hiện trên môi trường mới; sau đó chạy baseline v2. Không so v1 với v2 như before/after fix.

Đầu ra: baseline manifest chứa source/config hash, dependency/image identity, protocol, environment/resource inventory, command và các kết quả FAIL/PASS thực tế.

### Bước B — Bổ sung đo và khóa verdict

1. Thêm helper/report schema/protocol v2 với run directory riêng, các phép đo ở mục 4 và output giới hạn kích thước.
2. Viết unit tests cho verdict, percentile, missing samples, duplicate IDs, missing timings, profile isolation, sanitization và retention của nhiều run. Đây là test logic quyết định, không assert tốc độ theo wall-clock trong CI thường.
3. Chạy baseline diagnostic ON/OFF. Nếu instrumentation làm thay đổi đáng kể error count hoặc latency thì báo overhead, dùng acceptance không instrumentation làm verdict, không loại baseline xấu.
4. Lập timeline từ acquisition/hold/SQL/transaction exit; chia success/failure và phase. Xác định pool bão hòa vì hold dài, connection churn, cleanup/invalidation, event loop hay budget không đủ. Chưa có kết luận thì tiếp tục phép đo phân biệt, không đổi nhiều setting cùng lúc.

Đầu ra: ghi chú chẩn đoán ngắn, dữ liệu đối chiếu và một giả thuyết có phép kiểm bác bỏ.

### Bước C — Thực hiện bản sửa nhỏ nhất có bằng chứng

| Điều kiện quan sát được | Bản sửa ưu tiên | Regression bắt buộc |
| --- | --- | --- |
| Auth giữ connection lâu do query/materialization không cần thiết | Thu hẹp projection/round trips tại repository/auth theo plan SQL thực tế | Session invalid/expired/revoked, account inactive/deleted, role/owner, timezone/expiry boundary |
| Connection giữ qua công việc Python không cần DB | Đưa phần việc độc lập ra sau transaction khi dữ liệu đã materialize đầy đủ | Không lazy-load sau close; dữ liệu/CSV không đổi; transaction kết thúc đúng |
| Cancellation/exception không trả connection hoặc cleanup chậm bất thường | Sửa lifecycle/cleanup có giới hạn trên đúng đường lỗi | Request cancel, query/pool timeout, rollback failure, invalidation; request kế tiếp dùng pool được |
| Pool có churn hoặc thiếu budget so với workload đã cam kết | Chọn cấu hình deployment có bound theo mục 6 | Peak connections/CPU/memory trong budget, lỗi thật vẫn bounded, auth/booking không suy giảm |
| Nút thắt ở generator, Windows scheduler hoặc driver/topology | Tách client/server, đối chiếu Linux và môi trường release; sửa phần đã xác nhận | Không gọi thay môi trường là production fix; giữ cả kết quả cũ và mới |

`authenticate()` hiện dùng `find_with_user()` rồi `users.utc_now()` trong `_transaction()`. Gộp query là ứng viên cần đo, không phải quyết định sẵn. MySQL đánh giá `UTC_TIMESTAMP()` ở đầu statement theo [tài liệu MySQL 8.4](https://dev.mysql.com/doc/refman/8.4/en/date-and-time-functions.html); gộp query sẽ dịch thời điểm lấy clock so với query thời gian riêng. Phải kiểm tra expiry equality, clock UTC và semantics auth trước khi chấp nhận. Không thay DB clock bằng clock host hoặc cache bỏ qua revocation.

`_transaction()` dùng chung cho login/logout; nếu chỉ tối ưu read-auth thì ưu tiên sửa đường read có phạm vi riêng. Không đổi toàn bộ transaction của auth để cải thiện một endpoint mà thiếu regression login/issuance/revocation.

`export_student_appointments()` hiện map records thành schema trong read transaction rồi render CSV sau khi đã trả connection. Chỉ chuyển mapping ra ngoài khi số đo cho thấy cần và đã chứng minh không có ORM lazy access. Không đổi LIMIT 1001, owner guard, ordering hay overflow 409 để giảm việc xử lý.

Mỗi candidate đổi một yếu tố, có baseline v2 tương ứng và giữ tất cả kết quả. Tối đa một số cấu hình thử nghiệm nhỏ đã công bố trước; không quét setting cho đến khi tình cờ thấy một PASS.

### Bước D — HTTP/gateway proof

1. Bổ sung `tools/load/export_probe.py` cho export burst, tách entry point khỏi mixed runner. Tái dùng cấu hình/ownership/schema lifecycle và target-attestation checks của `tools/load`; không import helper dưới `tests/` vào tooling.
2. Dùng fixture profile NFR riêng: user hư cấu, history hợp lệ 100 hàng/owner và foreign canary. Ghi expected inventory private trong run; report công khai chỉ chứa counts và verdict. Giữ marker để cleanup biết schema thuộc run.
3. Nếu mở rộng fixture module của nhóm, giữ default mixed profile và các invariant của nó. Đo export là read-only: sau workload kiểm tra DB snapshot/invariant counts không đổi. Setup dữ liệu nằm ngoài timed burst.
4. Kiểm tra live target source/config/image attestation trước/sau run, HTTP request ID, đúng runtime schema và quyền owner. HTTPS dùng chứng chỉ hợp lệ; plain HTTP chỉ cho loopback lab theo contract hiện có.
5. HTTP probe phải chạy được khi không có Loki/Prometheus để chẩn đoán response trực tiếp, nhưng chứng nhận release cần đủ target/resource/evidence đã công bố. Thiếu telemetry hoặc target identity → INSUFFICIENT_EVIDENCE; không tự chuyển thành PASS.
6. Chạy lại mixed workload của nhóm và business invariants như kiểm tra tác động phụ. Kết quả của nó được ghi riêng với export NFR-03.

Tái dùng logic an toàn của tools/load nhưng không nới throughput/SLI/verdict của mixed workload thành mục tiêu export. CLI/flags của export probe sẽ được chốt cùng implementation; các lệnh v1 ở mục 10 không được mô tả thành probe HTTP mới đã có sẵn.

## 6. Quyết định pool/timeout và budget phát hành

Trước khi chỉnh setting, lập bảng cho từng API process/replica, worker process và các engine nền. Upper bound tổng connection ứng dụng là tổng `process_count × (pool_size + max_overflow)` của từng loại, cộng observer/ops/migration/backup và dự phòng quản trị. Đối chiếu với max_connections, RAM/CPU MySQL và tải ngoài export. Không lấy 10 connection của một API engine làm budget của toàn hệ thống.

Mức pool/timeout candidate chưa được chọn trong plan vì chưa có hold-time breakdown. Hướng quyết định:

1. Thử bản sửa code trên default 5+5/0,5 giây trước để xem có giải quyết được bottleneck mà giữ cấu hình cũ không.
2. Nếu cần deployment tuning, chọn bounded profile từ service time/arrival pattern và budget đã đo; giữ default case riêng. Các biến thử có thể gồm retained pool và overflow nhưng không đổi cùng timeout ở lần thử đầu.
3. Nếu thay pool timeout, phải phân tích DB timeout 1 giây và request deadline 3 giây, cả auth lẫn export/cleanup. Không đơn giản đặt queue timeout bằng 2,5 giây vì transaction/request có budget khác.
4. Profile được chọn phải thành cấu hình release có tên, documented overrides và startup/test kiểm tra settings có hiệu lực. Không dùng engine_factory đặc biệt chỉ trong test rồi gọi là sửa production.
5. Nếu default vẫn FAIL mà deployment profile đạt, chỉ công bố đạt trên đúng profile release và ghi default vẫn FAIL. Không âm thầm sửa assert default 5+5 trong harness lịch sử.
6. Nếu kết luận workload 80 vượt năng lực tài nguyên được nhóm cấp, cần quyết định capacity hoặc thay yêu cầu rõ ràng trước release. Không tự giảm concurrency, thêm admission queue hoặc đổi 503 thành 429 để gọi NFR cũ đã đạt.

Thay Compose/Nginx/file trong release configuration inventory có thể đổi configuration hash. Khi đó phải bổ sung exact predecessor-to-candidate transition và real-manifest rollout/rollback regression trên base thực tế. Env override không được che khỏi evidence: ghi effective settings allowlist và hash cấu hình đã redacted, không ghi raw secret file.

## 7. File dự kiến và phạm vi review

| File/nhóm file | Thay đổi dự kiến |
| --- | --- |
| `tools/load/export_probe.py` mới | Protocol burst HTTP, fixture profile selection, candidate checks và bounded report |
| Helper/report NFR trong `tools/load/` | Run identity, verdict, aggregation, instrumentation lifecycle; tách module nhỏ nếu cần |
| `tests/backend/integration/test_appointment_export_load_pool.py` và test NFR opt-in mới | Giữ v1 tái hiện; thêm v2 ON/OFF, baseline/candidate default riêng |
| `tests/load/test_export_probe.py` mới | Contract, malformed/missing samples, target mismatch, sanitization, profile isolation |
| Test MySQL integration NFR mới | Pool lifecycle/cancel/timeout và query/transaction behavior; assertions hành vi, không timing flaky |
| `services/auth.py`, `repositories/sessions.py`, hoặc cleanup/read service liên quan | Chỉ sửa nếu chẩn đoán chứng minh; kèm auth/transaction regressions |
| Config/deployment env/Compose | Chỉ khi chọn tuning có budget và dùng được trên release thật |
| `.github/workflows/` | Job tải opt-in/manual riêng với artifacts; không thay marker/zero-skip của CI chức năng |
| `docs/tasks/nfr-03-export-load.md` mới | Lệnh tái hiện, profile release, resource budget, kết quả và giới hạn |

Không mặc định sửa tất cả các file trong bảng. Mỗi commit phải giải thích file nào cần cho giả thuyết/bản sửa đã chọn. Giữ các sửa recovery của #55 làm dependency.

## 8. Ma trận xác minh

| Nhóm | Ca cần kiểm tra | Kỳ vọng |
| --- | --- | --- |
| Verdict | 560 đủ/thiếu/duplicate mẫu; 503/429/network error; sai CSV | Chỉ đủ mẫu, đúng dữ liệu, zero errors và latency đạt mới PASS |
| Statistics | Không success, đúng ranh percentile, warmup lỗi | Null không biến thành 0; warmup FAIL trong v2; không trộn phase |
| Provenance | Sai SHA/config/attestation hoặc changed source | Từ chối/chưa đủ evidence; không gán kết quả cho candidate khác |
| Evidence | Hai lượt cùng SHA, abort/timeout giữa run | Output không ghi đè; giữ partial report với failed/aborted status |
| Sanitization | Canary trong token, cookie, SQL params, exception message | Không lọt vào report/artifact công khai |
| Pool lifecycle | Exception, timeout, cancel khi acquire/execute/exit | Không để outstanding leak sau bounded drain; request tiếp theo hoạt động |
| Auth | Token absent/malformed, expired/equality boundary, revoked, inactive/deleted, role | HTTP/auth contracts hiện có giữ nguyên, không dùng cache stale |
| SQL | Session lookup plan, round trips, DB UTC | Chỉ query count thay đổi có chủ đích; semantics auth đúng |
| Export | 0/100/1.000/1.001, foreign owner, tie timestamps, formula fields | Giữ contract và LIMIT 1001; overflow 409 đúng, CSV đúng |
| Load | Shared/multi-session, first/warm burst, ON/OFF diagnostics | Report đủ nguồn, không cherry-pick PASS |
| Deployment | Effective settings, worker pool budget, mixed workload | Đúng profile release, không vượt budget/đánh đổi invariant |
| Release recovery | Nếu đổi audited configuration | Predecessor manifest thật được upgrade/rollback và drift bị từ chối |

Đối với bug runtime được xác định, thêm test tái hiện đúng boundary trên code cũ rồi green trên code sửa. Test chức năng cố định không assert p95 theo máy CI. Bài benchmark thật nằm ở job riêng, vẫn là release blocker dù CI chức năng xanh.

## 9. Evidence và phép so sánh cuối

Thư mục output: `build/nfr03/<protocol>/<full-source-sha>/<run-uuid>/`. Mỗi run có manifest, summary và raw sanitized samples. Reserve run directory bằng create-exclusive; chạy lại phải có UUID mới.

- Manifest: source/dirty flag, protocol/harness hash, config allowlist/hash, locked dependencies, Python/MySQL versions, image digests, transport/topology, effective deadlines/pool, UTC times và command đã loại secrets.
- Summary ≤256 KiB; raw bounded theo số request/stage tối đa của protocol, dự kiến cap 5 MiB/run. Nếu vượt cap hoặc thiếu sample thì INSUFFICIENT_EVIDENCE, không silently truncate rồi PASS. Ghi SHA-256 các file, archive checksum và kết quả aborted/fail trong finally.
- Public report không chứa bearer token/cookie, password/DSN secret, user/appointment identity, CSV body, SQL bind, raw traceback/message hoặc query-string URL. Dữ liệu private cần đối chiếu ở private run dir không upload.
- Artifact giữ theo retention đã công bố, kèm ngày hết hạn. PR chỉ lưu summary ngắn và run/artifact link; local ignored archive giữ checksum để tái đối chiếu. Không commit raw JSON/log vào nhánh feature.
- Baseline và candidate v2 đo xen kẽ A/B rồi B/A theo lịch cố định, cùng topology/resource/dependencies. Mỗi source/config tối thiểu ba lượt hoàn chỉnh; lưu cả FAIL. Không so Windows ASGI với Linux HTTP như một before/after improvement.

## 10. Checks và lệnh dự kiến

Các lệnh hiện có dưới đây dùng sau khi chuẩn bị venv và MySQL lab; chúng không phải kết quả vừa chạy. Values secret lấy từ môi trường riêng, không dán vào plan hay terminal transcript.

```powershell
# Sau khi tạo worktree/venv riêng từ đúng HEAD đã xác minh.
$nfrPython = 'D:\SE\tmp\nfr03-export-pool\.venv\Scripts\python.exe'
$env:PYTHONPATH = 'src/backend;tools/infra/offhost'
$env:PYTHONUTF8 = '1'

# Tái hiện harness v1, chỉ sau khi lưu bản report cũ để tránh ghi đè.
$env:BT3_RUN_LOAD = '1'
try {
    & $nfrPython -m pytest 'tests/backend/integration/test_appointment_export_load_pool.py::test_export_pool_profile[production]' -q -s --require-mysql
} finally {
    Remove-Item Env:BT3_RUN_LOAD
}

# Chạy sau khi thêm tests cho helper/probe mới.
& $nfrPython -m pytest tests/load -q
& $nfrPython -m pytest tests/backend/unit/test_transactions.py -q
& $nfrPython -m pytest tests/backend/integration/test_auth_endpoints.py tests/backend/integration/test_auth_failures.py tests/backend/integration/test_auth_concurrency.py tests/backend/integration/test_appointment_export.py -q --require-mysql

# Gate backend hiện có, cùng JUnit zero-skip check của workflow.
& $nfrPython -m pytest tests/backend tests/load -m 'not load' -q --require-mysql --junitxml=build/backend-tests.xml
```

Chạy Black/Ruff/Pyright đúng version repo trên diff, rồi các commands workflow cần cho source cuối. Export-probe CLI và v2 test node sẽ được bổ sung vào operations guide khi implementation thực sự tồn tại. Nếu sửa auth/settings dùng chung, chạy cả account lifecycle, booking và transaction/integration suites phù hợp, không chỉ export tests.

Job NFR riêng cần Linux + MySQL/gateway lab, kiểm tra opt-in/ownership, checkout sạch/full history khi release tooling cần, đủ resource và timeout công bố trước. Không đánh dấu job skipped/cancelled là thành công. CI chức năng vẫn giữ required tests/load và zero-skip; bài tải opt-in không bị loại khỏi điều kiện phát hành.

## 11. Thứ tự commit và điều kiện hoàn tất

1. **Measurement:** protocol/report/instrumentation và tests của verdict, run retention, cleanup. Chưa sửa runtime. Ghi baseline v2.
2. **Fix:** bản sửa runtime hoặc deployment config nhỏ nhất có bằng chứng và regression. Ghi cơ chế bottleneck cùng ảnh hưởng dự kiến.
3. **Release proof:** HTTP probe/profile/evidence, pipeline opt-in và operations guide; chạy final candidate cùng nguồn/ảnh/config đã ghim.

Nếu Bước B chưa tìm được nguyên nhân hoặc môi trường release chưa sẵn, bàn giao kết quả là chẩn đoán còn mở; không gọi NFR đã xử lý. Những thông tin phải xác minh trước certification gồm topology phát hành, resource budget MySQL/API, HTTPS target, effective runtime settings và telemetry. Thiếu môi trường là blocker của phép chứng nhận, không phải lý do giảm gate.

Điều kiện hoàn tất để đề nghị đóng NFR-03:

- [ ] Có baseline v2 đủ provenance và phép đo auth acquisition/hold/exit, cùng kết luận có bằng chứng.
- [ ] Bản sửa có regression hành vi; auth/session/security/booking/export contracts giữ đúng.
- [ ] Profile sẽ phát hành đã khai báo, settings thực tế khớp, tổng connection/resource trong budget.
- [ ] Ít nhất ba lượt v2 hoàn chỉnh qua HTTP/gateway đạt mọi burst: đủ 560 request, zero errors, CSV đúng, p95 ≤2,5 giây; generator/evidence đủ điều kiện.
- [ ] Shared-session và multi-session controls được báo riêng; không dùng mixed 20-session PASS thay export 80-request verdict.
- [ ] Sustained/mixed checks không cho thấy leak hay regression; các giới hạn quan sát được ghi rõ.
- [ ] Các workflow cần thiết của final HEAD thành công; artifact/summary trỏ đúng source/config, không chỉ dựa vào CI của #55.
- [ ] Nhóm review release profile và bản sửa. Reviewer của #55 nhận được link NFR PR/issue khi được người dùng yêu cầu gửi.

Đóng NFR-03 xác nhận workload/môi trường đã đo. Kết quả đó không tự chứng minh mọi mức tải hoặc SLO nhiều ngày của toàn hệ thống.
