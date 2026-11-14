"""MySQL-backed HTTP contract for administrative booking configuration."""

from uuid import uuid4

import pytest
import pytest_asyncio
from tests.backend.integration.test_admin_account_endpoints import (
    ORIGIN,
    promote_to_admin,
    sign_in,
    write_headers,
)

from app.models import ServiceSetting

pytestmark = pytest.mark.mysql
PURPOSES = "/api/v1/admin/purposes"
CONFIGURATION = "/api/v1/admin/booking-configuration"
PURPOSE = {
    "code": "CAREER_ADVICE",
    "name": "Career advice",
    "duration_minutes": 30,
    "requires_free_text": False,
    "is_active": True,
}
VERSION = "2026-01-01T00:00:00Z"


def policy_body(revision=VERSION, **changes):
    return {
        "expected_updated_at": revision,
        "timezone": "Asia/Ho_Chi_Minh",
        "start_step_minutes": 15,
        "booking_horizon_days": 7,
        "max_active_appointments": 3,
        "windows": [],
        **changes,
    }


def purpose_body(item, **changes):
    return {
        **{name: item[name] for name in PURPOSE if name != "code"},
        "expected_updated_at": item["updated_at"],
        **changes,
    }


def window(day, opens, closes):
    return {"weekday": day, "opens_at": opens, "closes_at": closes}


@pytest_asyncio.fixture
async def admin_policy_env(auth_env):
    client, factory, user_id, _ = auth_env
    async with factory.begin() as db:
        db.add(ServiceSetting())
    await promote_to_admin(factory, user_id)
    assert (await sign_in(client)).status_code == 200
    return auth_env


