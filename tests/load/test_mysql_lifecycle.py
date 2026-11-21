"""Required MySQL acceptance for isolated load fixture prepare/verify/cleanup."""

import os
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tools.load.config import OPT_IN_PHRASE, LoadConfig
from tools.load.fixtures import create_owned_schema, drop_owned_schema, verify_owned_schema
from tools.load.invariants import verify_post_run_invariants
from tools.load.runner import confirm_fixture_target, load_workload_fixture, run_workload


def _schema_database_url(mysql_url, schema_name):
    return make_url(mysql_url).set(database=schema_name).render_as_string(hide_password=False)


@pytest.mark.parametrize("password", ["test-only-password", "p@ss:/word%with+symbols"])
def test_mysql_acceptance_url_preserves_connection_credentials(password):
    from sqlalchemy.engine import URL

    original = URL.create(
        "mysql+asyncmy", username="lab", password=password, host="127.0.0.1", port=3306
    )
    configured = make_url(
        _schema_database_url(original.render_as_string(hide_password=False), "prl_load_" + "a" * 32)
    )

    assert configured.password == password
    assert configured.database == "prl_load_" + "a" * 32
    assert configured.host == original.host
    assert configured.username == original.username


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_load_schema_prepare_verify_and_cleanup(request):
    mysql_url = os.environ.get("MYSQL_TEST_URL")
    if not mysql_url:
        if request.config.getoption("--require-mysql", default=False):
            pytest.fail("MYSQL_TEST_URL is required for load-lab acceptance")
        pytest.skip("Set MYSQL_TEST_URL to run the isolated load-schema acceptance")

    run_id = uuid4().hex
    schema_name = f"prl_load_{run_id}"
    source_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    config = LoadConfig(
        target_url="http://127.0.0.1:8000",
        release_sha=source_sha,
        database_url=_schema_database_url(mysql_url, schema_name),
        opt_in=OPT_IN_PHRASE,
        duration_seconds=1,
        sessions=20,
    )
    password = "mysql-acceptance-" + uuid4().hex + "-private"
    await create_owned_schema(config, seed=53, password=password)
    try:
        dataset = await verify_owned_schema(config, run_id=run_id)
        assert dataset == {"staff": 10, "appointments": 1_000}

        from app.core.config import Settings
        from app.factory import create_app

        engine = create_async_engine(config.database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                proposal_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM change_proposals WHERE status = 'PENDING'")
                )
            assert proposal_count == 10
        finally:
            await engine.dispose()

        settings = Settings.model_validate(
            {
                "DATABASE_URL": config.database_url,
                "APPLICATION_ORIGIN": config.target_url,
                "COOKIE_SECRET": "load-integration-cookie-secret-" + run_id,
                "CSRF_SECRET": "load-integration-csrf-secret-" + run_id,
                "ACCEPTED_SCHEMA_REVISIONS": revision,
                "COOKIE_SECURE": False,
                "ENVIRONMENT": "test",
                "LOGIN_IP_ATTEMPTS": 100,
                "LOGIN_EMAIL_ATTEMPTS": 100,
            }
        )
        app = create_app(settings)
        fixture = load_workload_fixture(53, run_id, password)
        async with app.router.lifespan_context(app):
            import httpx

            def client_factory(**kwargs):
                return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

            await confirm_fixture_target(config.target_url, fixture, client_factory=client_factory)
            workload = await run_workload(
                config,
                fixture,
                seed=53,
                duration_seconds=1,
                client_factory=client_factory,
            )
            assert workload.created_proposal_ids
            assert workload.verified_replay_count >= len(workload.created_proposal_ids)
            invariants = await verify_post_run_invariants(
                config.database_url, workload, expected_release_sha=source_sha
            )
            assert invariants["passed"] is True
    finally:
        await drop_owned_schema(config, run_id=run_id)

    admin = create_async_engine(make_url(mysql_url), poolclass=NullPool)
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                text(
                    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :name"
                ),
                {"name": schema_name},
            )
        assert exists is None
    finally:
        await admin.dispose()
