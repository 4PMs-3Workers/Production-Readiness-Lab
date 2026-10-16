import asyncio
from time import monotonic
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import Settings
from app.core.database import create_database_engine
from app.factory import create_app


def settings(accepted="a1b2c3d4e5f6", timeout=0.2):
    return Settings.model_validate(
        {
            "DATABASE_URL": "mysql+asyncmy://user:password@127.0.0.1:1/prl",
            "APPLICATION_ORIGIN": "http://localhost:3000",
            "COOKIE_SECRET": "cookie-secret",
            "CSRF_SECRET": "csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": accepted,
            "READINESS_TIMEOUT_SECONDS": timeout,
        }
    )


class Result:
    def __init__(self, revisions):
        self.revisions = revisions

    def scalars(self):
        return self

    def all(self):
        return self.revisions


class Connection:
    def __init__(
        self,
        revisions=(),
        delay: float = 0,
        error=None,
        revision_delay: float | None = None,
        exit_delay: float = 0,
        scalar_delay: float | None = None,
    ):
        self.revisions = revisions
        self.delay = delay
        self.error = error
        self.revision_delay = revision_delay
        self.exit_delay = exit_delay
        self.scalar_delay = scalar_delay
        self.closed = False
        self.queries = []

    async def __aenter__(self):
        await asyncio.sleep(self.delay)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await asyncio.sleep(self.exit_delay)
        self.closed = True

    async def scalar(self, query):
        self.queries.append(str(query))
        await asyncio.sleep(self.delay if self.scalar_delay is None else self.scalar_delay)
        if self.error:
            raise self.error
        return 1

    async def execute(self, query):
        self.queries.append(str(query))
        await asyncio.sleep(self.delay if self.revision_delay is None else self.revision_delay)
        if self.error:
            raise self.error
        return Result(self.revisions)


class Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_liveness_never_queries_database_when_database_is_down():
    app = create_app(settings())
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        app.state.engine = Engine(Connection(error=ConnectionError("database down")))
        result = client.get("/health/live")
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert app.state.settings.readiness_timeout_seconds == 0.2


@pytest.mark.parametrize(
    ("revisions", "accepted", "status"),
    [
        (["a1b2c3d4e5f6"], "a1b2c3d4e5f6", 200),
        (["compatible2"], "a1b2c3d4e5f6,compatible2", 200),
        (["unknown"], "a1b2c3d4e5f6", 503),
        ([], "a1b2c3d4e5f6", 503),
        (["a1b2c3d4e5f6", "compatible2"], "a1b2c3d4e5f6,compatible2", 503),
    ],
)
def test_readiness_requires_exactly_one_accepted_revision(revisions, accepted, status):
    app = create_app(settings(accepted))
    connection = Connection(revisions)
    with TestClient(app) as client:
        app.state.engine = Engine(connection)
        result = client.get("/health/ready")
    assert result.status_code == status
    assert result.headers["Cache-Control"] == "no-store"
    assert connection.closed
    assert connection.queries == ["SELECT 1", "SELECT version_num FROM alembic_version"]
    if status == 503:
        assert result.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert result.headers["X-Error-Class"] == "service"


@pytest.mark.parametrize("delay", [0.25, 0.11])
def test_readiness_timeout_covers_connection_and_queries(delay):
    app = create_app(settings(timeout=0.2))
    connection = Connection(["a1b2c3d4e5f6"], delay=delay)
    started = monotonic()
    with TestClient(app) as client:
        app.state.engine = Engine(connection)
        result = client.get("/health/ready")
    elapsed = monotonic() - started
    assert result.status_code == 503
    assert elapsed < 0.7  # 0.2-second deadline + 0.5 seconds scheduling/test-client tolerance.
    assert connection.closed or not connection.queries  # No connection was acquired yet.


def test_readiness_timeout_covers_revision_query():
    app = create_app(settings(timeout=0.2))
    connection = Connection(["a1b2c3d4e5f6"], revision_delay=0.25)
    with TestClient(app) as client:
        app.state.engine = Engine(connection)
        started = monotonic()
        result = client.get("/health/ready")
        elapsed = monotonic() - started
    assert result.status_code == 503
    assert elapsed < 0.7
    assert connection.closed
    assert connection.queries == ["SELECT 1", "SELECT version_num FROM alembic_version"]


