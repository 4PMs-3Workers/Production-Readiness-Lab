from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import CheckConfig
from .evaluator import ProbeResult

_HEADER_LIMIT = 32768


@dataclass(frozen=True)
class ProbePolicy:
    timeout_seconds: float
    connect_timeout_seconds: float
    read_timeout_seconds: float


class HttpsTransport:
    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self.ssl_context = ssl_context

    async def probe(self, check: CheckConfig, policy: ProbePolicy) -> ProbeResult:
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                status, body_size = await self._request(check, policy)
            if body_size > check.max_response_bytes:
                return ProbeResult(check.name, False, status, "response_too_large")
            success = 200 <= status < 300 and status in check.expected_statuses
            return ProbeResult(check.name, success, status, "ok" if success else "upstream_error")
        except TimeoutError:
            return ProbeResult(check.name, False, None, "timeout")
        except ssl.SSLCertVerificationError:
            return ProbeResult(check.name, False, None, "tls_untrusted")
        except (
            ssl.SSLError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ValueError,
        ):
            return ProbeResult(check.name, False, None, "network_error")

    async def _request(self, check: CheckConfig, policy: ProbePolicy) -> tuple[int, int]:
        target = urlsplit(check.url)
        host = target.hostname
        if not host:
            raise ValueError("Invalid target")
        port = target.port or 443
        context = self.ssl_context or ssl.create_default_context()
        async with asyncio.timeout(policy.connect_timeout_seconds):
            reader, writer = await asyncio.open_connection(
                host, port, ssl=context, server_hostname=host, limit=_HEADER_LIMIT
            )
        try:
            path = target.path or "/"
            host_header = f"[{host}]" if ":" in host else host
            if port != 443:
                host_header += f":{port}"
            request = (
                f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\n"
                "User-Agent: prl-offhost-probe/0.1\r\nAccept: */*\r\nConnection: close\r\n\r\n"
            )
            writer.write(request.encode("ascii"))
            async with asyncio.timeout(policy.read_timeout_seconds):
                await writer.drain()
                raw_headers = await reader.readuntil(b"\r\n\r\n")
                if len(raw_headers) > _HEADER_LIMIT:
                    raise ValueError("Headers too large")
                lines = raw_headers.split(b"\r\n")
                pieces = lines[0].split(b" ", 2)
                if len(pieces) < 2 or not pieces[0].startswith(b"HTTP/"):
                    raise ValueError("Invalid response")
                status = int(pieces[1])
                if not 100 <= status <= 599:
                    raise ValueError("Invalid response")
                headers: dict[bytes, bytes] = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, separator, value = line.partition(b":")
                    if not separator:
                        raise ValueError("Invalid response")
                    headers[key.strip().lower()] = value.strip().lower()
                if 300 <= status < 400:
                    return status, 0
                max_bytes = check.max_response_bytes
                content_length = headers.get(b"content-length")
                if content_length is not None:
                    length = int(content_length)
                    if length > max_bytes:
                        return status, length
                if headers.get(b"transfer-encoding") == b"chunked":
                    size = await self._read_chunked(reader, max_bytes)
                elif content_length is not None:
                    length = int(content_length)
                    if length:
                        await reader.readexactly(length)
                    size = length
                else:
                    size = 0
                    while size <= max_bytes:
                        body = await reader.read(min(65536, max_bytes + 1 - size))
                        if not body:
                            break
                        size += len(body)
                return status, size
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass

    async def _read_chunked(self, reader: asyncio.StreamReader, max_bytes: int) -> int:
        size = 0
        while True:
            line = await reader.readline()
            if not line or len(line) > 128:
                raise ValueError("Invalid chunk")
            chunk_size = int(line.split(b";", 1)[0].strip(), 16)
            if chunk_size == 0:
                await reader.readuntil(b"\r\n")
                return size
            size += chunk_size
            if size > max_bytes:
                return size
            await reader.readexactly(chunk_size + 2)
