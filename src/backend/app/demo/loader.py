"""One-transaction, insert-only demo loading without any API startup dependency."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.database import create_database_engine
from app.core.passwords import PasswordPolicy, PasswordService
from app.demo.data import Dataset, SeedError, Window, load_dataset, overlaps, seed_id
from app.models import Purpose, ServiceHour, ServiceSetting, StaffPurpose, User

# Exact reviewed head includes #3's initial schema and the optional student fields.
SCHEMA_REVISION = "20261017_2350_student_profile"
TABLES = ("users", "purposes", "staff_purposes", "service_settings", "service_hours")
SYSTEM_SCHEMAS = {"mysql", "information_schema", "performance_schema", "sys"}


@dataclass(frozen=True)
class SeedConfig:
    url: URL = field(repr=False)
    target: str
    passwords: dict[str, str] = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SeedConfig":
        values = os.environ if env is None else env
        if values.get("ENVIRONMENT") not in {"local", "development", "demo"}:
            raise SeedError(
                "Configuration: ENVIRONMENT must explicitly be local, development or demo"
            )
        target = values.get("DEMO_SEED_DATABASE", "")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", target) or target.lower() in SYSTEM_SCHEMAS:
            raise SeedError("Configuration: DEMO_SEED_DATABASE must name a non-system database")
        try:
            url = make_url(values.get("DATABASE_URL", ""))
        except (ArgumentError, ValueError, TypeError):
            raise SeedError("Configuration: invalid DATABASE_URL") from None
        if url.drivername != "mysql+asyncmy" or not url.host or url.database != target:
            raise SeedError("Configuration: DATABASE_URL driver or explicit target does not match")
        passwords = {}
        for role in ("ADMIN", "STAFF", "STUDENT"):
            password = values.get(f"DEMO_{role}_PASSWORD", "")
            # Keep within #5's login contract; reject placeholder and blank configuration.
            if (
                not 12 <= len(password) <= 1024
                or not password.strip()
                or any(ord(c) < 32 for c in password)
                or password.lower().startswith(("replace-", "<", "change-me"))
            ):
                raise SeedError(f"Configuration: invalid DEMO_{role}_PASSWORD")
            passwords[role] = password
        return cls(url, target, passwords)


async def check_database(db: AsyncSession, target: str) -> None:
    if (await db.execute(text("SELECT DATABASE()"))).scalar_one() != target:
        raise SeedError("Target: selected database does not match the configured target")
    version = (await db.execute(text("SELECT VERSION()"))).scalar_one()
    if not version.startswith("8.4."):
        raise SeedError("Schema: MySQL 8.4 is required")
    revisions = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    if revisions != [SCHEMA_REVISION]:
        raise SeedError(
            "Schema: incompatible migration revision; migrate to the reviewed head first"
        )
    engines = (
        await db.execute(
            text(
                "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE()"
            )
        )
    ).all()
    storage = {row[0]: row[1] for row in engines}
    if any(storage.get(table) != "InnoDB" for table in TABLES):
        raise SeedError("Schema: all five seed tables must exist and use InnoDB")


def require_match(row: Any, expected: Mapping[str, Any], table: str) -> None:
    for name, value in expected.items():
        if getattr(row, name) != value:
            raise SeedError(f"Conflict: {table}.{name} differs from the reviewed manifest")


async def prepare(db: AsyncSession, data: Dataset) -> tuple[dict[str, list[Any]], dict[str, int]]:
    """Read and validate all conflicts before adding anything to the session."""
    pending: dict[str, list[Any]] = {table: [] for table in TABLES}
    existing: dict[str, int] = {table: 0 for table in TABLES}
    for account in data.accounts:
        identifier = seed_id("users", account.email)
        # Let MySQL apply the email column's collation, including ignorable characters.
        # Look up ID independently too, so split ownership and natural-key conflicts fail.
        matches = (
            await db.scalars(
                select(User).where(or_(User.id == identifier, User.email == account.email))
            )
        ).all()
        if matches:
            if len(matches) != 1:
                raise SeedError("Conflict: users identifier and email refer to different records")
            # The ID proves seed ownership; never adopt a different account by email alone.
            require_match(
                matches[0],
                {
                    "id": identifier,
                    "email": account.email,
                    "name": account.name,
                    "role": account.role,
                },
                "users",
            )
            existing["users"] += 1
        else:
            pending["users"].append(User(id=identifier, **account.model_dump()))
    purposes = (
        await db.scalars(
            select(Purpose).where(
                or_(
                    Purpose.code.in_([p.code for p in data.purposes]),
                    Purpose.id.in_([seed_id("purposes", p.code) for p in data.purposes]),
                )
            )
        )
    ).all()
    for purpose in data.purposes:
        identifier = seed_id("purposes", purpose.code)
        matches = [p for p in purposes if p.id == identifier or p.code == purpose.code]
        if matches:
            if len(matches) != 1:
                raise SeedError("Conflict: purposes identifier and code refer to different records")
            require_match(matches[0], {"id": identifier, **purpose.model_dump()}, "purposes")
            existing["purposes"] += 1
        else:
            pending["purposes"].append(Purpose(id=identifier, **purpose.model_dump()))
    for assignment in data.assignments:
        key = (
            seed_id("users", assignment.staff_email),
            seed_id("purposes", assignment.purpose_code),
        )
        row = await db.get(StaffPurpose, key)
        if row is not None:
            require_match(row, {"is_accepting": assignment.is_accepting}, "staff_purposes")
            existing["staff_purposes"] += 1
        else:
            pending["staff_purposes"].append(
                StaffPurpose(
                    staff_id=key[0], purpose_id=key[1], is_accepting=assignment.is_accepting
                )
            )
    setting = await db.get(ServiceSetting, 1)
    if setting is not None:
        require_match(setting, data.settings.model_dump(), "service_settings")
        existing["service_settings"] += 1
    else:
        pending["service_settings"].append(ServiceSetting(**data.settings.model_dump()))
    hours = (await db.scalars(select(ServiceHour))).all()
    for window in data.hours:
        identifier = seed_id("service_hours", window.key)
        match = None
        for hour in hours:
            other = Window(weekday=hour.weekday, opens_at=hour.opens_at, closes_at=hour.closes_at)
            if hour.id == identifier or window.key == other.key:
                require_match(hour, {"id": identifier, **window.model_dump()}, "service_hours")
                match = hour
            elif overlaps(window, other):
                raise SeedError("Conflict: service_hours overlaps a reviewed service window")
        if match is not None:
            existing["service_hours"] += 1
        else:
            pending["service_hours"].append(ServiceHour(id=identifier, **window.model_dump()))
    return pending, existing


async def load_into_engine(
    engine: AsyncEngine, config: SeedConfig, data: Dataset
) -> dict[str, dict[str, int]]:
    """Caller owns engine disposal; this function owns the entire transaction."""
    data.review()
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as db:
        await check_database(db, config.target)
        pending, existing = await prepare(db, data)
        if pending["users"]:
            passwords = await PasswordService.create(PasswordPolicy(), 2)
            for user in pending["users"]:
                user.password_hash = await passwords.hash(config.passwords[user.role])
        # Explicit flush ordering avoids relying on ORM relationships absent from these models.
        for table in TABLES:
            db.add_all(pending[table])
            await db.flush()
    return {
        table: {"inserted": len(pending[table]), "existing": existing[table]} for table in TABLES
    }


async def run_seed(config: SeedConfig) -> dict[str, dict[str, int]]:
    data = load_dataset()
    engine = create_database_engine(config.url, echo=False, hide_parameters=True)
    try:
        return await load_into_engine(engine, config, data)
    finally:
        await engine.dispose()
