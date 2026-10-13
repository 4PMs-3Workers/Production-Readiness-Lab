"""Alembic migration environment for Production Readiness Lab.

Supports two execution modes:
- Online (CLI): reads DATABASE_URL from the environment, creates a sync engine
  wrapper around the existing async engine, and runs migrations in-process.
- Programmatic (test fixtures): the caller passes an active connection via
  config.attributes["connection"] so migrations run inside the test's own
  transaction or connection.

The DATABASE_URL environment variable must use the mysql+asyncmy scheme.
Alembic's synchronous API cannot drive asyncmy directly; we hand it a
synchronous proxy via AsyncEngine.sync_engine.
"""

import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.engine import URL, make_url

from app.core.database import create_database_engine

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
    """Run migrations using an already-open synchronous connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database.

    If config.attributes["connection"] is set, the caller has already opened a
    connection (e.g., a test fixture's async connection run via run_sync).  In
    that case we use that connection directly so the migration runs inside the
    caller's session/schema context.

    Otherwise we create a fresh async engine from DATABASE_URL and drive it via
    the synchronous proxy (AsyncEngine.sync_engine), which is safe for Alembic's
    synchronous API.
    """
    # --- Programmatic / test-fixture path ---
    provided_connection: Any = config.attributes.get("connection")
    if provided_connection is not None:
        _run_migrations_with_connection(provided_connection)
        return

    # --- CLI / standalone path ---
    url = _database_url()
    async_engine = create_database_engine(url)
    try:
        with async_engine.sync_engine.connect() as connection:
            _run_migrations_with_connection(connection)
    finally:
        # Dispose synchronously; we are already outside any event loop here.
        async_engine.sync_engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
