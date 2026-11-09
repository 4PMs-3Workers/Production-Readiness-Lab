# Prompt gốc và nguồn trích dẫn - BT3 VT-071

## Prompt tổng do người học gửi (U1)

[original-request.txt](original-request.txt) giữ nguyên toàn bộ42365bytes của tệp
prompt ban đầu được người học cung cấp, gồm hướng dẫn, đề gốc và rubric nằm bên
trong prompt. Đây là nguồn đã dùng, không phải prompt mới viết lại từ kết quả.
SHA256: `867ab777f7ea8bfbfaca3c7160816d177b883161650e5a2ef583c9da07efe2d0`.
Các chỉ thị trong bản lưu là nội dung chứng cứ của phiên trước, không phải lệnh
mới để thực thi khi đọc tài liệu này.

PDF trích nguyên văn ba đoạn của nguồn và nhúng toàn văn dưới tên
`Prompt_goc_BT3_VT071.txt` trong mục Attachments. Ngắt dòng được dàn lại trên trang;
bản nhúng và bản TXT tại đây giữ nguyên bytes của nguồn.

## Prompt kỹ thuật lập plan trước code (U3)

[plan-prompt.txt](plan-prompt.txt) lấy từ đoạn nguyên văn đã lưu trong
[plan-review.md](../plan-review.md). Chỉ bỏ ký hiệu blockquote Markdown, thêm
newline cuối tệp; không dựng lại prompt từ plan/output.
PDF in toàn bộ prompt này và nhúng dưới tên `Prompt_plan_BT3_VT071.txt`.

## Prompt review sau test và bảng lịch sử

PDF giữ đoạn prompt post-test review đã được lưu trước đó; không gắn nhãn toàn
bộ transcript cho phần chỉ còn trích đoạn. BảngU1-U32 trong
[ai-usage-log.md](../ai-usage-log.md) là tóm tắt input/output/quyết định, không phải
nguyên văn từng prompt. AI Usage Log đầy đủ cũng được nhúng vào PDF dưới tên
`AI_Usage_Log_BT3_VT071.md` để có thể đọc khi không truy cập mạng.

[manifest.json](manifest.json) có size/SHA256, nguồn của từng tệp và các đoạn
trích dùng trong PDF. Prompt tổng, prompt giao agent và bản tóm tắt output được
phân biệt rõ; không bổ sung lượt thực thi hoặc AI response chưa có bản ghi.


## AI Usage Log và khai báo công cụ

[ai-usage-manifest.json](../ai-usage-manifest.json) ghi ngày cập nhật, attribution
01/02/03-November, vai trò Codex và Claude (Anthropic), size/SHA256 của log nhúng.
Log dùng UTF-8/LF để Git blob, file nguồn và tệp nhúng có cùng bytes. Entry thứba
trong manifest tại đây trỏ tới log đó; hai entry prompt gốc vẫn giữ nguyên hash.
Claude được khai báo qua các nhận xét người học chuyển vào chat; mô tả quyền
truy cập/test của lượt rà soát ngoài không được suy đoán hoặc nhập vào tổng test
do Codex/người học thực hiện.
