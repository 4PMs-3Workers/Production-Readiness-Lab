# Tự chạy và chuẩn bị vấn đáp - BT3 VT-071

Log hiện có là Codex thực thi theo yêu cầu của bạn. Theo README để chạy lại bằng
checkout/venv và MySQL test riêng; ghi lại command, output và quan sát thực tế.

## Lần chạy tối thiểu

1. Test real_login_scope: so exact owned IDs, foreign canary và cột không xuất.
2. Test overflow:1000rows ->200; thêm một row ->409 EXPORT_TOO_LARGE.
3. TC13 và TC18: phân biệt receipt/status oracle với CSV/DB cardinality oracle.
4. Đọc các mutant commit và log; nếu reproduce, chỉ helper trên nhánh thí nghiệm.
5. Coverage với greenlet/thread và diff JSON; đọc cùng assertion/mutation.
6. Load: chạy pool5+5, đọc error count và p95 success; so với mọi lượt NullPool
   FAIL/PASS, không dùng một lượt pass để kết luận đã sửa.

## Hai lệnh tự kiểm chứng trước khi viết reflection

Tự mở terminal ở repo, kích hoạt venv và đặt MYSQL_TEST_URL theo README (MySQL8.4
test riêng). Lưu HEAD, thời điểm, lệnh, exit code và điều bạn quan sát được. Các
lệnh dưới đây là hướng dẫn; chúng chưa phải evidence do bạn đã thực hiện.

```powershell
$studentRun = Join-Path (Get-Location) ('tmp/student-recheck/' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $studentRun | Out-Null
git rev-parse HEAD | Set-Content (Join-Path $studentRun 'head.txt')
python -m pytest tests/backend/integration/test_appointment_export.py::test_export_source_cardinality_after_retry -q --require-mysql 2>&1 | Tee-Object -FilePath (Join-Path $studentRun 'tc18.txt')
$studentTc18ExitCode = $LASTEXITCODE

# The harness writes a tracked canonical JSON: preserve it before your run.
$canonicalPoolReport = 'docs/assignments/VT-071/evidence/review-pool-production-performance.json'
$savedPoolReport = Join-Path $studentRun 'original-pool-report.json'
Copy-Item -LiteralPath $canonicalPoolReport -Destination $savedPoolReport
$env:BT3_RUN_LOAD = '1'
try {
    python -m pytest 'tests/backend/integration/test_appointment_export_load_pool.py::test_export_pool_profile[production]' -q -s --require-mysql 2>&1 | Tee-Object -FilePath (Join-Path $studentRun 'pool.txt')
    $studentPoolExitCode = $LASTEXITCODE
    Copy-Item -LiteralPath $canonicalPoolReport -Destination (Join-Path $studentRun 'student-pool-report.json')
} finally {
    Copy-Item -LiteralPath $savedPoolReport -Destination $canonicalPoolReport
    Remove-Item Env:BT3_RUN_LOAD -ErrorAction SilentlyContinue
}
@{ tc18 = $studentTc18ExitCode; pool = $studentPoolExitCode } | ConvertTo-Json | Set-Content (Join-Path $studentRun 'exit-codes.json')
```

TC18 trên code sạch kỳ vọng1pass; đọc assertion len(exported)==1, ID và COUNT DB.
Pool test có thể fail acceptance trên máy bạn; giữ JSON/log và giải thích phase,
stage/leaf exception, p95 success. Nếu setup/DB chưa chạy thì đó là lỗi setup, không
phải kết quả tải hợp lệ. Kiểm tra timestamp của student-pool-report.json khớp lượt
mới; nếu test không tới bước ghi report, bản copy có thể vẫn là evidence cũ.

Ghi bằng lời của bạn: “Tôi chạy lúc..., HEAD..., TC18 trả..., pool có.../560 lỗi;
tôi đọc assertion..., quan sát..., điều chưa rõ là...”. Chỉ dùng những chỗ bạn thật
sự đã làm và nhìn thấy; kết quả của bạn có thể khác số302/560 trong báo cáo.

## Câu hỏi vấn đáp và ý chính

- Vì sao static export đứng trước route UUID? FastAPI xét thứ tự; baseline parse
  export như appointment_id và trả422.
- Vì sao một SELECT LIMIT1001 thay vì OFFSET loop dưới READ COMMITTED? Mỗi statement
  có snapshot; nhiều trang có thể dịch khi insert. Sentinelrow phát hiện quá cap.
- Vì sao TC12 cần MySQL thật? Fake object không thực thi tên cột/query/schema/adapter;
  query lỗi thật trả503 sanitized, rồi request mới phải phục hồi.
- Mutation gốc chứng minh gì? Replay guard mất ->503 !=200. Unique vẫn ngăn insert
  trùng, nên chưa chạm count oracle.
- Mutant source+unique khác gì? Chỉ bỏ index trong schema UUID do test sở hữu;
  retry tạo row thứhai và TC18 fail2 !=1 tại CSV. Không đổi production schema.
