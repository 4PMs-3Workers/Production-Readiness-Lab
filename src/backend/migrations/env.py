"""Alembic migration environment for Production Readiness Lab.

Supports two execution modes:
- Online (CLI): reads DATABASE_URL from the environment, creates an async engine,
  and runs migrations inside asyncio.run() via connection.run_sync().
- Programmatic (test fixtures): the caller passes an active connection via
  config.attributes["connection"] so migrations run inside the test's own
  transaction or connection.

The DATABASE_URL environment variable must use the mysql+asyncmy scheme.
Alembic's synchronous migration API is wrapped via connection.run_sync() to
properly run in the async context without MissingGreenlet errors.
"""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

# Ensure src/backend is on sys.path so 'app' package is importable when running alembic.
_current_dir = Path(__file__).resolve().parent  # .../src/backend/migrations
_project_pkg_dir = _current_dir.parent  # .../src/backend
if str(_project_pkg_dir) not in sys.path:
    sys.path.insert(0, str(_project_pkg_dir))

# ruff: noqa: E402 — app imports must come after sys.path manipulation above
from alembic import context
from sqlalchemy import Connection, text
from sqlalchemy.engine import URL, make_url

from app.core.database import create_database_engine
from app.core.schema_lifecycle import lock_name, migration_connection

# Import the full model registry so that all 11 tables are registered on
# Base.metadata before autogenerate or upgrade inspects it.
from app.models import Base  # noqa: F401 — registers all ORM models

# Alembic Config object, which provides access to the values in alembic.ini.
config = context.config

# Configure Python logging from the alembic.ini [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object whose tables this environment manages.
target_metadata = Base.metadata


def _database_url() -> URL:
    """Read and validate DATABASE_URL from the environment.

    Raises RuntimeError if the variable is absent or uses the wrong driver.
    Never prints the URL or credentials to logs.
    """
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Export it before running alembic commands, for example:\n"
            "  export DATABASE_URL=mysql+asyncmy://user:password@host:3306/dbname"
        )
    url = make_url(raw)
    if url.drivername != "mysql+asyncmy":
        raise ValueError(
            "DATABASE_URL must use the mysql+asyncmy driver. "
            "Received drivername: [redacted for security]"
        )
    return url


def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without connecting to the database.

    Useful for generating reviewed SQL scripts before applying them.
    """
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations_with_connection(connection: Connection) -> None:
    """Run migrations using an already-open synchronous connection.

    This is called via connection.run_sync() to bridge async/sync contexts.
    """
    database = connection.execute(text("SELECT DATABASE()")).scalar_one()
    owns_guard = connection.execute(
        text("SELECT IS_USED_LOCK(:name) = CONNECTION_ID()"), {"name": lock_name(database)}
    ).scalar_one()
    if owns_guard != 1:
        raise RuntimeError("Online migrations require the schema lifecycle guard")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    """Run migrations against a live database using async engine.

    Creates an AsyncEngine using the configured create_database_engine() helper,
    which applies UTC timezone, strict SQL mode, and READ COMMITTED isolation.
    Runs migrations via connection.run_sync() to properly handle async context.
    """
    url = _database_url()
    connectable = create_database_engine(url)

    try:
        async with migration_connection(connectable) as connection:
            await connection.run_sync(_run_migrations_with_connection)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database.

    If config.attributes["connection"] is set, the caller has already opened a
    connection (e.g., a test fixture's async connection run via run_sync).  In
    that case we use that connection directly so the migration runs inside the
    caller's session/schema context. All online callers must use migration_connection
    to hold the schema lifecycle lock through the outer version-table commit.

    Otherwise we create a fresh async engine from DATABASE_URL and run it via
    asyncio.run() to properly handle the async context.
    """
    # --- Programmatic / test-fixture path ---
    provided_connection: Any = config.attributes.get("connection")
    if provided_connection is not None:
        _run_migrations_with_connection(provided_connection)
        return

    # --- CLI / standalone path (async) ---
    asyncio.run(run_migrations_online_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
