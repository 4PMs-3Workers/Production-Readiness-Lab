from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class NotificationEvent:
    event_id: str
    check_name: str
    firing: bool
    occurred_at: str


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    outcome: str


class WebhookNotifier:
    def __init__(self, url: str, *, timeout_seconds: float, token: str | None = None) -> None:
        if not 0 < timeout_seconds <= 15:
            raise ValueError("Webhook settings are invalid")
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise ValueError("Webhook settings are invalid") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not url.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
            or (parsed.scheme == "http" and host not in {"127.0.0.1", "::1", "localhost"})
        ):
            raise ValueError("Webhook settings are invalid")
        if token is not None and (
            not 1 <= len(token) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise ValueError("Webhook settings are invalid")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.token = token

    @classmethod
    def from_environment(
        cls, url: str, *, timeout_seconds: float, token_env: str | None
    ) -> WebhookNotifier:
        token = os.environ.get(token_env) if token_env else None
        if token_env and token is None:
            raise ValueError("Webhook settings are invalid")
        return cls(url, timeout_seconds=timeout_seconds, token=token)

    async def deliver(self, event: NotificationEvent) -> DeliveryResult:
        if (
            not re.fullmatch(r"[0-9a-f]{64}", event.event_id)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", event.check_name)
            or not isinstance(event.firing, bool)
            or len(event.occurred_at) > 40
        ):
            return DeliveryResult(False, "invalid_event")
        payload = json.dumps(
            {
                "schema": 1,
                "event_id": event.event_id,
                "check": event.check_name,
                "status": "firing" if event.firing else "resolved",
                "occurred_at": event.occurred_at,
            },
            separators=(",", ":"),
        ).encode()
        if len(payload) > 2048:
            return DeliveryResult(False, "invalid_event")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                status = await self._post(payload, event.event_id)
            return DeliveryResult(
                200 <= status < 300, "accepted" if 200 <= status < 300 else "rejected"
            )
        except TimeoutError:
            return DeliveryResult(False, "timeout")
        except (
            OSError,
            ssl.SSLError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ValueError,
        ):
            return DeliveryResult(False, "network_error")

    async def _post(self, payload: bytes, event_id: str) -> int:
        target = urlsplit(self.url)
        host = target.hostname
        if not host:
            raise ValueError("Invalid target")
        port = target.port or (443 if target.scheme == "https" else 80)
        context = ssl.create_default_context() if target.scheme == "https" else None
        reader, writer = await asyncio.open_connection(
            host,
            port,
            ssl=context,
            server_hostname=host if context else None,
            limit=16384,
        )
        try:
            host_header = f"[{host}]" if ":" in host else host
            if port != (443 if target.scheme == "https" else 80):
                host_header += f":{port}"
            headers = [
                f"POST {target.path or '/'} HTTP/1.1",
                f"Host: {host_header}",
                "User-Agent: prl-offhost-probe/0.1",
                "Content-Type: application/json",
                f"Content-Length: {len(payload)}",
                f"Idempotency-Key: {event_id}",
                "Connection: close",
            ]
            if self.token:
                headers.append(f"Authorization: Bearer {self.token}")
            writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + payload)
            await writer.drain()
            raw_headers = await reader.readuntil(b"\r\n\r\n")
            if len(raw_headers) > 16384:
                raise ValueError("Invalid response")
            first = raw_headers.split(b"\r\n", 1)[0].split(b" ", 2)
            if len(first) < 2 or not first[0].startswith(b"HTTP/"):
                raise ValueError("Invalid response")
            return int(first[1])
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
