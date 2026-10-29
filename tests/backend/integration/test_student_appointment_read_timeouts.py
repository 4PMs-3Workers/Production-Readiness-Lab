"""Real MySQL read budgets must expire independently of the HTTP deadline."""

import pytest
from sqlalchemy import text
from tests.backend.integration.test_appointments_api import assert_private
from tests.backend.integration.test_booking_context import sign_in
from tests.backend.integration.test_student_cancellation import seed

from app.dependencies.database import get_db_session
from app.repositories import appointments, catalog


@pytest.mark.parametrize("endpoint", ["history", "detail", "catalog"])
async def test_private_reads_enforce_query_budget_and_cleanup(booking_env, monkeypatch, endpoint):
    env, _, start = booking_env
    appointment_id = await seed(env, start)
    await sign_in(env)
    targets = {
        "history": (appointments, "read_student_appointment_page", "/api/v1/appointments"),
        "detail": (
            appointments,
            "find_student_appointment",
            f"/api/v1/appointments/{appointment_id}",
        ),
        "catalog": (catalog, "read_purpose_page", "/api/v1/purposes"),
    }
    repository, method, path = targets[endpoint]
    assert (await env.client.get(path)).status_code == 200
    normal_settings = env.app.state.settings
    env.app.state.settings = normal_settings.model_copy(update={"db_query_timeout_seconds": 0.2})
    assert env.app.state.settings.request_timeout_seconds > 0.6
    original = getattr(repository, method)

    async def delayed_read(db, *args, **kwargs):
        await db.execute(text("SELECT SLEEP(0.6)"))
        return await original(db, *args, **kwargs)

    async with env.factory() as db:

        async def held_session():
            # Keep the session open: request teardown must not conceal leaked transactions.
            yield db

        env.app.dependency_overrides[get_db_session] = held_session
        try:
            with monkeypatch.context() as patch:
                patch.setattr(repository, method, delayed_read)
                response = await env.client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
            assert_private(response, error_class="service")
            assert not db.in_transaction()
            assert await db.scalar(text("SELECT 1")) == 1
            await db.rollback()
            # Recovery uses the normal budget; only the injected slow read must
            # satisfy the deliberately tight deadline on a shared CI runner.
            env.app.state.settings = normal_settings
            recovered = await env.client.get(path)
            assert recovered.status_code == 200
            assert_private(recovered)
            assert not db.in_transaction()
        finally:
            env.app.state.settings = normal_settings
            env.app.dependency_overrides.pop(get_db_session)
