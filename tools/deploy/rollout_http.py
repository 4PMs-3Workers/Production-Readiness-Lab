"""Private operator-supplied smoke identity and bounded cookie/CSRF requests."""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from tools.deploy.release_manifest import ReleaseError, load_json
from tools.deploy.rollout_runtime import Deadline
from tools.deploy.rollout_state import RolloutError


@dataclass(frozen=True, repr=False)
class SmokeCredentials:
    email: str
    password: str
    purpose_code: str
    staff_id: str | None = None


def credentials(path: Path) -> SmokeCredentials:
    try:
        if not path.is_absolute() or path.is_symlink():
            raise ValueError()
        with path.open("rb") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            raise ValueError()
        value = load_json(raw)
        if not {"email", "password", "purpose_code"} <= set(value) or not set(value) <= {
            "email",
            "password",
            "purpose_code",
            "staff_id",
        }:
            raise ValueError()
        if any(not isinstance(value[key], str) for key in ("email", "password", "purpose_code")):
            raise ValueError()
        if (
            not 1 <= len(value["email"]) <= 254
            or not 1 <= len(value["password"]) <= 1024
            or any(ord(c) < 32 for c in value["email"] + value["password"])
            or not re.fullmatch(r"[A-Z0-9_]{1,64}", value["purpose_code"])
        ):
            raise ValueError()
        staff = value.get("staff_id")
        if staff is not None:
            staff = str(UUID(staff))
        return SmokeCredentials(value["email"], value["password"], value["purpose_code"], staff)
    except (OSError, ValueError, TypeError, ReleaseError):
        raise RolloutError("smoke-failed") from None


class SmokeSession:
    def __init__(
        self,
        origin: str,
        budget: Deadline,
        *,
        tls: ssl.SSLContext | bool = True,
        transport: httpx.BaseTransport | None = None,
    ):
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RolloutError("incompatible-configuration")
        self.origin, self.budget, self.csrf = origin.rstrip("/"), budget, ""
        self.client = httpx.Client(
            base_url=self.origin,
            verify=tls,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> SmokeSession:
        return self

    def __exit__(self, *args) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        status: int | tuple[int, ...],
        *,
        key: str | None = None,
        private: bool = False,
        **kwargs,
    ) -> httpx.Response:
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise RolloutError("smoke-failed")
        headers = {"Origin": self.origin, "Accept-Encoding": "identity"}
        if method not in {"GET", "HEAD"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            with self.client.stream(
                method, path, headers=headers, timeout=self.budget.remaining(5), **kwargs
            ) as response:
                if (
                    response.status_code not in ((status,) if isinstance(status, int) else status)
                    or response.headers.get("Content-Encoding", "identity") != "identity"
                ):
                    raise RolloutError("smoke-failed")
                chunks, size = [], 0
                incoming = (
                    [response.content] if response.is_stream_consumed else response.iter_raw()
                )
                for chunk in incoming:
                    self.budget.remaining()
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise RolloutError("smoke-failed")
                    chunks.append(chunk)
                self.budget.remaining()
                if private and (
                    response.headers.get("Cache-Control") != "private, no-store"
                    or not response.headers.get("X-Request-ID")
                ):
                    raise RolloutError("smoke-failed")
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=b"".join(chunks),
                    request=response.request,
                )
        except (httpx.HTTPError, ValueError):
            self.budget.remaining()
            raise RolloutError("smoke-failed") from None

    def login(self, account: SmokeCredentials) -> None:
        self.request(
            "POST",
            "/api/v1/auth/login",
            200,
            json=dict(email=account.email, password=account.password),
        )
        try:
            token = self.request("GET", "/api/v1/auth/csrf", 200).json()["csrf_token"]
            if not isinstance(token, str) or not 1 <= len(token) <= 2048:
                raise ValueError()
            self.csrf = token
        except (KeyError, TypeError, ValueError):
            raise RolloutError("smoke-failed") from None
