# Đọc diff production BT3 - nguồn15526cb

Đây là hướng dẫn do AI chuẩn bị. Trạng thái: người học chưa xác nhận hoàn tất
review diff. Checklist bên dưới để trống; việc có tài liệu này không phải bằng
chứng đã đọc hay đã chấp nhận patch. Reflection giữ nguyên các quan sát tự chạy
đã có; chưa thêm cảm nhận hoặc lời xác nhận review chưa được người học cung cấp.

## Mở đúng diff

Chạy trong bản sinh viên đã dùng để thực hành. Hai lệnh Git chỉ đọc:

```powershell
Set-Location 'D:\SE\tmp\bt3-student-20261102-083514'
git --no-pager diff --stat aa8c5b0 15526cb -- src/
git --no-pager diff aa8c5b0 15526cb -- src/
```

Kết quả dự kiến:4file Python,118dòng thêm,2dòng bỏ. `aa8c5b0` là baseline đề bài;
`15526cb` là source đã tự chạy. Lệnh này không bao gồm tests, CI/dependency,
OpenAPI/gateway đã sinh hoặc evidence. Các phần đó có trong review map của README
và vẫn cần review tương ứng trước khi nhóm merge PR.

## Đọc theo luồng dữ liệu

1. **schemas/appointments.py**: import/schema mới giữ query `status` optional,
   enum theo contract; `extra="forbid"` ngăn chọn owner/page qua query. Role và
   duplicate query còn được kiểm tra ở dependency hiện có. Hãy phân biệt422 do
   schema với403 do guard và SQL owner scope.
2. **repositories/appointments.py**: thêm import status và hàm đọc export. SQL
   lấy owner từ `student_id` do route truyền, optional status, thứ tự
   `starts_at DESC,id DESC`, rồi giới hạn số hàng. `_student_detail_statement()`
   là helper join hiện có; đọc cả helper để biết cột nào xuất ra. Một SELECT
   LIMIT1001 cho snapshot của một statement dưới READ COMMITTED; lặp nhiều trang
   có thể quan sát các snapshot khác nhau. Không suy rộng thành snapshot nhiều
   statement hoặc bảo đảm thứ tự ổn định khi dữ liệu thay đổi giữa hai request.
3. **services/appointment_export.py**: đọc từng import/hằng số và hai hàm.
   Giới hạn1000, hàng1001 là sentinel; quá giới hạn trả409, không cắt CSV.
   `_list_item` ánh xạ cột công khai trong `read_transaction`; format CSV sau khi
   rời transaction. `model_dump(mode="json")` dùng serializer schema hiện có;
   kiểm tra UTC ở schema/test. `csv.writer` quote dấu phẩy, quote và newline;
   dấu nháy đơn xử lý prefix formula, kể cả full-width và tab/CR/LF. Không coi
   prefix là chứng minh an toàn trên mọi spreadsheet client.
4. **api/appointments.py**: xem import đã gộp rồi route export mới trước route
   UUID. Chuỗi `export` phải match static route; nếu route tham số đứng trước,
   request có thể bị nhận là appointment_id và fail validation. Route dùng
   `require_student`, query guard và session DB, truyền `context.user_id`;
   trả UTF-8 CSV và tên file cố định. OpenAPI khai báo các response/header;
   kiểm tra middleware hiện có để biết Cache-Control/X-Request-ID được đặt ở đâu.

Các helper/dependency hiện có cần đọc cùng diff: `reject_duplicate_catalog_query`,
`require_student`, `read_transaction`, `_student_detail_statement`, `_list_item`,
serializer của AppointmentListItem và middleware response. Dòng không đổi vẫn
có thể quyết định hành vi mới. Không suy ra từ tên hàm rằng mọi contract đã đúng.

## Đối chiếu test và giải thích bằng lời của mình

| Câu cần giải thích | Oracle / nơi đọc |
|---|---|
| Ai trả403 khi bỏ guard chính? Vì sao M3 sống? | Query guard hiện có cũng kiểm tra STUDENT; `test_export_real_session_role_is_denied` và mutant role_primary |
| Owner có thực sự được lọc ở DB? | `test_export_real_login_scope_contract_and_inactive_catalog`; đối chiếu exact owned IDs bằng MySQL thật |
| Vì sao lấy1001 thay vì1000 hoặc phân trang? | `test_export_rejects_overflow_instead_of_truncation`;409 với1001, không partial CSV |
| CSV giữ nội dung và UTC thế nào? | `test_csv_text_and_formula_contract`, `test_export_timestamp_does_not_depend_on_local_clock` |
| TC13 khác TC18 thế nào? | TC13 (`test_booking_replay_after_cancellation_exports_one_source_row`) đòi200 + receipt gốc trước khi kiểm CSV/DB; TC18 (`test_export_source_cardinality_after_retry`) cho phép200/201 để mutant đi tới assertion cardinality và bị bắt ở2≠1 |
| Vì sao100%coverage chưa đủ? | Chỉ xác nhận các statement/arc đã chạy; mutation M3 còn sống, NFR-03 vẫnFAIL |
| Vì sao312lỗi chưa chứng minh export chậm? | Tất cả ở auth pool queue, trước export; CancelledError tại rollback ở lượt Null khác không chứng minh SELECT chậm |

Production `booking.py` không đổi. M5 chỉ tồn tại trên nhánh mutant: bỏ replay
guard và thêm6dòng setup test để drop unique index trong schema fixture sở hữu;
assertion giữ nguyên. Không áp diff mutant vào nhánh nộp.

## Tự đánh dấu sau khi thực sự làm

- [ ] Tôi đã mở toàn bộ diff4file và đọc cả các helper quyết định hành vi.
- [ ] Tôi giải thích được mỗi import, query condition, limit, branch và response.
- [ ] Tôi đối chiếu các oracle phía trên, nêu được giới hạn và test còn thiếu.
- [ ] Tôi ghi lại bằng lời của mình điều đã hiểu, chỗ chưa hiểu và quyết định
      chấp nhận hoặc yêu cầu sửa; chưa đánh dấu nếu chỉ đọc hướng dẫn này.

Review cá nhân này không phải team approval. PR55 hiện vẫn gộp chức năng,
CI/dependency và evidence; kế hoạch tách trước team merge còn giữ trong README.
