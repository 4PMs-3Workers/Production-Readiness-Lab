# Frontend bootstrap screenshots — milestones 1 + 2

Captured on 2026-10-14 from the production build of the combined frontend
foundation and interactive mock booking implementation.
The Vietnamese application content is intentional. Every image is a full-page
capture; the dimensions below describe the browser viewport, so some images are
taller than the viewport.

| Route and state                                    | Desktop viewport: 1440 × 900                               | Mobile viewport: 390 × 844                               |
| -------------------------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------- |
| `/login` — login scaffold                          | [Login — desktop](login-desktop.png)                       | [Login — mobile](login-mobile.png)                       |
| `/booking` — step 1: consultation purpose          | [Purpose — desktop](booking-purpose-desktop.png)           | [Purpose — mobile](booking-purpose-mobile.png)           |
| `/booking` — step 2: adviser selection             | [Adviser — desktop](booking-staff-desktop.png)             | [Adviser — mobile](booking-staff-mobile.png)             |
| `/booking` — step 3: date and time selection       | [Date and time — desktop](booking-time-desktop.png)        | [Date and time — mobile](booking-time-mobile.png)        |
| `/booking` — step 4: booking review                | [Review — desktop](booking-review-desktop.png)             | [Review — mobile](booking-review-mobile.png)             |
| `/booking/result` — successful mock booking        | [Booking result — desktop](booking-result-desktop.png)     | [Booking result — mobile](booking-result-mobile.png)     |
| `/booking/result` — empty state in a fresh session | [Empty result — desktop](booking-result-empty-desktop.png) | [Empty result — mobile](booking-result-empty-mobile.png) |

The successful result is captured after returning through **Đặt lịch khác**,
**Đăng nhập**, and **Kết quả** to verify that navigation retains the latest mock
booking. A page refresh or a new tab starts a new in-memory demo session.
The booking pages disclose that the data is simulated and no real appointment
is created. Login remains a scaffold; booking is available without signing in.

This is the current screenshot set for the combined milestones 1 + 2 scope in
[issue #7](https://github.com/4PMs-3Workers/Production-Readiness-Lab/issues/7).
It replaces the earlier scaffold-only images. The historical screenshots remain
available at commit `c06b45d`; existing commit-pinned PR images still refer to that
historical version. See the [handoff](../../tasks/frontend-bootstrap.md) for the
current scope and verification results.
