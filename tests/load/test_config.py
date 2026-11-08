from urllib.parse import quote

import pytest
from sqlalchemy.engine import make_url
from tools.load.config import LoadConfig, LoadSafetyError, validate_load_config


def valid_config(**overrides):
    values = {
        "target_url": "https://candidate.example.invalid",
        "release_sha": "a" * 40,
        "database_url": "mysql+asyncmy://load:secret@localhost/prl_load_" + "b" * 32,
        "opt_in": "I_UNDERSTAND_THIS_WRITES_TO_AN_ISOLATED_LAB_SCHEMA",
        "duration_seconds": 600,
        "sessions": 20,
    }
    values.update(overrides)
    return LoadConfig(**values)


def test_validated_config_parses_database_url_and_standard_workload():
    config = validate_load_config(valid_config())

    assert config.duration_seconds == 600
    assert config.sessions == 20
    assert make_url(config.database_url).database == "prl_load_" + "b" * 32


@pytest.mark.parametrize(
    "overrides",
    [
        {"opt_in": ""},
        {"target_url": "http://candidate.example.invalid"},
        {"target_url": "https://user:secret@candidate.example.invalid"},
        {"target_url": "https://candidate.example.invalid/?token=secret"},
        {"release_sha": "development"},
        {"database_url": "mysql+asyncmy://load:secret@localhost/shared"},
        {"sessions": 21},
        {"duration_seconds": 601},
    ],
)
def test_invalid_or_unsafe_config_is_rejected(overrides):
    with pytest.raises(LoadSafetyError):
        validate_load_config(valid_config(**overrides))


def test_local_http_target_is_allowed_only_for_loopback():
    assert (
        validate_load_config(valid_config(target_url="http://127.0.0.1:8443")).target_url
        == "http://127.0.0.1:8443"
    )
    with pytest.raises(LoadSafetyError):
        validate_load_config(valid_config(target_url="http://10.0.0.10:8443"))


def test_database_url_redaction_removes_password():
    url = "mysql+asyncmy://load:" + quote("very-secret") + "@localhost/prl_load_" + "b" * 32
    config = validate_load_config(valid_config(database_url=url))

    assert "very-secret" not in config.safe_database_label
    assert config.safe_database_label == "mysql+asyncmy://load:***@localhost/prl_load_" + "b" * 32