- Vì sao primary role mutant sống? Query dependency vẫn require_student. Bỏ cả hai
  guard bị test bắt200 !=403; không bịa test để giết bảo vệ dư thừa.
- Coverage vì sao bỏ sót dòng? So sánh cùng TC10 xác nhận cần trackinggreenlet/thread
  cho SQLAlchemy asyncio. 100% không chứng minh mọi combination/state.
- Latest load pass có sửa503 không? Không. Lỗi đã tái hiện; read deadline và pool
  queue timeout là hai cơ chế quan sát được, chưa có production fix.
- CSV prefix bảo đảm mọi Excel workflow? Chỉ test text/parse contract; chưa test
  client thực hoặc save/reopen.

## Trả lời rõ năm điểm trọng tâm

1. Route: static export phải match trước route UUID; UUID validation failure không
   làm FastAPI tự thử route kế tiếp. Khi M3 dùng require_auth, dependency query vẫn
   gọi require_student -> role check -> FORBIDDEN_ROLE403; handler chưa được chạy.
2. Snapshot: READ COMMITTED tạo snapshot theo statement, không giữ nguyên qua cả
   page loop. LIMIT1001 là sentinel để fail closed khi quá cap1000, không truncate.
3. DB contract: TC12 cho MySQL thực thi SQL cố ý sai cột; fake object pass không
   xác minh query/schema/UUID-time adapters.503 sanitized rồi recovery là oracle.
4. Hai oracle: TC13 yêu cầu200+receipt gốc; TC18 cho phép200/201 để tiến tới count=1.
   M5 thêm6dòng setup test để bỏ index trong schema riêng, assertions không đổi.
5. Coverage: cùng TC10/source, default tracer bỏ raise56 còn greenlet/thread ghi
   nhận; config tracking khắc phục phép đo, không làm code đúng hơn bằng số100%.

Thêm câu tải: lần xác nhận sau MySQL startup có276/560503, kể cả warm. Log xác nhận
deadline read_transaction tại export đã hết, chưa phân biệt nguyên nhân sâu CPU/
connection/query. Không tuyên bố chỉ cold-start hoặc warmup đã sửa lỗi.

## Pool, deadline và cơ chế tải

- NullPool khác5+5 thế nào? NullPool tạo kết nối mỗi lần acquire, không giới hạn
  đồng thời bằng hàng đợi pool;80request ở đây dùng cả auth lẫn export, quan sát
 160kết nối mới/burst khi thành công. Pool5+5 có tối đa10checkout và queue0.5s.
- Chứng minh lỗi pool bằng gì?302request cuối503ởauth; leaf có module sqlalchemy.exc
  và isinstance TimeoutError của SQLAlchemy; pool sampled checkout=10. Không chỉ
  nhìn tên TimeoutError vốn trùng với builtins.TimeoutError.
- Vì sao ở auth? Đây là bước DB trước export;80request tranh tối đa10checkout,
  request nào chờ quá0.5s có thể503 ngay trong auth nên chưa tới handler export.
  Chưa từ đó suy ra SQL auth chậm hoặc có hai connection giữ đồng thời/request.
- Cùng session có chỉ một checkout không? Không. Dependency được cache theo request,
  auth và export dùng hai transaction tuần tự; commit/rollback trả connection rồi
  transaction sau checkout lại.160kết nối NullPool/burst phù hợp hai lần acquire,
  không chứng minh giữ hai connection đồng thời. Gộp transaction cần đo hold time.
- Vì sao83NullPool503không chứng minh SELECT chậm? Interception ghi CancelledError
  tại rollback sau execute/Result.all/mapping; cả cleanup nằm trong read deadline1s.
- Có chứng minh truy vấn mồ côi không? Chưa. Final snapshot sau burst không còn
  active owned queries, nhưng monitor nominal250ms bị event-loop lag nên có thể bỏ
  sót query ngắn. Aborted_clients tăng83không tự chứng minh orphan accumulation.
- Fetch nghĩa gì? AsyncSession.execute đã nhận/buffer hàng từ driver; Result.all
  chỉ lấy hàng đã đệm. Các timer bao gồm scheduling; không cộng các p95stage.
- Vì sao không dùng lượt1 để kết luận cơ chế? Counts0/340 vẫn là quan sát thật,
  nhưng thiếu leaf module/isinstance; tên TimeoutError không phân biệt pool queue
  với deadline. Lượt2/3 có295/302pool timeout được xác minh; thứ tự chạy còn confound.
- Hướng xử lý nào đã chọn? Chưa sửa production. Ưu tiên đo wait/hold và ramp tải,
  rồi thử riêng admission limit, pool capacity, pool timeout hoặc transaction scope;
  giữ mọi error/rejection trong gate. README nêu tradeoff/bằng chứng cho từng cách.

## Reflection của bạn

Sau khi tự chạy, ghi một điều khiến bạn hiểu khác trước và điểm còn chưa rõ. Viết
250-350 từ bằng giọng của bạn. Bản AI draft ghi đúng việc ủy quyền cho Codex; không
đổi thành tuyên bố tự tay chạy nếu chưa thực hiện.
