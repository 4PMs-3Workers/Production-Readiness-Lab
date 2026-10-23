# Task 1 report: bounded safe telemetry runtime

Status: complete.

Base: `4a3cde87d40cb7004d99499468945aa65ab4cf21` on `feature/observability-foundation`.
Implementation commit: `253ce9f1854a2d416bf36c66c440f592572d39b2` (`feat(observability): add bounded telemetry runtime`).

## Implementation

- Added `TelemetryRuntime` and `create_telemetry(settings, *, service)`, with public `register_routes(routes: Iterable[str]) -> None`, `emit(record: Mapping[str, object]) -> bool`, `span(name: SpanName)`, and `close(deadline: float) -> Awaitable[None]` interfaces for Task 2.
- The finite span vocabulary is `http.server`, `booking.create`, `proposal.create`, `proposal.accept`, `proposal.decline`, `proposal.expire`, `db.query`, `db.commit`, `db.rollback`, `worker.scan`, and `worker.candidate`.
- Route registration accepts at most 128 unique ASCII templates of at most 128 characters, rejects query, fragment, and control characters, always allows `__unmatched__`, freezes before telemetry starts, and allows idempotent same-set registration.
- Added explicit record, SDK span, and SDK log sanitizers. Span events, links, status descriptions, arbitrary instrumentation scopes, unknown attributes, and arbitrary SDK resource environment attributes are discarded. Only an explicit service, environment, and release Resource is used.
- Wired actual OTLP HTTP log and trace exporters through independent daemon lanes with drop-newest admission, active-work capacity accounting, encoded item limits, expiry, capped batches, bounded owner-deadline shutdown, and finite loss accounting. Exporter exceptions and response-body diagnostics are contained.
- Added per-runtime Prometheus registries and bounded-label HTTP, database, and telemetry metrics; no global providers, registries, or root logging handlers are installed.
- Extracted the shared `Publisher` to `app.core.publishing`; `app.worker.publishing` forwards the previous public constants, `Record`, and `Publisher` while preserving the worker queue capacity behavior.
- Added bounded telemetry settings and endpoint validation, plus pinned OpenTelemetry API/SDK/OTLP HTTP 1.45.1, Prometheus client 0.26.0, and resolved transitive dependencies.

## Files

- `src/backend/app/observability/{__init__,runtime,records,exporting,metrics}.py`
- `src/backend/app/core/publishing.py`, `src/backend/app/worker/publishing.py`, `src/backend/app/core/config.py`
- `tests/backend/unit/observability/test_runtime.py`, `tests/backend/unit/test_config.py`
- `pyproject.toml`, `requirements.txt`

## TDD and verification

RED before implementation:

- `.venv\Scripts\python.exe -m pytest -q tests/backend/unit/observability/test_runtime.py` — 9 expected failures because the bounded telemetry runtime/export lane did not yet exist.
- `.venv\Scripts\python.exe -m pytest -q tests/backend/unit/observability/test_runtime.py::test_runtime_admission_counts_the_active_stdout_write` — failed as expected when a second record was admitted while the only-capacity stdout writer was blocked.

GREEN after implementation:

- `.venv\Scripts\python.exe -m pytest -q tests/backend/unit --junitxml=build/task-1-units.xml` — **571 passed, 3 warnings in 32.32s**. Output is preserved in `.superpowers/sdd/2026-10-23-observability-foundation/task-1-units.log`; JUnit XML is at `build/task-1-units.xml`. The warnings are existing Alembic `path_separator` deprecations.
- Ruff check on all changed Python files — passed.
- Black check on all changed Python files — 10 files unchanged.
- `.venv\Scripts\pyright.exe --project pyproject.toml` — 0 errors, 0 warnings, 0 informations.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements found.
- `git diff --cached --check` — passed before the implementation commit.

The full unit suite includes loopback OTLP log/trace wire-format checks, canary redaction for successful and failing OTLP responses, resource environment poisoning, blocked exporter/stdout shutdown behavior, and worker publisher compatibility.

## Self-review and concerns

- Changes are limited to Task 1 runtime/configuration/dependencies and their unit tests; no business behavior or deployment files were changed.
- Telemetry callbacks and exporter shutdown paths contain exceptions without logging raw payloads. Queueing/export work uses daemon-owned threads rather than the asyncio default executor.
- No remaining implementation concerns. The only test warnings are the 3 existing Alembic `path_separator` deprecations also present in the baseline.
