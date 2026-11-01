# AI plan and review before implementation

Tool: Codex coding agent /root/implementation_plan. Prompt: read only baseline
aa8c5b0; propose small executable export slice, AC, files, real DB tests, fallback,
mandatory mutation without inventing a GET guard; do not code or manufacture mistakes.

## Original plan (preserved before implementation)

1. GET /api/v1/appointments/export; STUDENT, public AppointmentListItem fields, CSV.
2. Reuse require_student/get_db_session/reject_duplicate_catalog_query/read_transaction;
   static route before dynamic appointment_id.
3. Initially prefer AppointmentListQuery/read_student_appointment_page and loop pages.
   Root must decide full/page semantics, row limit and snapshot consistency.
4. Allowlist public fields, csv.writer; decide formula escaping.
5. API tests first; real MySQL migration and two-owner data; inactive catalog history.
6. Create -> replay -> export workflow; possibly replay after cancellation.
7. Green -> diff review -> actual AI review -> regenerate OpenAPI.
8. Temporarily remove replay branch in booking._resolve_replay; run workflow; restore.
9. Coverage/risk analysis; order/isolation/concurrency review; 80-task, 3-5 rounds,
   nearest-rank p95, raw measurements, ASGI vs production limitations.

Planned files: api/appointments.py, services/student_appointments.py, possibly
schemas/appointments.py, repositories/appointments.py, integration/unit export tests,
experiment tools, assignment docs, generated docs/api/openapi.yaml. booking.py temporary only.

Assumptions: STUDENT permission inherited from history; CSV a new contract; paging
may be slow/inconsistent; source replay mutation does not prove export idempotency;
without MySQL label doubles as harness only.

## Root review decisions

P1 - CHANGE: reject page-loop reuse. core/database.py uses READ COMMITTED;
repositories/appointments.py performs COUNT then OFFSET/LIMIT per page. Multiple
SELECTs in one transaction do not establish one repeatable snapshot at this isolation;
concurrent inserts can move offsets. AppointmentListQuery also exposes page/page_size.
Use separate status-only query, one owner-filtered SELECT LIMIT 1001, fail closed at
>1000 rows. A single SELECT gives one statement snapshot; no claim across requests.

P2 - CHANGE: resolve optional formula handling as required behavior before code.
models/user.py permits general name text; models/appointment.py location String(500)
and Purpose.name can contain spreadsheet formula prefixes. csv.writer handles CSV
syntax, not formula interpretation. Add tested apostrophe prefix for =,+,-,@ after
leading whitespace; preserve UTF-8, commas, quotes and line breaks with csv.writer.
No claim of universal spreadsheet-client security.

P3 - REFINE: put export service/formatting in services/appointment_export.py rather
than enlarge student_appointments.py; keep route thin and public fields fixed.

P4 - ACCEPT WITH LIMIT: mutate the actual booking replay guard. It is not an export
guard. Use source create/retry/export assertion and explicitly scope the conclusion.

P5 - ACCEPT: do not add browser UI/E2E for a backend-only slice. Use real login and
DB HTTP workflow. Static checks and regression suite must supplement new tests.

Review performed by AI against repository; human plan/diff/merge review is not claimed.
