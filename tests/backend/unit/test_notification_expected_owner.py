"""A notification request must check its own authenticated identity."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from starlette.requests import Request

from app.api.notifications import check_expected_notification_owner
from app.core.errors import AppError
from app.domain.auth import AuthContext

OWNER = UUID("00000000-0000-0000-0000-000000000001")
OTHER = UUID("00000000-0000-0000-0000-000000000002")
CONTEXT = AuthContext(
    session_id=UUID("00000000-0000-0000-0000-000000000003"),
    user_id=OWNER,
    email="owner@example.invalid",
    name="Owner",
    role="STUDENT",
    expires_at=datetime.now(UTC) + timedelta(hours=1),
)


def request(*headers: tuple[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/notifications",
            "headers": [(name.lower().encode(), value.encode()) for name, value in headers],
            "query_string": b"",
        }
    )


def test_expected_owner_is_optional_for_other_api_clients():
    check_expected_notification_owner(request(), CONTEXT)


def test_expected_owner_accepts_the_same_authenticated_request():
    check_expected_notification_owner(
        request(("X-Expected-User-ID", str(OWNER)), ("X-Expected-Role", "STUDENT")),
        CONTEXT,
    )


@pytest.mark.parametrize(
    "expected_user,expected_role",
    [(OTHER, "STUDENT"), (OWNER, "STAFF")],
)
def test_expected_owner_rejects_another_identity(expected_user: UUID, expected_role: str):
    with pytest.raises(AppError) as caught:
        check_expected_notification_owner(
            request(
                ("X-Expected-User-ID", str(expected_user)),
                ("X-Expected-Role", expected_role),
            ),
            CONTEXT,
        )
    assert (caught.value.status_code, caught.value.code) == (409, "ACCOUNT_CHANGED")


@pytest.mark.parametrize(
    "headers",
    [
        (("X-Expected-User-ID", str(OWNER)),),
        (("X-Expected-Role", "STUDENT"),),
        (("X-Expected-User-ID", "invalid"), ("X-Expected-Role", "STUDENT")),
        (("X-Expected-User-ID", str(OWNER)), ("X-Expected-Role", "ADMIN")),
        (
            ("X-Expected-User-ID", str(OWNER)),
            ("X-Expected-User-ID", str(OTHER)),
            ("X-Expected-Role", "STUDENT"),
        ),
    ],
)
def test_expected_owner_rejects_incomplete_or_invalid_headers(headers: tuple[tuple[str, str], ...]):
    with pytest.raises(AppError) as caught:
        check_expected_notification_owner(request(*headers), CONTEXT)
    assert (caught.value.status_code, caught.value.code) == (422, "VALIDATION_ERROR")
