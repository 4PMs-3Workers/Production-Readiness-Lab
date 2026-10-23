"""FastAPI composition and application-owned database resources."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from secrets import token_hex

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.types import Message, Receive, Scope, Send

from app.api import api_v1_router
from app.api.booking_context import router as booking_context_router
from app.api.errors import AppError, error_response, install_error_handlers
from app.api.health import router as health_router
from app.core.auth_http import AuthHttpMiddleware
from app.core.catalog_http import PrivateApiHttpMiddleware
from app.core.config import Settings, load_settings
from app.core.database import create_database_engine
from app.core.login_throttle import LoginThrottle
from app.core.passwords import PasswordPolicy, PasswordService
from app.core.request_context import RequestIdMiddleware, TrustedGatewayMiddleware

EngineFactory = Callable[..., AsyncEngine]
logger = logging.getLogger("app.errors")


class ResponseInterruptedError(RuntimeError):
    """Safe ASGI failure marker for a response that cannot be completed."""


class SanitizedFastAPI(FastAPI):
    """Keep exceptions already converted to HTTP 500 out of server tracebacks."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return

        response_started = False
        response_complete = False

        async def track_response(message: Message) -> None:
            nonlocal response_started, response_complete
            await send(message)
            if message["type"] == "http.response.start":
                response_started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        try:
            await super().__call__(scope, receive, track_response)
        except Exception as exc:
            logger.error("Unhandled application exception: %s", type(exc).__name__)
        else:
            return

        if response_started:
            if not response_complete:
                raise ResponseInterruptedError("Application response interrupted") from None
            return

        state = scope.setdefault("state", {})
        state.setdefault("request_id", token_hex(16))
        response = error_response(
            Request(scope),
            AppError(500, "INTERNAL_SERVER_ERROR", "Internal server error", "service"),
        )
        await response(scope, receive, send)


def create_app(
    settings: Settings | None = None, *, engine_factory: EngineFactory = create_database_engine
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
        try:
            app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
            app.state.passwords = await PasswordService.create(
                PasswordPolicy(), config.password_max_concurrency
            )
            yield
        finally:
            try:
                try:
                    if pending_readiness_tasks:
                        await asyncio.gather(*pending_readiness_tasks, return_exceptions=True)
                finally:
                    await engine.dispose()
            finally:
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
