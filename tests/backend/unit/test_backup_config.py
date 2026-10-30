import pytest

from app.backup.config import BackupError, Reason, database_name, identifier, load_config


def values(tmp_path):
    return {
        "DATABASE_URL": "mysql+asyncmy://backup:private@localhost/prl",
        "BACKUP_STORE": str(tmp_path),
    }


def test_backup_configuration_uses_explicit_private_credentials_and_bounded_defaults(tmp_path):
    config = load_config(values(tmp_path))
    assert config.keep_count >= 1 and config.max_seconds <= 3600
    assert "private" not in repr(config) and "mysql+asyncmy" not in repr(config)
    assert (
        database_name(config.database_url) == "prl"
        and identifier("audit_events") == "`audit_events`"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("DATABASE_URL", "mysql+asyncmy://x:secret@host/mysql"),
        ("DATABASE_URL", "sqlite:///private"),
        ("BACKUP_STORE", "relative"),
        ("BACKUP_KEEP_COUNT", "0"),
        ("BACKUP_MAX_SECONDS", "3601"),
        ("BACKUP_SOURCE_SHA", "private-not-a-commit"),
        ("BACKUP_IMAGE_ID", "password"),
    ],
)
def test_invalid_recovery_settings_never_echo_supplied_values(tmp_path, key, value):
    env = values(tmp_path)
    env[key] = value
    with pytest.raises(BackupError) as error:
        load_config(env)
    assert str(error.value) == Reason.CONFIGURATION


@pytest.mark.parametrize("name", ["x`", "x.y", "x;DROP", "", "../source"])
def test_unreviewed_sql_identifiers_are_rejected(name):
    with pytest.raises(BackupError):
        identifier(name)
