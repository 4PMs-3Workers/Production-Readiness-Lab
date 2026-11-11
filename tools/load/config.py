"""Strict configuration and opt-in guards for load runs that write application data."""

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy.engine import URL, make_url

OPT_IN_PHRASE = "I_UNDERSTAND_THIS_WRITES_TO_AN_ISOLATED_LAB_SCHEMA"
_RELEASE_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SCHEMA = re.compile(r"prl_load_[0-9a-f]{32}\Z")
_DATABASE_QUERY_OPTIONS = frozenset({"charset"})


class LoadSafetyError(ValueError):
    """A load run failed a preflight safety check."""


@dataclass(frozen=True)
class LoadConfig:
    target_url: str
    release_sha: str
    database_url: str = field(repr=False)
    opt_in: str = field(repr=False)
    duration_seconds: int = 600
    sessions: int = 20

    @property
    def schema_name(self) -> str:
        database = make_url(self.database_url).database
        if database is None:
            raise LoadSafetyError("Database URL must name a dedicated load schema")
        return database

    @property
    def safe_database_label(self) -> str:
        return make_url(self.database_url).render_as_string(hide_password=True)


def validate_load_config(config: LoadConfig) -> LoadConfig:
    """Validate all write-affecting settings before opening any network connection."""
    if config.opt_in != OPT_IN_PHRASE:
        raise LoadSafetyError("Explicit load-test opt-in is required")
    if _RELEASE_SHA.fullmatch(config.release_sha) is None:
        raise LoadSafetyError("Candidate release must be a 40-character lowercase source SHA")
    try:
        parsed = urlsplit(config.target_url)
        port = parsed.port
    except ValueError:
        raise LoadSafetyError("Target URL is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise LoadSafetyError("Target must be an origin URL without credentials or query data")
    if parsed.scheme != "https" and not _is_loopback(parsed.hostname):
        raise LoadSafetyError("Plain HTTP is allowed only for a loopback lab target")
    if port is not None and not 1 <= port <= 65535:
        raise LoadSafetyError("Target URL port is invalid")
    if not 1 <= config.sessions <= 20:
        raise LoadSafetyError("Session count must be between 1 and 20")
    if not 1 <= config.duration_seconds <= 600:
        raise LoadSafetyError("Duration must be between 1 and 600 seconds")
    try:
        database_url: URL = make_url(config.database_url)
    except (TypeError, ValueError):
        raise LoadSafetyError("Database URL is invalid") from None
    if (
        database_url.drivername != "mysql+asyncmy"
        or _SCHEMA.fullmatch(database_url.database or "") is None
    ):
        raise LoadSafetyError("Database URL must target a dedicated prl_load_<run UUID> schema")
    if not database_url.username or not database_url.host:
        raise LoadSafetyError("Database URL must include a user and host")
    if set(database_url.query) - _DATABASE_QUERY_OPTIONS:
        raise LoadSafetyError("Database URL contains unsupported options that can redirect writes")
    return config


def server_level_database_url(database_url: str) -> URL:
    """Build an admin URL that cannot inherit a schema from the configured tenant URL."""
    tenant = make_url(database_url)
    return URL.create(
        drivername=tenant.drivername,
        username=tenant.username,
        password=tenant.password,
        host=tenant.host,
        port=tenant.port,
        query=dict(tenant.query),
    )


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
