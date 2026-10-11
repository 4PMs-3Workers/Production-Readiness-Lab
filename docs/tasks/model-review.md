# Rà soát database models và chạy kiểm tra toàn bộ

**Ngày thực hiện:** 11/10/2026

**Nhánh:** `feature/database-models`

**Trạng thái:** Đã rà soát, sửa các phát hiện và kiểm tra local

## Mục tiêu và tài liệu đối chiếu

Đọc toàn bộ code đã thêm cho models, bộ chuyển đổi kiểu, kết nối database, tests và CI;
kiểm tra tính phù hợp với kiến trúc và chạy kiểm thử trên MySQL thật.

Trong repository, tài liệu thiết kế database là
[database-design.md](../architecture/database-design.md) v1.3; không có file tên
`dataset_design`. Đồng thời đối chiếu [system-design.md](../architecture/system-design.md),
[CONVENTION.md](../other/CONVENTION.md) và [GITFLOW.md](../other/GITFLOW.md).

## Kết quả đối chiếu kiến trúc

| Phần kiểm tra | Kết quả |
|---|---|
| Phạm vi bảng | Đủ 11/11: `users`, `sessions`, `purposes`, `staff_purposes`, `service_settings`, `service_hours`, `staff_unavailability`, `appointments`, `change_proposals`, `notifications`, `audit_events`. Không loại bảng nào. |
| Từ điển dữ liệu | Cột, kiểu SQL, độ dài, độ chính xác và NULL khớp mục 3 của thiết kế. Giá trị mặc định và khóa chính đã đối chiếu. |
| Khóa và ràng buộc | FK dùng `RESTRICT`; FK ghép nhân viên–nhu cầu, UNIQUE và CHECK khớp thiết kế. |
| Đề nghị đang chờ | `pending_guard` là cột virtual; UNIQUE cho phép tối đa một `PENDING` và nhiều bản ghi lịch sử. |
| Chỉ mục | Đủ 12 chỉ mục phục vụ truy vấn trong mục 7, đúng thứ tự cột và chiều sắp xếp; có chỉ mục cần thiết cho FK. |
| Lưu trữ và collation | InnoDB, utf8mb4; mã dùng collation phân biệt hoa/thường và giữ khoảng trắng cuối; email không phân biệt hoa/thường nhưng phân biệt dấu. |
| Chuyển đổi kiểu | UUID dùng thứ tự byte chuẩn; datetime UTC giữ microsecond; TIME không chứa múi giờ/phần lẻ; SHA-256 đúng 32 byte. |
| Kết nối | UTC, `READ COMMITTED`, SQL mode nghiêm ngặt và kiểm tra kết nối trước khi dùng. |
| Tổ chức code | Models trong `src/backend/app/models`, tên SQL theo snake_case, ánh xạ có chú thích kiểu; nhánh đúng dạng `feature/<feature-name>`. |

Không phát hiện sai lệch schema cần sửa trong 11 models sau khi rà soát độc lập và chạy DDL
trên MySQL 8.4.11. Các bản sửa của lần rà soát này nằm ở tests và workflow CI.

## Đối chiếu riêng với system-design.md

| Mục thiết kế hệ thống | Phần code hiện tại | Đánh giá |
|---|---|---|
| 2.2 — Truy cập dữ liệu | SQLAlchemy 2, async engine, driver asyncmy, MySQL 8.4/InnoDB, `READ COMMITTED`. | Phù hợp phần engine/models; Alembic chưa triển khai. |
| 5.1–5.2 — Module và tầng | Đủ bảng cho Accounts, Catalog, Availability, Appointments, Notifications và Audit; models chỉ ánh xạ dữ liệu/ràng buộc, không tự commit/rollback hay thực hiện luồng nghiệp vụ. | Đúng trách nhiệm tầng models; repository/service và session theo request/worker cần được triển khai tiếp. |
| 5.3 — Cấu trúc code | Dùng `src/backend/app/models`, `src/backend/app/core` và `tests/backend` đã có trong skeleton repository. | Giữ tổ chức thực tế của dự án; tài liệu trình bày đường dẫn rút gọn `backend/app` và `backend/tests`. |
| 6.2 — Thời gian | `DATETIME(6)` lưu UTC, adapter chuyển/gắn UTC, mỗi kết nối dùng `+00:00`; cấu hình nghiệp vụ mặc định `Asia/Ho_Chi_Minh`. | Phù hợp phần lưu trữ; ISO 8601 tại API và giờ database sau khi lấy khóa thuộc service/API. |
| 6.3 — Đồng thời và chống gửi lặp | Generated column/UNIQUE bảo vệ một đề nghị chờ; UNIQUE sinh viên/key cùng hash được lưu trên lịch. | Đúng cơ sở lưu trữ; khóa hàng, retry, kiểm tra hash và dựng lại response chưa triển khai. |
| 9.2 — Quyền database | Models có dữ liệu phiên/audit cần thiết; tests dùng tài khoản tạo/xóa schema riêng. | Chưa cấu hình tài khoản migration/runtime, quyền audit INSERT/SELECT hay phân quyền API. |
| 10 — Pool và timeout | Engine có `pool_pre_ping`; fixture có timeout kết nối dành cho tests. | Chưa xác định ngân sách pool/timeout/retry/shutdown cho API và worker; timeout test không đại diện cấu hình vận hành. |
| 12–13 — Kiểm chứng và phát hành | CI chạy lint/typecheck và unit/integration trên MySQL; local đã đạt 70 tests và CI của commit `62acd33` đã thành công. | Chưa triển khai build/E2E, migration/rollback, kiểm thử đồng thời, tải và SLO. |

