"""The internal metrics endpoint works against the real MySQL app lifecycle."""

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import event

from app.factory import create_app
from app.observability.runtime import TelemetryRuntime


@pytest.mark.asyncio
async def test_internal_metrics_needs_no_auth_csrf_or_database(auth_settings, mysql_engine):
    statements: list[str] = []

    def listener(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(mysql_engine.sync_engine, "before_cursor_execute", listener)

    def telemetry_factory(settings, *, service):
        return TelemetryRuntime(settings, service, stdout_sink=lambda _line: None)

    app = create_app(
        auth_settings,
        engine_factory=lambda *args, **kwargs: mysql_engine,
        telemetry_factory=telemetry_factory,
    )
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
            ) as client:
                response = await client.get("/metrics")
    finally:
        event.remove(mysql_engine.sync_engine, "before_cursor_execute", listener)

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "prl_http_requests_total" in response.text
    assert statements == []


@pytest.mark.asyncio
async def test_readiness_stays_available_when_otlp_exporter_is_down(auth_settings, mysql_engine):
    current_revision = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert current_revision is not None
    settings = auth_settings.model_copy(
        update={
            "telemetry_enabled": True,
            "telemetry_otlp_endpoint": "http://127.0.0.1:1",
            "accepted_schema_revisions_raw": current_revision,
        }
    )

    app = create_app(settings, engine_factory=lambda *args, **kwargs: mysql_engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
        ) as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
