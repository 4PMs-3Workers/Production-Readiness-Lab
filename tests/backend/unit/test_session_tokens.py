import hashlib

import pytest


def test_session_token_hashes_cookie_ascii(monkeypatch):
    from app.core.session_tokens import hash_session_token, new_session_token, valid_session_token

    monkeypatch.setattr("secrets.token_bytes", lambda count: b"a" * count)
    token = new_session_token()
    assert token == "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"
    assert valid_session_token(token)
    assert hash_session_token(token) == hashlib.sha256(token.encode("ascii")).digest()


@pytest.mark.parametrize("token", ["", "a", "!" * 43, "é" * 43, "a" * 44, "a" * 42 + "="])
def test_invalid_session_token_is_rejected(token):
    from app.core.session_tokens import valid_session_token

    assert not valid_session_token(token)
