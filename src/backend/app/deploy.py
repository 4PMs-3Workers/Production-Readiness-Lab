"""Explicit, bounded deployment jobs. Normal API/worker startup never writes schema."""

import asyncio
import os
import sys
from collections.abc import Sequence

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
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


SOFT_DELETE_SCHEMA = "20261127_1200_quota_acct_merge"
SOFT_DELETE_PREDECESSOR = "20261019_1200_proposal_replay"


async def migrate(*, rollback_soft_delete: bool = False) -> None:
    url = make_url(os.environ["DATABASE_URL"])
    engine = create_database_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    try:
        async with asyncio.timeout(120):
            async with migration_connection(engine) as connection:

                def run_revision(sync_connection: Connection) -> None:
                    config = Config("alembic.ini")
                    config.attributes["connection"] = sync_connection
                    if not rollback_soft_delete:
                        command.upgrade(config, "head")
                        return
                    current = MigrationContext.configure(sync_connection).get_current_revision()
                    if current != SOFT_DELETE_SCHEMA:
                        raise RuntimeError("Unexpected schema for soft-delete rollback")
                    if sync_connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM users WHERE deleted_at IS NOT NULL "
                            "OR is_active_before_delete IS NOT NULL)"
                        )
                    ):
                        raise RuntimeError("Cannot downgrade: lifecycle state would be lost")
                    quota = sync_connection.scalar(
                        text("SELECT max_active_appointments FROM service_settings WHERE id = 1")
                    )
                    if quota != 3:
                        raise RuntimeError(
                            "Cannot downgrade: configured booking quota would be lost"
                        )
                    command.downgrade(config, SOFT_DELETE_PREDECESSOR)

                await connection.run_sync(run_revision)
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"schema", "migrate", "rollback-soft-delete"}:
        print("Usage: python -m app.deploy {schema|migrate|rollback-soft-delete}", file=sys.stderr)
        return 2
    try:
        if args[0] == "schema":
            asyncio.run(check_schema())
        else:
            asyncio.run(migrate(rollback_soft_delete=args[0] == "rollback-soft-delete"))
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
