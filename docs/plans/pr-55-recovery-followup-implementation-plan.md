# Implementation plan: recovery và release gate của PR #55

Ngày lập: 25/11/2026, Asia/Bangkok. Trạng thái: **kế hoạch để xem trước khi triển khai**.

## 1. Căn cứ và phạm vi

- Review cần xử lý: [Duyhai2005, 24/11/2026](https://github.com/4PMs-3Workers/Production-Readiness-Lab/pull/55#issuecomment-7132980502).
- HEAD PR và checkout đã đối chiếu: `65238c00b6b43267d71e025742d7e89fc373a057`.
- Base `develop`: `aed6c68a4a081f558d01e9ba4c39cd031439af47`.
- Worktree dự kiến: `D:\SE\tmp\pr55-review-fixes`, nhánh `codex/pr55-review-fixes`.
- Hai predecessor đã đăng ký: account lifecycle từ `12a74f7308550ccc294a783edde79db267a526d1`, CSV từ `aed6c68a4a081f558d01e9ba4c39cd031439af47`.

Lượt lập plan đã đọc code, tests, workflows và review hiện tại. Chưa sửa production/test, chưa chạy test tái hiện hai regression mới. Vì vậy phần mô tả dưới đây phân biệt đường lỗi thấy trong code với kết quả runtime cần xác minh khi triển khai.

Mục tiêu gồm hai phần: sửa hai lỗi recovery P1 trước khi đề nghị merge, và xử lý NFR-03 đến khi có bằng chứng đủ điều kiện phát hành. CI chức năng xanh không chứng minh bài tải đã đạt.

PDF đã nộp giữ nguyên. Dùng commit mới để giữ permalink và lịch sử bằng chứng. Không rebase, force-push, merge PR hay xóa nhánh mutant trong công việc này. Log/JSON mới ghi vào `build/vt071/` hoặc artifact CI, không đưa lại evidence thô vào diff.

## 2. P1: CLI mới mất transition khi `deploy --resume`

### 2.1. Lỗi xảy ra thế nào

Các điểm liên quan:

- `tools/deploy/rollout.py`: `reviewed_transition()` chọn transition bằng `previous_host_sha256`; `controller()` truyền `state.configuration_sha256` vào hàm này.
- `tools/deploy/rollout_controller.py`: `_begin()` lưu host hash candidate trước migration; `_migration_revisions()` chỉ cho phép schema predecessor khi `self.transition` còn đúng và manifest stable khớp.
- `tools/deploy/rollout_store.py`: `manifest(selection)` đọc cache và kiểm tra manifest khớp đầy đủ với selection, gồm payload digest.

Chuỗi lỗi dự kiến:

1. Host đang chạy account-lifecycle predecessor, database còn `PREDECESSOR_SCHEMA`.
2. Deploy candidate xác minh preflight rồi `_begin(rebind=True)` ghi host hash candidate. Stable vẫn là release cũ; đây chưa phải promotion.
3. Tiến trình dừng trước khi schema upgrade hoàn tất.
4. CLI mới đọc host hash candidate và so với các hash predecessor. Không hash nào khớp, nên `transition=None`.
5. `_migration_revisions()` trả `[SCHEMA]` thay vì `[SCHEMA, PREDECESSOR_SCHEMA]`. Resume có thể bị từ chối dù database và stable vẫn hợp lệ.

Đây là mất ngữ cảnh recovery sau khi khởi tạo lại tiến trình. Test hiện có trong `test_rollout_schema_transition.py` resume bằng controller cũ sau migration, nên vẫn giữ transition trong RAM. Các test tạo lại controller ở `test_rollout_recovery_retry.py` chưa bao phủ chuyển cấu hình và schema này.

### 2.2. Hướng sửa được chọn

Khôi phục transition từ **manifest stable đã được xác minh**, kết hợp danh tính host và operation hiện tại. Tận dụng state/cache đang có để tránh thêm format state chỉ nhằm giữ một giá trị có thể suy ra an toàn.

1. Bổ sung bộ chọn transition nhận được state hiện tại và stable manifest đã kiểm tra bằng `store.manifest(state.stable)`.
2. So manifest với đúng predecessor configuration đã đăng ký. Không chọn account transition chỉ vì schema mang tên cũ.
3. Trước lần rebind đầu, vẫn yêu cầu host hash khớp predecessor chính xác, gồm overlay, observability và selections như HTTP port.
4. Sau rebind, chỉ khôi phục khi host hash đúng candidate hiện tại và stable manifest khớp một predecessor đã đăng ký. Schema cho phép đi theo transition đã nhận diện.
5. Khi stable đã là candidate/current, không lấy `previous_stable` để tiếp tục mở quyền nhận schema cũ.
6. Hỗ trợ cả resume của operation gián đoạn và deploy lại sau một lần rollback hợp lệ: rollback thành công có thể giữ host hash candidate nhưng stable đã trở về predecessor.
7. Manifest thiếu, bị sửa, selection không khớp hoặc host hash lạ phải bị từ chối theo lỗi hiện có. Không fallback sang transition đầu tiên khi thiếu bằng chứng.

Việc đọc state dùng cho quyết định cuối phải nằm trong `operation_lock`, cùng snapshot mà controller chuẩn bị thực thi. Dự kiến thêm callback resolver tùy chọn cho `RolloutController`, được CLI cung cấp và controller gọi sau khi load state trong khóa. Giữ đường truyền transition trực tiếp cho fixture/caller hiện có khi cần; quy định rõ ưu tiên, tránh hai nguồn lựa chọn mâu thuẫn. Test khóa và identity hiện tại phải tiếp tục đạt.

Giữ các ràng buộc đang có: resume đúng target/digest; candidate `previous_stable` khớp stable; registry/image verification; xác minh schema; không tiếp tục deploy khi đã quyết định automatic rollback; không promotion trước readiness và smoke.

Không sửa bằng cách luôn nhận cả hai schema hoặc trì hoãn ghi host hash mà chưa phân tích đầy đủ state machine. Khi triển khai, nếu phát hiện state/cache hiện tại không đủ phân biệt trường hợp hợp lệ, dừng ở kết quả tái hiện và cập nhật thiết kế trước khi đổi format state.

### 2.3. Test tái hiện và hồi quy

| Ca | Thiết lập và thao tác | Kết quả bắt buộc |
| --- | --- | --- |
| R1 | Account predecessor; crash sau rebind nhưng trước `migrate()`; bỏ controller cũ; mở lại store và tạo controller qua đường chọn của CLI | Resume nhận schema cũ, migrate và hoàn tất; stable/previous_stable đúng |
| R2 | Crash sau migration đã commit nhưng trước grants; mở CLI/controller mới | Resume nhận schema mới; chạy lại verification; không downgrade nếu không có lỗi |
| R3 | Như R2 nhưng apply grants thất bại | Recovery/rollback về predecessor theo contract; dữ liệu và identity được giữ |
| R4 | State đang gián đoạn nhưng thiếu `--resume`, đổi reference hoặc digest target | Từ chối, giữ recovery intent và stable |
| R5 | Host hash lạ, thay port/profile, cache thiếu hoặc stable manifest/digest bị sửa | Từ chối; không nới schema hay bắt đầu thay đổi dịch vụ |
| R6 | Stable đã là current nhưng previous_stable còn là account predecessor | Không tiếp tục nhận schema predecessor chỉ vì có lịch sử cũ |
| R7 | Host candidate đã rollback hợp lệ về predecessor rồi deploy lại | Chọn đúng transition và deploy được |
| R8 | Bộ chọn cho local, HTTPS, HTTPS + observability; cả account và CSV predecessor | Chọn đúng transition; CSV không được mở quyền nhận account schema |

Test R1 phải thất bại trên code `65238c0` tại đường chọn/preflight dự kiến, không được thất bại do fixture thiếu manifest. Chỉ test helper trả đúng transition chưa đủ: ít nhất một test phải gọi constructor/controller thật của CLI, với external effects được thay bằng fixture.

Linux rehearsal sẽ bổ sung một điểm gián đoạn có kiểm soát sau khi state rebind đã ghi nhưng database vẫn ở predecessor schema. Tạo lại controller/runtime từ state trên đĩa bằng cùng resolver của CLI rồi resume. Xác minh schema, readiness/smoke, image identity, DB volume và booking đã commit. Điểm gián đoạn chỉ nằm trong harness.

## 3. P1: rollback CSV bị áp restriction policy của account predecessor

### 3.1. Lỗi xảy ra thế nào

Trong `RolloutController._restore()`, điều kiện hiện tại là `prepared.manifest.configuration != current`. Mọi cấu hình predecessor đều đi qua `predecessor_policy_guard()`.

Guard này chạy checker `tools/deploy/rollout_policy.py` trong image predecessor. Checker đòi mọi ranh giới lịch lặp có đúng một local instant trong chu kỳ kiểm tra, nên từ chối ranh giới trong DST gap/fold. Guard còn dừng API writers và kiểm tra lại snapshot policy trước khi cho đi tiếp.

Restriction này cần được giữ cho transition account lifecycle theo hợp đồng rollback hiện có. Với CSV predecessor `aed6c68`, code availability của hai bản được reviewer xác nhận xử lý giống nhau. Cấu hình route-map khác nhau không đủ để suy ra phải áp restriction lịch cũ. Hệ quả là một lịch hợp lệ với cả hai bản vẫn có thể làm rollback trả `rollback-incompatible`.

Test Linux hiện tại có nhánh CSV rollback rồi `return` trước đoạn tạo policy tùy chỉnh, nên không đi qua lịch DST. Test unit của checker chỉ xác nhận nó từ chối lịch này; chưa kiểm tra controller có gọi checker đúng transition hay không.

### 3.2. Hướng sửa được chọn

1. Phân loại rollback target bằng **configuration predecessor chính xác** trong registry transition đã review.
2. Chỉ gọi `predecessor_policy_guard()` khi target là account-lifecycle predecessor cần restriction đó. CSV predecessor và current dùng đường rollback thông thường.
3. Quyết định guard dựa trên hợp đồng target, không chỉ dựa vào `needs_downgrade`: một rollback retry có thể đã downgrade schema nhưng vẫn phải giữ kiểm tra policy của target account.
4. Dùng một helper/metadata rõ nghĩa cho đặc tính này, gắn với account transition. Không dùng thứ tự vòng lặp, tên release hay một boolean mặc định cho phép mọi transition lạ.
5. `consumer.prepare(expected=target)` và `require_compatible()` tiếp tục từ chối configuration không đăng ký trước khi tới quyết định guard.
6. Giữ nguyên checker, recheck sau drain, soft-delete reversibility check, downgrade, image verification, smoke và preservation checks cho account rollback.

Phạm vi sửa chính nằm ở `_restore()` và helper nhận diện transition nếu cần. Không cần thay quy tắc availability của ứng dụng để khắc phục lỗi phân loại rollback này.

### 3.3. Test tái hiện và hồi quy

| Ca | Thiết lập và thao tác | Kết quả bắt buộc |
| --- | --- | --- |
| P1 | CSV predecessor; lịch America/New_York Chủ nhật 02:30–03:30 và 09:00–12:00 | Rollback thành công, giữ nguyên policy và booking |
| P2 | Account predecessor với cùng lịch DST | Vẫn từ chối đúng `rollback-incompatible`; không đổi stable/schema/dịch vụ |
| P3 | Account predecessor với policy tương thích | Guard chạy, giữ kiểm tra drain/recheck và cho rollback hợp lệ |
| P4 | Account predecessor với soft-delete chưa reversible | Vẫn từ chối downgrade, không mất dữ liệu |
| P5 | Account rollback retry khi schema đã ở predecessor | Không bỏ policy guard vì `needs_downgrade=False` |
| P6 | CSV automatic recovery khi candidate readiness thất bại và có lịch DST | Recovery không bị guard account chặn |
| P7 | Target config lạ hoặc manifest/digest bị sửa | Fail trước rollout; việc tách guard không mở quyền nhận target lạ |

Ở unit test, guard giả phải chủ động báo lỗi nếu bị gọi cho CSV, đồng thời có ca chứng minh account vẫn gọi guard. Ở Linux rehearsal, tạo policy qua API admin thật, dùng thời điểm cố định 13/03/2027 để hỏi slots ngày 14/03/2027 qua cả hai image và xác minh kết quả tương đương. Sau rollback kiểm tra policy vẫn bằng snapshot đã lưu, slots, DB identity, booking và selected images.

Tách setup policy chung khỏi đoạn return của CSV để CSV thực sự chạy lịch tùy chỉnh. Account vẫn giữ nhánh từ chối DST và nhánh sửa về policy tương thích rồi rollback. Không chỉ xóa early return rồi đổi tất cả assertion sang thành công.

## 4. File dự kiến thay đổi

| File | Nội dung dự kiến |
| --- | --- |
| `tools/deploy/rollout.py` | Chọn transition từ state và stable đã xác minh; wire resolver vào CLI |
| `tools/deploy/rollout_controller.py` | Gọi resolver trong operation lock; scope policy guard theo target transition |
| `tools/deploy/release_manifest.py` | Chỉ bổ sung helper/metadata dùng chung nếu cần; giữ registry/hash và opt-in hiện có |
| `tests/backend/unit/test_rollout_cli.py` | Fresh CLI/controller, selection theo stable và từ chối identity sai |
| `tests/backend/unit/test_rollout_schema_transition.py` | Crash trước/sau migration với controller mới; recovery và redeploy |
| `tests/backend/unit/test_rollout_recovery_retry.py` | Bổ sung khi cần kiểm chứng recovery intent qua lần khởi tạo mới |
| `tests/backend/unit/test_rollout_rollback.py` hoặc file transition chuyên biệt | Guard đúng account/CSV, manual và automatic recovery |
| `tests/rollout/test_configuration_transition.py` | Linux crash/resume thật và CSV rollback với policy DST |

Ưu tiên tái dùng fixtures hiện có và manifest dựng từ commit thật. Không thay historical assertion hoặc deadline chỉ để làm test mới đạt. `rollout_state.py`, `rollout_store.py` và format JSON dự kiến không cần đổi; nếu cần, phải bổ sung phân tích tương thích vào plan.

## 5. NFR-03: kế hoạch để đủ điều kiện phát hành

### 5.1. Trạng thái và điều chưa biết

Theo PR/review, lượt `e7392e3` có 341/560 HTTP 503, tất cả auth pool queue timeout; successful-request p95 0,729–0,909 s. Cấu hình pool 5+5, queue timeout 0,5 s. Đây vẫn là kết quả FAIL vì tiêu chí yêu cầu zero errors và p95 ≤ 2,5 s.

Số liệu chỉ định vị điểm timeout. Nó chưa chứng minh tăng pool, tăng timeout, giảm query hay sửa cleanup là cách khắc phục phù hợp. Lỗi recovery ở mục 2–3 không tự sửa NFR.

Các đường code cần đo: `services/auth.py::authenticate()` và `_transaction()`; `dependencies/database.py::get_db_session()`; `core/transactions.py::read_transaction()`; engine pool trong `factory.py`. Auth hiện có truy vấn session/user và lấy thời gian DB trong transaction. Cần đo từng phần trước khi quyết định có tối ưu query hay không.

### 5.2. Trình tự điều tra

1. Sau khi hai regression recovery đạt test, ghim candidate SHA và dependencies. Ghi phiên bản MySQL, số process/worker, CPU, pool settings, timeout và telemetry của đúng môi trường release.
2. Dùng schema/server test riêng. Chạy lại baseline với harness production pool hiện tại, giữ 80 request/burst và bảy phase. Lưu toàn bộ lượt, kể cả FAIL. Gọi phase đầu theo đúng đặc tính harness: đã migrations/seed/login/serial request, không tự coi là cold restart.
3. Bổ sung metrics có giới hạn trong harness: thời gian chờ checkout, thời gian giữ connection từ checkout đến checkin, SQL/commit/rollback, số checkout/transaction mỗi request, pool peak, invalidation và event-loop lag. Gắn correlation ID nội bộ, không ghi cookie/token/thông tin người dùng.
4. Phân biệt stage auth dùng `_transaction()` với export dùng `read_transaction()`. Timer bao scheduling và buffered execute phải được ghi rõ; không gọi tất cả thời gian execute là thời gian MySQL chạy SELECT.
5. Đo SQL/DB activity và host CPU cùng khoảng thời gian; so các burst và thời điểm checkin. Xác minh connection được trả sau lỗi/cancel, và profiling có làm thay đổi đáng kể kết quả hay không.
6. Nếu ASGI harness vẫn timeout, tái kiểm qua gateway/HTTP trên môi trường release tương ứng trước khi khẳng định tác động triển khai. Hai kiểu harness phải được ghi riêng.

### 5.3. Chọn hướng sửa theo bằng chứng

| Bằng chứng | Hướng xử lý có thể chọn | Điều kiện phải giữ |
| --- | --- | --- |
| Connection bị giữ qua phần việc không cần DB hoặc không trả sau cancel | Thu hẹp transaction/lifetime hoặc sửa cleanup | Không mất rollback, isolation, ownership hay release connection |
| Auth query/round-trip chiếm phần lớn hold time | Tối ưu query và plan/index sau khi đo | Giữ kiểm tra expiry, revoked session, active account và semantics thời gian DB |
| Cấu hình pool không đủ cho concurrency release đã cam kết | Đề xuất cấu hình theo budget worker/replica × pool và MySQL max_connections | Đo tài nguyên tổng, các workload khác, p95 và lỗi; nhóm review cấu hình |
| Host/event-loop hoặc driver là nút thắt | Tách client/server, đo môi trường phù hợp hoặc sửa nguyên nhân đã xác định | Giữ số đo cùng host cũ và giới hạn so sánh |

Mỗi thử nghiệm đổi một yếu tố và có baseline đối chiếu. Không cache bỏ qua revocation, không thay clock DB bằng clock máy mà chưa giữ contract, không biến 503 thành 429 hoặc bỏ request lỗi để đạt zero-error gate.

Nếu chọn tuning deployment, giữ bài test default 5+5 hiện tại để phản ánh default; thêm profile cấu hình release được khai báo rõ. Không sửa assertion mặc định của harness thành một giá trị lớn hơn rồi gọi là default production đã đạt. Mọi thay đổi timeout phải nằm trong whole-request budget và được giải thích bằng số đo.

### 5.4. Điều kiện đóng NFR-03

- Chạy đúng profile sẽ phát hành; ghi rõ khác biệt nếu default vẫn không đạt.
- Cả phase đầu và các warm gate đều zero errors và successful-request p95 ≤ 2,5 s; báo cả tổng requests, lỗi và latency, không chỉ p95.
- Chạy lặp tối thiểu ba lượt hoàn chỉnh theo protocol thống nhất, giữ kết quả mọi lượt; kiểm chứng qua HTTP/gateway trên topology dự kiến phát hành.
- Không vượt connection/resource budget và không làm hỏng auth/session/booking contracts.
- Có source/config/environment identity, command và evidence artifact cho reviewer. Nếu chưa đạt, release gate tiếp tục mở với lý do cụ thể.

NFR nên là một commit/PR riêng sau hai sửa recovery để nhóm đánh giá thay đổi runtime/config. Nếu nhóm quản lý bằng issue, nội dung mục này có thể chuyển sang issue liên kết PR #55; việc lập plan chưa tạo issue hay gửi tin cho lead.

## 6. Thứ tự triển khai và xác minh

1. Kiểm tra lại remote HEAD/base và worktree trước khi bắt đầu. Nếu base đổi, đánh giá delta trước khi tích hợp; giữ SHA đã nộp.
2. Viết regression R1 và P1 để tái hiện hai lỗi trên code chưa sửa. Ghi vị trí fail và state/schema thực tế, tránh fixture fail sai nguyên nhân.
3. Sửa recovery transition; chạy nhóm CLI/schema/recovery/identity/locking và các ca từ chối. Hoàn tất Linux crash/resume test.
4. Sửa guard rollback; chạy unit policy/runtime/controller, manual/automatic recovery và Linux DST rehearsal cho cả hai predecessor.
5. Chạy quality checks đúng workflow, rồi Backend models và Serialized release rollback trên cùng candidate cuối. Theo dõi các workflow bắt buộc khác; không đổi code tùy chọn trong lúc chờ CI.
6. Tiến hành baseline/diagnosis NFR theo mục 5, chọn fix có bằng chứng và chạy lại checks liên quan đến phạm vi runtime/config thay đổi.
7. Cập nhật mô tả PR với test/run link, SHA cuối, hai regression đã sửa và trạng thái NFR riêng. Ghi rõ phần release-tooling cần nhóm review. PDF giữ nguyên.

Các lệnh dự kiến dưới đây là hướng dẫn chạy sau khi triển khai, chưa phải kết quả đã chạy. Python/dependencies phải thuộc candidate; `MYSQL_TEST_URL` lấy từ môi trường test riêng.

```powershell
$pr55Python = 'D:\SE\.venv\Scripts\python.exe'
$env:PYTHONPATH = 'src/backend;tools/infra/offhost'

& $pr55Python -m pytest tests/backend/unit/test_rollout_cli.py tests/backend/unit/test_rollout_schema_transition.py tests/backend/unit/test_rollout_recovery_retry.py tests/backend/unit/test_rollout_interruption.py tests/backend/unit/test_rollout_rollback.py tests/backend/unit/test_rollout_runtime.py tests/backend/unit/test_rollout_policy.py tests/backend/unit/test_release_manifest.py -q

& $pr55Python -m pytest tests/backend/unit -k 'rollout or release' -q
& $pr55Python -m pyright --venvpath D:\SE

# Suite Backend đầy đủ cần MySQL test riêng; CI kiểm tra zero skipped qua JUnit.
& $pr55Python -m pytest tests/backend tests/load -m 'not load' -q --require-mysql --junitxml=build/backend-tests.xml

# Load opt-in: không gộp với kết quả CI chức năng.
$env:BT3_RUN_LOAD = '1'
try {
    & $pr55Python -m pytest 'tests/backend/integration/test_appointment_export_load_pool.py::test_export_pool_profile[production]' -q -s --require-mysql
} finally {
    Remove-Item Env:BT3_RUN_LOAD
}
```

Black/Ruff chạy trên các file thực sự thay đổi, rồi dùng full commands của workflow hiện tại. Linux rehearsal cần checkout sạch, full Git history, Linux, Docker, skopeo và oras; dùng job **Serialized release rollback** có `RUN_ROLLOUT_REHEARSAL=1`, không coi test không chạy được trên Windows là PASS. Job chạy toàn bộ `tests/rollout`, không skip ca cũ để thay bằng ca mới.

## 7. Commit và điều kiện bàn giao

Commit dự kiến:

1. Recovery transition từ stable đã xác minh + regression fresh CLI trước/sau migration.
2. Scope policy guard cho account transition + regression CSV DST và Linux rehearsal.
3. Khi đã có chẩn đoán: fix NFR runtime/config cùng tests/evidence tương ứng, được review riêng.

Hai commit đầu có thể cùng ở PR #55. Bằng chứng mới chỉ lưu artifact hoặc `build/`; mô tả PR giữ summary ngắn. Không đưa hàng chục nghìn dòng log trở lại diff.

Tiêu chí bàn giao phần recovery:

- [ ] Đã thấy hai regression fail trên code cũ vì đúng lỗi review.
- [ ] Resume bằng CLI/controller mới thành công từ schema cũ; ca sau migration cũng đạt.
- [ ] Sai target, digest, host identity, config hoặc cache vẫn bị từ chối.
- [ ] CSV rollback với lịch DST thành công; account rollback vẫn giữ restriction và soft-delete checks.
- [ ] Linux rehearsal xác minh dữ liệu đã commit, volume, schema, images và stable pointers.
- [ ] Backend/Serialized rollback và các workflow bắt buộc của HEAD cuối đạt, không có required skip.
- [ ] PR mô tả đúng trạng thái; PDF và permalink lịch sử giữ nguyên.

Tiêu chí phát hành còn bao gồm NFR-03 đạt mục 5.4 và review bắt buộc của nhóm. Hoàn tất hai lỗi recovery không đồng nghĩa hoàn tất release gate.
