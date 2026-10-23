"""Controlled gateway upstream using the real request-ID, cookie and CSRF policies.

No database, password authentication, or replacement application server is provided.
"""

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


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def echo(request: Request, path: str):
    body = await request.body()
    if path == "api/v1/upstream-error":
        return JSONResponse(
            {"error": {"code": "UPSTREAM_UNAVAILABLE", "request_id": request.state.request_id}},
            status_code=503,
            headers={
                "Retry-After": "7",
                "Cache-Control": "private, no-store",
                "X-Error-Class": "service",
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
        headers={"Cache-Control": "private, no-store", "Retry-After": "7"},
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
