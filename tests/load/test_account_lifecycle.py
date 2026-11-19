from functools import partial

import httpx
import pytest
from tools.load.account_lifecycle import (
    LifecycleLoadError,
    percentile,
    run,
    validate_origin,
    validate_target_inventory,
)


def test_nearest_rank_p95_is_deterministic():
    assert percentile([10.0, 40.0, 20.0, 30.0], 0.95) == 40.0


def test_percentile_rejects_empty_samples():
    with pytest.raises(ValueError):
        percentile([], 0.95)


@pytest.fixture
def rows():
    return [
        {
            "id": f"00000000-0000-0000-0000-{index + 1:012d}",
            "expected_updated_at": "2026-11-14T12:00:00Z",
            "session_cookie": f"session-{index}",
            "csrf_cookie": f"csrf-cookie-{index}",
            "csrf_token": f"csrf-token-{index}",
        }
        for index in range(120)
    ]


def test_target_inventory_requires_unique_accounts_and_admin_sessions(rows):
    selected = validate_target_inventory(rows)

    assert len(selected) == 120
    with pytest.raises(LifecycleLoadError):
        validate_target_inventory(rows[:-1])
    rows[1]["session_cookie"] = rows[0]["session_cookie"]
    with pytest.raises(LifecycleLoadError):
        validate_target_inventory(rows)


def test_target_inventory_defaults_to_active_and_preserves_explicit_inactivity(rows):
    rows[1]["is_active"] = False

    selected = validate_target_inventory(rows)

    assert selected[0].get("is_active") is True
    assert selected[1].get("is_active") is False


@pytest.mark.parametrize("invalid", ["false", 0, None])
def test_target_inventory_rejects_non_boolean_active_state(rows, invalid):
    rows[0]["is_active"] = invalid

    with pytest.raises(LifecycleLoadError):
        validate_target_inventory(rows)


def test_load_origin_rejects_remote_plain_http_and_mismatched_origins():
    with pytest.raises(LifecycleLoadError):
        validate_origin("http://example.com", "http://example.com")
    with pytest.raises(LifecycleLoadError):
        validate_origin("https://api.example.com", "https://other.example.com")
    assert validate_origin("http://127.0.0.1:8000", "http://127.0.0.1:8000") == (
        "http://127.0.0.1:8000"
    )


def test_load_origin_rejects_fragment():
    with pytest.raises(LifecycleLoadError):
        validate_origin("https://api.example.com", "https://api.example.com/#fragment")


@pytest.mark.parametrize(
    ("origin", "original_active", "restored_active", "expected_failures"),
    [
        ("https://api.example.com/", True, True, 0),
        ("https://api.example.com", False, False, 0),
        ("https://api.example.com", False, True, 1),
    ],
)
async def test_load_sends_canonical_origin_and_checks_restored_activity(
    monkeypatch, rows, origin, original_active, restored_active, expected_failures
):
    rows[0]["is_active"] = original_active
    target = validate_target_inventory(rows)[0]
    target_path = f"/api/v1/admin/accounts/{target['id']}"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers["Origin"] != "https://api.example.com":
            return httpx.Response(403, json={"error": {"code": "INVALID_ORIGIN"}})
        if request.method == "DELETE" and request.url.path == target_path:
            return httpx.Response(
                200,
                json={
                    "updated_at": "2026-11-14T12:01:00Z",
                    "deleted_at": "2026-11-14T12:01:00Z",
                    "is_active": False,
                },
            )
        assert request.method == "POST" and request.url.path == f"{target_path}/restore"
        return httpx.Response(
            200,
            json={
                "updated_at": "2026-11-14T12:02:00Z",
                "deleted_at": None,
                "is_active": restored_active,
            },
        )

    client_factory = partial(httpx.AsyncClient, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    result = await run("https://api.example.com", origin, [target])

    assert result["completed_restore_requests"] == 1
    assert result["failures"] == expected_failures
