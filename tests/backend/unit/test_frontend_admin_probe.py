"""The test-only response probe must never fabricate an account write result."""

import pytest
from tools.scripts import frontend_e2e_backend as harness

ACCOUNT = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    ("method", "path", "matched"),
    [
        ("POST", "/api/v1/admin/accounts", True),
        ("PATCH", f"/api/v1/admin/accounts/{ACCOUNT}", True),
        ("POST", f"/api/v1/admin/accounts/{ACCOUNT}/password", True),
        ("GET", "/api/v1/admin/accounts", False),
        ("PATCH", "/api/v1/appointments", False),
        ("POST", f"/api/v1/admin/accounts/{ACCOUNT}", False),
        ("PATCH", f"/api/v1/admin/accounts/{ACCOUNT}/password", False),
        ("POST", "/api/v1/appointments", True),
    ],
)
@pytest.mark.parametrize("status", [200, 201, 409])
async def test_admin_probe_only_truncates_success_after_real_app_completed(
    tmp_path, monkeypatch, method, path, matched, status
):
    for key in ("LOSS", "HOLD", "HELD"):
        monkeypatch.setattr(harness, key, tmp_path / key)
    harness.LOSS.write_text("once", encoding="ascii")
    committed = False
    original = [
        {"type": "http.response.start", "status": status, "headers": [(b"content-length", b"7")]},
        {"type": "http.response.body", "body": b"receipt"},
    ]

    async def app(_scope, _receive, send):
        nonlocal committed
        for message in original:
            await send(message)
        committed = True

    sent = []

    async def send(message):
        assert committed or not matched
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    await harness.ProbeCommittedResponse(app)(
        {"type": "http", "method": method, "path": path}, receive, send
    )
    if matched and status in (200, 201):
        assert sent[0]["status"] == status
        assert sent[1]["body"] == b""
    else:
        assert sent == original
    assert harness.LOSS.exists() is not matched
