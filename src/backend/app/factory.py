"""FastAPI composition and application-owned database resources."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from secrets import token_hex
from time import perf_counter

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.datastructures import MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from app.api import api_v1_router
from app.api.booking_context import router as booking_context_router
from app.api.errors import AppError, error_response, install_error_handlers
from app.api.health import router as health_router
from app.api.metrics import metrics_response
from app.core.auth_http import AuthHttpMiddleware
from app.core.catalog_http import PrivateApiHttpMiddleware
from app.core.config import Settings, load_settings
from app.core.database import create_database_engine
from app.core.login_throttle import LoginThrottle
from app.core.passwords import PasswordPolicy, PasswordService
from app.core.request_context import RequestIdMiddleware, TrustedGatewayMiddleware
from app.observability.database import attach_database
from app.observability.http import (
    new_trace_id,
    normalize_method,
    resolve_route,
    route_templates,
    server_span,
)
from app.observability.runtime import TelemetryRuntime, create_telemetry

EngineFactory = Callable[..., AsyncEngine]
TelemetryFactory = Callable[..., TelemetryRuntime]
logger = logging.getLogger("app.errors")


def _noop() -> None:
    pass


class ResponseInterruptedError(RuntimeError):
    """Safe ASGI failure marker for a response that cannot be completed."""


class SanitizedFastAPI(FastAPI):
    """Keep exceptions already converted to HTTP 500 out of server tracebacks."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return

        started_at = perf_counter()
        state = scope.setdefault("state", {})
        route = resolve_route(
            scope,
            self.router.routes,
            redirect_slashes=self.router.redirect_slashes,
        )
        method = normalize_method(scope.get("method"))
        state["normalized_route"] = route
        state.pop("internal_outcome", None)
        telemetry = getattr(self.state, "telemetry", None)
        response_started = False
        response_complete = False
        response_status: int | None = None
        trace_id = ""

        async def track_response(message: Message) -> None:
            nonlocal response_started, response_complete, response_status
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-PRL-Route"] = route
                headers["X-Trace-ID"] = trace_id
                try:
                    del headers["X-PRL-Outcome"]
                except KeyError:
                    pass
                if state.get("internal_outcome") == "login_throttled":
                    headers["X-PRL-Outcome"] = "login_throttled"
            await send(message)
            if message["type"] == "http.response.start":
                response_started = True
                response_status = message["status"]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        with server_span(telemetry) as span:
            trace_id = new_trace_id(span)
            if span is not None:
                try:
                    span.set_attribute("http.route", route)
                    span.set_attribute("http.request.method", method)
                except Exception:
                    pass
            try:
                try:
                    await super().__call__(scope, receive, track_response)
                except Exception as exc:
                    logger.error("Unhandled application exception: %s", type(exc).__name__)
                    if response_started:
                        if not response_complete:
                            state.setdefault("error_code", "http_5xx")
                            raise ResponseInterruptedError(
                                "Application response interrupted"
                            ) from None
                    else:
                        state.setdefault("request_id", token_hex(16))
                        response = error_response(
                            Request(scope),
                            AppError(
                                500,
                                "INTERNAL_SERVER_ERROR",
                                "Internal server error",
                                "service",
                            ),
                        )
                        await response(scope, receive, track_response)
            finally:
                status = response_status if response_status is not None else 500
                duration_seconds = max(0.0, perf_counter() - started_at)
                if span is not None:
                    try:
                        span.set_attribute("http.response.status_code", status)
                    except Exception:
                        pass
                if telemetry is not None:
                    record: dict[str, object] = {
                        "event": "http.server.request",
                        "request_id": state.get("request_id"),
                        "trace_id": trace_id,
                        "route": route,
                        "method": method,
                        "status": status,
                        "duration_seconds": duration_seconds,
                        "response_complete": response_complete,
                    }
                    error_code = state.get("error_code")
                    if isinstance(error_code, str):
                        record["error_code"] = error_code
                    try:
                        telemetry.metrics.record_http_request(
                            route,
                            method,
                            status,
                            duration_seconds,
                            response_complete=response_complete,
                        )
                    except Exception:
                        pass
                    try:
                        telemetry.emit(record)
                    except Exception:
                        pass


def create_app(
    settings: Settings | None = None,
    *,
    engine_factory: EngineFactory = create_database_engine,
    telemetry_factory: TelemetryFactory | None = None,
) -> FastAPI:
    config = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = engine_factory(
            config.database_url,
            connect_args={"connect_timeout": config.db_connect_timeout_seconds},
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_timeout=config.db_pool_timeout_seconds,
        )
        app.state.engine = engine
        pending_readiness_tasks: set[asyncio.Task[None]] = set()
        app.state.pending_readiness_tasks = pending_readiness_tasks
        telemetry: TelemetryRuntime | None = None
        database_detach: Callable[[], None] = _noop
        try:
            app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
            app.state.passwords = await PasswordService.create(
                PasswordPolicy(), config.password_max_concurrency
            )
            if not any(getattr(route, "path", None) == "/metrics" for route in app.router.routes):
                app.add_api_route(
                    "/metrics",
                    metrics_response,
                    methods=["GET"],
                    include_in_schema=False,
                )
            templates = route_templates(app.router.routes)
            try:
                factory = telemetry_factory or create_telemetry
                telemetry = factory(config, service="api")
                telemetry.register_routes(templates)
                database_detach = attach_database(engine, telemetry)
            except Exception:
                if telemetry is not None:
                    try:
                        await telemetry.close(asyncio.get_running_loop().time() + 1.0)
                    except Exception:
                        pass
                telemetry = None
            app.state.telemetry = telemetry
            yield
        finally:
            try:
                try:
                    if pending_readiness_tasks:
                        await asyncio.gather(*pending_readiness_tasks, return_exceptions=True)
                finally:
                    try:
                        database_detach()
                    except Exception:
                        pass
                    try:
                        if telemetry is not None:
                            await telemetry.close(asyncio.get_running_loop().time() + 1.0)
                    except Exception:
                        pass
                    await engine.dispose()
            finally:
                if hasattr(app.state, "telemetry"):
                    del app.state.telemetry
                if hasattr(app.state, "session_factory"):
                    del app.state.session_factory
                if hasattr(app.state, "passwords"):
                    del app.state.passwords
                del app.state.pending_readiness_tasks
                del app.state.engine

    app = SanitizedFastAPI(lifespan=lifespan)
    app.state.settings = config
    app.state.login_throttle = LoginThrottle(config)
    install_error_handlers(app)
    app.add_middleware(AuthHttpMiddleware, settings=config, throttle=app.state.login_throttle)
    app.add_middleware(PrivateApiHttpMiddleware, settings=config)
    app.add_middleware(TrustedGatewayMiddleware, trusted_gateway_cidrs=config.trusted_gateway_cidrs)
    app.add_middleware(RequestIdMiddleware, trusted_gateway_cidrs=config.trusted_gateway_cidrs)
    app.include_router(health_router)
    app.include_router(api_v1_router)
    app.include_router(booking_context_router, prefix="/api/v1")
    return app
