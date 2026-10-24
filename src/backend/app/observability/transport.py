"""Per-exporter OTLP HTTP sessions that bound SDK diagnostic inputs."""

from typing import Protocol, cast

import requests
from requests.models import Response

SAFE_OTLP_RESPONSE_REASON = "OTLP collector returned an unsuccessful response"
SAFE_OTLP_CONNECTION_ERROR = "OTLP transport request failed"
SAFE_OTLP_REQUEST_ERROR = "OTLP request failed"

_RETRYABLE_TRANSPORT_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.Timeout,
    requests.exceptions.SSLError,
    requests.exceptions.ProxyError,
)


class _SessionRequest(Protocol):
    """Requests' alias-heavy call inputs are widened only at this public delegation seam."""

    def __call__(
        self,
        method: str,
        url: str | bytes,
        params: object = None,
        data: object = None,
        headers: object = None,
        cookies: object = None,
        files: object = None,
        auth: object = None,
        timeout: object = None,
        allow_redirects: bool = True,
        proxies: object = None,
        hooks: object = None,
        stream: bool | None = None,
        verify: object = None,
        cert: object = None,
        json: object = None,
    ) -> Response: ...


class SanitizingOTLPSession(requests.Session):
    """Sanitize public Requests responses before the SDK builds diagnostics."""

    def request(
        self,
        method: str,
        url: str | bytes,
        params: object = None,
        data: object = None,
        headers: object = None,
        cookies: object = None,
        files: object = None,
        auth: object = None,
        timeout: object = None,
        allow_redirects: bool = True,
        proxies: object = None,
        hooks: object = None,
        stream: bool | None = None,
        verify: object = None,
        cert: object = None,
        json: object = None,
    ) -> Response:
        try:
            request = cast(_SessionRequest, super().request)
            response = request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                cookies=cookies,
                files=files,
                auth=auth,
                timeout=timeout,
                allow_redirects=allow_redirects,
                proxies=proxies,
                hooks=hooks,
                stream=stream,
                verify=verify,
                cert=cert,
                json=json,
            )
        except Exception as error:
            if isinstance(error, _RETRYABLE_TRANSPORT_ERRORS):
                raise requests.exceptions.ConnectionError(SAFE_OTLP_CONNECTION_ERROR) from None
            raise requests.exceptions.RequestException(SAFE_OTLP_REQUEST_ERROR) from None
        response.reason = SAFE_OTLP_RESPONSE_REASON
        return response
