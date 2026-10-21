from unittest.mock import AsyncMock, Mock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.worker.resources import SUPPORTED_SCHEMA_REVISIONS, open_resources


def test_worker_schema_support_requires_review_when_migration_head_changes():
    assert set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads()) == set(
        SUPPORTED_SCHEMA_REVISIONS
    )


async def test_worker_creates_a_finite_separate_pool_and_disposes_it_on_failure(auth_settings):
    engine = Mock()
    engine.dispose = AsyncMock()
    create = Mock(return_value=engine)
    with pytest.raises(RuntimeError):
        async with open_resources(auth_settings, engine_factory=create) as resources:
            assert resources.engine is engine
            assert resources.sessions.kw["expire_on_commit"] is False
            raise RuntimeError("test-only failure")
    create.assert_called_once_with(
        auth_settings.database_url,
        connect_args={"connect_timeout": 1},
        pool_size=5,
        max_overflow=5,
        pool_timeout=0.5,
    )
    engine.dispose.assert_awaited_once()
