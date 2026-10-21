"""Run from the repository root: python -m app.worker [--once]."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from typing import NoReturn

from app.core.config import ConfigurationError, Settings, load_settings
from app.worker.models import FailureReason, WorkerIdentity, WorkerState
from app.worker.reporting import Reporter
from app.worker.runtime import Shutdown, run_worker
from app.worker.signals import install_signal_handlers


class UsageError(Exception):
    """Invalid CLI arguments; their values must not be echoed into diagnostics."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError()


class ReportStreamHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> NoReturn:
        # Let Reporter count output failures instead of logging printing a traceback.
        raise RuntimeError("Worker report output failed") from None


def configure_logging() -> None:
    logger = logging.getLogger("app.worker")
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = ReportStreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers[:] = [handler]
    # Database/loop exception payloads must not bypass the allowlisted worker reports.
    for name in ("sqlalchemy", "asyncmy", "asyncio"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)


async def serve(settings: Settings, *, once: bool) -> int:
    shutdown = Shutdown(settings.worker_grace_seconds)
    with install_signal_handlers(shutdown):
        return await run_worker(settings, shutdown=shutdown, once=once)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = Parser(
        description="Expire due appointment change proposals from durable database state."
    )
    parser.add_argument("--once", action="store_true", help="Process at most one batch and exit.")
    try:
        args = parser.parse_args(argv)
        settings = load_settings()
        return asyncio.run(serve(settings, once=args.once))
    except (ConfigurationError, UsageError):
        Reporter().emit(
            "fatal",
            WorkerState(WorkerIdentity().worker_id),
            reason=FailureReason.CONFIGURATION_INVALID,
        )
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        Reporter().emit(
            "fatal",
            WorkerState(WorkerIdentity().worker_id),
            reason=FailureReason.UNEXPECTED_FAILURE,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
