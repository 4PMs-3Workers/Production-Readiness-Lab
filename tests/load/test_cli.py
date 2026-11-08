import pytest
from tools.load import cli
from tools.load.config import OPT_IN_PHRASE, LoadSafetyError


def test_missing_write_opt_in_fails_before_async_setup(monkeypatch):
    monkeypatch.setenv(
        "PRL_LOAD_DATABASE_URL",
        "mysql+asyncmy://load:secret@localhost/prl_load_" + "b" * 32,
    )
    monkeypatch.delenv("PRL_LOAD_TEST_OPT_IN", raising=False)
    called = False

    def run_async(_coroutine):
        nonlocal called
        called = True
        raise AssertionError("no setup or network work is allowed before opt-in")

    monkeypatch.setattr(cli.asyncio, "run", run_async)
    with pytest.raises(LoadSafetyError, match="opt-in"):
        cli.main(
            [
                "prepare",
                "--target-url",
                "https://candidate.example.invalid",
                "--release-sha",
                "a" * 40,
                "--run-id",
                "b" * 32,
            ]
        )
    assert called is False


def test_database_schema_must_match_run_id_before_network_or_writes(monkeypatch):
    monkeypatch.setenv(
        "PRL_LOAD_DATABASE_URL",
        "mysql+asyncmy://load:secret@localhost/prl_load_" + "c" * 32,
    )
    monkeypatch.setenv("PRL_LOAD_TEST_OPT_IN", OPT_IN_PHRASE)
    called = False

    def run_async(_coroutine):
        nonlocal called
        called = True
        raise AssertionError("schema mismatch must fail before setup")

    monkeypatch.setattr(cli.asyncio, "run", run_async)
    with pytest.raises(LoadSafetyError, match="match --run-id"):
        cli.main(
            [
                "prepare",
                "--target-url",
                "https://candidate.example.invalid",
                "--release-sha",
                "a" * 40,
                "--run-id",
                "b" * 32,
            ]
        )
    assert called is False


def test_candidate_release_must_match_local_migration_source(monkeypatch):
    monkeypatch.setattr(
        cli,
        "collect_provenance",
        lambda _root: {
            "checkout_source_sha": "a" * 40,
            "checkout_has_uncommitted_changes": False,
        },
    )
    cli._require_matching_checkout("a" * 40)
    with pytest.raises(LoadSafetyError, match="checkout must be clean"):
        cli._require_matching_checkout("b" * 40)
    monkeypatch.setattr(
        cli,
        "collect_provenance",
        lambda _root: {
            "checkout_source_sha": "a" * 40,
            "checkout_has_uncommitted_changes": True,
        },
    )
    with pytest.raises(LoadSafetyError, match="checkout must be clean"):
        cli._require_matching_checkout("a" * 40)


def test_missing_prometheus_url_fails_before_async_setup(monkeypatch):
    monkeypatch.setenv(
        "PRL_LOAD_DATABASE_URL",
        "mysql+asyncmy://load:secret@localhost/prl_load_" + "b" * 32,
    )
    monkeypatch.setenv("PRL_LOAD_TEST_OPT_IN", OPT_IN_PHRASE)
    monkeypatch.setenv("PRL_LOAD_LOKI_URL", "https://loki.example.invalid")
    monkeypatch.delenv("PRL_LOAD_PROMETHEUS_URL", raising=False)
    called = False

    def run_async(_coroutine):
        nonlocal called
        called = True
        raise AssertionError("required telemetry must be checked before setup")

    monkeypatch.setattr(cli.asyncio, "run", run_async)
    with pytest.raises(LoadSafetyError, match="PRL_LOAD_PROMETHEUS_URL"):
        cli.main(
            [
                "run",
                "--target-url",
                "https://candidate.example.invalid",
                "--release-sha",
                "a" * 40,
                "--run-id",
                "b" * 32,
            ]
        )
    assert called is False