async def test_admin_routes_require_persisted_role_origin_and_csrf(auth_env):
    client, factory, user_id, _ = auth_env
    async with factory.begin() as db:
        db.add(ServiceSetting())
    identifier = f"{PURPOSES}/{uuid4()}"
    routes = [
        ("GET", PURPOSES, None),
        ("GET", identifier, None),
        ("GET", CONFIGURATION, None),
        ("POST", PURPOSES, PURPOSE),
        ("PUT", identifier, purpose_body({**PURPOSE, "updated_at": VERSION})),
        ("PUT", CONFIGURATION, policy_body()),
    ]
    for method, path, body in routes:
        response = await client.request(method, path, json=body)
        assert response.status_code == 401
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]

    assert (await sign_in(client)).status_code == 200
    for method, path, body in routes:
        response = await client.request(method, path, json=body, headers=write_headers(client))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"

    await promote_to_admin(factory, user_id)
    for method, path, body in routes:
        if method == "GET":
            continue
        for headers, code in (
            (ORIGIN, "INVALID_CSRF"),
            ({**write_headers(client), "Origin": "https://untrusted.invalid"}, "INVALID_ORIGIN"),
        ):
            response = await client.request(method, path, json=body, headers=headers)
            assert response.status_code == 403 and response.json()["error"]["code"] == code
        response = await client.request(
            method,
            path,
            content=b"{}",
            headers={**write_headers(client), "Content-Type": "text/plain"},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_catalog_filter_activation_and_immutable_code(admin_policy_env):
    client, _, _, _ = admin_policy_env
    headers = write_headers(client)
    response = await client.post(PURPOSES, json=PURPOSE, headers=headers)
    assert response.status_code == 201, response.json()
    item = response.json()
    path = f"{PURPOSES}/{item['id']}"
    assert (await client.get(path)).json() == item
    assert (await client.get(f"{PURPOSES}?is_active=true")).json()["items"] == [item]
    changed_code = await client.put(
        path, json=purpose_body(item, code="UNEXPECTED_CODE"), headers=headers
    )
    assert changed_code.status_code == 422
    assert "UNEXPECTED_CODE" not in changed_code.text
    response = await client.put(path, json=purpose_body(item, is_active=False), headers=headers)
    assert response.status_code == 200
    inactive = response.json()
    assert inactive["code"] == item["code"] and not inactive["is_active"]
    assert (await client.get(f"{PURPOSES}?is_active=false")).json()["items"] == [inactive]
    assert (await client.get(f"{PURPOSES}?page=2")).json()["items"] == []
    assert (await client.get(f"{PURPOSES}?page=1&page=2")).status_code == 422
    assert (await client.delete(path, headers=headers)).status_code == 405
    response = await client.put(path, json=purpose_body(inactive, is_active=True), headers=headers)
    assert response.status_code == 200 and response.json()["is_active"] is True


@pytest.mark.parametrize("duration", [0, -1, 32768, "20", True])
async def test_purpose_duration_uses_the_positive_mysql_range(admin_policy_env, duration):
    client, _, _, _ = admin_policy_env
    response = await client.post(
        PURPOSES, json={**PURPOSE, "duration_minutes": duration}, headers=write_headers(client)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "Invented/Zone"},
        {"timezone": "Factory"},
        {"timezone": "America"},
        {"start_step_minutes": 0},
        {"booking_horizon_days": 32768},
        {"max_active_appointments": 0},
        {"max_active_appointments": 32768},
        {"max_active_appointments": "2"},
        {"max_active_appointments": True},
        {"windows": [window(0, "08:00:00", "09:00:00")]},
        {"windows": [window(1, "08:00:00", "24:00:00")]},
        {"windows": [window(1, "08:00:00", "08:00:00")]},
        {"windows": [window(1, "23:00:00", "00:00:00")]},
        {"windows": [window(1, "08:00:00Z", "09:00:00")]},
        {"windows": [window(1, "08:00:00.01", "09:00:00")]},
        {"windows": [window(1, "09:00:00", "11:00:00"), window(1, "08:00:00", "09:00:01")]},
        {"windows": [window(1, "08:00:00", "11:00:00")] * 2},
    ],
)
async def test_invalid_policy_and_overlaps_leave_configuration_intact(admin_policy_env, changes):
    client, _, _, _ = admin_policy_env
    baseline = (await client.get(CONFIGURATION)).json()
    rejected = await client.put(
        CONFIGURATION,
        json=policy_body(baseline["updated_at"], **changes),
        headers=write_headers(client),
    )
    assert rejected.status_code == 422, rejected.json()
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await client.get(CONFIGURATION)).json() == baseline


@pytest.mark.parametrize(
    "revision",
    ["0001-01-01T00:00:00+00:01", "9999-12-31T23:59:59-00:01"],
)
async def test_revision_outside_utc_range_is_rejected_on_both_resources(admin_policy_env, revision):
    client, _, _, _ = admin_policy_env
    headers = write_headers(client)
    created = await client.post(PURPOSES, json=PURPOSE, headers=headers)
    assert created.status_code == 201, created.json()
    item = created.json()
    for path, payload in (
        (CONFIGURATION, policy_body(revision)),
        (f"{PURPOSES}/{item['id']}", purpose_body(item, expected_updated_at=revision)),
    ):
        baseline = (await client.get(path)).json()
        response = await client.put(path, json=payload, headers=headers)
        assert response.status_code == 422, response.json()
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert (await client.get(path)).json() == baseline


async def test_adjacent_windows_and_other_days_are_ordered_and_omitted_days_closed(
    admin_policy_env,
):
    client, _, _, _ = admin_policy_env
    baseline = (await client.get(CONFIGURATION)).json()
    windows = [
        window(7, "00:00:00", "23:59:59"),
        window(1, "09:00:00", "10:00:00"),
        window(1, "08:00:00", "09:00:00"),
    ]
    response = await client.put(
        CONFIGURATION,
        json=policy_body(baseline["updated_at"], windows=windows),
        headers=write_headers(client),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["windows"] == list(reversed(windows))
    assert (await client.get(CONFIGURATION)).json() == response.json()
