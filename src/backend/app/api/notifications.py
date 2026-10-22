"""Personal notification inbox API for students and staff."""

from collections import Counter
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_error
from app.dependencies.auth import require_csrf, require_roles
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.common import ErrorResponse, PageResponse
from app.schemas.notifications import (
    NotificationListItem,
    NotificationListQuery,
    NotificationReadRequest,
    NotificationReadResponse,
)
from app.services import notifications as notification_service

router = APIRouter(tags=["notifications"])
require_notification_user = require_roles("STUDENT", "STAFF")


def _responses(
    success_models: dict[int | str, type[BaseModel]], *error_statuses: int
) -> dict[int | str, dict[str, Any]]:
    request_id = {"schema": {"type": "string"}, "description": "Identifier for this request"}
    cache_control = {
        "schema": {"type": "string"},
        "description": "Private responses are not stored",
    }
    success_headers = {"X-Request-ID": request_id, "Cache-Control": cache_control}
    error_headers = success_headers | {
        "X-Error-Class": {"schema": {"type": "string"}, "description": "Error class"}
    }
    responses: dict[int | str, dict[str, Any]] = {
        status: {"model": model, "headers": success_headers}
        for status, model in success_models.items()
    }
    responses.update(
        {
            status: {
                "model": ErrorResponse,
                "headers": error_headers,
                "description": "Sanitized notification error",
            }
            for status in error_statuses
        }
    )
    return responses


async def reject_duplicate_notification_query(
    request: Request,
    _context: Annotated[AuthContext, Depends(require_notification_user)],
) -> None:
    del _context
    keys = [key for key, _ in request.query_params.multi_items()]
    if any(count > 1 for count in Counter(keys).values()):
        raise catalog_error("VALIDATION_ERROR")


@router.get(
    "/notifications",
    response_model=PageResponse[NotificationListItem],
    responses=_responses({200: PageResponse[NotificationListItem]}, 401, 403, 422, 500, 503),
    summary="List the authenticated user's notifications",
)
async def list_notifications(
    request: Request,
    response: Response,
    query: Annotated[NotificationListQuery, Query()],
    context: Annotated[AuthContext, Depends(require_notification_user)],
    _query_guard: Annotated[None, Depends(reject_duplicate_notification_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PageResponse[NotificationListItem]:
    del _query_guard
    response.headers["Cache-Control"] = "private, no-store"
    return await notification_service.list_notifications(
        db,
        context=context,
        query=query,
        settings=request.app.state.settings,
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationReadResponse,
    responses=_responses({200: NotificationReadResponse}, 401, 403, 404, 413, 422, 500, 503),
    summary="Persistently mark one owned notification as read",
    openapi_extra={
        "parameters": [
            {
                "name": "X-CSRF-Token",
                "in": "header",
                "required": True,
                "description": "Signed token matching the prl_csrf cookie.",
                "schema": {"type": "string"},
            },
            {
                "name": "Origin",
                "in": "header",
                "required": False,
                "description": "Preferred same-origin header; Referer is the fallback.",
                "schema": {"type": "string", "format": "uri"},
            },
            {
                "name": "Referer",
                "in": "header",
                "required": False,
                "description": "Checked only when Origin is absent.",
                "schema": {"type": "string", "format": "uri"},
            },
        ]
    },
)
async def mark_notification_read(
    notification_id: UUID,
    request: Request,
    response: Response,
    _payload: NotificationReadRequest,
    context: Annotated[AuthContext, Depends(require_notification_user)],
    _csrf_context: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NotificationReadResponse:
    del _csrf_context
    if request.query_params:
        raise catalog_error("VALIDATION_ERROR")
    response.headers["Cache-Control"] = "private, no-store"
    return await notification_service.mark_notification_read(
        db,
        notification_id=notification_id,
        context=context,
        settings=request.app.state.settings,
    )
