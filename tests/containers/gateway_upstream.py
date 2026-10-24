"""Controlled gateway upstream using the real request-ID, cookie and CSRF policies.

No database, password authentication, or replacement application server is provided.
"""

import re
import secrets
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.errors import install_error_handlers
from app.core.auth_cookies import set_auth_cookies
from app.core.config import load_settings
from app.core.csrf import issue_csrf_token, verify_csrf_token
from app.core.origin import require_same_origin
from app.core.request_context import RequestIdMiddleware

settings = load_settings()
session_id = UUID("00000000-0000-0000-0000-000000000001")
app = FastAPI()
app.add_middleware(RequestIdMiddleware, trusted_gateway_cidrs=settings.trusted_gateway_cidrs)
install_error_handlers(app)


def route_template(method: str, path: str) -> str:
    canonical = path.rstrip("/") or "/"
    if method == "GET" and canonical == "/api/v1/purposes":
        return "/api/v1/purposes"
    if method == "POST" and canonical == "/api/v1/auth/login":
        return "/api/v1/auth/login"
    if method == "GET" and re.fullmatch(r"/api/v1/appointments/[^/]+", canonical):
        return "/api/v1/appointments/{appointment_id}"
    return "__unmatched__"


def safe_response(request: Request, *, status: int, error_class: str | None = None,
                  route_marker: str | None = None, outcome: str | None = None,
                  trace_id: str | None = None):
    headers = {"Cache-Control": "private, no-store", "Retry-After": "7"}
    if error_class is not None:
        headers["X-Error-Class"] = error_class
    if route_marker is not None:
        headers["X-PRL-Route"] = route_marker
    if outcome is not None:
        headers["X-PRL-Outcome"] = outcome
    headers["X-Trace-ID"] = trace_id if trace_id is not None else secrets.token_hex(16)
    if status >= 400:
        body = {"error": {"code": "FIXTURE_ERROR", "request_id": request.state.request_id}}
    else:
        body = {"role": sys.argv[2], "path": request.url.path}
    return JSONResponse(body, status_code=status, headers=headers)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def echo(request: Request, path: str):
    body = await request.body()
    route = route_template(request.method, request.url.path)
    fixture = request.query_params.get("prl_test", "")
    if path == "api/v1/not-a-published-operation":
        return safe_response(request, status=404, route_marker=route)
    if path == "api/v1/upstream-error":
        return JSONResponse(
            {"error": {"code": "UPSTREAM_UNAVAILABLE", "request_id": request.state.request_id}},
            status_code=503,
            headers={
                "Retry-After": "7",
                "Cache-Control": "private, no-store",
                "X-Error-Class": "service",
                "X-Trace-ID": secrets.token_hex(16),
                "X-PRL-Route": "__unmatched__",
            },
        )
    if path == "api/v1/mutate":
        require_same_origin(request, settings)
        verify_csrf_token(
            cookie=request.cookies.get("prl_csrf"),
            header=request.headers.get("x-csrf-token"),
            session_id=session_id,
            secret=settings.csrf_secret.get_secret_value(),
        )

    if fixture == "input":
        return safe_response(
            request, status=422, error_class="input", route_marker=route
        )
    if fixture == "object_404":
        return safe_response(
            request, status=404, error_class="input", route_marker=route
        )
    if fixture == "published_wrong_marker":
        return safe_response(
            request, status=200, route_marker="__unmatched__"
        )
    if fixture == "published_missing_marker":
        return safe_response(request, status=404, error_class="input")
    if fixture == "service_4xx":
        return safe_response(
            request, status=400, error_class="service", route_marker=route
        )
    if fixture == "service_2xx":
        return safe_response(
            request, status=200, error_class="service", route_marker=route
        )
    if fixture == "business_422":
        return safe_response(
            request, status=422, error_class="business", route_marker=route
        )
    if fixture == "unclassified_4xx":
        return safe_response(request, status=400, route_marker=route)
    if fixture == "unknown_class_4xx":
        return safe_response(
            request,
            status=400,
            error_class="secret-error-class-canary",
            route_marker=route,
        )
    if fixture == "trace_invalid":
        return safe_response(
            request, status=200, route_marker=route, trace_id="not-a-trace-id"
        )
    if fixture == "trace_zero":
        return safe_response(
            request, status=200, route_marker=route, trace_id="0" * 32
        )
    if fixture == "service":
        return safe_response(
            request, status=503, error_class="service", route_marker=route
        )
    if fixture == "capacity_429":
        return safe_response(
            request, status=429, error_class="input", route_marker=route
        )
    if fixture == "login_throttled":
        return safe_response(
            request,
            status=429,
            error_class="input",
            route_marker=route,
            outcome="login_throttled",
        )
    if sys.argv[2] == "frontend" and fixture == "copy_marker":
        return safe_response(
            request, status=200, route_marker="/api/v1/purposes"
        )

    response = JSONResponse(
        {
            "role": sys.argv[2],
            "path": request.url.path,
            "query": request.url.query,
            "headers": dict(request.headers),
            "body_size": len(body),
            "request_id": request.state.request_id,
            "peer": request.client.host,
        },
        headers={
            "Cache-Control": "private, no-store",
            "Retry-After": "7",
            "X-PRL-Route": route,
            "X-Trace-ID": secrets.token_hex(16),
        },
    )
    if path == "api/v1/cookies":
        now = datetime.now(UTC)
        set_auth_cookies(
            response,
            session_token="test-session",
            csrf_token=issue_csrf_token(session_id, settings.csrf_secret.get_secret_value()),
            expires_at=now + timedelta(minutes=5),
            now=now,
            settings=settings,
        )
    return response


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(sys.argv[1]), proxy_headers=False, access_log=False)
