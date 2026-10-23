"""Timing-only SQLAlchemy listeners and finite database failure classification."""

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from time import perf_counter
from typing import Protocol, cast

from sqlalchemy import event
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.exc import DBAPIError, DisconnectionError, SQLAlchemyError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.observability.runtime import TelemetryRuntime
from app.observability.tracing import safe_span

_FAILURE_MARKER = "_prl_database_failure_recorded"
_CONNECTION_ERROR_CODES = frozenset({2002, 2003, 2006, 2013, 2055})
_STATEMENT_OPERATIONS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


class _SpanLike(Protocol):
    def set_attribute(self, key: str, value: object) -> None: ...


def _operation(statement: str) -> str:
    """Reduce SQL text to a finite operation without retaining or exporting it."""
    try:
        token = statement.lstrip().split(None, 1)[0].rstrip(";,").upper()
    except (AttributeError, IndexError):
        return "OTHER"
    return token if token in _STATEMENT_OPERATIONS else "OTHER"


def _error_chain(error: BaseException) -> tuple[BaseException, ...]:
    values: list[BaseException] = []
    pending: list[BaseException] = [error]
    while pending and len(values) < 8:
        current = pending.pop()
        if current in values:
            continue
        values.append(current)
        for nested in (
            getattr(current, "orig", None),
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return tuple(values)


def _error_code(error: BaseException) -> int | None:
    for current in _error_chain(error):
        number = getattr(current, "errno", None)
        if isinstance(number, int) and not isinstance(number, bool):
            return number
        args = getattr(current, "args", ())
        if args and isinstance(args[0], int) and not isinstance(args[0], bool):
            return args[0]
    return None


def _failure_reason(error: BaseException, *, kind: str = "statement") -> str:
    if any(isinstance(current, TimeoutError) for current in _error_chain(error)):
        return "pool_timeout"
    code = _error_code(error)
    if code == 1205:
        return "lock_timeout"
    if code == 1213:
        return "deadlock"
    if code in _CONNECTION_ERROR_CODES or any(
        isinstance(current, DisconnectionError)
        or (isinstance(current, DBAPIError) and current.connection_invalidated)
        for current in _error_chain(error)
    ):
        return "connection_error"
    if kind == "transaction":
        return "transaction_error"
    if isinstance(error, SQLAlchemyError) or any(
        isinstance(current, SQLAlchemyError) for current in _error_chain(error)
    ):
        return "statement_error"
    return "unknown"


def record_database_failure(
    telemetry: TelemetryRuntime | None,
    error: BaseException,
    *,
    kind: str = "statement",
) -> None:
    """Record typed failures once per telemetry owner across listener and service layers."""
    if telemetry is None:
        return
    try:
        chain = _error_chain(error)
        owner_id = id(telemetry)
        for current in chain:
            recorded_owners = getattr(current, _FAILURE_MARKER, ())
            if isinstance(recorded_owners, (tuple, set, frozenset)) and owner_id in recorded_owners:
                return
        for current in chain:
            try:
                recorded_owners = getattr(current, _FAILURE_MARKER, ())
                if not isinstance(recorded_owners, (tuple, set, frozenset)):
                    recorded_owners = ()
                setattr(current, _FAILURE_MARKER, tuple(set(recorded_owners) | {owner_id}))
            except BaseException:
                pass
        telemetry.metrics.record_db_failure(_failure_reason(error, kind=kind))
    except BaseException:
        pass


def attach_database(engine: AsyncEngine, telemetry: TelemetryRuntime) -> Callable[[], None]:
    """Attach safe query timing and pool observations to one owner-held engine."""
    sync_engine = engine.sync_engine
    pool = sync_engine.pool
    state_lock = threading.Lock()
    detach_lock = threading.Lock()
    pending_lock = threading.Lock()
    pending_spans: dict[int, tuple[AbstractContextManager[object | None] | None, float, str]] = {}
    attached = True

    def pool_count(method_name: str) -> int | None:
        method = getattr(pool, method_name, None)
        if not callable(method):
            return None
        try:
            value = method()
        except BaseException:
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
        return None

    initial_checked_out = pool_count("checkedout") or 0
    initial_idle = pool_count("checkedin") or 0
    pool_state = {
        "checked_out": initial_checked_out,
        "physical": initial_checked_out + initial_idle,
    }

    def update_pool_metrics() -> None:
        with state_lock:
            checked_out = max(0, pool_state["checked_out"])
            physical = max(checked_out, pool_state["physical"])
        size = pool_count("size") or 0
        overflow_count = pool_count("overflow")
        if overflow_count is not None:
            overflow = overflow_count
        elif size > 0:
            overflow = max(0, physical - size)
        else:
            overflow = 0
        try:
            telemetry.metrics.db_pool_checked_out.set(checked_out)
            telemetry.metrics.db_pool_idle.set(max(0, physical - checked_out))
            telemetry.metrics.db_pool_overflow.set(overflow)
        except BaseException:
            pass

    def safe_attribute(span: object | None, key: str, value: object) -> None:
        if span is None:
            return
        try:
            cast(_SpanLike, span).set_attribute(key, value)
        except BaseException:
            pass

    def before_cursor_execute(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        del cursor, parameters, executemany
        started = perf_counter()
        operation = _operation(statement)
        manager: AbstractContextManager[object | None] | None = safe_span(
            telemetry.span, "db.query"
        )
        try:
            span = manager.__enter__()
        except BaseException:
            span = None
            manager = None
        safe_attribute(span, "db.system.name", "mysql")
        if operation in _STATEMENT_OPERATIONS:
            safe_attribute(span, "db.operation.name", operation)
        key = id(context) if context is not None else id(connection)
        with pending_lock:
            pending_spans[key] = (manager, started, operation)

    def finish_statement(
        connection: Connection,
        context: ExecutionContext | None,
        error: BaseException | None = None,
    ) -> None:
        key = id(context) if context is not None else id(connection)
        with pending_lock:
            pending = pending_spans.pop(key, None)
        if pending is None:
            return
        manager, started, operation = pending
        try:
            telemetry.metrics.record_db_statement(operation, max(0.0, perf_counter() - started))
        except BaseException:
            pass
        if error is not None:
            record_database_failure(telemetry, error)
        if manager is not None:
            try:
                manager.__exit__(type(error) if error is not None else None, error, None)
            except BaseException:
                pass

    def after_cursor_execute(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        del cursor, statement, parameters, executemany
        finish_statement(connection, context)

    def handle_error(exception_context) -> None:
        error = exception_context.sqlalchemy_exception or exception_context.original_exception
        context = exception_context.execution_context
        connection = exception_context.connection
        if connection is not None:
            finish_statement(connection, context, error)
        else:
            record_database_failure(telemetry, error)

    def on_connect(dbapi_connection: object, connection_record: object) -> None:
        del dbapi_connection, connection_record
        with state_lock:
            pool_state["physical"] += 1
        update_pool_metrics()

    def on_close(dbapi_connection: object, connection_record: object) -> None:
        del dbapi_connection, connection_record
        with state_lock:
            pool_state["physical"] = max(0, pool_state["physical"] - 1)
        update_pool_metrics()

    def on_checkout(
        dbapi_connection: object, connection_record: object, connection_proxy: object
    ) -> None:
        del dbapi_connection, connection_record, connection_proxy
        with state_lock:
            pool_state["checked_out"] += 1
        update_pool_metrics()

    def on_checkin(dbapi_connection: object, connection_record: object) -> None:
        del dbapi_connection, connection_record
        with state_lock:
            pool_state["checked_out"] = max(0, pool_state["checked_out"] - 1)
        update_pool_metrics()

    handlers: tuple[tuple[object, str, Callable[..., object]], ...] = (
        (sync_engine, "before_cursor_execute", before_cursor_execute),
        (sync_engine, "after_cursor_execute", after_cursor_execute),
        (sync_engine, "handle_error", handle_error),
        (pool, "connect", on_connect),
        (pool, "close", on_close),
        (pool, "checkout", on_checkout),
        (pool, "checkin", on_checkin),
    )
    installed: list[tuple[object, str, Callable[..., object]]] = []
    try:
        for target, name, callback in handlers:
            event.listen(target, name, callback)
            installed.append((target, name, callback))
        update_pool_metrics()
    except BaseException:
        for target, name, callback in reversed(installed):
            try:
                event.remove(target, name, callback)
            except BaseException:
                pass
        return lambda: None

    def detach() -> None:
        nonlocal attached
        with detach_lock:
            if not attached:
                return
            attached = False
        for target, name, callback in reversed(installed):
            try:
                event.remove(target, name, callback)
            except BaseException:
                pass

    return detach
