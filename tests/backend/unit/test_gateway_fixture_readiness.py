import httpx
import pytest
from tests.containers import test_gateway


@pytest.mark.parametrize("late_role", ["api", "frontend"])
def test_gateway_readiness_waits_for_both_upstreams(monkeypatch, late_role):
    monkeypatch.setattr(test_gateway.time, "sleep", lambda _: None)
    attempts = {"api": 0, "frontend": 0}
    successful = set()

    def upstream(request):
        role = "api" if request.url.path == "/api/v1/probe" else "frontend"
        attempts[role] += 1
        if role == late_role and attempts[role] < 3:
            return httpx.Response(502, json={"error": {"code": "UPSTREAM_UNAVAILABLE"}})
        successful.add(role)
        return httpx.Response(200, json={"role": role})

    with httpx.Client(
        base_url="http://gateway.test", transport=httpx.MockTransport(upstream)
    ) as client:
        test_gateway.wait_for_gateway_upstreams(client)

    assert successful == {"api", "frontend"}
    assert attempts[late_role] == 3


@pytest.mark.parametrize("failure", ["unavailable", "wrong-role", "invalid-json", "transport"])
def test_gateway_readiness_rejects_unready_frontend_without_exposing_body(monkeypatch, failure):
    monkeypatch.setattr(test_gateway.time, "sleep", lambda _: None)
    frontend_attempts = 0

    def upstream(request):
        nonlocal frontend_attempts
        if request.url.path == "/api/v1/probe":
            return httpx.Response(200, json={"role": "api"})
        frontend_attempts += 1
        if failure == "transport":
            raise httpx.ConnectError("private-connection-detail", request=request)
        if failure == "invalid-json":
            return httpx.Response(200, text="private-response-body")
        if failure == "wrong-role":
            return httpx.Response(200, json={"role": "api", "detail": "private-response-body"})
        return httpx.Response(502, json={"error": {"detail": "private-response-body"}})

    with httpx.Client(
        base_url="http://gateway.test", transport=httpx.MockTransport(upstream)
    ) as client:
        with pytest.raises(pytest.fail.Exception) as caught:
            test_gateway.wait_for_gateway_upstreams(client)

    assert 1 <= frontend_attempts <= 60
    assert "frontend" in str(caught.value)
    assert "private-" not in str(caught.value)


def test_gateway_readiness_rejects_success_after_startup_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(test_gateway.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(test_gateway.time, "sleep", lambda _: None)

    def slow_upstream(request):
        now[0] += 10
        role = "api" if request.url.path == "/api/v1/probe" else "frontend"
        return httpx.Response(200, json={"role": role})

    with httpx.Client(
        base_url="http://gateway.test", transport=httpx.MockTransport(slow_upstream)
    ) as client:
        with pytest.raises(pytest.fail.Exception, match="deadline"):
            test_gateway.wait_for_gateway_upstreams(client)
