# Appointment change proposals: API, replay và precondition

**Trạng thái:** đã triển khai backend cho [issue #25](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/25), ngày 19/10/2026, trên nhánh `feature/appointment-change-proposals`. Bao gồm năm API, metadata/migration replay và shared expiry transition. Worker polling/lifecycle và UI nằm ngoài phạm vi; feature cần phát hành cùng companion worker.

Implementation bắt đầu từ `origin/develop` tại `f5fed91` (đã gồm PR #24 và PR #23). Migration mới `20261019_1200_proposal_replay` theo sau head thực tế `20261017_2350_student_profile`. Thiết kế ban đầu đối chiếu tại `408cc64`; không rewrite các migration đã áp dụng.

## 1. Quyết định thiết kế

Dùng ba thành phần cho POST tạo/thay đề nghị:

| Thành phần                         | Trả lời câu hỏi                                          | Cách lưu/kiểm tra                                          |
| ---------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------- |
| `Idempotency-Key` và hash nội dung | Thao tác này đã commit chưa?                             | Lưu trên proposal được tạo; unique theo appointment.       |
| `expected_proposal_version`        | Có đề nghị nào được tạo sau lần staff đọc dữ liệu không? | So với bộ đếm trên appointment, tăng khi tạo/thay đề nghị. |
| `expected_pending_proposal_id`     | Đề nghị mà staff định thay có còn đang chờ không?        | So với ID pending thực tế, hoặc `null` nếu không có.       |

Key nhận diện **một ý định đã gửi**. Hai precondition nhận diện **ngữ cảnh đã đọc**. Chúng giải quyết hai vấn đề khác nhau và đều bắt buộc. Không tự sửa precondition hoặc đổi key để phát lại một request chưa rõ kết quả.

Lưu metadata ngay trên `change_proposals`, vì một lần tạo/thay thành công luôn tạo đúng một proposal và các giá trị của biên nhận có thể dựng lại từ dữ liệu bất biến. Chưa cần bảng operation receipts riêng, process-local cache hoặc JSON snapshot của toàn bộ response.

Giữ nguyên `appointments.creation_request_key/hash`: hai trường đó chỉ dành cho đặt lịch, không dùng cho đề nghị.

### 1.1. Vì sao cần cả version và pending ID?

| Chỉ dùng                            | Lỗ hổng                                                                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Key                                 | Hai tab gửi hai key mới vẫn có thể ghi đè ý định của nhau.                                                                                                                     |
| Key + pending ID/null               | Tab cũ thấy không có pending; P1 được tạo rồi chấp nhận; tab cũ lại thấy điều kiện `null` đúng và tạo đề nghị ngoài ý muốn. Đây là tình huống trạng thái quay về hình dạng cũ. |
| Key + version chỉ tăng khi tạo      | Staff chuẩn bị thay P1; sinh viên chấp nhận P1 mà version chưa tăng; request thay P1 có thể bị hiểu thành tạo đề nghị mới.                                                     |
| Key + version tạo + pending ID/null | Version chặn chu kỳ tạo rồi kết thúc; pending ID chặn quyết định/hủy vừa xảy ra.                                                                                               |

Một phương án khác hợp lệ là revision tăng trên **mọi** chuyển trạng thái proposal. Phương án ở đây giữ bộ đếm chỉ thuộc luồng tạo/thay, nên cancellation, student decisions và worker không phải cập nhật thêm bộ đếm. Không dùng `updated_at`, thứ tự UUID hoặc `created_at DESC` làm concurrency token.

## 2. Những điều luôn phải đúng

1. Một `(appointment_id, Idempotency-Key)` đã commit chỉ ánh xạ đến một proposal và một hash.
2. Replay đúng key/hash trả biên nhận tạo ban đầu; không thay proposal hiện tại.
3. Một thao tác mới chỉ được ghi nếu **cả hai** precondition khớp sau khi khóa appointment.
4. `proposal_version` chỉ tăng một lần cho mỗi proposal mới; replay, rollback và các quyết định không làm tăng.
5. `CHANGE_PENDING` có đúng một proposal `PENDING`; trạng thái khác không có pending. Unique hiện có chỉ bảo đảm phần “tối đa một”; service kiểm tra phần còn lại.
6. Tạo/thay proposal giữ nguyên interval/location của appointment. Chỉ acceptance áp dụng giá trị đề nghị.
7. Một thay đổi nghiệp vụ commit cùng đúng một audit event và hai thông báo cho hai người tham gia. Replay/no-op không thêm bản ghi nào.
8. Trạng thái kết thúc và metadata giải quyết proposal không bị sửa lại. Key, hash, created version, proposed values, `created_at`, `expires_at` cũng bất biến.

## 3. Hợp đồng HTTP

### 3.1. Đọc lịch sử và ngữ cảnh để gửi request

Giữ hai GET theo issue:

```text
GET /api/v1/staff/me/appointments/{appointment_id}/change-proposals
GET /api/v1/appointments/{appointment_id}/change-proposals
```

Response là schema riêng mở rộng pagination hiện có, không sửa `PageResponse` chung:

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0,
  "context": {
    "appointment_id": "80000000-0000-4000-8000-000000000001",
    "appointment_status": "UPCOMING",
    "proposal_version": 0,
    "pending_proposal_id": null
  }
}
```

- Mỗi item có ID, appointment ID, proposed interval/location, status, `created_by`, `created_at`, `expires_at`, `resolved_at`, `resolved_by`; có thể trả thêm nullable `created_version` để đối chiếu lịch sử legacy.
- Giữ `page >= 1`, `1 <= page_size <= 100`, mặc định `1/20`; sắp `created_at DESC, id DESC`.
- `context` lấy từ appointment và truy vấn pending thực tế, không suy ra từ item đầu của trang. Trang sau hoặc trang rỗng vẫn có context đầy đủ.
- Dựng context, total và page trong **một SQL SELECT không khóa**: appointment đã lọc quyền là hàng gốc, LEFT JOIN pending và derived page, scalar subquery đếm total. Hàng gốc vẫn tồn tại nếu page rỗng. Tất cả phần đọc dùng cùng statement snapshot; giữ isolation `READ COMMITTED` của hệ thống.
- Pagination có thứ tự xác định trong mỗi lần đọc; không cam kết snapshot cố định qua nhiều request khi lịch sử tiếp tục được thêm. Context chỉ là điều kiện dự kiến và luôn được kiểm tra lại lúc ghi.
- Không trả creation key/hash trong history. GET không yêu cầu CSRF.

### 3.2. Tạo hoặc thay đề nghị

```http
POST /api/v1/staff/me/appointments/80000000-0000-4000-8000-000000000001/change-proposals
Idempotency-Key: 6ac835d4-76cd-449e-a96e-91d9d1f8b6dc
Content-Type: application/json
Origin: https://app.example.invalid
X-CSRF-Token: <signed-token-matching-cookie>
Cookie: prl_session=<session>; prl_csrf=<signed-token>

{
  "expected_proposal_version": 0,
  "expected_pending_proposal_id": null,
  "proposed_starts_at": "2026-10-23T09:00:00+07:00",
  "proposed_location": "Phòng B204"
}
```

Quy tắc schema:

- Cả bốn trường đều bắt buộc; hai trường nullable vẫn phải xuất hiện. Không dùng “thiếu location” để ngầm giữ nguyên địa điểm.
- `expected_proposal_version` là JSON integer thực sự, không nhận bool/chuỗi/float; trong `[0, 9007199254740991]` để client JavaScript biểu diễn chính xác.
- Pending ID là UUID hoặc `null`. Nếu khác `null`, ID phải thuộc appointment được cấp quyền; ID không tồn tại hoặc thuộc lịch khác trả cùng `404 NOT_FOUND`. ID lịch sử đã kết thúc vẫn là tham chiếu hợp lệ để kiểm tra replay, sau đó có thể thất bại ở precondition.
- Start nhận chuỗi timestamp có offset, chuẩn hóa UTC; từ chối naive datetime, numeric epoch và độ chính xác vượt quá sáu chữ số thập phân thay vì âm thầm làm mất dữ liệu.
- Location là chuỗi tối đa 500 ký tự hoặc `null`. `null` có nghĩa là bỏ địa điểm; muốn giữ nguyên phải gửi giá trị hiện tại. Từ chối chuỗi chỉ có whitespace; chuỗi hợp lệ được giữ nguyên, không trim hay chuẩn hóa Unicode ngầm.
- Location-only vẫn gửi start hiện tại. End luôn do server tính từ `appointment.duration_minutes`, không lấy lại duration mới của purpose.
- Cấm extra fields: actor/owner, status, expiry/resolution, duration/end, mô tả tư vấn... Không nhận query parameters cho POST.
- Key dùng validation hiện có `IdempotencyKey`, phân biệt chính xác theo `CODE_COLLATION`, không trim/case-fold. Từ chối header `Idempotency-Key` trùng lặp thay vì tự chọn một giá trị.

Lần tạo mới trả `201`, replay trả `200`, dùng cùng schema biên nhận bất biến:

```json
{
  "id": "90000000-0000-4000-8000-000000000001",
  "appointment_id": "80000000-0000-4000-8000-000000000001",
  "created_version": 1,
  "created_at": "2026-10-19T04:00:00Z",
  "expires_at": "2026-10-21T17:00:00Z"
}
```

Ví dụ expiry trên giả định lịch gốc ngày 22/10, đề nghị ngày 23/10, timezone nghiệp vụ `Asia/Ho_Chi_Minh`. Cutoff là 00:00 ngày 22/10, tức 17:00 UTC ngày 21/10.

Biên nhận xác nhận **đã tạo proposal này**; không có trường trạng thái appointment/proposal hiện tại. `created_version` là version ngay sau lần tạo đó, không dùng làm context hiện tại khi nhận replay. Đọc GET history/detail để biết trạng thái mới nhất. Không dựng response từ status mutable rồi trả khác nhau tùy thời điểm retry.

Thay đề nghị dùng cùng endpoint, key mới cho ý định mới, version hiện tại và pending ID đang thấy:

```json
{
  "expected_proposal_version": 1,
  "expected_pending_proposal_id": "90000000-0000-4000-8000-000000000001",
  "proposed_starts_at": "2026-10-24T10:00:00+07:00",
  "proposed_location": null
}
```

### 3.3. Quyết định của sinh viên

```text
POST /api/v1/appointments/{appointment_id}/change-proposals/{proposal_id}/accept
POST /api/v1/appointments/{appointment_id}/change-proposals/{proposal_id}/decline
```

Body bắt buộc là `{}`, cấm trường bổ sung. Không cần key riêng: proposal ID và action nhận diện quyết định; mỗi proposal chỉ có một kết quả cuối.

Response `200` là resolution đã lưu, ví dụ:

```json
{
  "id": "90000000-0000-4000-8000-000000000001",
  "appointment_id": "80000000-0000-4000-8000-000000000001",
  "status": "ACCEPTED",
  "resolved_at": "2026-10-20T03:00:00Z",
  "resolved_by": "70000000-0000-4000-8000-000000000001"
}
```

Retry accept trên `ACCEPTED`, hoặc decline trên `DECLINED`, trả resolution cũ trước khi kiểm tra hạn/trạng thái appointment hiện tại. Không chạy lại acceptance hay hủy lịch lần nữa. Thử action trái ngược hoặc quyết định proposal `SUPERSEDED/CANCELLED/EXPIRED` trả `409 PROPOSAL_STATE_CONFLICT`.

Việc replay vẫn yêu cầu phiên hiện tại hợp lệ, đúng vai trò và ownership. Không trả lịch sử cho người mất quyền. Replay không yêu cầu người tham gia còn lại hoặc purpose/acceptance vẫn đủ điều kiện đặt mới.

Mọi POST, kể cả replay, yêu cầu signed CSRF và same-origin theo cơ chế sẵn có. Mọi success/failure giữ `Cache-Control: private, no-store`, request ID của lần HTTP hiện tại và error envelope/classification chung. Role sai bị chặn; ADMIN không có quyền ngầm. Đối tượng ngoài phạm vi và đối tượng không tồn tại trả cùng `404`.

## 4. Metadata cần thêm và migration

### 4.1. `appointments`

Thêm `proposal_version BIGINT NOT NULL DEFAULT 0`, CHECK trong `[0, 9007199254740991]`.

Đây là số thứ tự **tạo proposal**, không phải version của toàn bộ appointment và không phải số lượng pending. Dưới appointment lock, lần tạo/thay mới lấy `next_version = proposal_version + 1` và ghi atomically vào cả appointment lẫn proposal mới. Không tăng khi accept, decline, expiry, cancellation hay outcome. Chặn tràn số bằng lỗi dịch vụ, không wrap hoặc reset.

### 4.2. `change_proposals`

| Cột mới                 | Kiểu                                      | Ý nghĩa                                         |
| ----------------------- | ----------------------------------------- | ----------------------------------------------- |
| `creation_request_key`  | `VARCHAR(128)` nullable, `CODE_COLLATION` | Key của POST tạo/thay, scope theo appointment.  |
| `creation_request_hash` | `VARBINARY(32)` nullable qua `SHA256Hash` | Hash v1 của nội dung chuẩn hóa và precondition. |
| `created_version`       | `BIGINT` nullable                         | Version được cấp khi proposal này được tạo.     |

Thêm unique:

```text
uq_change_proposals_creation_request (appointment_id, creation_request_key)
uq_change_proposals_created_version  (appointment_id, created_version)
```

Giữ `uq_change_proposals_pending (appointment_id, pending_guard)` hiện có.

CHECK mới phải có đúng hai nhánh rõ ràng:

```text
(key IS NULL AND hash IS NULL AND created_version IS NULL)
OR
(key IS NOT NULL AND hash IS NOT NULL AND created_version IS NOT NULL
 AND CHAR_LENGTH(TRIM(key)) > 0
 AND OCTET_LENGTH(hash) = 32
 AND created_version BETWEEN 1 AND 9007199254740991)
```

Nhánh all-null dành cho dữ liệu legacy; service mới chỉ tạo nhánh đầy đủ. Các điều kiện `IS NOT NULL` là cần thiết, tránh CHECK chấp nhận một kết quả UNKNOWN khi metadata bị thiếu một phần. Không có CHECK xuyên bảng bảo đảm version hai bảng bằng nhau; service và tests chịu trách nhiệm.

MySQL cho phép nhiều giá trị NULL trong unique index, nên lịch sử legacy không cần key giả để vượt migration. Xem [MySQL 8.4: unique indexes](https://dev.mysql.com/doc/refman/8.4/en/create-index.html#unique-indexes).

### 4.3. Upgrade, legacy và retention

1. Tạo revision mới sau **head thực tế trên nhánh triển khai**; không rewrite applied migrations hoặc hardcode parent từ nhánh frontend.
2. Thêm counter mặc định 0, ba cột nullable, CHECK và hai unique. Không sửa proposed values/status/timestamps cũ.
3. Proposal cũ giữ cả ba cột NULL. Appointment cũ bắt đầu version 0, kể cả khi đã có proposal legacy: đây là baseline của protocol mới, không phải đếm lại lịch sử.
4. Legacy pending vẫn được đọc, thay hoặc quyết định bình thường. Khi thay, dùng `(version=0, pending_id=legacy_id)`; proposal mới nhận version 1. Không hỗ trợ replay POST tạo cũ khi trước đó chưa có key/receipt.
5. Kiểm tra dữ liệu bất nhất trước khi bật feature, bao gồm pending pair và duration; không tự sửa lịch sử bằng migration này. Nếu DDL dừng giữa chừng, dừng rollout và đối chiếu `alembic_version`, `SHOW CREATE TABLE` với từng bước của revision. Hoàn tất các DDL còn thiếu bằng migration job; chỉ stamp revision khi toàn bộ cột/CHECK/UNIQUE đã khớp và kiểm tra schema thành công. Không chạy lại mù quáng các ADD đã áp dụng hoặc drop metadata để phục hồi.
6. Giữ key/hash cùng proposal trong suốt thời gian giữ lịch sử, tối thiểu 24 giờ và không đặt TTL ngắn cho metadata replay. Không xóa riêng receipt rồi cho phép dùng lại key. Purge tương lai phải giữ counter và giữ tombstone nếu vẫn phục vụ appointment đó; cần thiết kế riêng trước khi cho phép purge.
7. Downgrade phải từ chối nếu có metadata mới hoặc `proposal_version > 0`; việc proposal đã kết thúc không làm mất nhu cầu replay. Rollback ứng dụng không tự chạy migration down.

## 5. Chuẩn hóa nội dung và scope key

Scope là `(appointment_id, creation_request_key)` cho duy nhất endpoint staff create/replace. Assignment/participants bất biến; kiểm tra staff đang được giao trước khi tra key. Cùng key ở appointment khác hoặc ở endpoint booking là scope khác, dù client vẫn nên sinh UUID ngẫu nhiên cho mọi ý định mới.

Canonical payload v1:

```json
{
  "actor_id": "<uuid-from-authenticated-session>",
  "appointment_id": "<uuid-from-path>",
  "expected_pending_proposal_id": null,
  "expected_proposal_version": 0,
  "operation": "appointment-change-proposal-create:v1",
  "proposed_location": "Phòng B204",
  "proposed_starts_at": "2026-10-23T02:00:00.000000Z"
}
```

Serialize theo cùng cách với `domain/booking.py`: JSON UTF-8, `sort_keys=True`, compact separators, `ensure_ascii=False`; UUID lowercase chuẩn, timestamp UTC đúng sáu chữ số microsecond; SHA-256 lấy đủ 32 byte. Hai timestamp tương đương offset có cùng hash. Phân biệt `null` với chuỗi; giữ nguyên chuỗi hợp lệ, kể cả khác biệt Unicode/whitespace có ý nghĩa.

Hash bao gồm **cả hai precondition**; sửa precondition là sửa nội dung thao tác. Không đưa key, CSRF/session, request ID, thời gian hiện tại, expiry, cấu hình hiện tại hay end/duration suy ra vào hash. Protocol v1 phải giữ cách canonicalize ổn định trong các release còn phục vụ receipt v1; nếu đổi thuật toán cần version metadata/migration tương ứng, không âm thầm đổi hàm hash.

Chỉ ghi receipt cho thao tác **thành công đã commit**. Validation/business failure rollback không giữ key, không memoize lỗi. Client vẫn dùng key mới khi sửa ý định sau một lỗi rõ ràng; không giả định mọi lần HTTP đã được ghi nhận vĩnh viễn.

## 6. Thuật toán tạo/thay và thứ tự kiểm tra

Service sở hữu transaction; repository nhận session đó. Dùng `READ COMMITTED`, các budget request/query/lock hiện có, và `populate_existing=True` khi đọc lại ORM state dưới khóa.

1. Xác thực, kiểm tra STAFF, CSRF/origin, schema và canonical hash. Các input không hợp lệ có thể bị từ chối trước khi tra receipt.
2. Bắt đầu transaction, khóa `service_settings FOR SHARE`. Đọc sơ bộ appointment đã lọc assignment để xác định hai participant ID.
3. Khóa từng account theo UUID-byte tăng dần bằng các SELECT riêng; khóa appointment `FOR UPDATE` sau đó.
4. Đọc lại assignment/participants và actor active/role. Nếu không còn quyền, trả cùng `404` phù hợp cơ chế hiện có. Tham chiếu `expected_pending_proposal_id` khác null phải thuộc appointment; kiểm tra bằng query scoped, không trả dữ liệu từ lịch khác.
5. Tra receipt `(appointment_id, key)` dưới appointment lock:
   - Có và hash giống: dựng biên nhận từ cột bất biến, kết thúc transaction đọc bằng rollback, trả `200`.
   - Có nhưng hash khác: rollback, trả `409 IDEMPOTENCY_CONFLICT`.
   - Không có: tiếp tục thao tác mới.
6. Khóa pending proposal và các association rows cần thiết theo protocol chung. Kiểm tra cặp appointment/pending nhất quán; nếu hỏng invariant, trả `503`, không âm thầm sửa. So cả version và pending ID với precondition; khác bất kỳ trường nào trả `409 PROPOSAL_PRECONDITION_FAILED`.
7. Yêu cầu appointment `UPCOMING` hoặc `CHANGE_PENDING`; trạng thái kết thúc trả `409 APPOINTMENT_STATE_CONFLICT`.
8. Sau **mọi** lock có thể chờ, chạy câu SQL riêng `SELECT UTC_TIMESTAMP(6)` lấy `now`. Không nhúng đồng hồ vào chính câu SELECT đang chờ lock: các hàm current time được tính tại đầu statement. Xem [MySQL 8.4: current time evaluation](https://dev.mysql.com/doc/refman/8.4/en/date-and-time-functions.html).
9. Nếu thay pending và `now >= old.expires_at`, trả `409 PROPOSAL_EXPIRED`. Không cho request mới kéo dài cửa sổ đã hết hạn. Replay ở bước 5 vẫn thành công dù proposal đã hết hạn/kết thúc.
10. Kiểm tra active participant roles, purpose/acceptance, ngày đặt, service windows, start alignment, unavailability, staff/student overlap bằng duration đã lưu; loại chính appointment này khỏi overlap. Kiểm tra có thay đổi ít nhất start hoặc location so với appointment hiện tại, nếu không trả `409 PROPOSAL_NO_CHANGE`.
11. Tính expiry từ 00:00 địa phương của ngày sớm hơn giữa start appointment hiện tại và proposed start; chuyển UTC và yêu cầu `now < expires_at`. Lưu cutoff, không tính lại khi replay hoặc sau khi timezone config đổi. Dùng `business_day_bounds` và chính sách lỗi cấu hình hiện có, không cộng/trừ cứng 7 giờ.
12. Nếu thay: set proposal cũ `SUPERSEDED`, `resolved_at=now`, `resolved_by=staff_id`; **flush UPDATE cũ trước khi INSERT mới** để giải phóng unique pending. Đây vẫn là một transaction, chưa commit.
13. Tạo proposal mới `PENDING`, interval đầy đủ, `created_at=now`, expiry, actor, key/hash và `created_version=version+1`; tăng counter appointment, set `CHANGE_PENDING`. Dùng cùng DB time đã kiểm tra để tránh default timestamp trễ làm mâu thuẫn `expires_at > created_at`.
14. Ghi một audit và hai notifications, flush, dựng biên nhận bất biến. `commit_safely` thành công mới trả `201`; lỗi rollback cả supersede, counter, insert và side effects.

Đặt receipt lookup **trước** precondition, trạng thái, expiry và availability là bắt buộc: một replay có thể mang version cũ chính vì request gốc đã thành công. Kiểm tra quyền và scoped reference vẫn đứng trước replay; replay không phải cơ chế bỏ qua authorization.

Không có fast path đọc receipt ngoài khóa trong thiết kế ban đầu. Tối ưu thêm sau nếu đo thấy cần và vẫn bảo đảm quyền/trạng thái đọc. Cùng key đang chạy sẽ chờ khóa trong budget; hết budget trả sanitized `503`, không suy ra `REQUEST_IN_PROGRESS` chỉ từ một lock timeout.

Khóa nhân viên và sinh viên đồng bộ luồng này với booking và thay đổi availability của #22. Giữ khóa đến commit/rollback theo [MySQL 8.4: locking reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html). Không cần giữ transaction trong thời gian chờ sinh viên phản hồi.

### 6.1. Pseudocode phần quyết định replay/precondition

```text
require_current_authorization_and_scoped_references()
saved = find_creation_receipt(appointment.id, key)
if saved exists:
    if saved.creation_request_hash != request_hash:
        fail IDEMPOTENCY_CONFLICT
    return original_receipt(saved), replay=true

pending = lock_current_pending(appointment.id)
assert_consistent_pair(appointment, pending)
if appointment.proposal_version != payload.expected_proposal_version:
    fail PROPOSAL_PRECONDITION_FAILED
if id_or_null(pending) != payload.expected_pending_proposal_id:
    fail PROPOSAL_PRECONDITION_FAILED

require_eligible_appointment_state()
now = read_database_time_after_all_required_locks()
require_unexpired_pending_and_valid_new_proposal(now)
supersede_if_needed_then_insert_bundle_and_increment_version()
commit_safely()
return original_receipt(new_proposal), replay=false
```

Key mới nhưng nội dung proposed giống pending không tự được coi là replay. Nếu precondition đúng và vẫn khác appointment gốc, đó là một thao tác thay mới được yêu cầu tường minh. Retry phải giữ key cũ; server không đoán ý định dựa trên sự trùng nhau của giờ/địa điểm.

## 7. Quyết định, hủy và expiry cùng tồn tại thế nào?

### 7.1. Student decision

Lấy cùng thứ tự khóa settings → participant accounts UUID-byte → appointment → target/pending proposal. Nếu cần khóa hai proposal khác nhau, lấy theo UUID-byte tăng dần. Kiểm tra target bằng cả proposal ID và appointment ID, không chỉ tra global ID.

Sau authorization, kiểm tra resolution replay trước: action trùng trạng thái `ACCEPTED/DECLINED` và metadata hợp lệ thì trả kết quả đã lưu; metadata kết thúc bị thiếu/sai actor là lỗi dịch vụ. Không kiểm tra expiry hay khả dụng đặt mới cho replay.

Với quyết định mới: yêu cầu target chính là pending của appointment `CHANGE_PENDING`, đọc DB time sau tất cả khóa, yêu cầu `now < expires_at`.

- **Accept:** kiểm tra lại toàn bộ eligibility và proposed interval bằng duration đã lưu; bỏ qua chính appointment. Nếu xung đột, rollback giữ original appointment và pending. Nếu hợp lệ, áp dụng interval/location, set `UPCOMING`/`ACCEPTED`, lưu student resolution. Không kiểm tra “đã có ba lịch thì cấm” như khi tạo mới: lịch đang chiếm một suất và acceptance không tăng quota.
- **Decline:** set `CANCELLED`/`DECLINED`, `resolved_at=cancelled_at=now`, hai actor bằng student; không kiểm tra purpose/acceptance hoặc active status của người tham gia còn lại chỉ để từ chối. Actor hiện tại vẫn phải được xác thực và đủ quyền.
- Đúng hạn `now == expires_at` hoặc trễ hơn trả `409 PROPOSAL_EXPIRED`, không tự chuyển EXPIRED trong HTTP request.

Counter tạo đề nghị không đổi; pending ID đổi thành `null` sau quyết định. Vì vậy request thay P1 đến sau acceptance bị precondition chặn dù version chưa đổi.

Đồng hồ sau khóa là mốc đánh giá quyết định. Commit có thể hoàn tất sau mốc đó; không cam kết thời điểm trả HTTP trước expiry. Một request bắt đầu trước hạn nhưng **lấy đủ khóa sau hạn** phải bị từ chối.

### 7.2. Cancellation và outcome hiện có

Giữ luồng cancellation: khi hủy `CHANGE_PENDING`, đóng proposal thành `CANCELLED`, giữ metadata tạo và version, ghi audit/thông báo hiện có. Request tạo mới sau cancellation gặp lỗi trạng thái hoặc precondition; replay key cũ vẫn chỉ trả receipt cũ.

Outcome chỉ nhận appointment `UPCOMING` sau giờ kết thúc, giữ hợp đồng #21. Nếu acceptance đã thành công rồi appointment được hủy/ghi outcome/tạo P2, retry quyết định P1 vẫn trả resolution P1, không đưa appointment về trạng thái cũ. Booking creation receipt cũng giữ nguyên.

### 7.3. Handoff cho expiry worker

Shared helper tại `app.services.change_proposals`:

```text
expire_change_proposal_in_transaction(
    db, proposal_id, settings, request_id
) -> EXPIRED | NOT_DUE | TERMINAL | NOT_FOUND | INCONSISTENT
```

- Caller mở transaction riêng và sở hữu commit/rollback, budget và bounded retry. Helper không tự begin/commit, không giữ transaction qua nhiều lượt polling, không bắt rồi nuốt database exception.
- Caller chưa giữ khóa nghiệp vụ khác. Có thể truyền ID lấy từ scan không khóa; không khóa proposal trước rồi mới gọi helper, vì sẽ đảo thứ tự khóa.
- Helper tự xác định participant IDs, lấy toàn bộ khóa theo thứ tự chung, đọc lại cặp appointment/proposal và DB time. Dùng persisted expiry, không phụ thuộc policy timezone hiện tại hoặc eligibility đặt mới.
- Chỉ `PENDING + CHANGE_PENDING + now >= expires_at` mới đổi `EXPIRED/CANCELLED`, lưu cùng `now`, `resolved_by=NULL`, `cancelled_by=NULL`; giữ nguyên interval/location và version. Dùng cancellation reason cố định theo hợp đồng nghiệp vụ, không lấy từ nội dung tư vấn.
- Ghi một `CHANGE_PROPOSAL_EXPIRED` audit với system actor NULL và hai notifications trong cùng transaction. Actual event type/action cần thống nhất với worker trước release.
- Terminal target là no-op, kể cả appointment đã có trạng thái mới hoặc proposal mới; chưa đến hạn cũng no-op. `PENDING` đi cùng appointment sai trạng thái là `INCONSISTENT`, không ghi đè terminal appointment, không tự sửa một nửa cặp. Caller có thể ghi metric/log identifier đã làm sạch để xử lý vận hành.
- Không yêu cầu participant đang active/đúng vai trò hiện tại để giải phóng lịch hết hạn. Tài khoản vẫn được khóa để đồng bộ với các tác vụ khác.
- Không tìm thấy target trả `NOT_FOUND`; các no-op không thêm audit/notification. Lần gọi sau một uncertain commit sẽ thấy terminal nếu lần trước đã commit và không tạo thêm side effects.

## 8. Cạnh tranh và phục hồi

### 8.1. Những lịch thực thi cần chứng minh

```mermaid
sequenceDiagram
    participant A as Tab A
    participant API as Proposal API
    participant DB as MySQL
    participant B as Tab B
    A->>API: Key K1, context (0, null)
    API->>DB: Commit P1, version 1, receipt K1
    API--xA: Response bị mất
    B->>API: Đọc context (1, P1)
    B->>API: Key K2, thay P1
    API->>DB: Commit P2, version 2; P1 SUPERSEDED
    A->>API: Retry đúng K1 và body cũ
    API->>DB: Tra receipt K1 trước precondition
    API-->>A: 200 receipt P1; P2 giữ nguyên
```

| Tình huống                                             | Kết quả bắt buộc                                                                                            |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| Hai request cùng key/hash, cùng context                | Một `201`; request còn lại replay `200` cùng ID/created version/timestamps.                                 |
| Cùng key nhưng khác hash                               | Nếu một thao tác commit, request khác trả `409 IDEMPOTENCY_CONFLICT`; không có proposal thứ hai.            |
| Hai key mới, cùng version/pending ID                   | Một thao tác commit; request đến sau khóa trả `409 PROPOSAL_PRECONDITION_FAILED`.                           |
| A đã tạo P1 nhưng mất response; B thay P1 bằng P2      | Retry A trả receipt P1; P2 vẫn pending, không có P3.                                                        |
| A chưa commit/đã rollback; B tạo P1; A retry ý định cũ | A không có receipt, context cũ không khớp; `409`, không thay P1.                                            |
| Tab cũ đọc `(0,null)`; P1 được tạo rồi accept          | Actual `(1,null)`; request mới của tab cũ bị chặn bằng version.                                             |
| Staff đọc `(1,P1)`; sinh viên accept P1                | Actual `(1,null)`; request thay P1 bị chặn bằng pending ID.                                                 |
| Accept và replace P1 cạnh tranh                        | Bên commit trước quyết định; replace trước thì accept P1 conflict, accept trước thì replace stale conflict. |
| Accept và decline cùng P1                              | Một resolution cuối; action còn lại conflict; retry action thắng trả resolution cũ.                         |
| Accept và booking chiếm proposed slot                  | Cùng khóa account; bên sau kiểm tra lại. Nếu booking thắng, accept conflict và giữ lịch gốc.                |
| Cancellation và create/replace/decision cạnh tranh     | Cùng khóa; không có pending trên appointment đã hủy, không phục hồi terminal appointment.                   |
| Worker, quyết định hoặc thay đề nghị chờ khóa qua hạn  | DB time sau khóa quyết định; expiry thắng thì terminal; request mới quá hạn không gia hạn.                  |

Giờ gốc vẫn occupied trong lúc chờ, proposed slot không được reserved. Những query overlap chỉ dùng `UPCOMING/CHANGE_PENDING`, không coi pending proposal là một lịch thứ hai.

### 8.2. Lỗi commit và deadline

Tái sử dụng `core/transactions.py`:

- Deadlock/lock timeout MySQL `1205/1213`: rollback **toàn bộ** attempt; chỉ retry trong `max_deadlock_retries` và thời gian còn lại của cùng request deadline. Mỗi attempt phải tra lại receipt và precondition; không cấp lại version từ biến Python cũ.
- Duplicate `uq_change_proposals_creation_request`: rollback rồi tra lại receipt trong transaction mới, vẫn xác minh quyền; hash giống replay, khác conflict. Nếu không đọc được receipt thì trả `503`, không tự chuyển sang insert mới. Chỉ nhận diện đúng tên constraint, không coi mọi `1062` là replay.
- Duplicate pending hoặc created-version unique là lỗi invariant/concurrency cần xử lý như lỗi dịch vụ; không suy luận là cùng key, không tự thay một proposal khác.
- Lỗi trước commit đã xác định rollback: không giữ proposal, counter, audit hay notifications của attempt. Một POST từ client sau đó được đánh giá lại.
- Lỗi transport/cancel/timeout trong lúc commit có thể chưa rõ kết quả: invalidate connection, trả sanitized `503` nếu còn có thể gửi response; không tự chạy lại write trong request đó. Không coi `503` hoặc mất response là bằng chứng rollback.
- Nếu commit đã thành công nhưng serialize/HTTP delivery thất bại, receipt vẫn ở database và client phục hồi theo cùng protocol.

### 8.3. Client phục hồi mà không tạo thêm ý định

1. Trước dispatch, giữ nguyên appointment ID, key và toàn bộ body của ý định đang gửi, gắn đúng tài khoản hiện tại. Không lưu credential trong intent; nếu persist nội dung location phải theo chính sách dữ liệu của client.
2. Sau timeout/network error/`503`, trạng thái là “chưa xác định”. Cho phép retry tường minh hoặc retry có backoff hữu hạn bằng **đúng key/body/path cũ**; không dùng middleware tự replay mọi POST.
3. Nếu lần cũ commit, retry trả `200` receipt dù đã có P2 hoặc expiry/decision. Nếu lần cũ rollback, service có thể thực hiện đúng ý định đó khi context vẫn đúng; nếu context đã đổi, trả precondition conflict và dừng.
4. Nhận receipt thì đọc lại history/detail. Nếu receipt thuộc P1 đã superseded, hiển thị lịch sử/current state; không coi receipt là pending hiện tại.
5. Nhận `PROPOSAL_PRECONDITION_FAILED` thì refresh, cho người dùng xem thay đổi và xác nhận một ý định mới. Chỉ lúc đó sinh key mới với context mới. Không tự “rebase” payload lên version mới.
6. Nếu mất cả key/body, đọc lịch sử và trạng thái để quyết định bước tiếp theo; không đoán rằng không thấy ở trang 1 nghĩa là request chưa commit. GET history không thay thế receipt lookup theo key; POST lại với đúng key/body là kênh phục hồi chính.

Quyết định accept/decline retry cùng action và proposal ID; không chuyển sang proposal mới nhất một cách tự động.

## 9. Mã lỗi và side effects

| HTTP / code đề xuất                             | Khi nào                                                                  | Hành vi client                                              |
| ----------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------- |
| `401` / auth hiện có                            | Phiên không hợp lệ/hết hạn                                               | Đăng nhập lại; vẫn giữ intent để phục hồi đúng tài khoản.   |
| `403 FORBIDDEN_ROLE` / CSRF hiện có             | Vai trò hoặc bảo vệ POST không hợp lệ                                    | Sửa phiên/token theo cơ chế chung.                          |
| `404 NOT_FOUND`                                 | Appointment ngoài quyền, hoặc proposal reference không thuộc appointment | Không tiết lộ đối tượng khác.                               |
| `409 IDEMPOTENCY_CONFLICT`                      | Receipt cùng key, hash khác                                              | Dừng; sửa lỗi quản lý intent, không tự đổi key để gửi tiếp. |
| `409 PROPOSAL_PRECONDITION_FAILED`              | Version hoặc pending ID không khớp                                       | Refresh và xác nhận ý định mới.                             |
| `409 APPOINTMENT_STATE_CONFLICT`                | Không còn trạng thái cho phép tạo/đổi                                    | Đọc appointment hiện tại.                                   |
| `409 PROPOSAL_STATE_CONFLICT`                   | Quyết định trái kết quả hoặc target đã kết thúc bởi thao tác khác        | Đọc resolution/history; không phát lại action khác tự động. |
| `409 PROPOSAL_EXPIRED`                          | Thao tác mới với pending đã hết hạn, hoặc cutoff mới không còn tương lai | Chờ worker/đọc trạng thái; không gia hạn bằng replacement.  |
| `409 PROPOSAL_NO_CHANGE`                        | Proposed start/location đều bằng appointment hiện tại                    | Sửa ý định trước khi gửi lại.                               |
| `409 STAFF_UNAVAILABLE` / `STUDENT_UNAVAILABLE` | Xung đột interval/unavailability khi kiểm tra dưới khóa                  | Giữ nguyên appointment/proposal, chọn lại sau refresh.      |
| `422 VALIDATION_ERROR`                          | Schema/header/query/timestamp sai                                        | Sửa input; không có receipt được ghi.                       |
| `503 SERVICE_UNAVAILABLE`                       | Budget, invariant hoặc kết quả commit chưa rõ                            | Không suy luận rollback; phục hồi bằng đúng intent.         |

Lỗi purpose/acceptance/date/window/alignment tái sử dụng taxonomy hiện có ở phần validation tương ứng; phải liệt kê mapping cuối cùng trong OpenAPI lúc triển khai, không mở thêm một error envelope. Sau authorization và validation cú pháp, thứ tự create/replace là: **receipt → invariant → precondition → appointment state → expiry → eligibility/availability**. Không kiểm tra expiry để từ chối một receipt đã commit.

| Thao tác thực sự thay đổi dữ liệu  | Audit action đề xuất       | Notification event đề xuất | Actor       |
| ---------------------------------- | -------------------------- | -------------------------- | ----------- |
| Tạo proposal                       | `CHANGE_PROPOSAL_CREATED`  | `CHANGE_PROPOSAL_CREATED`  | Staff       |
| Thay pending bằng proposal mới     | `CHANGE_PROPOSAL_REPLACED` | `CHANGE_PROPOSAL_REPLACED` | Staff       |
| Accept                             | `CHANGE_PROPOSAL_ACCEPTED` | `CHANGE_PROPOSAL_ACCEPTED` | Student     |
| Decline, đồng thời hủy appointment | `CHANGE_PROPOSAL_DECLINED` | `CHANGE_PROPOSAL_DECLINED` | Student     |
| Expire, đồng thời hủy appointment  | `CHANGE_PROPOSAL_EXPIRED`  | `CHANGE_PROPOSAL_EXPIRED`  | NULL/system |

Mỗi hàng là một audit và hai notifications, không nhân đôi sự kiện proposal và appointment trong cùng thao tác. Hủy trực tiếp tiếp tục dùng `APPOINTMENT_CANCELLED` hiện có. Audit chỉ chứa identifier/revision cần thiết như appointment ID, proposal ID, superseded ID; không chứa location, mô tả tư vấn, body, key/hash, token hay credential. Request ID trong audit là của lần thực hiện thay đổi; request ID trong replay response là của lần HTTP hiện tại.

## 10. Tương thích release và phạm vi triển khai

- Migration thêm cột/index, giữ bảng và dữ liệu cũ. Cập nhật models/schema checks, accepted migration revisions, môi trường test/CI và tài liệu. Chỉ đánh dấu revision tương thích sau khi xác minh trên head thật, không hardcode một revision cũ của issue khác.
- Cần triển khai các route/service đề nghị và shared expiry helper cùng protocol; feature chỉ phát hành cho người dùng khi companion worker sẵn sàng. Issue #25 không viết entry point/polling/heartbeat của worker.
- Các thao tác #21 cancellation/outcome không cần tăng `proposal_version`, nhưng phải tiếp tục khóa appointment/proposal và giữ metadata tạo bất biến. Phải kiểm thử trên image/schema dự kiến rollback, không chỉ dựa vào việc migration là additive.
- Khi rollback về ứng dụng chưa có proposal endpoints, receipt/data vẫn giữ trong DB nhưng kênh replay và phản hồi có thể tạm không khả dụng. Không thể tuyên bố feature tiếp tục đầy đủ trên image cũ. Dừng nhận intent mới, giữ client intents và kế hoạch xử lý expiry; ưu tiên release sửa lỗi tương thích nếu vẫn cần phục vụ quyết định. Không drop metadata để ép quay lại schema cũ.
- #22 dùng cùng account lock cho acceptance/unavailability; không phụ thuộc endpoint của #22 để bắt đầu, nhưng phải test interoperability trước release.
- Thiết kế này không thêm staff/student UI, generic idempotency platform, inbox API, admin features, seed hay deployment implementation.

## 11. Ma trận kiểm chứng bắt buộc khi triển khai

| Nhóm              | Bằng chứng cần có                                                                                                                                                                                                                  |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Canonicalization  | Offset tương đương cùng hash; key phân biệt đúng; null/location khác nhau; đổi một precondition đổi hash; actor/path được bind từ server; loại request ID/CSRF.                                                                    |
| Validation/quyền  | Trường bắt buộc-nullable, extra fields, bool/string/float version, timestamps naive/numeric/quá precision, duplicate key header; STAFF/STUDENT/ADMIN; foreign pending reference và foreign decision path đều 404.                  |
| History/context   | Phân trang 1..100, tie created time theo UUID, trang rỗng có context, một SQL snapshot cho context/page/total; không lộ key/hash; token không lấy từ item đầu.                                                                     |
| Replay            | Sau create/replace/accept/decline/expiry/cancel/outcome/P2; receipt ID/version/timestamps giữ nguyên; số proposal/audit/notification không tăng.                                                                                   |
| Hai precondition  | Hai tab, version cũ không pending, pending vừa accepted nhưng version bằng nhau, cùng key nhưng body/precondition đổi; bắt đủ các hàng ở §8.1.                                                                                     |
| Nghiệp vụ         | Location-only, null bỏ location, stored duration khác purpose duration hiện tại, no change, inactive participant/purpose/acceptance; overlap loại chính appointment, proposed slot không reserved, acceptance không ăn thêm quota. |
| Cutoff            | Ngày gốc sớm hơn/ngày mới sớm hơn/cùng ngày, offset khác ngày UTC, `now == expires_at`, lock wait qua cutoff, config timezone thay sau tạo không đổi saved expiry.                                                                 |
| Failure injection | Lỗi sau flush superseded, sau insert, sau audit, giữa hai notification, trước commit, sau commit mất response, commit transport failure và cancellation; counter/records rollback đồng bộ hoặc phục hồi đúng receipt.              |
| Expiry helper     | Gọi lặp, not due, terminal/missing/inconsistent pair, inactive participants, system actors, cạnh tranh với student/cancel/replace; caller commit/rollback sở hữu toàn bộ bundle.                                                   |
| Migration         | Empty schema upgrade, upgrade có history/pending legacy, nullable unique, partial metadata CHECK bị chặn, counter/version uniqueness, guarded downgrade, models khớp Alembic.                                                      |
| Tích hợp          | Alembic-initialized MySQL 8.4 thật; races với booking/#21/#22; required checks không silently skip khi thiếu MySQL.                                                                                                                |

Nghiệm thu gồm Black, Ruff, Pyright, toàn bộ backend suite, migration checks và deterministic OpenAPI regeneration. Fixture `mysql_engine` giữ chế độ skip tùy chọn cho developer không có MySQL; pipeline bắt buộc truyền `--require-mysql` để fail rõ ràng khi thiếu `MYSQL_TEST_URL`, đồng thời kiểm tra không có test skip trong JUnit.

Các file triển khai: model appointment/proposal và Alembic revision mới; `domain/change_proposals.py` (canonicalization); schemas/repository/service proposal; router năm endpoint và đăng ký router; shared expiry helper; OpenAPI; tests và tài liệu kiến trúc. Giữ nguyên `core/transactions.py` làm cơ chế commit/rollback chung.

**Bằng chứng triển khai:** xem mục 12 bên dưới. Nhánh hiện tại là working tree chưa có reviewed commit/PR; không coi base SHA là bằng chứng review implementation.

## 12. Chạy và kiểm chứng implementation

Chạy migration trước khi phục vụ các endpoint mới:

```text
python -m alembic upgrade head
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
python -m alembic check
ACCEPTED_SCHEMA_REVISIONS=20261019_1200_proposal_replay
```

API mới cần revision này; không cấu hình readiness chấp nhận revision cũ thiếu cột replay. Đồng bộ cấu hình cho API/worker và chỉ phát hành feature khi worker companion đã được triển khai. `.env.example` và harness frontend E2E đã cập nhật head mới.

Các lệnh nghiệm thu dùng Python trong `.venv` với `MYSQL_TEST_URL` trỏ tới MySQL 8.4 riêng. Test fixture tạo schema ngẫu nhiên qua Alembic cho từng test rồi dọn đúng schema đó; không dùng schema ứng dụng thật.

```text
python -m black --check src/backend tests/backend tools/scripts/export_openapi.py
python -m ruff check src/backend tests/backend tools/scripts/export_openapi.py
python -m pyright
python -m pytest tests/backend -q --require-mysql --junitxml=build/issue25-backend-tests.xml
python tools/scripts/export_openapi.py
```

`--require-mysql` làm test fail rõ ràng nếu thiếu `MYSQL_TEST_URL`; CI backend cũng dùng tùy chọn này và tiếp tục kiểm tra không có test skip. Trên Windows, có thể cần chạy ngoài sandbox để tạo asyncio loop và kết nối MySQL; Black có thể dùng `--workers 1`.

Môi trường xác minh: Windows, Python 3.12.14, MySQL 8.4.11/InnoDB riêng trên localhost. CI tiếp tục chạy Python 3.11 và MySQL 8.4.11. Kết quả:

| Kiểm tra           | Kết quả                                                                                                                                         |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Black              | 149 file, không cần format lại.                                                                                                                 |
| Ruff               | Pass toàn bộ backend và tests.                                                                                                                  |
| Pyright            | 0 errors, 0 warnings.                                                                                                                           |
| Migration smoke    | Upgrade từ schema rỗng, đúng một head, upgrade lần hai no-op.                                                                                   |
| `alembic check`    | Không có schema/model drift (`No new upgrade operations detected`).                                                                             |
| OpenAPI            | Hai lần export cùng SHA-256 `0423ad48c798b5f3ad9751d951bc691337f01fa73bc9ee7967436c23bdedff4a`; không thay đổi semantic của endpoint/schema cũ. |
| Backend suite cuối | **814 passed, 0 failed, 0 errors, 0 skipped** trên MySQL 8.4.11, 792,16 giây; JUnit `build/issue25-backend-tests.xml` đã đối chiếu số lượng.    |

Ba kiểm tra revision/readiness cũ đã chuyển sang `ScriptDirectory.get_current_head()` thay vì gắn cứng revision hồ sơ. Bộ riêng gồm 45 ca safety/boundary/migration và bảy ca xác nhận sửa revision + cạnh tranh tạo/thay đã pass trước lượt full suite cuối.

#22 chưa có endpoints trong base; kiểm tra tương thích hiện tại gồm revalidation dữ liệu acceptance/unavailability dưới account lock. Chưa triển khai entry point hoặc lifecycle worker trong issue này.
