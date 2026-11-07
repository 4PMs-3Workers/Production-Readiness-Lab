import json

import pytest
from tools.backup.offhost_contract import (
    Credentials,
    RemoteConfig,
    RemoteError,
    canonical,
    contract,
    load_config,
    point_key,
    private_file,
)


def configuration(**changes):
    return RemoteConfig.model_validate(
        dict(
            endpoint="https://store.example:9000",
            region="us-east-1",
            bucket="prl-backups",
            prefix="managed-backups",
            environment="test",
            store="primary",
            source_id="a" * 64,
            recipient="age1" + "a" * 58,
            **changes,
        )
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "http://store.example"),
        ("endpoint", "https://secret@store.example"),
        ("endpoint", "https://store.example/path"),
        ("endpoint", "https://store.example?q=x"),
        ("prefix", "../outside"),
        ("environment", "arbitrary/private/path"),
        ("source_id", "not-a-digest"),
        ("recipient", "passphrase"),
        ("max_seconds", 0),
        ("max_seconds", True),
        ("attempts", 100),
        ("max_bytes", 5 * 1024**3),
        ("keep_count", 0),
        ("format_version", True),
    ],
)
def test_invalid_or_unsafe_configuration_is_refused(field, value):
    data = configuration().model_dump()
    data[field] = value
    with pytest.raises(ValueError):
        RemoteConfig.model_validate(data)


def test_identity_is_stable_across_operational_limits_but_scopes_every_source():
    config = configuration()
    other = RemoteConfig.model_validate(dict(config.model_dump(), max_seconds=30, keep_count=1))
    assert other.identity == config.identity
    other = RemoteConfig.model_validate(dict(config.model_dump(), source_id="b" * 64))
    assert other.scope != config.scope and other.identity != config.identity
    key = point_key(config, "prl-backup-20261001T000000Z-" + "a" * 32)
    assert key.startswith(config.scope + "points/")
    with pytest.raises(RemoteError):
        point_key(config, "../outside")


def test_duplicate_fields_secrets_and_missing_inputs_are_not_accepted(tmp_path):
    raw = canonical(configuration())
    with pytest.raises(RemoteError):
        contract(raw[:-1] + b',"backend":"s3-age"}', RemoteConfig)
    path = tmp_path / "config.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    assert load_config(path) == configuration()
    with pytest.raises(RemoteError):
        private_file(tmp_path / "missing", 1024)
    credentials = contract(
        json.dumps(dict(access_key="example", secret_key="private-secret")).encode(), Credentials
    )
    assert "private-secret" not in repr(credentials)
    assert "store.example" not in repr(configuration())
