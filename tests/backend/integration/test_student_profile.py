"""Owned student profile reads against an Alembic-initialized MySQL schema."""

from uuid import uuid4

import pytest

from app.models import User

PATH = "/api/v1/students/me"


async def sign_in(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": "  Good-password!  "},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200


def assert_private(response, status=200):
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Request-ID"]


async def test_profile_returns_only_current_students_explicit_fields(auth_env):
    client, factory, identifier, _ = auth_env
    async with factory.begin() as db:
        student = await db.get(User, identifier)
        assert student is not None
        student.student_code = "TEST-001"
        student.faculty = "Khoa kiểm thử"
        student.major = "Ngành kiểm thử"
        student.gender = "FEMALE"
        db.add(
            User(
                id=uuid4(),
                email="other@example.invalid",
                name="Other Student",
                role="STUDENT",
                password_hash="not-for-response",
                student_code="OTHER-STUDENT-CODE",
            )
        )
    await sign_in(client)
    response = await client.get(PATH)
    assert_private(response)
    assert response.json() == {
        "id": str(identifier),
        "email": "student@example.invalid",
        "name": "Test Student",
        "role": "STUDENT",
        "student_code": "TEST-001",
        "faculty": "Khoa kiểm thử",
        "major": "Ngành kiểm thử",
        "gender": "FEMALE",
    }
    assert "password" not in response.text
    assert "OTHER-STUDENT-CODE" not in response.text


async def test_existing_accounts_return_null_for_missing_profile_fields(auth_env):
    client, _, _, _ = auth_env
    await sign_in(client)
    response = await client.get(PATH)
    assert_private(response)
    for field in ("student_code", "faculty", "major", "gender"):
        assert response.json()[field] is None


@pytest.mark.parametrize("role", [None, "STAFF", "ADMIN"])
async def test_profile_requires_authenticated_student(auth_env, role):
    client, factory, identifier, _ = auth_env
    if role:
        await sign_in(client)
        async with factory.begin() as db:
            user = await db.get(User, identifier)
            assert user is not None
            user.role = role
    response = await client.get(PATH)
    assert_private(response, 403 if role else 401)
    assert response.json()["error"]["code"] == ("FORBIDDEN_ROLE" if role else "INVALID_SESSION")


@pytest.mark.parametrize("query", ["student_id=other", "id=other&id=third"])
async def test_profile_rejects_client_supplied_identity(auth_env, query):
    client, _, _, _ = auth_env
    await sign_in(client)
    response = await client.get(PATH + "?" + query)
    assert_private(response, 422)
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_profile_database_failure_is_private_and_sanitized(auth_env, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.repositories import users

    client, _, _, _ = auth_env
    await sign_in(client)

    async def fail(*args):
        raise OperationalError("secret-sql", {}, Exception("secret-password"))

    monkeypatch.setattr(users, "find_student_by_id", fail)
    response = await client.get(PATH)
    assert_private(response, 503)
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "secret-" not in response.text
