"""Validated process configuration; error messages never include supplied values."""

import re
from collections.abc import Callable
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from math import isfinite
from typing import Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(ValueError):
    """Startup configuration failed validation."""


def _valid_hostname(host: str) -> bool:
    try:
        ip_address(host)
        return True
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_host) > 253:
        return False
    labels = ascii_host.removesuffix(".").split(".")
    return all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is not None
        for label in labels
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = Field(alias="DATABASE_URL", repr=False)
    application_origin: str = Field(alias="APPLICATION_ORIGIN")
    cookie_secret: SecretStr = Field(alias="COOKIE_SECRET", repr=False)
    csrf_secret: SecretStr = Field(alias="CSRF_SECRET", repr=False)
    accepted_schema_revisions_raw: str = Field(alias="ACCEPTED_SCHEMA_REVISIONS", repr=False)
    trusted_gateway_cidrs_raw: str = Field(default="", alias="TRUSTED_GATEWAY_CIDRS", repr=False)
    environment: Literal["local", "test", "demo", "production"] = Field(
        default="local", alias="ENVIRONMENT"
    )
    cookie_secure: bool = Field(default=True, alias="COOKIE_SECURE")
    cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax", alias="COOKIE_SAMESITE"
    )
    cookie_domain: str | None = Field(default=None, alias="COOKIE_DOMAIN")
    session_lifetime_seconds: int = Field(
        default=86400, gt=0, le=2592000, alias="SESSION_LIFETIME_SECONDS"
    )
    login_max_body_bytes: int = Field(default=4096, gt=0, alias="LOGIN_MAX_BODY_BYTES")
    api_max_body_bytes: int = Field(default=32768, gt=0, alias="API_MAX_BODY_BYTES")
    login_ip_attempts: int = Field(default=30, gt=0, alias="LOGIN_IP_ATTEMPTS")
    login_email_attempts: int = Field(default=5, gt=0, alias="LOGIN_EMAIL_ATTEMPTS")
    login_window_seconds: int = Field(default=60, gt=0, alias="LOGIN_WINDOW_SECONDS")
    login_state_max_entries: int = Field(default=10000, gt=0, alias="LOGIN_STATE_MAX_ENTRIES")
    password_max_concurrency: int = Field(default=2, gt=0, alias="PASSWORD_MAX_CONCURRENCY")
    db_pool_size: int = Field(default=5, gt=0, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, ge=0, alias="DB_MAX_OVERFLOW")
    db_connect_timeout_seconds: float = Field(default=1, gt=0, alias="DB_CONNECT_TIMEOUT_SECONDS")
    db_pool_timeout_seconds: float = Field(default=0.5, gt=0, alias="DB_POOL_TIMEOUT_SECONDS")
    db_query_timeout_seconds: float = Field(default=1, gt=0, alias="DB_QUERY_TIMEOUT_SECONDS")
    db_lock_timeout_seconds: float = Field(default=0.5, gt=0, alias="DB_LOCK_TIMEOUT_SECONDS")
    request_timeout_seconds: float = Field(default=3, gt=0, alias="REQUEST_TIMEOUT_SECONDS")
    readiness_timeout_seconds: float = Field(default=2, gt=0, alias="READINESS_TIMEOUT_SECONDS")
    max_deadlock_retries: int = Field(default=2, ge=0, alias="MAX_DEADLOCK_RETRIES")
    worker_poll_interval_seconds: float = Field(
        default=5, gt=0, alias="WORKER_POLL_INTERVAL_SECONDS"
    )
    worker_batch_size: int = Field(default=100, gt=0, le=1000, alias="WORKER_BATCH_SIZE")
    worker_replica_slot: int = Field(default=1, ge=1, le=8, alias="WORKER_REPLICA_SLOT")
    worker_grace_seconds: float = Field(default=30, gt=0, alias="WORKER_GRACE_SECONDS")
    worker_heartbeat_interval_seconds: float = Field(
        default=15, gt=0, alias="WORKER_HEARTBEAT_INTERVAL_SECONDS"
    )
    telemetry_enabled: bool = Field(default=False, alias="TELEMETRY_ENABLED")
    telemetry_otlp_endpoint: str = Field(
        default="http://collector:4318", alias="TELEMETRY_OTLP_ENDPOINT"
    )
    service_release: str = Field(default="unknown", alias="SERVICE_RELEASE")
    telemetry_trace_queue_capacity: int = Field(
        default=512, gt=0, le=512, alias="TELEMETRY_TRACE_QUEUE_CAPACITY"
    )
    telemetry_log_queue_capacity: int = Field(
        default=256, gt=0, le=256, alias="TELEMETRY_LOG_QUEUE_CAPACITY"
    )
    telemetry_max_batch_size: int = Field(default=32, gt=0, le=32, alias="TELEMETRY_MAX_BATCH_SIZE")
    telemetry_max_item_bytes: int = Field(
        default=8192, gt=0, le=8192, alias="TELEMETRY_MAX_ITEM_BYTES"
    )
    telemetry_max_span_attributes: int = Field(
        default=16, gt=0, le=16, alias="TELEMETRY_MAX_SPAN_ATTRIBUTES"
    )
    telemetry_max_attribute_chars: int = Field(
        default=128, ge=13, le=128, alias="TELEMETRY_MAX_ATTRIBUTE_CHARS"
    )
    telemetry_max_spans_per_request: int = Field(
        default=64, gt=0, le=64, alias="TELEMETRY_MAX_SPANS_PER_REQUEST"
    )
    telemetry_network_timeout_seconds: float = Field(
        default=0.5, gt=0, le=0.5, alias="TELEMETRY_NETWORK_TIMEOUT_SECONDS"
    )
    telemetry_item_max_age_seconds: float = Field(
        default=1, gt=0, le=1, alias="TELEMETRY_ITEM_MAX_AGE_SECONDS"
    )

    @field_validator("database_url")
    @classmethod
    def require_mysql_driver(cls, value: str) -> str:
        try:
            url = make_url(value)
        except (ValueError, TypeError, ArgumentError) as exc:
            raise ValueError("Invalid database URL") from exc
        if url.drivername != "mysql+asyncmy" or not url.database:
            raise ValueError("DATABASE_URL must use mysql+asyncmy and include a database")
        return value

    @field_validator(
        "db_connect_timeout_seconds",
        "db_pool_timeout_seconds",
        "db_query_timeout_seconds",
        "db_lock_timeout_seconds",
        "request_timeout_seconds",
        "readiness_timeout_seconds",
        "worker_poll_interval_seconds",
        "worker_grace_seconds",
        "worker_heartbeat_interval_seconds",
        "telemetry_network_timeout_seconds",
        "telemetry_item_max_age_seconds",
    )
    @classmethod
    def require_finite_timeout(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("Timeout must be finite")
        return value

    @field_validator("telemetry_otlp_endpoint")
    @classmethod
    def require_otlp_endpoint(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            valid_port = parsed.port is None or 0 < parsed.port < 65536
        except ValueError as exc:
            raise ValueError("Invalid TELEMETRY_OTLP_ENDPOINT") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not _valid_hostname(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not valid_port
        ):
            raise ValueError("TELEMETRY_OTLP_ENDPOINT must be an HTTP(S) base URL")
        return value.rstrip("/")

    @field_validator("service_release")
    @classmethod
    def require_safe_release(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value):
            raise ValueError("Invalid SERVICE_RELEASE")
        return value

    @field_validator("application_origin")
    @classmethod
    def require_origin(cls, value: str) -> str:
        if any(
            character.isspace() or ord(character) < 32 or character == "\\" for character in value
        ):
            raise ValueError("Invalid APPLICATION_ORIGIN")
        try:
            parsed = urlsplit(value)
            valid_port = parsed.port is None or 0 < parsed.port < 65536
        except ValueError as exc:
            raise ValueError("Invalid APPLICATION_ORIGIN") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not _valid_hostname(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or "?" in value
            or "#" in value
            or parsed.query
            or parsed.fragment
            or not valid_port
        ):
            raise ValueError("APPLICATION_ORIGIN must be an HTTP(S) origin")
        return value.rstrip("/")

    @field_validator("cookie_secret", "csrf_secret")
    @classmethod
    def require_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Secret must be nonempty")
        return value

    @field_validator("accepted_schema_revisions_raw")
    @classmethod
    def require_revisions(cls, value: str) -> str:
        revisions = [part.strip() for part in value.split(",")]
        if not revisions or any(
            not re.fullmatch(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", revision) for revision in revisions
        ):
            raise ValueError("ACCEPTED_SCHEMA_REVISIONS must contain revision IDs")
        return value

    @field_validator("trusted_gateway_cidrs_raw")
    @classmethod
    def require_cidrs(cls, value: str) -> str:
        try:
            for part in value.split(","):
                if part.strip():
                    ip_network(part.strip(), strict=False)
                elif value.strip():
                    raise ValueError("Empty CIDR")
        except ValueError as exc:
            raise ValueError("Invalid TRUSTED_GATEWAY_CIDRS") from exc
        return value

    @field_validator("cookie_domain")
    @classmethod
    def require_cookie_domain(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if any(
            character.isspace() or ord(character) < 32 or character == "\\" for character in value
        ):
            raise ValueError("Invalid COOKIE_DOMAIN")
        parsed = urlsplit("//" + value)
        if (
            parsed.hostname != value
            or not _valid_hostname(value)
            or parsed.port is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("COOKIE_DOMAIN must be a hostname")
        return value

    @model_validator(mode="after")
    def require_secure_cookie_policy(self) -> Self:
        if self.cookie_domain is not None or self.cookie_samesite != "lax":
            raise ValueError("Authentication requires host-only SameSite=Lax cookies")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None requires secure cookies")
        if self.environment in {"demo", "production"}:
            if not self.cookie_secure or not self.application_origin.startswith("https://"):
                raise ValueError("Demo and production require HTTPS and secure cookies")
        return self

    @property
    def accepted_schema_revisions(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.accepted_schema_revisions_raw.split(","))

    @property
    def trusted_gateway_cidrs(self) -> tuple[IPv4Network | IPv6Network, ...]:
        if not self.trusted_gateway_cidrs_raw.strip():
            return ()
        return tuple(
            ip_network(part.strip(), strict=False)
            for part in self.trusted_gateway_cidrs_raw.split(",")
        )


def load_settings() -> Settings:
    """Read process settings and report only invalid field names on failure."""
    try:
        # BaseSettings supplies required fields from env/.env at runtime.
        return cast(Callable[[], Settings], Settings)()
    except ValidationError as exc:
        fields = sorted(
            {str(error["loc"][0]) if error["loc"] else "policy" for error in exc.errors()}
        )
        raise ConfigurationError("Invalid configuration fields: " + ", ".join(fields)) from None
