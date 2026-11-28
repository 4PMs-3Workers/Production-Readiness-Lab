# Implementation plan: sửa các finding review PR #69

Ngày lập: 27/11/2026, Asia/Bangkok. Trạng thái: **plan trước triển khai**.

## 1. Căn cứ, source và phạm vi

- Review: [Duyhai2005 ngày 27/11](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/69#pullrequestreview-6086890034).
- PR69 HEAD đã đối chiếu: `f527bd10d793379de114b07e5ea77d81abaf2085`.
- Nhánh/worktree: `codex/nfr03-export-pool`, `D:\SE\tmp\nfr03-export-pool`.
- Base PR69 hiện còn là `codex/bt3-23001952-vt071`, HEAD `d3ed6db`.
- PR55 đã merge ngày 25/11, merge commit `8e34f997f2e9b5a56afab2dc176e0a585db22269`.
- `develop` lúc lập plan: `2be610c2b67f4e3d14a7c561129c6018774a2b6f`.

Đã đọc các caller và code của năm finding. Chưa sửa harness, test hay workflow; chưa chạy regression mới. Các đường lỗi dưới đây đã đối chiếu bằng code, còn RED/GREEN sẽ được thực hiện ở bước triển khai.

Mục tiêu là làm phép đo đúng topology, không từ chối burst nhanh vì thiếu mẫu, không treo observer/cleanup và không bỏ sót CI khi đầu vào thay đổi. Giữ thay đổi trong harness, tests, workflow và hướng dẫn vận hành. Không đổi auth/export production, pool candidate, deadline nghiệp vụ hoặc PDF đã nộp để giải quyết các finding này.

Các cấu hình vẫn giữ: default 5+5/queue 0,5 giây; candidate40/overflow0/queue 0,75 giây; DB query timeout 1 giây; request timeout 3 giây. Gate vẫn yêu cầu đủ 560 request, CSV đúng, zero errors, p95 ≤2,5 giây ở mọi burst và generator evidence hợp lệ.

## 2. Tích hợp với develop trước khi chốt bản sửa

1. Ghi lại local/remote HEAD, worktree status và HEAD develop mới nhất khi bắt đầu. Bảo toàn mọi thay đổi không thuộc lượt sửa này.
2. Fetch rồi merge develop vào nhánh NFR bằng commit mới; không rebase/force-push. Đây là cập nhật nhánh PR, chưa phải merge PR69 vào develop.
3. Đọc `AGENTS.md` mới cùng hướng dẫn ở các thư mục bị chạm. File AGENTS trên develop mang scope PR66; giữ nhiệm vụ và nhánh PR69 theo yêu cầu người dùng, đồng thời áp dụng những quy tắc kiểm chứng/bảo toàn có liên quan. Không chuyển sang nhánh PR66 hoặc sửa bài của người khác.
4. So delta và giải quyết conflict nếu có. NFR chỉ thêm file ở HEAD đang kiểm tra, nhưng không suy ra không có conflict trước khi merge thực tế.
5. Kiểm tra đặc biệt phần mới về booking quota, schema merge, release manifest, rollout recovery và fixtures. Benchmark nhập `SCHEMA` từ `release_manifest.py`; nó phải theo schema của source kết hợp, không hard-code revision của bài cũ để qua test.
6. Đổi base PR69 sang `develop` sau khi candidate đã tích hợp. Kiểm tra diff với merge base mới chỉ còn NFR, không kéo lại toàn bộ PR55/PR66 hoặc thay đổi ngoài phạm vi.
7. Giữ draft cho đến khi regression và CI của HEAD cuối hoàn tất; không dùng kết quả xanh của `f527bd1` làm kết quả source mới.

So sánh API hiện tại cho thấy develop đi trước `d3ed6db` 37 commit, gồm thay đổi schema/rollout và booking quota. Vì vậy ngoài NFR lab cần chạy lại các workflow bắt buộc trên source kết hợp, đặc biệt Backend models, Serialized release rollback và Containerized demo.

## 3. P2: burst nhanh bị gọi nhầm là generator saturated

Nguồn: [inline review](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/69#discussion_r4718366357).

### Nguyên nhân và tác động

`run_bursts()` dùng chung một watcher cho lag và MySQL snapshots. Nó đợi 250 ms giữa các lượt và dừng ngay khi downloads hoàn tất. Burst 300 ms có thể chỉ có một mẫu lag; `finish_report()` hiện coi dưới hai mẫu là saturation. Như vậy 80 response hợp lệ, zero lag, latency tốt vẫn bị FAIL do cách lấy mẫu.

### Hướng sửa

- Tách sampler lag khỏi observer MySQL; chỉ sampler lag chạy nhịp nhanh, không tăng tần suất query DB.
- Dùng chu kỳ lag cố định **10 ms** và cửa sổ quan sát tối thiểu **30 ms**, khai báo bằng hằng số nội bộ, không thêm tùy chọn CLI mới.
- Sampler báo ready trước khi thả barrier của workload. Nếu downloads kết thúc rất nhanh, sampler chỉ hoàn tất phần cửa sổ tối thiểu còn thiếu; không giữ response, không thêm thời gian quan sát sau response vào latency HTTP.
- Ghi riêng `request_window_seconds` và `lag_observation_window_seconds`, chu kỳ lấy mẫu và số mẫu thực tế. Dùng clock monotonic; không tạo hàng loạt mẫu zero để bù các tick đã bỏ lỡ.
- Giữ ngưỡng lag 0,1 giây và tỷ lệ missed tick hiện có. Cửa sổ quá ít mẫu hoặc lỗi collector vẫn làm evidence không hợp lệ, không tự coi là zero lag. Phân biệt evidence thiếu với saturation đã quan sát được trong report; chỉ PASS khi evidence đủ và không saturated.
- Không sửa bằng cách đổi minimum từ hai mẫu xuống một, bỏ generator gate hay thêm sleep vào mỗi request.

### Regression

1. Tái hiện reviewer: burst 300 ms, 80 response đúng, clock/scheduler không lag → đủ mẫu và không saturated.
2. Burst rất nhanh dưới 20 ms → cửa sổ tối thiểu vẫn đủ mẫu; latency đã chốt trước khi sampler dừng, không bị cộng tail quan sát.
3. Lag vượt ngưỡng/tick bị bỏ lỡ → vẫn từ chối; không bị mẫu tail tốt che mất sự cố.
4. Không có mẫu, task bị hủy hoặc collector lỗi → evidence incomplete và không PASS.

Test logic thời gian dùng clock/scheduler kiểm soát được hoặc AsyncMock trong helper nhỏ, không đặt assert tốc độ chặt theo wall-clock của runner. Real HTTP lab vẫn kiểm tra hành vi thật.

## 4. P2: topology có thể bị WEB_CONCURRENCY thay đổi

Nguồn: [inline review](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/69#discussion_r4718366365).

`native_api()` kế thừa môi trường và gọi Uvicorn không có `--workers`. Theo finding, `WEB_CONCURRENCY=2` làm chạy hai pool độc lập, trái với mô tả một API process/pool40.

Hướng sửa nhỏ nhất:

- Thêm **`--workers 1`** vào argv khi launch cả factory thường và diagnostic factory.
- Report ghi rõ API worker count=1. Không bỏ toàn bộ environment vì còn các biến cần cho runtime; không thêm mode multi-worker ngoài yêu cầu review.
- Giữ loopback binding, credentials qua environment, không log secrets, deadline readiness và cleanup process do harness sở hữu.

Regression đặt `WEB_CONCURRENCY=2` trong môi trường cha, capture argv qua Popen fake và xác nhận explicit worker count luôn là một. Cover cả hai factory modes và readiness failure/cleanup. Native HTTP lab sau sửa chạy với `WEB_CONCURRENCY=2` cố ý để kiểm chứng override thực tế; không chỉ kiểm tra một list string trong unit test.

## 5. P2: observer MySQL có thể giữ benchmark và làm mất evidence

Nguồn: [inline review](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/69#discussion_r4718366373).

### Nguyên nhân

Deadline 30 giây hiện bao `gather(downloads)` nhưng không bao `mysql_snapshot()` trước/trong/sau burst. `stop.set()` không ngắt query mà watcher đã bắt đầu; `await watcher` và `await monitor.dispose()` chưa có giới hạn riêng. Nếu query hoặc cleanup treo, code có thể chưa tới phần lưu report.

Ngoài ra, `report['bursts']` hiện chỉ nhận summary sau khi watcher và snapshot cuối hoàn tất. Do đó một observer hỏng có thể làm mất cả 80 response đã thu xong.

### Hướng sửa

1. Đặt timeout cho **observer riêng**, không đổi query/request timeout của API. Budget dự kiến: connect/read/write I/O 1 giây; mỗi snapshot hoặc metadata query có deadline tổng 2 giây.
2. Package asyncmy 0.2.16 đang cài có `connect_timeout`/`read_timeout`/`write_timeout`; dùng đúng connect_args của monitor. Outer timeout vẫn cần vì một snapshot có nhiều query và nhiều lần đọc packet.
3. Bao mọi chỗ dùng observer: acquire connection, version/max_connections metadata, snapshot đầu, watcher snapshots và snapshot cuối. Không chỉ bọc một caller trong while loop.
4. Khi downloads xong: signal stop; chờ watcher trong budget đóng **1 giây**. Nếu chưa dừng, cancel rồi drain thêm tối đa **1 giây**. Dùng bounded wait phù hợp; không trông chờ `wait_for()` tự bảo đảm giới hạn nếu coroutine còn trì hoãn cancellation.
5. Nếu observer timeout/lỗi/shutdown không xong, đánh dấu benchmark evidence không hoàn chỉnh và dừng run. Không tiếp tục sử dụng monitor connection đã bị hủy giữa protocol. Invalidate/close/dispose trong cleanup có budget riêng, dự kiến **2 giây** cho từng phần cleanup cần thiết; không gọi một await unbounded sau khi đã timeout.
6. Dùng khả năng cancel/read-timeout của driver được pin, không gọi các hàm private của driver để force-close tùy tiện. CI job timeout 25 phút chỉ là lớp bảo vệ cuối, không thay timeout trong harness. Không thể dùng asyncio timeout để cam kết ngắt code CPU đồng bộ không chịu nhường event loop; phạm vi kiểm chứng là I/O async của driver được pin.
7. Khởi tạo phase record trước burst và giữ các response đã hoàn tất ngay khi chúng về. Summary cuối cập nhật record đó, không append bản sao. Nếu observer hỏng sau downloads, các response vẫn còn trong partial report.
8. Bảo đảm report writer vẫn được gọi từ finally kể cả khi observer/cleanup thất bại. Thu lỗi cleanup bằng mã hữu hạn như `observer_timeout`, `observer_error`, `observer_shutdown_timeout`; không log str(error), SQL, DSN hoặc response body.
9. `passed=False` khi metadata/observer evidence không đủ. Giữ đúng số response thực sự thu được; không giả lập missing samples hoặc biến lỗi observer thành HTTP 503 của ứng dụng.

### Regression

| Fault injection | Điều cần chứng minh |
| --- | --- |
| Observer execute chờ một Event không được set | Timeout tại snapshot, kết thúc có giới hạn, không cần MySQL thật |
| Query metadata/version treo | Không mắc kẹt trước burst; partial report được lưu |
| Watcher đang query khi downloads hoàn tất | Stop/cancel/drain đúng budget; không để task lỗi không được thu hồi |
| Snapshot cuối hoặc dispose lỗi | Giữ đủ 80 response đã hoàn tất và reason observer/cleanup riêng |
| Một phần downloads hoàn tất rồi burst bị cancel | Giữ partial samples, completed=false, passed=false và checksum hợp lệ |
| Bình thường | Observer còn hoạt động đúng, records không bị duplicate và không có pending watcher |

Tests dùng timeout test nhỏ hơn qua hằng số được monkeypatch và safety timeout ngoài cùng rộng hơn. Có ca kiểm chứng task bị cancel và cleanup thật sự được gọi, không chỉ assert exception. Khi dùng fake trì hoãn cancellation, test phải tự release trong finally để không làm treo teardown.

## 6. P2: workflow chưa theo dõi đủ đầu vào

Nguồn: [inline review](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/69#discussion_r4718366383).

Giữ workflow opt-in/manual và các gate hiện có; mở rộng `pull_request.paths` theo dependency thật, không viết một bộ suy luận dependency mới:

- Giữ `.github/workflows/nfr03-export-lab.yml`, `src/backend/**`, `tests/load/**`, `tools/load/**`, `tools/deploy/profiles/**`.
- Thêm root `requirements*.txt`, bao chuỗi dev → offhost → runtime, và `pyproject.toml`.
- Thêm `tools/infra/offhost/**` vì job editable-install package này.
- Thay filter chỉ `*nfr03*` bằng `tests/backend/integration/**` để bao conftest và các module đang được import như export fixture, load_review, booking_context, appointments_api.
- Thêm `tests/backend/conftest.py`, `tests/backend/unit/test_nfr03*.py`, `alembic.ini` và `tools/deploy/release_manifest.py` là đầu vào trực tiếp của fixtures/schema/setup hiện tại.
- Sau retarget, bỏ nhánh base tạm `codex/bt3-23001952-vt071` khỏi danh sách branches; giữ develop/main và workflow_dispatch.

Thêm contract test trong `tests/backend/unit/test_nfr03_harness.py`, đọc YAML bằng reader hiện có hoặc PyYAML BaseLoader để xử lý key `on` đúng. Assert các filters bắt buộc còn đủ và workflow commands chạy các regression mới. Không tự mô phỏng toàn bộ thuật toán glob của GitHub bằng fnmatch Python có semantics khác.

## 7. P3: khôi phục ASGI occupancy sampling

Nguồn: [inline review](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/69#discussion_r4718366390).

Biến `target` ở outer scope là HTTP origin hoặc None. `observe()` hiện gán lại `target = perf_counter() + 0.25`, làm điều kiện `not target` luôn false và mất `pool_checked_out` cho ASGI.

Đổi tên deadline cục bộ thành `next_snapshot_at` hoặc bỏ nó khỏi observer khi tách lag sampler. Các kiểm tra transport tiếp tục dùng `target` origin bên ngoài. Không gọi pool của application chưa khởi động trong native HTTP mode.

Regression: ASGI mode có occupancy khi diagnostics ON và OFF; native HTTP mode không đọc nhầm engine ở process cha và không giả lập occupancy bằng zero. Native occupancy không có số đo phải là unavailable như hiện tại.

## 8. Cấu trúc sửa và test dự kiến

| File | Thay đổi |
| --- | --- |
| `test_appointment_export_nfr03.py` | Tách lag/observer, timeout và shutdown, giữ partial evidence, sửa shadowing, metadata protocol |
| `nfr03_native.py` | Explicit workers1, giữ startup/cleanup hiện có |
| `tools/load/export_probe.py` | Chỉ đặt helper lag/shutdown nhỏ nếu cần để chia sẻ/test; không thêm runner framework |
| `tests/backend/unit/test_nfr03_harness.py` mới | Regression fast burst, observer stall/cleanup, topology, occupancy và contract workflow paths/commands bằng fake/AsyncMock; không dependency mới |
| `.github/workflows/nfr03-export-lab.yml` | Bổ sung paths, bỏ base tạm, chạy regression mới trong quality/contracts step |
| `docs/tasks/nfr-03-export-load.md` | Protocol, timeout budgets, worker count, kết quả và giới hạn thực tế |

Tái dùng stdlib asyncio, các test fixture hiện có và PyYAML đã cài. Tránh copy thêm harness hoặc thêm config/env flags chỉ phục vụ các fault injection; test thay hằng số hoặc fake boundary có sẵn.

## 9. Trình tự kiểm chứng

1. Tạo regression cho từng finding và xác nhận fail đúng nguyên nhân trên code cũ. Fast-burst test không được RED chỉ vì helper mới chưa tồn tại; worker test phải dùng environment WEB_CONCURRENCY=2; observer test phải tái hiện query không phản hồi.
2. Sửa sampler/observer/occupancy cùng tests; sửa worker argv và filters cùng contract test. Chạy targeted tests sau từng thay đổi.
3. Giữ tests mới ngoài marker load và ngoài BT3_RUN_LOAD để CI chức năng luôn chạy chúng. Cập nhật explicit file lists trong Black/Ruff/Pyright/pytest của NFR workflow; tests mới chỉ được thêm file mà không được workflow gọi là chưa hoàn tất.
4. Chạy các unit/harness tests, `tests/load`, profile contracts và MySQL export integration có `--require-mysql`. JUnit required acceptance không được skip.
5. Chạy ASGI smoke với profiling ON/OFF để xác nhận occupancy xuất hiện; native HTTP smoke với WEB_CONCURRENCY=2 để xác nhận một worker thật.
6. Chạy baseline và ba candidate HTTP iterations trên source sạch sau tích hợp. Giữ baseline dù FAIL; baseline setup lỗi/thiếu report không được chấp nhận như một performance FAIL hợp lệ.
7. Nâng nhãn protocol sang **nfr03-export-v3** vì cadence/window/observer behavior đã thay đổi; cập nhật report và output directory cùng lúc. Các consumer tìm report bằng rglob hiện tại vẫn phải kiểm tra source/protocol và số lượng run rõ ràng.
8. Giữ nguyên raw report v2 và số đo lịch sử. Kết quả v2 vẫn thuộc source và protocol cũ; không dùng chúng để chứng nhận sampler đã sửa, không so khác protocol như một cải thiện production.
9. Đẩy HEAD cuối sau khi review staged diff. Kiểm tra mọi push/PR workflow áp dụng trên HEAD đó, gồm full Backend, Rollback, Containerized demo và NFR lab sau retarget develop. Đọc log thật nếu fail; không tạo commit tùy chọn lúc đang chờ CI.
10. Cập nhật mô tả PR bằng source/base, mapping năm findings → tests/run, artifact và hạn retention. Chỉ chuyển draft khi không còn finding chưa xử lý và CI của source kết hợp đã đạt.

Các lệnh sẽ dùng venv riêng của worktree. Lệnh pytest dự kiến sau khi file tests mới tồn tại:

```powershell
$pr69Python = 'D:\SE\tmp\nfr03-export-pool\.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = 'src/backend;tools/infra/offhost'

& $pr69Python -m pytest tests/backend/unit/test_nfr03_harness.py tests/backend/unit/test_nfr03_profile.py tests/load/test_export_probe.py -q

# MySQL lab riêng, MYSQL_TEST_URL được cấp qua môi trường.
& $pr69Python -m pytest tests/load tests/backend/unit/test_nfr03_harness.py tests/backend/unit/test_nfr03_profile.py tests/backend/integration/test_appointment_export.py -q --require-mysql --junitxml=build/nfr03/contracts.xml

# Workflow Linux sẽ chạy baseline và ba lượt candidate trên đúng HEAD.
```

Contract workflow được gộp trong file harness ở mục 8 và các commands trên. Quality checks dùng các version đã pin trong source kết hợp. Nếu merge chạm rollout fixtures/code thì chạy toàn bộ họ unit rollout/release theo AGENTS, không chỉ targeted NFR tests.

## 10. Commit, deliverables và điều kiện hoàn tất

Giữ ít commit có mục đích rõ:

1. Merge develop, giữ lịch sử và các bằng chứng đã ghim.
2. Sửa sampling/observer/occupancy, worker topology và regression tests liên quan.
3. Cập nhật CI filters/commands và hướng dẫn/summary khi cần tách cho review.

Raw JSON/log giữ trong ignored build hoặc artifact, không đưa vào commit. Không chỉnh lại PDF hay số liệu cũ để trùng source mới. Không thay pool/deadline production để làm phép đo mới xanh.

Checklist bàn giao:

- [ ] Năm finding có regression RED/GREEN đúng boundary.
- [ ] Burst nhanh không bị coi quá tải chỉ do nhịp lấy mẫu; evidence thiếu vẫn không PASS.
- [ ] WEB_CONCURRENCY bên ngoài không thể đổi lab thành nhiều worker/pool.
- [ ] Observer stalls và shutdown có giới hạn; finished response/partial report không bị mất.
- [ ] ASGI occupancy có lại ở cả hai chế độ instrumentation; native không báo pool giả.
- [ ] Dependency/fixture-only changes kích hoạt NFR workflow; tests mới thực sự được chạy.
- [ ] PR69 base là develop; diff giữ scope NFR và các gate kế thừa không bị nới.
- [ ] CI và ba candidate iterations đạt trên HEAD cuối/source kết hợp, artifact gắn đúng protocol v3.
- [ ] PR mô tả đúng kết quả và giới hạn; draft chỉ được đưa tiếp khi đủ điều kiện review.

Hoàn tất plan này sửa tính đúng đắn của lab. NFR-03 cho production vẫn cần gateway/TLS, target attestation, worker/mixed workload, resource/connection budget và review deployment thực tế như đã công bố; không tự đóng release gate dựa vào ba lượt native HTTP.

## Follow-up: v4 lag cutoff

The first v3 review found that a healthy sample due after the request burst could lower p95 and hide a slow in-window sample. Protocol v4 records each sample deadline, freezes the request cutoff once, and computes lag verdicts from deadlines at or before that cutoff. Tail observations fill the two-sample minimum only when the request window has fewer than two samples. The report records which samples fed the verdict. Four deterministic regression cases cover the p95 boundary, an inclusive late wake, short requests, and the complete `run_bursts()` reporting path.

The v4 change updates the report protocol and output directory and must rerun the Linux baseline plus three candidate iterations on a clean final source. V2 and v3 results remain historical evidence.
