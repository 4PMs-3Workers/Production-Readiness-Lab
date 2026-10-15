"""Catalog paging and authorization checks against an Alembic-created MySQL schema."""

from uuid import UUID

from app.models import Purpose, StaffPurpose, User

ORIGIN = {"Origin": "http://localhost:3000"}
CREDENTIALS = {"email": "student@example.invalid", "password": "  Good-password!  "}


async def sign_in(client):
    return await client.post("/api/v1/auth/login", json=CREDENTIALS, headers=ORIGIN)


async def seed_catalog(factory):
    purpose_ids = {
        "ZETA": UUID("00000000-0000-0000-0000-000000000101"),
        "BETA": UUID("00000000-0000-0000-0000-000000000102"),
        "ALPHA": UUID("00000000-0000-0000-0000-000000000103"),
        "INACTIVE": UUID("00000000-0000-0000-0000-000000000104"),
        "OTHER": UUID("00000000-0000-0000-0000-000000000105"),
    }
    staff_ids = {
        "tie_first": UUID("00000000-0000-0000-0000-000000000201"),
        "tie_second": UUID("00000000-0000-0000-0000-000000000202"),
        "zeta": UUID("00000000-0000-0000-0000-000000000203"),
        "inactive": UUID("00000000-0000-0000-0000-000000000204"),
        "student": UUID("00000000-0000-0000-0000-000000000205"),
        "admin": UUID("00000000-0000-0000-0000-000000000206"),
        "other_purpose": UUID("00000000-0000-0000-0000-000000000207"),
        "not_accepting": UUID("00000000-0000-0000-0000-000000000208"),
    }
    async with factory.begin() as db:
        for code in ("ZETA", "BETA", "ALPHA", "INACTIVE", "OTHER"):
            db.add(
                Purpose(
                    id=purpose_ids[code],
                    code=code,
                    name=f"Purpose {code}",
                    duration_minutes=30,
                    requires_free_text=code == "BETA",
                    is_active=code != "INACTIVE",
                )
            )
        users = [
            ("tie_first", "Alex"),
            ("tie_second", "Alex"),
            ("zeta", "Zoe"),
            ("inactive", "Inactive Staff"),
            ("student", "Student role"),
            ("admin", "Admin role"),
            ("other_purpose", "Other purpose"),
            ("not_accepting", "Not accepting"),
        ]
        for key, name in users:
            db.add(
                User(
                    id=staff_ids[key],
                    email=f"{key}@example.invalid",
                    name=name,
                    role="STUDENT" if key == "student" else "ADMIN" if key == "admin" else "STAFF",
                    password_hash="unused-test-hash",
                    is_active=key != "inactive",
                )
            )
        db.add_all(
            [
                StaffPurpose(staff_id=staff_ids["tie_first"], purpose_id=purpose_ids["ALPHA"]),
                StaffPurpose(staff_id=staff_ids["tie_second"], purpose_id=purpose_ids["ALPHA"]),
                StaffPurpose(staff_id=staff_ids["zeta"], purpose_id=purpose_ids["ALPHA"]),
                StaffPurpose(staff_id=staff_ids["inactive"], purpose_id=purpose_ids["ALPHA"]),
                StaffPurpose(staff_id=staff_ids["student"], purpose_id=purpose_ids["ALPHA"]),
                StaffPurpose(staff_id=staff_ids["admin"], purpose_id=purpose_ids["ALPHA"]),
                StaffPurpose(staff_id=staff_ids["other_purpose"], purpose_id=purpose_ids["OTHER"]),
                StaffPurpose(
                    staff_id=staff_ids["not_accepting"],
                    purpose_id=purpose_ids["ALPHA"],
                    is_accepting=False,
                ),
            ]
        )
    return purpose_ids, staff_ids


