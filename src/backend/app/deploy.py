"""Explicit, bounded deployment jobs. Normal API/worker startup never writes schema."""

import asyncio
import os
import sys
from collections.abc import Sequence

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import create_database_engine
from app.core.schema_lifecycle import migration_connection
from app.worker.resources import open_resources


async def check_schema() -> None:
    settings = load_settings()
    async with asyncio.timeout(settings.readiness_timeout_seconds):
        async with open_resources(settings) as resources:
            await resources.check_compatibility(settings)


async def migrate() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    engine = create_database_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    try:
        async with asyncio.timeout(120):
            async with migration_connection(engine) as connection:

                def upgrade(sync_connection: Connection) -> None:
                    config = Config("alembic.ini")
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"schema", "migrate"}:
        print("Usage: python -m app.deploy {schema|migrate}", file=sys.stderr)
        return 2
    try:
        asyncio.run(check_schema() if args[0] == "schema" else migrate())
    except Exception:
        # Driver and migration exceptions may contain SQL, parameters or credentials.
        print(
            "Deployment operation failed; check configuration, database and schema", file=sys.stderr
        )
        return 1
    print("Deployment operation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
