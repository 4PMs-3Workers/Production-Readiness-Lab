"""MySQL connection invariants shared by API, workers and integration tests."""

from typing import Any

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

STRICT_SQL_MODE = (
    "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)


def create_database_engine(
    url: str | URL, *, connect_args: dict[str, Any] | None = None, **options: Any
) -> AsyncEngine:
    """Configure every new connection, including replacements after reconnect."""
    if make_url(url).drivername != "mysql+asyncmy":
        raise ValueError("The backend requires MySQL with the asyncmy driver")
    driver_options = dict(connect_args or {})
    driver_options.update(
        charset="utf8mb4",
        init_command=f"SET time_zone = '+00:00', sql_mode = '{STRICT_SQL_MODE}'",
    )
    return create_async_engine(
        url,
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        connect_args=driver_options,
        **options,
    )
