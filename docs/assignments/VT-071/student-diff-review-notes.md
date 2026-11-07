# Ghi chú review diff cá nhân - Nguyễn Tố Uyên

Ngày ghi nhận:03/11/2026. Người học cung cấp bảy nhận xét dưới đây sau khi đọc
diff bốn file production và trả lời bộ câu hỏi vấn đáp. Codex chỉ định dạng tài
liệu; giữ nguyên nội dung nhận xét và quyết định của người học.

Phạm vi: `git diff aa8c5b0 4695035 -- src/` (4file Python); production code giống
source15526cb đã thực hành. Các commit tài liệu sau không thay đổi phần code này.

## Nhận xét và quyết định của người học

1. Em hiểu file `api/appointments.py` dùng để tạo API xuất lịch hẹn ra CSV và kiểm tra quyền truy cập của sinh viên.
2. File `schemas/appointments.py` định nghĩa dữ liệu đầu vào, cho phép lọc theo trạng thái và từ chối các tham số không hợp lệ.
3. File `repositories/appointments.py` truy vấn lịch hẹn từ database, lọc theo đúng người dùng và giới hạn số bản ghi được lấy.
4. File `services/appointment_export.py` xử lý việc tạo CSV, giới hạn tối đa 1000 lịch hẹn và tránh các giá trị có thể bị hiểu thành công thức.
5. Ban đầu em chưa hiểu tại sao phải lấy 1001 bản ghi, nhưng sau khi đọc em hiểu dòng dư được dùng để phát hiện vượt giới hạn.
6. Em vẫn chưa chắc về khả năng chịu tải vì một số lần kiểm thử với 80 yêu cầu đồng thời còn xuất hiện lỗi 503.
7. Nhìn chung em đồng ý với cách triển khai patch, nhưng muốn kiểm tra và xử lý thêm vấn đề hiệu năng trước khi chấp nhận merge.

## Đối chiếu với phạm vi báo cáo

Các file trên nằm dưới `src/backend/app/`. CSV được kiểm ở text/parse contract;
báo cáo chưa tuyên bố an toàn trên mọi spreadsheet client. NFR-03 vẫn FAIL; các
503 pool đã xác minh nằm ở auth trước export. Ý kiến đồng ý với cách triển khai
không phải chấp thuận merge ngay hoặc thay cho review của nhóm.

Nguồn kết quả đo: [ledger tự kiểm chứng](evidence/student-20261102/README.md).
Checklist đọc diff: [student-diff-review-guide.md](student-diff-review-guide.md).
