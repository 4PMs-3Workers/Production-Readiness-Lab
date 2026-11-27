# Tích hợp schema và kiểm chứng trước merge — 27/11/2026

Codex AI thực hiện và review sau yêu cầu merge PR #66 của người dùng. Nguồn quota trước tích hợp là `1cd809911ebee006ba6adef9410e184701b703f7`; develop được tích hợp là `8e34f997f2e9b5a56afab2dc176e0a585db22269`. Baseline bài tập vẫn là `12a74f7308550ccc294a783edde79db267a526d1`. Người dùng đã cho phép rõ ràng việc tích hợp schema và kiểm thử sau khi automatic approval review chặn bước tích hợp ban đầu. Chưa có xác nhận sinh viên tự review hoặc hiểu mọi thay đổi.

## Hợp đồng cuối

Migration mới `20261127_1200_quota_acct_merge` nối nguyên trạng hai migration quota và soft-delete; không sửa migration lịch sử. Alembic báo đúng một head. Mỗi pin lịch sử có catalog bảng/cột riêng; migration bắt đầu từ catalog chính xác của stable manifest. Resume chỉ nhận catalog candidate hoàn chỉnh khi đúng immutable target đang cần recovery đã đi vào migration. Manifest phải được xác minh trước select/render và schema probe. Proof lịch sử được đối chiếu với manifest lưu trong cache của đúng release.

Rollback về `20261019_1200_proposal_replay` chỉ được phép nếu quota bằng 3 và không còn dữ liệu soft-delete cần giữ. Kiểm tra cả hai trước mọi DDL, vì MySQL DDL commit ngầm và hai nhánh không phải một transaction. Read-only refusal giữ nguyên pending deploy, target, event history và recovery requirement. Kiểm tra lại sau drain API; từ chối phải có đủ thời gian khởi động lại API. Rollback về hai pin nhánh trung gian bị từ chối, không tuyên bố tương thích.

Đối chiếu hash dùng Git blob bytes, không dùng hash CRLF của working tree. ACCOUNT transition dùng baseline bài tập; ADMIN transition dùng develop vừa tích hợp, chỉ đổi Compose và grants. Không thêm accepted hash để vượt kiểm thử. Fixture route-map của baseline có LF và hash được test. Source CSV cũ `aed6c68` cần đường nâng cấp qua develop được duyệt, không được chấp nhận trực tiếp. State v1 bỏ field false/None như reader cũ yêu cầu; state có migration marker/source thật cần controller mới.

## Review và các failure đã quan sát

Lượt pytest đầu của bản tích hợp: 4 failed, 1.972 passed, 676 skipped. Các lỗi là thứ tự/configuration của fixture legacy manifest, binding schema khi render một source lịch sử, policy guard bị áp dụng nhầm cho CSV transition, và subprocess local nạp backend từ editable install ở workspace khác. Đã sửa đúng source/configuration; chạy pytest với `PYTHONPATH` trỏ backend của worktree. Không làm production bỏ qua catalog thiếu cột.

Review độc lập bằng AI phát hiện test MySQL mới dùng UPDATE trên bảng chưa seed nên không tạo quota 2; đã tạo singleton rõ ràng bằng ORM. Review cũng phát hiện hai lỗi hành vi. Test đồng hồ giả cho thấy restart chỉ còn 6 giây thay vì ít nhất 15, và guard vẫn drain khi chỉ còn 99 giây. Hai test này RED trước sửa. Gộp quota/lifecycle vào một SQL và yêu cầu dự trữ 100 giây trước drain; tổng deadline rollback vẫn 300 giây. Test mới cho `--resume` trên target khác sau một deploy đã hoàn tất RED vì controller nhận sai catalog candidate và báo succeeded. Đã yêu cầu recovery đang pending và target trùng khớp trước override; test kiểm tra state trên đĩa và một deploy hợp lệ tiếp theo. Nhóm đầy đủ sau sửa qua 269 test.

Lượt frontend đầu chạy đồng thời với các bộ nặng có 5 failure do timeout chờ UI trong 4 file: `admin-session-generation`, `booking-submission`, `proposal-panel`, `api-booking-recovery`. Chạy riêng toàn bộ với `--maxWorkers=2` qua 44 files, 500 tests; không đổi assertion, timeout hay cấu hình CI. Typecheck, lint và formatting đều qua. Node thực tế `v24.17.0`, npm dùng đúng `12.2.0`; `npm ci` audit 272 packages, 0 vulnerabilities.

## Kiểm chứng và giới hạn

Lượt pytest đầy đủ trên nguồn cuối qua **1.979 tests, 676 skipped, 7 deprecation warnings**, exit 0 trong 257,38 giây. Lệnh: `python -m pytest -q -p no:cacheprovider --basetemp <scratch> --junitxml <scratch>/result.xml`, với `PYTHONPATH` trỏ `src/backend` trong worktree. Skip local không được tính là MySQL hoặc Linux đã chạy.

Python 3.12 trong môi trường cô lập. Black kiểm tra 70 Python files đã stage; Ruff qua; Pyright 1.1.407 báo 0 errors/warnings/informations. OpenAPI export khớp byte với artifact đã commit. `pip-audit==2.10.1` trong môi trường audit riêng nhận đủ `requirements.txt`, `requirements-offhost.txt`, `requirements-dev.txt` và báo không có vulnerability đã biết; exceptions vẫn rỗng. `git diff --cached --check` với `core.whitespace=cr-at-eol` qua, giữ nguyên prompt bytes CRLF lịch sử được đánh dấu `-text` từ develop.

78 test MySQL liên quan được collect thành công. Local không có Docker Linux engine/MySQL test endpoint, nên skip không phải bằng chứng integration đã chạy. CI phải xác nhận migration, toàn bộ MySQL backend, container acceptance, frontend API và rehearsal Linux trên chính head mới, gồm cả push duplicates. Các bộ controller dùng effects giả không chứng minh hiệu năng hoặc live recovery. Log RED/GREEN, mutation/load và PDF cũ được giữ nguyên; đây là bổ sung có ngày, không thay snapshot cũ. Không triển khai dịch vụ trong nhiệm vụ này.
