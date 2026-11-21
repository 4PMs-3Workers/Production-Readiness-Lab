"""The UI harness resets all catalog/policy state only inside its owned schema."""

import argparse
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from tools.scripts import frontend_e2e_backend as harness

from app.models import Purpose, ServiceHour, ServiceSetting, StaffPurpose

pytestmark = pytest.mark.mysql


async def test_catalog_reset_restores_values_revisions_links_and_windows(
    mysql_engine, tmp_path, monkeypatch, capsys
):
    del mysql_engine  # Enforce the shared --require-mysql prerequisite policy first.
    monkeypatch.setattr(harness, "RUNTIME", tmp_path)
    for name, filename in (
        ("FIXTURE", "fixture.json"),
        ("SETTINGS_SNAPSHOT", "outage.json"),
        ("LOSS", "loss"),
        ("HOLD", "hold"),
        ("HELD", "held"),
    ):
        monkeypatch.setattr(harness, name, tmp_path / filename)
    fixture = await harness.prepare()
    engine = harness.engine_for(fixture)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as db:
            purpose = await db.get(Purpose, UUID(fixture["purpose"]))
            setting = await db.get(ServiceSetting, 1)
            assert purpose is not None and setting is not None
            purpose.code = "CHANGED"
            purpose.name = "Changed fixture"
            purpose.duration_minutes = 17
            purpose.requires_free_text = False
            purpose.is_active = False
            setting.timezone = "UTC"
            setting.start_step_minutes = 17
            setting.booking_horizon_days = 11
            await db.execute(delete(ServiceHour))
            extra = Purpose(id=uuid4(), code="EXTRA", name="Extra", duration_minutes=1)
            db.add(extra)
            await db.flush()
            db.add(StaffPurpose(staff_id=UUID(fixture["staff"]), purpose_id=extra.id))
        for _ in range(2):
            await harness.operate(argparse.Namespace(action="reset"))
            async with factory.begin() as db:
                assert await harness.catalog_snapshot(db, fixture) == fixture["catalog_baseline"]
                assert await db.scalar(select(func.count()).select_from(Purpose)) == 1
                assert await db.scalar(select(func.count()).select_from(StaffPurpose)) == 2
    finally:
        await engine.dispose()
        await harness.cleanup(fixture)
