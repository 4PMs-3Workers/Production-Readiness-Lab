# Risk strategy and decision table (before patch)

R1: cross-owner disclosure (impact high). R2: mock masks wrong SQL column/UUID/time/joins
or session contract (high). R3: malformed CSV/formula/private fields (high).
R4: partial/oversized download and DB failures (high). R5: source duplication on retry
(high). R6: contention/latency and test nondeterminism (medium).

Unit: CSV formatting with public model instances, fixed timestamps, formula/quoting.
API harness: role denial and invalid queries using dependency overrides (fast, limited).
Integration/API: real MySQL 8.4, Alembic migrations, persisted sessions, owner predicate,
joins, BinaryUUID/UTCDateTime adapters, bounds, DB statement fault and recovery.
Critical workflow: HTTP login -> create -> cancel -> retry -> export with committed DB oracle.
Browser E2E: planned only if UI is added; not needed to verify this API-only slice.
No queue/cloud/export-job tests: those components are not introduced by this change.

## Decision table

T=true; F=false; -=irrelevant because an earlier decision denies processing.
S=authenticated role STUDENT; V=query valid (including unique keys); B=DB contract works;
N=matching owned row count. A=authenticated session. Rules evaluated by observable outcome.

| Condition/action | D1 | D2 | D3 | D4 | D5 | D6 | D7 |
|---|---|---|---|---|---|---|---|
| A | F | T | T | T | T | T | T |
| S | - | F | T | T | T | T | T |
| V | - | T | F | T | T | T | T |
| B | - | - | - | F | T | T | T |
| N | - | - | - | - | 0 | 1..1000 | >1000 |
| 401 | X | | | | | | |
| 403 | | X | | | | | |
| 422 | | | X | | | | |
| 503 sanitized | | | | X | | | |
| 200 header-only | | | | | X | | |
| 200 scoped CSV | | | | | | X | |
| 409 EXPORT_TOO_LARGE | | | | | | | X |
| Test IDs | TC02,14 | TC03,04 | TC06,07 | TC12 | TC09 | TC01,05,08,10,11 | TC10 |

Auth false + role true is infeasible. Downstream DB/count combinations after denial do
not affect expected output. D2 uses valid query to isolate role oracle; no assumption
about precedence between multiple simultaneous invalid inputs. Owner and status are
data dimensions in D6, tested with distinct known IDs and foreign canary fields.

## Cases

| ID | Requirement/risk | Level | Setup/input | Expected | Priority |
|---|---|---|---|---|---|
| TC01 | AC01/R2 | integration/API | real login, 3 owned rows | 200 CSV, fixed columns/headers | P0 |
| TC02 | AC02/R1 | API harness | no context | 401, no attachment | P0 |
| TC03 | AC02/R1 | API harness | STAFF | 403 | P0 |
| TC04 | AC02/R1 | API harness | ADMIN | 403 | P0 |
| TC05 | AC03/R1 | real DB | two students, foreign canary | only exact owned IDs | P0 |
| TC06 | AC03/R1 | API harness | caller student_id/page/page_size | 422 | P0 |
| TC07 | AC03/R2 | API harness | bad/repeated status | 422 VALIDATION_ERROR | P0 |
| TC08 | AC03/R2 | real DB/API | status=CANCELLED | exact known cancelled ID | P0 |
| TC09 | AC04/R4 | real DB/API | no owned history | header-only CSV | P1 |
| TC10 | AC04/R4 | real DB/API | 1000 then 1001 owned rows | 200 all, then 409 no partial CSV | P0 |
| TC11 | AC01/R2,R3 | real DB/API | inactive catalog, Unicode/quotes/newline | history retained, CSV parsed exactly | P1 |
| TC12 | AC04/R2 | real DB fault | fake rows pass, rewrite real SQL to missing column | 503 no SQL leak, next request recovers | P0 |
| TC13 | AC05/R5 | real HTTP workflow | create/cancel/retry same key | original receipt, one DB/CSV row | P0 |
| TC14 | AC02/R2 | real session boundary | actual no cookie | 401 | P0 |
| TC15 | AC01/R3 | unit | =,+,-,@ after whitespace; ordinary/empty/null | apostrophe prefix only hazardous text | P1 |
| TC16 | AC04/R4 | service harness | deterministic DB TimeoutError | sanitized 503 | P1 |

Setup creates unique schema per test, migrates it, and teardown drops only owned schema.
No production URL. Fixed semantic data/UUIDs; dynamic booking date uses DB clock in
existing booking_env. Expected IDs/columns declared independently of implementation.
Flaky review: test order/isolation, timezone/fixed-time formatting, event barriers and
repetition; do not hide failures with automatic retries.
