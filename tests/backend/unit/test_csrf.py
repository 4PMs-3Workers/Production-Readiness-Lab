from uuid import UUID

import pytest

from app.api.errors import AppError


def test_signed_csrf_is_bound_to_session():
    from app.core.csrf import issue_csrf_token, verify_csrf_token

    token = issue_csrf_token(UUID(int=1), "test-secret")
    verify_csrf_token(cookie=token, header=token, session_id=UUID(int=1), secret="test-secret")
    for cookie, header, session_id, secret in [
        (None, token, 1, "test-secret"),
        (token, None, 1, "test-secret"),
        (token, token, 2, "test-secret"),
        (token, token, 1, "different"),
        (token, token + "a", 1, "test-secret"),
        ("v1.bad.bad", "v1.bad.bad", 1, "test-secret"),
        (token + "x", token + "x", 1, "test-secret"),
    ]:
        with pytest.raises(AppError) as caught:
            verify_csrf_token(
                cookie=cookie, header=header, session_id=UUID(int=session_id), secret=secret
            )
        assert caught.value.code == "INVALID_CSRF"
