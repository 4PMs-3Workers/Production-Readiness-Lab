"""Process liveness and release/database compatibility readiness."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.errors import ServiceUnavailable
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/health", tags=["health"])
HEALTH_HEADERS = {
    "Cache-Control": {"description": "Always no-store", "schema": {"type": "string"}},
    "X-Request-ID": {"description": "Request correlation ID", "schema": {"type": "string"}},
}
HEALTH_ERROR_HEADERS = {
    **HEALTH_HEADERS,
    "X-Error-Class": {"description": "Error class", "schema": {"type": "string"}},
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
        500: {"model": ErrorResponse, "headers": HEALTH_ERROR_HEADERS},
    },
)
async def live(response: Response) -> LiveResponse:
    response.headers["Cache-Control"] = "no-store"
    return LiveResponse(status="live")


async def _probe_database(
    engine: AsyncEngine, accepted_revisions: frozenset[str], cleanup_started: asyncio.Event
) -> None:
    async with engine.connect() as connection:
        try:
            if await connection.scalar(text("SELECT 1")) != 1:
                raise ServiceUnavailable()
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            revisions = result.scalars().all()
            if len(revisions) != 1 or revisions[0] not in accepted_revisions:
                raise ServiceUnavailable()
        finally:
            cleanup_started.set()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={
        200: {"headers": HEALTH_HEADERS},
        503: {
            "model": ErrorResponse,
            "description": "Database unavailable or schema incompatible",
            "headers": HEALTH_ERROR_HEADERS,
        },
        500: {"model": ErrorResponse, "headers": HEALTH_ERROR_HEADERS},
    },
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    pending_tasks: set[asyncio.Task[None]] = request.app.state.pending_readiness_tasks
    cleanup_started = asyncio.Event()
    task = asyncio.create_task(
        _probe_database(
            request.app.state.engine, settings.accepted_schema_revisions, cleanup_started
        )
    )
    pending_tasks.add(task)

    def finish_probe(completed: asyncio.Task[None]) -> None:
        pending_tasks.discard(completed)
        if not completed.cancelled():
            completed.exception()  # Consume failures after a timed-out response.

    task.add_done_callback(finish_probe)
    try:
        done, _ = await asyncio.wait({task}, timeout=settings.readiness_timeout_seconds)
        if not done:
            raise ServiceUnavailable()
        task.result()
    except Exception:
        raise ServiceUnavailable() from None
    finally:
        if not task.done() and not cleanup_started.is_set():
            task.cancel()  # Cancellation enters cleanup; lifespan waits for the tracked task.
    return ReadyResponse(status="ready")
