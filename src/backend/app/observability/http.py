"""Safe route resolution and request-local context for HTTP observations."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from secrets import token_hex
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.trace import Span
from starlette._utils import get_route_path
from starlette.routing import Match
from starlette.types import Scope

from app.observability.records import METHODS, UNMATCHED_ROUTE
from app.observability.runtime import TelemetryRuntime


def normalize_method(value: object) -> str:
    method = value.upper() if isinstance(value, str) else ""
    return method if method in METHODS else "OTHER"


def registered_routes(routes: Iterable[Any]) -> tuple[Any, ...]:
    """Expand FastAPI's lazy included routers into effective route templates."""
    result: list[Any] = []
    for route in routes:
        expand = getattr(route, "effective_route_contexts", None)
        if callable(expand):
            try:
                contexts = expand()
                if isinstance(contexts, Iterable):
                    result.extend(contexts)
            except Exception:
                continue
        else:
            result.append(route)
    return tuple(result)


def route_templates(routes: Iterable[Any]) -> tuple[str, ...]:
    """Return the app's unique finite templates, including lazy router prefixes."""
    return tuple(
        sorted(
            {
                path
                for route in registered_routes(routes)
                if isinstance((path := getattr(route, "path", None)), str)
            }
        )
    )


def resolve_route(scope: Scope, routes: Iterable[Any], *, redirect_slashes: bool) -> str:
    """Resolve a request to a registered template without retaining the client path."""
    route_list = registered_routes(routes)

    def matching_path(candidate: Scope) -> tuple[str | None, str | None]:
        partial: str | None = None
        for route in route_list:
            try:
                match, _child_scope = route.matches(candidate)
            except Exception:
                continue
            path = getattr(route, "path", None)
            if not isinstance(path, str):
                continue
            if match == Match.FULL:
                return path, partial
            if match == Match.PARTIAL and partial is None:
                partial = path
        return None, partial

    full, partial = matching_path(scope)
    if full is not None:
        return full
    if partial is not None:
        return partial

    route_path = get_route_path(scope)
    if redirect_slashes and route_path != "/":
        alternate = dict(scope)
        if route_path.endswith("/"):
            alternate["path"] = alternate["path"].rstrip("/")
        else:
            alternate["path"] += "/"
        for route in route_list:
            try:
                match, _child_scope = route.matches(alternate)
            except Exception:
                continue
            path = getattr(route, "path", None)
            if match != Match.NONE and isinstance(path, str):
                return path
    return UNMATCHED_ROUTE


@contextmanager
def server_span(telemetry: TelemetryRuntime | None) -> Iterator[Span | None]:
    """Start a root span with incoming and unrelated ambient context discarded."""
    token = otel_context.attach(otel_context.Context())
    manager = None
    span: Span | None = None
    try:
        if telemetry is not None:
            try:
                manager = telemetry.span("http.server")
                span = manager.__enter__()
            except Exception:
                manager = None
        try:
            yield span
        except BaseException as exc:
            if manager is not None:
                try:
                    manager.__exit__(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass
            raise
        else:
            if manager is not None:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    pass
    finally:
        try:
            otel_context.detach(token)
        except Exception:
            pass


def new_trace_id(span: Span | None) -> str:
    if span is not None:
        try:
            context = span.get_span_context()
            if context.is_valid:
                return f"{context.trace_id:032x}"
        except Exception:
            pass
    return token_hex(16)
