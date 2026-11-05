import pytest
from pydantic import ValidationError

from app.schemas.admin_accounts import AccountCreateRequest, AccountQuery


def test_account_query_validates_bounded_filters():
    query = AccountQuery.model_validate(
        {"page": 2, "page_size": 50, "role": "STAFF", "is_active": False, "search": "staff"}
    )

    assert query.page == 2
    assert query.page_size == 50
    assert query.role == "STAFF"
    assert query.is_active is False
    assert query.search == "staff"

    with pytest.raises(ValidationError):
        AccountQuery.model_validate({"page_size": 101})
    with pytest.raises(ValidationError):
        AccountQuery.model_validate({"unexpected": True})


def test_account_creation_normalizes_email_and_keeps_password_secret():
    request = AccountCreateRequest.model_validate(
        {
            "email": "  New.Staff@Example.Invalid ",
            "name": "New Staff",
            "role": "STAFF",
            "password": "Initial-password!",
        }
    )

    assert request.email == "new.staff@example.invalid"
    assert request.password.get_secret_value() == "Initial-password!"
    assert "Initial-password!" not in repr(request)


@pytest.mark.parametrize("email", ["", "invalid", "a b@example.invalid", "@example.invalid"])
def test_account_creation_rejects_invalid_email(email):
    with pytest.raises(ValidationError):
        AccountCreateRequest.model_validate(
            {"email": email, "name": "New Staff", "role": "STAFF", "password": "password"}
        )
