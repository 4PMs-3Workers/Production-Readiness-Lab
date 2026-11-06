from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .state import StateStore

_CHECK_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MAX_CONFIG_BYTES = 65536
_MONITOR_KEYS = {
    "interval_seconds",
    "timeout_seconds",
    "connect_timeout_seconds",
    "read_timeout_seconds",
    "failure_threshold",
    "recovery_threshold",
    "state_path",
    "max_state_bytes",
    "max_pending_notifications",
    "delivery_timeout_seconds",
    "delivery_cycle_timeout_seconds",
    "delivery_retries",
    "ca_bundle_path",
}
_NOTIFICATION_KEYS = {"webhook_url", "token_env"}
_CHECK_KEYS = {"name", "url", "expected_statuses", "max_response_bytes"}


@dataclass(frozen=True)
class CheckConfig:
    name: str
    url: str
    expected_statuses: frozenset[int]
    max_response_bytes: int


@dataclass(frozen=True)
class MonitorConfig:
    checks: tuple[CheckConfig, ...]
    interval_seconds: int
    timeout_seconds: int
    connect_timeout_seconds: int
    read_timeout_seconds: int
    failure_threshold: int
    recovery_threshold: int
    state_path: Path
    ca_bundle_path: Path | None
    max_state_bytes: int
    max_pending_notifications: int
    delivery_timeout_seconds: int
    delivery_cycle_timeout_seconds: int
    delivery_retries: int
    notification_url: str
    token_env: str | None


def _bounded_int(table: dict, name: str, default: int, low: int, high: int) -> int:
    value = table.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("Monitor configuration is invalid")
    return value


def _validate_url(value: object, *, allow_loopback_http: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or not value.isascii()
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Monitor configuration is invalid")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("Monitor configuration is invalid") from None
    if (
        parsed.scheme not in ({"https", "http"} if allow_loopback_http else {"https"})
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or "\\" in parsed.path
    ):
        raise ValueError("Monitor configuration is invalid")
    if parsed.scheme == "http":
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = host.lower() == "localhost"
        if not allow_loopback_http or not is_loopback:
            raise ValueError("Monitor configuration is invalid")
    return value


def load_config(path: str | Path) -> MonitorConfig:
    config_path = Path(path)
    try:
        raw = config_path.read_bytes()
        if not raw or len(raw) > _MAX_CONFIG_BYTES:
            raise ValueError("configuration size")
        data = tomllib.loads(raw.decode("utf-8"))
        monitor = data["monitor"]
        notification = data["notification"]
        raw_checks = data["checks"]
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError):
        raise ValueError("Monitor configuration is invalid") from None
    if (
        set(data) != {"monitor", "notification", "checks"}
        or not isinstance(monitor, dict)
        or not isinstance(notification, dict)
        or not isinstance(raw_checks, list)
        or set(monitor) - _MONITOR_KEYS
        or set(notification) - _NOTIFICATION_KEYS
        or not 1 <= len(raw_checks) <= 8
    ):
        raise ValueError("Monitor configuration is invalid")
    interval = _bounded_int(monitor, "interval_seconds", 30, 5, 300)
    timeout = _bounded_int(monitor, "timeout_seconds", 10, 1, 30)
    connect = _bounded_int(monitor, "connect_timeout_seconds", 4, 1, timeout)
    read = _bounded_int(monitor, "read_timeout_seconds", 4, 1, timeout)
    failure_threshold = _bounded_int(monitor, "failure_threshold", 3, 1, 10)
    recovery_threshold = _bounded_int(monitor, "recovery_threshold", 2, 1, 10)
    max_state_bytes = _bounded_int(monitor, "max_state_bytes", 65536, 4096, 1048576)
    max_pending = _bounded_int(monitor, "max_pending_notifications", 32, 1, 64)
    delivery_timeout = _bounded_int(monitor, "delivery_timeout_seconds", 5, 1, 15)
    delivery_cycle_timeout = _bounded_int(monitor, "delivery_cycle_timeout_seconds", 30, 1, 60)
    delivery_retries = _bounded_int(monitor, "delivery_retries", 2, 0, 5)
    maximum_cycle = max(interval, timeout + delivery_cycle_timeout)
    detection_bound = failure_threshold * maximum_cycle + timeout
    if connect + read > timeout or detection_bound > 180:
        raise ValueError("Monitor configuration is invalid")

    checks: list[CheckConfig] = []
    seen: set[str] = set()
    for item in raw_checks:
        if not isinstance(item, dict) or set(item) - _CHECK_KEYS:
            raise ValueError("Monitor configuration is invalid")
        name = item.get("name")
        if not isinstance(name, str) or not _CHECK_NAME.fullmatch(name) or name in seen:
            raise ValueError("Monitor configuration is invalid")
        statuses = item.get("expected_statuses", [200])
        if (
            not isinstance(statuses, list)
            or not 1 <= len(statuses) <= 10
            or any(
                isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599
                for status in statuses
            )
        ):
            raise ValueError("Monitor configuration is invalid")
        response_limit = item.get("max_response_bytes", 4096)
        if (
            isinstance(response_limit, bool)
            or not isinstance(response_limit, int)
            or not 1 <= response_limit <= 65536
        ):
            raise ValueError("Monitor configuration is invalid")
        checks.append(
            CheckConfig(
                name=name,
                url=_validate_url(item.get("url")),
                expected_statuses=frozenset(statuses),
                max_response_bytes=response_limit,
            )
        )
        seen.add(name)

    if max_pending < len(checks):
        raise ValueError("Monitor configuration is invalid")

    if max_state_bytes < StateStore.worst_case_bytes(max_pending):
        raise ValueError("Monitor configuration is invalid")

    webhook_url = _validate_url(notification.get("webhook_url"), allow_loopback_http=True)
    token_env = notification.get("token_env")
    if token_env is not None and (
        not isinstance(token_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", token_env)
    ):
        raise ValueError("Monitor configuration is invalid")
    state_value = monitor.get("state_path", "state.json")
    if not isinstance(state_value, str) or not state_value or len(state_value) > 512:
        raise ValueError("Monitor configuration is invalid")
    state_path = Path(state_value)
    if not state_path.is_absolute():
        state_path = config_path.parent / state_path
    ca_bundle_value = monitor.get("ca_bundle_path")
    ca_bundle_path = None
    if ca_bundle_value is not None:
        if not isinstance(ca_bundle_value, str) or not Path(ca_bundle_value).is_absolute():
            raise ValueError("Monitor configuration is invalid")
        ca_bundle_path = Path(ca_bundle_value)
        if not ca_bundle_path.is_file():
            raise ValueError("Monitor configuration is invalid")
    return MonitorConfig(
        tuple(checks),
        interval,
        timeout,
        connect,
        read,
        failure_threshold,
        recovery_threshold,
        state_path,
        ca_bundle_path,
        max_state_bytes,
        max_pending,
        delivery_timeout,
        delivery_cycle_timeout,
        delivery_retries,
        webhook_url,
        token_env,
    )
