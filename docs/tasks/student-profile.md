# Hồ sơ sinh viên

Yêu cầu bổ sung trong bước review thủ công issue #20: chuyển thông tin sinh viên
khỏi trang chủ sang một mục sidebar riêng và thêm mã sinh viên, khoa, ngành, giới tính.

Xem [mô tả thay đổi và review issue #20](issue-20-change-description.md) để đối chiếu
phạm vi từng file, kết quả kiểm thử và thứ tự triển khai chung.

- Route `/profile`, sidebar “Thông tin sinh viên” có trạng thái đang chọn và breadcrumb.
- Trang chủ tập trung vào đặt lịch và lịch đã đặt. Mock cũng dùng route hồ sơ riêng.
- API `GET /api/v1/students/me` chỉ trả hồ sơ của STUDENT đang đăng nhập, không nhận ID
  hay query từ client. STAFF/ADMIN bị chặn; response thành công và lỗi dùng private,
  no-store và giới hạn thời gian request/query như các API cá nhân hiện có.
- Dữ liệu gồm id, name, email, role, student_code, faculty, major, gender. Không trả hash
  mật khẩu, trạng thái nội bộ hay hồ sơ người khác. Auth login/me giữ nguyên contract.
- Bốn trường mới cho phép NULL để giữ nguyên tài khoản cũ. UI hiện “Chưa cập nhật”;
  gender có MALE/FEMALE/OTHER/UNDISCLOSED, hiển thị Nam/Nữ/Khác/Không cung cấp.
- Màn hình chỉ đọc. Chưa bổ sung luồng chỉnh sửa hoặc tự nhập hồ sơ.

Migration `20261017_2350_student_profile` nối tiếp `20261016_1200_audit_reqid128`,
thêm bốn cột nullable vào users và ràng buộc giá trị giới tính. Upgrade không thay
thông tin tài khoản cũ; downgrade từ chối nếu đã có dữ liệu hồ sơ để tránh mất dữ liệu.
Chạy Alembic upgrade head trước khi khởi động và đặt
`ACCEPTED_SCHEMA_REVISIONS=20261019_1200_proposal_replay` theo `.env.example` hiện tại
(revision mới của đề nghị đổi lịch nối tiếp migration hồ sơ).

Harness E2E nạp hồ sơ hư cấu với mã TEST-STUDENT/TEST-OTHER, khoa/ngành kiểm thử,
giới tính Không cung cấp. Không dùng dữ liệu cá nhân thật. Bộ kiểm tra gồm API quyền
truy cập, thiếu dữ liệu, lỗi DB, migration nâng cấp/từ chối mất dữ liệu, UI thiếu dữ
liệu và E2E desktop/mobile qua sidebar, reload và đổi tài khoản.

Ảnh kiểm tra từ tài khoản hư cấu: [desktop](../evidence/student-profile/desktop.jpg),
[mobile — điều hướng](../evidence/student-profile/mobile-top.jpg),
[mobile — thông tin](../evidence/student-profile/mobile-details.jpg).

Kiểm chứng local 18/10/2026: 118 unit/component, 66 mock E2E, 16 API E2E
desktop/mobile và 638 backend tests đều đạt; backend không skip, E2E không retry.
Hai production build API/mock, lint/format, TypeScript và Pyright đạt. Artifact
OpenAPI khớp bản xuất lại. Runtime dùng cửa sổ D+1…D+7; gửi trực tiếp D+8 bị chặn
ở availability và tạo lịch. Chưa commit/push/PR tại thời điểm ghi kết quả.
