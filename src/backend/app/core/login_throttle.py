"""Per-process fixed windows with bounded state; no plaintext email retention."""

import math
import threading
from collections.abc import Callable
from time import monotonic

from app.api.auth_errors import auth_error
from app.core.config import Settings


class LoginThrottle:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = monotonic) -> None:
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._ips: dict[str, tuple[float, int]] = {}
        self._emails: dict[str, tuple[float, int]] = {}

    def _check(self, state: dict[str, tuple[float, int]], key: str, limit: int) -> None:
        with self._lock:
            now = self._clock()
            while state:
                oldest = next(iter(state))
                if state[oldest][0] > now:
                    break
                del state[oldest]
            if key not in state:
                if len(state) >= self._settings.login_state_max_entries:
                    raise auth_error(
                        "LOGIN_THROTTLED",
                        retry_after=math.ceil(next(iter(state.values()))[0] - now),
                    )
                state[key] = (now + self._settings.login_window_seconds, 0)
            expires, count = state[key]
            if count >= limit:
                raise auth_error("LOGIN_THROTTLED", retry_after=math.ceil(expires - now))
            state[key] = (expires, count + 1)

    def check_ip(self, peer: str) -> None:
        self._check(self._ips, peer, self._settings.login_ip_attempts)

    def check_email(self, identity: bytes) -> None:
        self._check(self._emails, identity.hex(), self._settings.login_email_attempts)
