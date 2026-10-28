"""Profile upgrades preserve existing accounts and refuse destructive downgrades."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schema_lifecycle import migration_connection
from app.models import User


def migrate(connection, target, *, downgrade=False):
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    operation = command.downgrade if downgrade else command.upgrade
    operation(cfg, target)


async def test_profile_upgrade_preserves_existing_account_and_marks_new_fields_unknown(
    mysql_engine,
):
    identifier = uuid4()
    async with migration_connection(mysql_engine) as connection:
        await connection.run_sync(
            lambda db: migrate(db, "20261016_1200_audit_reqid128", downgrade=True)
        )
        await connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name, role, is_active, "
                "created_at, updated_at) VALUES (:id, 'legacy@example.invalid', 'unused-hash', "
                "'Legacy Student', 'STUDENT', 1, CURRENT_TIMESTAMP(6), CURRENT_TIMESTAMP(6))"
            ),
            {"id": identifier.bytes},
        )
        await connection.run_sync(lambda db: migrate(db, "head"))
    async with AsyncSession(mysql_engine) as db:
        student = await db.get(User, identifier)
        assert student is not None
        assert student.email == "legacy@example.invalid"
        assert student.name == "Legacy Student"
        assert student.password_hash == "unused-hash"
        assert (student.student_code, student.faculty, student.major, student.gender) == (
            None,
            None,
            None,
            None,
        )


async def test_profile_downgrade_refuses_to_discard_populated_fields(mysql_engine):
    async with AsyncSession(mysql_engine, expire_on_commit=False) as db:
        student = User(
            email="profile@example.invalid",
            name="Fictional Student",
            role="STUDENT",
            password_hash="unused-hash",
            student_code="TEST-PRESERVE",
            faculty="Khoa kiểm thử",
        )
        db.add(student)
        await db.commit()
        identifier = student.id
    with pytest.raises(RuntimeError, match="populated fields would be lost"):
        async with migration_connection(mysql_engine) as connection:
            await connection.run_sync(
                lambda db: migrate(db, "20261016_1200_audit_reqid128", downgrade=True)
            )
    async with AsyncSession(mysql_engine) as db:
        loaded = await db.get(User, identifier)
        assert loaded is not None
        assert loaded.student_code == "TEST-PRESERVE"
        assert loaded.faculty == "Khoa kiểm thử"
