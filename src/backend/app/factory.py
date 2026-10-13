"""FastAPI composition and application-owned database resources."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api import api_v1_router
from app.api.errors import install_error_handlers
from app.api.health import router as health_router
from app.core.config import Settings, load_settings
from app.core.database import create_database_engine
from app.core.request_context import RequestIdMiddleware

EngineFactory = Callable[..., AsyncEngine]


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
        try:
            app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
            yield
        finally:
            await engine.dispose()
            del app.state.session_factory
            del app.state.engine

    app = FastAPI(lifespan=lifespan)
    app.state.settings = config
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware, trusted_gateway_cidrs=config.trusted_gateway_cidrs)
    app.include_router(health_router)
    app.include_router(api_v1_router)
    return app