async def test_purposes_are_active_sorted_and_paginated(auth_env):
    client, factory, _, _ = auth_env
    purpose_ids, _ = await seed_catalog(factory)
    assert (await sign_in(client)).status_code == 200

    response = await client.get("/api/v1/purposes")

    assert response.status_code == 200
    assert set(response.json()) == {"items", "page", "page_size", "total"}
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 20
    assert response.json()["total"] == 4
    assert [row["code"] for row in response.json()["items"]] == ["ALPHA", "BETA", "OTHER", "ZETA"]
    assert response.json()["items"][0]["id"] == str(purpose_ids["ALPHA"])
    assert response.headers["cache-control"] == "private, no-store"


async def test_inactive_or_unknown_purpose_is_404(auth_env):
    client, factory, _, _ = auth_env
    purpose_ids, _ = await seed_catalog(factory)
    await sign_in(client)

    for value in (purpose_ids["INACTIVE"], UUID("00000000-0000-0000-0000-000000000999")):
        response = await client.get(f"/api/v1/staff?purpose_id={value}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_valid_purpose_without_staff_is_empty(auth_env):
    client, factory, _, _ = auth_env
    purpose_ids, _ = await seed_catalog(factory)
    await sign_in(client)

    response = await client.get(f"/api/v1/staff?purpose_id={purpose_ids['BETA']}")

    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


async def test_staff_filter_and_name_id_tiebreak(auth_env):
    client, factory, _, _ = auth_env
    purpose_ids, staff_ids = await seed_catalog(factory)
    await sign_in(client)

    response = await client.get(f"/api/v1/staff?purpose_id={purpose_ids['ALPHA']}")

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert [row["id"] for row in response.json()["items"]] == [
        str(staff_ids["tie_first"]),
        str(staff_ids["tie_second"]),
        str(staff_ids["zeta"]),
    ]
    assert all(set(row) == {"id", "name"} for row in response.json()["items"])
    assert response.headers["cache-control"] == "private, no-store"


async def test_pagination_bounds_and_late_empty_page(auth_env):
    client, factory, _, _ = auth_env
    purpose_ids, staff_ids = await seed_catalog(factory)
    await sign_in(client)

    first = await client.get("/api/v1/purposes?page=2&page_size=2")
    last_staff = await client.get(
        f"/api/v1/staff?purpose_id={purpose_ids['ALPHA']}&page=2&page_size=2"
    )
    empty = await client.get("/api/v1/purposes?page=3&page_size=2")
    for query in ("page=0", "page_size=0", "page_size=101"):
        invalid = await client.get(f"/api/v1/purposes?{query}")
        assert invalid.status_code == 422

    assert first.status_code == 200
    assert [row["code"] for row in first.json()["items"]] == ["OTHER", "ZETA"]
    assert last_staff.status_code == 200
    assert [row["id"] for row in last_staff.json()["items"]] == [str(staff_ids["zeta"])]
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "page": 3, "page_size": 2, "total": 4}


async def test_query_extras_duplicates_and_missing_ids_are_422(auth_env):
    client, factory, _, _ = auth_env
    purpose_ids, _ = await seed_catalog(factory)
    await sign_in(client)

    paths = (
        "/api/v1/purposes?student_id=somebody-else",
        "/api/v1/purposes?page=1&page=1",
        "/api/v1/staff",
        "/api/v1/staff?purpose_id=not-a-uuid",
        f"/api/v1/staff?purpose_id={purpose_ids['ALPHA']}&purpose_id={purpose_ids['ALPHA']}",
    )
    for path in paths:
        response = await client.get(path)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_student_only_no_admin_bypass(auth_env):
    client, factory, user_id, _ = auth_env
    await seed_catalog(factory)

    assert (await client.get("/api/v1/purposes")).status_code == 401
    assert (await sign_in(client)).status_code == 200
    async with factory.begin() as db:
        (await db.get(User, user_id)).role = "ADMIN"

    response = await client.get("/api/v1/purposes")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


async def test_catalog_get_does_not_require_csrf_or_origin(auth_env):
    client, factory, _, _ = auth_env
    await seed_catalog(factory)
    await sign_in(client)
    client.cookies.delete("prl_csrf")

    response = await client.get("/api/v1/purposes", headers={})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
