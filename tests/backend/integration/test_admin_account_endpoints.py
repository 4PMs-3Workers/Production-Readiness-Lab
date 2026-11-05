from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import AuditEvent, User

ORIGIN = {"Origin": "http://localhost:3000"}
ADMIN_ACCOUNTS = "/api/v1/admin/accounts"


async def sign_in(client):
    return await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": "  Good-password!  "},
        headers=ORIGIN,
    )


async def promote_to_admin(factory, user_id):
    async with factory.begin() as db:
        user = await db.get(User, user_id)
        assert user is not None
        user.role = "ADMIN"


def write_headers(client):
    return {**ORIGIN, "X-CSRF-Token": client.cookies["prl_csrf"]}


@pytest.mark.mysql
async def test_account_api_rechecks_admin_role_and_requires_csrf(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)

    denied = await client.get(ADMIN_ACCOUNTS)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN_ROLE"

    await promote_to_admin(factory, user_id)
    assert (await client.get(ADMIN_ACCOUNTS)).status_code == 200

    missing_csrf = await client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "new.staff@example.invalid",
            "name": "New Staff",
            "role": "STAFF",
            "password": "Initial-password!",
        },
        headers=ORIGIN,
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "INVALID_CSRF"


@pytest.mark.mysql
async def test_admin_can_filter_and_paginate_accounts_without_secrets(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, user_id)
    async with factory.begin() as db:
        db.add_all(
            [
                User(
                    id=uuid4(),
                    email="alpha-staff@example.invalid",
                    name="Alpha Staff",
                    role="STAFF",
                    password_hash="never-return-this-hash",
                ),
                User(
                    id=uuid4(),
                    email="beta-staff@example.invalid",
                    name="Beta Staff",
                    role="STAFF",
                    password_hash="never-return-this-hash",
                ),
                User(
                    id=uuid4(),
                    email="inactive-staff@example.invalid",
                    name="Inactive Staff",
                    role="STAFF",
                    password_hash="never-return-this-hash",
                    is_active=False,
                ),
            ]
        )

    response = await client.get(
        f"{ADMIN_ACCOUNTS}?role=STAFF&is_active=true&search=staff&page=1&page_size=1"
    )

    assert response.status_code == 200
    page = response.json()
    assert page["page"] == 1
    assert page["page_size"] == 1
    assert page["total"] == 2
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["role"] == "STAFF" and item["is_active"] is True
    assert "password" not in item and "password_hash" not in item
    assert "never-return-this-hash" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.mysql
async def test_admin_account_creation_normalizes_email_and_rejects_collation_duplicates(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, user_id)
    payload = {
        "email": "  New.Staff@Example.Invalid ",
        "name": "New Staff",
        "role": "STAFF",
        "password": "Initial-password!",
    }

    created = await client.post(ADMIN_ACCOUNTS, json=payload, headers=write_headers(client))

    assert created.status_code == 201
    item = created.json()
    assert item["email"] == "new.staff@example.invalid"
    assert item["role"] == "STAFF"
    assert item["is_active"] is True
    assert "password" not in item and "password_hash" not in item
    assert "Initial-password!" not in created.text
    assert "private" in created.headers["cache-control"]

    duplicate = await client.post(
        ADMIN_ACCOUNTS,
        json={**payload, "email": "NEW.STAFF@example.invalid"},
        headers=write_headers(client),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    async with factory() as db:
        audit_count = await db.scalar(select(func.count()).select_from(AuditEvent))
        audit = await db.scalar(select(AuditEvent))
    assert audit_count == 1
    assert audit is not None
    assert audit.action == "ACCOUNT_CREATED"
    assert "password" not in str(audit.event_metadata).lower()