def test_readiness_returns_within_deadline_when_connection_cleanup_is_slow():
    app = create_app(settings(timeout=0.05))
    connection = Connection(["a1b2c3d4e5f6"], scalar_delay=0.1, exit_delay=0.3)
    with TestClient(app) as client:
        app.state.engine = Engine(connection)
        started = monotonic()
        result = client.get("/health/ready")
        elapsed = monotonic() - started
        assert result.status_code == 503
        assert elapsed < 0.2  # 0.05-second budget plus scheduling and test-client tolerance.
        assert connection.queries == ["SELECT 1"]
    assert connection.closed  # Shutdown waits for deferred cleanup to finish.


def test_shutdown_waits_for_shielded_sqlalchemy_connection_close(monkeypatch):
    events = []

    class ShieldedConnection(Connection):
        async def __aexit__(self, exc_type, exc_value, traceback):
            await AsyncConnection.__aexit__(
                cast(AsyncConnection, self), exc_type, exc_value, traceback
            )

        async def close(self):
            events.append("close_started")
            await asyncio.sleep(0.3)
            events.append("close_finished")

    connection = ShieldedConnection(["a1b2c3d4e5f6"])
    engine = create_database_engine(settings().database_url)
    original_dispose = AsyncEngine.dispose

    async def recorded_dispose(self):
        events.append("engine_disposed")
        await original_dispose(self)

    monkeypatch.setattr(AsyncEngine, "connect", lambda self: connection)
    monkeypatch.setattr(AsyncEngine, "dispose", recorded_dispose)
    app = create_app(settings(timeout=0.05), engine_factory=lambda *args, **kwargs: engine)
    with TestClient(app) as client:
        started = monotonic()
        result = client.get("/health/ready")
        elapsed = monotonic() - started
        assert result.status_code == 503
        assert elapsed < 0.2  # 0.05-second budget plus scheduling and test-client tolerance.
        assert "close_started" in events
    assert events.index("close_finished") < events.index("engine_disposed")


def test_readiness_sanitizes_query_failure_and_closes_connection():
    app = create_app(settings())
    connection = Connection(error=RuntimeError("SELECT secret_password FROM users"))
    with TestClient(app) as client:
        app.state.engine = Engine(connection)
        result = client.get("/health/ready")
    assert result.status_code == 503
    assert "secret_password" not in result.text
    assert result.headers["Cache-Control"] == "no-store"
    assert connection.closed


@pytest.mark.asyncio
async def test_readiness_on_migrated_mysql_schema(mysql_engine):
    current_revision = "20261016_1200_audit_reqid128"
    app = create_app(
        settings(accepted=current_revision),
        engine_factory=lambda *args, **kwargs: mysql_engine,
    )
    async with mysql_engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == current_revision
    with TestClient(app) as client:
        result = client.get("/health/ready")
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_readiness_rejects_missing_alembic_version_table(mysql_engine):
    async with mysql_engine.begin() as connection:
        await connection.execute(text("DROP TABLE alembic_version"))
    app = create_app(settings(), engine_factory=lambda *args, **kwargs: mysql_engine)
    with TestClient(app) as client:
        result = client.get("/health/ready")
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_readiness_rejects_multiple_installed_revisions(mysql_engine):
    async with mysql_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('compatible2')")
        )
    app = create_app(
        settings("a1b2c3d4e5f6,compatible2"),
        engine_factory=lambda *args, **kwargs: mysql_engine,
    )
    with TestClient(app) as client:
        result = client.get("/health/ready")
    assert result.status_code == 503


@pytest.mark.asyncio
async def test_readiness_accepts_other_explicitly_compatible_revision(mysql_engine):
    async with mysql_engine.begin() as connection:
        await connection.execute(text("UPDATE alembic_version SET version_num = 'compatible2'"))
    app = create_app(
        settings("a1b2c3d4e5f6,compatible2"),
        engine_factory=lambda *args, **kwargs: mysql_engine,
    )
    with TestClient(app) as client:
        result = client.get("/health/ready")
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_readiness_deadline_includes_exhausted_pool(mysql_engine):
    engine = create_database_engine(mysql_engine.url, pool_size=1, max_overflow=0, pool_timeout=10)
    connection = await engine.connect()
    try:
        app = create_app(settings(timeout=0.2), engine_factory=lambda *args, **kwargs: engine)
        with TestClient(app) as client:
            started = monotonic()
            result = client.get("/health/ready")
            elapsed = monotonic() - started
        assert result.status_code == 503
        assert elapsed < 0.7
    finally:
        await connection.close()
        await engine.dispose()
