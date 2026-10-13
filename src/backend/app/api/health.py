"""Process liveness and release/database compatibility readiness."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.api.errors import ServiceUnavailable
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/health", tags=["health"])
HEALTH_HEADERS = {
    "Cache-Control": {"description": "Always no-store", "schema": {"type": "string"}},
    "X-Request-ID": {"description": "Request correlation ID", "schema": {"type": "string"}},
}


class LiveResponse(BaseModel):
    status: Literal["live"]


class ReadyResponse(BaseModel):
    status: Literal["ready"]


@router.get(
    "/live",
    response_model=LiveResponse,
    responses={
        200: {"headers": HEALTH_HEADERS},
        500: {"model": ErrorResponse, "headers": HEALTH_HEADERS},
    },
)
async def live(response: Response) -> LiveResponse:
    response.headers["Cache-Control"] = "no-store"
    return LiveResponse(status="live")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={
        200: {"headers": HEALTH_HEADERS},
        503: {
            "model": ErrorResponse,
            "description": "Database unavailable or schema incompatible",
            "headers": HEALTH_HEADERS,
        },
        500: {"model": ErrorResponse, "headers": HEALTH_HEADERS},
    },
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            async with request.app.state.engine.connect() as connection:
                if await connection.scalar(text("SELECT 1")) != 1:
                    raise ServiceUnavailable()
                result = await connection.execute(text("SELECT version_num FROM alembic_version"))
                revisions = result.scalars().all()
                if len(revisions) != 1 or revisions[0] not in settings.accepted_schema_revisions:
                    raise ServiceUnavailable()
    except Exception:
        raise ServiceUnavailable() from None
    return ReadyResponse(status="ready")
