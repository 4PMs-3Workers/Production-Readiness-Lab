import httpx
import pytest
from tools.deploy.rollout_http import SmokeCredentials, SmokeSession, credentials
from tools.deploy.rollout_runtime import Deadline
from tools.deploy.rollout_state import RolloutError


def test_cookie_session_csrf_and_replay_key_follow_existing_auth_contract():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("login"):
            return httpx.Response(200, headers={"Set-Cookie": "session=private; Path=/; HttpOnly"})
        if request.url.path.endswith("csrf"):
            return httpx.Response(200, json={"csrf_token": "signed-private-token"})
        return httpx.Response(
            201,
            json={"id": "private-id"},
            headers={"Cache-Control": "private, no-store", "X-Request-ID": "r"},
        )

    with SmokeSession(
        "https://example.invalid", Deadline(30), transport=httpx.MockTransport(handler)
    ) as session:
        session.login(SmokeCredentials("private@example.invalid", "private-password", "CAREER"))
        session.request(
            "POST", "/api/v1/appointments", 201, key="one-operation", private=True, json={}
        )
    assert requests[-1].headers["Cookie"] == "session=private"
    assert requests[-1].headers["X-CSRF-Token"] == "signed-private-token"
    assert requests[-1].headers["Idempotency-Key"] == "one-operation"
    assert requests[-1].headers["Origin"] == "https://example.invalid"


def test_redirect_does_not_forward_credentials_to_another_origin():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})

    with SmokeSession(
        "https://example.invalid", Deadline(30), transport=httpx.MockTransport(handler)
    ) as session:
        with pytest.raises(RolloutError, match="smoke-failed"):
            session.login(SmokeCredentials("private@example.invalid", "secret", "CAREER"))
    assert seen == ["example.invalid"]


@pytest.mark.parametrize("raw", [b"{", b'{"email":"private"}', b'{"password":NaN}', b"x" * 8193])
def test_invalid_private_input_produces_only_a_static_failure(tmp_path, raw):
    path = tmp_path / "smoke.json"
    path.write_bytes(raw)
    with pytest.raises(RolloutError) as error:
        credentials(path)
    assert str(error.value) == "smoke-failed"


def test_credentials_are_not_rendered_by_repr_and_whitespace_is_preserved(tmp_path):
    path = tmp_path / "smoke.json"
    path.write_text(
        '{"email":"fixture@example.invalid","password":" secret ","purpose_code":"CAREER"}'
    )
    account = credentials(path)
    assert account.password == " secret "
    assert "secret" not in repr(account) and "fixture@example.invalid" not in repr(account)


def test_streaming_responses_cannot_extend_the_overall_deadline():
    now = [0.0]

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(10):
                now[0] += 1
                yield b"x"

    budget = Deadline(3, clock=lambda: now[0])
    with SmokeSession(
        "https://example.invalid",
        budget,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=SlowStream())),
    ) as session:
        with pytest.raises(RolloutError, match="deadline-exceeded"):
            session.request("GET", "/login", 200)
    assert now[0] == 3
