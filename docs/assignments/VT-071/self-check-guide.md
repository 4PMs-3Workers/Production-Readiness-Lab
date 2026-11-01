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
- Latest load pass có sửa503 không? Chưa chứng minh. Không tái hiện lỗi nên không có
  exception chain cũ; cold/resource/deadline vẫn là hypotheses.
- CSV prefix bảo đảm mọi Excel workflow? Chỉ test text/parse contract; chưa test
  client thực hoặc save/reopen.

## Reflection của bạn

Sau khi tự chạy, ghi một điều khiến bạn hiểu khác trước và điểm còn chưa rõ. Viết
250-350 từ bằng giọng của bạn. Bản AI draft ghi đúng việc ủy quyền cho Codex; không
đổi thành tuyên bố tự tay chạy nếu chưa thực hiện.
