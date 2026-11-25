from pathlib import Path

from dotenv import dotenv_values

from app.core.config import Settings


def test_candidate_profile_uses_real_settings_without_changing_defaults(auth_settings):
    path = Path(__file__).resolve().parents[3] / "tools/deploy/profiles/nfr03-candidate.env.example"
    candidate = Settings.model_validate(
        {**auth_settings.model_dump(by_alias=True), **dotenv_values(path)}
    )
    default = auth_settings
    assert (
        candidate.db_pool_size,
        candidate.db_max_overflow,
        candidate.db_pool_timeout_seconds,
    ) == (40, 0, 0.75)
    assert (default.db_pool_size, default.db_max_overflow, default.db_pool_timeout_seconds) == (
        5,
        5,
        0.5,
    )
    assert candidate.db_query_timeout_seconds == default.db_query_timeout_seconds == 1
    assert candidate.request_timeout_seconds == default.request_timeout_seconds == 3