Dependencies Python đã được pin bằng `==` trong các file requirements. Chưa có quy trình
sinh/xác minh lockfile cho release; CI mới chọn Python `3.11`, chưa khóa bản patch, và service
MySQL mới dùng tag `8.4.11`, chưa khóa digest. Các yêu cầu tái lập bản phát hành trong mục 2.2
và 12 cần hoàn thiện khi triển khai môi trường và pipeline phát hành.

Kết luận theo system-design: phần models và engine hiện tại phù hợp trách nhiệm được giao;
chưa đủ bằng chứng để xác nhận toàn bộ hệ thống đã đáp ứng thiết kế.

## Các phát hiện đã sửa

| Phát hiện | Bản sửa |
|---|---|
| CI chưa kiểm tra kiểu trên toàn bộ source/tests. | Thêm Pyright 1.1.407, Node.js 22 và `.venv` vào workflow. |
| Tám chỗ đọc `DBAPIError.orig.args[0]` chưa xử lý kiểu có thể là `None`. | Dùng helper kiểm tra `orig` và mã lỗi là số nguyên trước khi xác nhận lỗi MySQL. |
| Test email qua ORM bị bước chuẩn hóa email che mất khả năng kiểm chứng collation database. | Thêm truy vấn SQL trực tiếp với email viết hoa, đồng thời giữ kiểm tra phân biệt dấu. |
| Chưa tự động đối chiếu toàn bộ cột/kiểu/NULL với tài liệu. | Thêm unit test đọc từ điển dữ liệu và so sánh với metadata của models; bổ sung test collation. |
| Dữ liệu mẫu có `UPCOMING` cùng đề nghị `PENDING`; lịch sử dùng `EXPIRED` rồi tạo đề nghị mới; hạn đề nghị chưa đúng mốc giờ. | Chuyển lịch hẹn sang `CHANGE_PENDING` khi có đề nghị; dùng lịch sử `SUPERSEDED` có người xử lý; đặt hạn đúng 00:00 giờ Việt Nam theo thiết kế. |
| Chưa kiểm chứng SQL mode bằng dữ liệu ngày không hợp lệ. | Thêm hai integration tests ghi SQL trực tiếp, xác nhận ngày bằng không và ngày không tồn tại bị từ chối với lỗi `1292`. |

File liên quan:

- [test_model_schema.py](../../tests/backend/unit/test_model_schema.py)
- [test_mysql_models.py](../../tests/backend/integration/test_mysql_models.py)
- [backend-models.yml](../../.github/workflows/backend-models.yml)

## Kiểm tra thực tế

Môi trường integration tests: Ubuntu WSL, Python 3.11.17, SQLAlchemy 2.0.44,
asyncmy 0.2.10, MySQL 8.4.11/InnoDB. Pyright chạy trên Windows với `.venv` của workspace.

| Kiểm tra | Kết quả |
|---|---|
| Unit tests | 27 đạt. |
| Integration tests | 43 đạt, tạo bảng và ghi/đọc cả 11 models trên MySQL thật. |
| Tổng | 70 đạt; 0 lỗi, 0 thất bại, 0 bỏ qua. |
| Black | 21 file Python không cần định dạng lại. |
| Ruff | Đạt. |
| Pyright toàn bộ source/tests | 0 lỗi, 0 cảnh báo. |
| Cài backend dạng editable | Thành công; import `app.models.Base` từ thư mục gốc và đăng ký đủ 11 bảng. |
| Dọn môi trường | Không còn schema `prl_models_test_*`; đã dừng MySQL riêng dùng cho tests. |

Báo cáo JUnit của lần xác nhận 70 tests được ghi vào `build/model-review-tests.xml`;
file tạm này đã được dọn trước commit. Kết quả được ghi tại bảng trên; có thể tạo lại báo cáo
bằng lệnh trong [tài liệu tests](model-tests.md).
Hướng dẫn chạy lại nằm trong [tài liệu models](../architecture/database-models.md).

## Phạm vi đã kiểm chứng và công việc còn lại

Phần ánh xạ lưu trữ và các ràng buộc database đã đúng thiết kế trong phạm vi models.
Các quy tắc liên quan nhiều hàng hoặc người thao tác phải được triển khai và kiểm thử ở service:
vai trò/quyền sở hữu, giao khoảng, hạn mức ba lịch, lịch không nhận, thứ tự khóa, chuyển trạng thái,
actor, hạn đặt/hủy, trường bất biến và giao dịch audit/thông báo. Models không tự thực hiện các
quy tắc này; thiết kế cũng giao chúng cho service.

Chưa triển khai API/service nghiệp vụ, migration Alembic, dữ liệu seed hay quyền INSERT/SELECT
riêng cho tài khoản audit. Các bài kiểm tra đồng thời và SLO cần đợi luồng nghiệp vụ được triển khai.
Workflow đã chạy thành công trên GitHub Actions ở commit `62acd33`:
[kết quả CI](https://github.com/4PMs-3Workers/Production-Readiness-Lab/actions/runs/38112810486).
Kết quả này xác nhận bộ kiểm tra của tầng models trên runner và MySQL thật; chưa xác nhận
review/approve hoặc merge PR.

Thay đổi được chia thành các commit theo chức năng và bàn giao qua nhánh
`feature/database-models`. Commit bàn giao tham chiếu issue bằng `Closes #1`.
Nhánh mặc định của repository là `main`, nên GitHub chỉ tự đóng issue khi commit này
được merge vào `main`; push feature branch chưa đóng issue. PR chưa được tạo hoặc merge.
