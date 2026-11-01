# Tự chạy và chuẩn bị vấn đáp - BT3 VT-071

Log hiện có là Codex thực thi theo yêu cầu của bạn. Theo README để chạy lại bằng
checkout/venv và MySQL test riêng; ghi lại command, output và quan sát thực tế.

## Lần chạy tối thiểu

1. Test real_login_scope: so exact owned IDs, foreign canary và cột không xuất.
2. Test overflow:1000rows ->200; thêm một row ->409 EXPORT_TOO_LARGE.
3. TC13 và TC18: phân biệt receipt/status oracle với CSV/DB cardinality oracle.
4. Đọc các mutant commit và log; nếu reproduce, chỉ helper trên nhánh thí nghiệm.
5. Coverage với greenlet/thread và diff JSON; đọc cùng assertion/mutation.
6. Load: xem error count và p95 success, so cả historical FAIL và latest PASS.

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
- Vì sao83NullPool503không chứng minh SELECT chậm? Interception ghi CancelledError
  tại rollback sau execute/Result.all/mapping; cả cleanup nằm trong read deadline1s.
- Có chứng minh truy vấn mồ côi không? Chưa. Final snapshot sau burst không còn
  active owned queries, nhưng monitor nominal250ms bị event-loop lag nên có thể bỏ
  sót query ngắn. Aborted_clients tăng83không tự chứng minh orphan accumulation.
- Fetch nghĩa gì? AsyncSession.execute đã nhận/buffer hàng từ driver; Result.all
  chỉ lấy hàng đã đệm. Các timer bao gồm scheduling; không cộng các p95stage.

## Reflection của bạn

Sau khi tự chạy, ghi một điều khiến bạn hiểu khác trước và điểm còn chưa rõ. Viết
250-350 từ bằng giọng của bạn. Bản AI draft ghi đúng việc ủy quyền cho Codex; không
đổi thành tuyên bố tự tay chạy nếu chưa thực hiện.
