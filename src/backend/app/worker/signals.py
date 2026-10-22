"""Portable signal registration, restoring the caller's previous handlers."""

import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager

from app.worker.runtime import Shutdown


@contextmanager
def install_signal_handlers(
    shutdown: Shutdown, *, loop: asyncio.AbstractEventLoop | None = None
) -> Iterator[None]:
    active_loop = loop or asyncio.get_running_loop()
    installed = []

    def dispatch(signum, frame):
        active_loop.call_soon_threadsafe(shutdown.request_stop)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(signum)
            try:
                active_loop.add_signal_handler(signum, shutdown.request_stop)
                uses_loop = True
            except NotImplementedError:
                signal.signal(signum, dispatch)
                uses_loop = False
            installed.append((signum, previous, uses_loop))
        yield
    finally:
        for signum, previous, uses_loop in reversed(installed):
            if uses_loop:
                active_loop.remove_signal_handler(signum)
            signal.signal(signum, previous)
