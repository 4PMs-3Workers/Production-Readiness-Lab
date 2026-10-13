"""The app owns its engine; a request owns its session, not its commit."""

from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import create_database_engine
from app.dependencies.database import get_db_session
from app.factory import create_app


def settings() -> Settings:
    return Settings.model_validate(
        {
            "DATABASE_URL": "mysql+asyncmy://user:password@127.0.0.1:1/prl",
            "APPLICATION_ORIGIN": "http://localhost:3000",
            "COOKIE_SECRET": "cookie-secret",
            "CSRF_SECRET": "csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
        }
    )


def test_lifespan_disposes_engine_without_database_connecting():
    engine = create_database_engine(settings().database_url)
    original_pool = engine.sync_engine.pool
    app = create_app(settings(), engine_factory=lambda *args, **kwargs: engine)

    with TestClient(app) as client:
        assert client.get("/missing").status_code == 404
        assert app.state.engine is engine

    assert engine.sync_engine.pool is not original_pool


@pytest.mark.asyncio
async def test_session_dependency_never_commits_and_rolls_back(mysql_engine):
    app = create_app(settings(), engine_factory=lambda *args, **kwargs: mysql_engine)

    @app.post("/__test/session", include_in_schema=False)
    async def session_route(session: Annotated[AsyncSession, Depends(get_db_session)]):
        await session.execute(
            text(
                "INSERT INTO service_settings "
                "(id, timezone, start_step_minutes, booking_horizon_days, updated_at) "
                "VALUES (1, 'Asia/Ho_Chi_Minh', 15, 7, UTC_TIMESTAMP(6))"
            )
        )
        assert session.in_transaction()
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post("/__test/session")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
    async with mysql_engine.connect() as connection:
        assert await connection.scalar(text("SELECT COUNT(*) FROM service_settings")) == 0


@pytest.mark.asyncio
async def test_session_dependency_rolls_back_after_exception(mysql_engine):
    app = create_app(settings(), engine_factory=lambda *args, **kwargs: mysql_engine)

    @app.post("/__test/session-error", include_in_schema=False)
    async def session_route(session: Annotated[AsyncSession, Depends(get_db_session)]):
        await session.execute(
            text(
                "INSERT INTO service_settings "
                "(id, timezone, start_step_minutes, booking_horizon_days, updated_at) "
                "VALUES (1, 'Asia/Ho_Chi_Minh', 15, 7, UTC_TIMESTAMP(6))"
            )
        )
        raise RuntimeError("failure before commit")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/__test/session-error").status_code == 500
    async with mysql_engine.connect() as connection:
        assert await connection.scalar(text("SELECT COUNT(*) FROM service_settings")) == 0
