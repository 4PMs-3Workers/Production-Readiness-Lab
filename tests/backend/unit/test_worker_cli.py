import json
import logging
import signal
from unittest.mock import Mock

import pytest

from app.core.config import ConfigurationError
from app.worker import __main__ as cli
from app.worker.runtime import Shutdown
from app.worker.signals import install_signal_handlers


@pytest.fixture(autouse=True)
def restore_cli_logging():
    loggers = [
        logging.getLogger(name) for name in ("app.worker", "sqlalchemy", "asyncmy", "asyncio")
    ]
    previous = [
        (logger, logger.level, logger.propagate, logger.disabled, logger.handlers[:])
        for logger in loggers
    ]
    try:
        yield
    finally:
        for logger, level, propagate, disabled, handlers in previous:
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled
            for handler in logger.handlers:
                if handler not in handlers:
                    handler.close()
            logger.handlers[:] = handlers


def test_help_needs_no_environment_or_database(monkeypatch, capfd):
    load = Mock(side_effect=AssertionError("must not load configuration for --help"))
    monkeypatch.setattr(cli, "load_settings", load)
    with pytest.raises(SystemExit) as exit_status:
        cli.main(["--help"])
    assert exit_status.value.code == 0
    assert "--once" in capfd.readouterr().out
    load.assert_not_called()


def test_configuration_errors_never_publish_supplied_values(monkeypatch, capfd):
    monkeypatch.setattr(
        cli, "load_settings", Mock(side_effect=ConfigurationError("private-secret"))
    )
    assert cli.main([]) == 2
    captured = capfd.readouterr()
    assert "private-secret" not in captured.out + captured.err
    assert json.loads(captured.out)["reason"] == "CONFIGURATION_INVALID"


def test_invalid_arguments_are_sanitized_instead_of_argparse_echoing_them(capfd):
    assert cli.main(["--private-secret-argument"]) == 2
    captured = capfd.readouterr()
    assert "private-secret" not in captured.out + captured.err
    assert json.loads(captured.out)["reason"] == "CONFIGURATION_INVALID"


def test_cli_reenables_its_logger_after_external_logging_reconfiguration(capfd):
    logger = logging.getLogger("app.worker")
    logger.disabled = True
    assert cli.main(["--invalid"]) == 2
    captured = capfd.readouterr()
    assert logger.disabled is False
    assert json.loads(captured.out)["reason"] == "CONFIGURATION_INVALID"


async def test_cli_output_failure_is_counted_without_logging_internal_exception_details(capfd):
    from app.worker.models import WorkerIdentity, WorkerState
    from app.worker.reporting import Reporter

    def failed_output(value):
        raise RuntimeError("private-output-failure")

    cli.configure_logging()
    state = WorkerState(WorkerIdentity().worker_id)
    async with Reporter(failed_output) as reports:
        assert reports.emit("heartbeat", state)
    assert state.reporting_failures == 1
    assert capfd.readouterr().err == ""


@pytest.mark.parametrize("fallback", [False, True])
async def test_signals_request_stop_and_restore_previous_handlers(monkeypatch, fallback):
    import app.worker.signals as module

    loop = Mock()
    if fallback:
        loop.add_signal_handler.side_effect = NotImplementedError
    loop.call_soon_threadsafe.side_effect = lambda callback: callback()
    previous = Mock()
    set_signal = Mock()
    monkeypatch.setattr(module.signal, "getsignal", Mock(return_value=previous))
    monkeypatch.setattr(module.signal, "signal", set_signal)
    shutdown = Shutdown(1)
    with install_signal_handlers(shutdown, loop=loop):
        if fallback:
            handler = set_signal.call_args_list[0].args[1]
            handler(signal.SIGTERM, None)
        else:
            callback = loop.add_signal_handler.call_args_list[0].args[1]
            callback()
        assert shutdown.stop.is_set()
    restored = [call for call in set_signal.call_args_list if call.args[1] is previous]
    assert len(restored) == 2
