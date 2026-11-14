import pytest
from tools.load.account_lifecycle import (
    LifecycleLoadError,
    percentile,
    validate_origin,
    validate_target_inventory,
)


def test_nearest_rank_p95_is_deterministic():
    assert percentile([10.0, 40.0, 20.0, 30.0], 0.95) == 40.0


def test_percentile_rejects_empty_samples():
    with pytest.raises(ValueError):
        percentile([], 0.95)


def test_target_inventory_requires_unique_accounts_and_admin_sessions():
    rows = [
        {
            "id": f"00000000-0000-0000-0000-{index + 1:012d}",
            "expected_updated_at": "2026-11-14T12:00:00Z",
            "session_cookie": f"session-{index}",
            "csrf_cookie": f"csrf-cookie-{index}",
            "csrf_token": f"csrf-token-{index}",
        }
        for index in range(120)
    ]

    selected = validate_target_inventory(rows)

    assert len(selected) == 120
    with pytest.raises(LifecycleLoadError):
        validate_target_inventory(rows[:-1])
    rows[1]["session_cookie"] = rows[0]["session_cookie"]
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
