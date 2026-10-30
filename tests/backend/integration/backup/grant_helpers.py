"""Disposable direct-grant accounts; no mutations to server configuration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.backup.config import identifier
from app.core.database import create_database_engine


def literal_scope(database: str) -> str:
    identifier(database)
    return "`" + database.replace("_", r"\_") + "`.*"


@asynccontextmanager
async def scoped_account(
    administrator: AsyncEngine,
    database: str,
    privileges: str = "SELECT, TRIGGER, EVENT",
    *,
    extra_scopes: tuple[tuple[str, str], ...] = (),
    global_privileges: str = "BACKUP_ADMIN,SHOW_ROUTINE",
) -> AsyncIterator[AsyncEngine]:
    user = "prl_grant_" + uuid4().hex[:20]
    created, engine = False, None
    try:
        async with administrator.begin() as db:
            # Regression specifically exercises MySQL's escaped-literal semantics.
            assert (await db.execute(text("SELECT @@global.partial_revokes"))).scalar_one() == 0
            await db.execute(text(f"CREATE USER '{user}'@'%' IDENTIFIED BY 'fictional-grant-test'"))
            created = True
            for scope, rights in ((literal_scope(database), privileges), *extra_scopes):
                await db.execute(text(f"GRANT {rights} ON {scope} TO '{user}'@'%'"))
            await db.execute(text(f"GRANT {global_privileges} ON *.* TO '{user}'@'%'"))
        engine = create_database_engine(
            administrator.url.set(
                database=database, username=user, password="fictional-grant-test"
            ),
            poolclass=NullPool,
            hide_parameters=True,
        )
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with administrator.begin() as db:
                await db.execute(text(f"DROP USER '{user}'@'%'"))


async def assert_literal_grant(engine: AsyncEngine, database: str) -> None:
    async with engine.connect() as db:
        grants = (await db.execute(text("SHOW GRANTS FOR CURRENT_USER"))).scalars().all()
    assert sum(f" ON {literal_scope(database)} TO " in grant for grant in grants) == 1
    assert not any(f" ON {identifier(database)}.* TO " in grant for grant in grants)
