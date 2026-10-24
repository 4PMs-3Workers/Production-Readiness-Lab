"""Personal notification inbox API for students and staff."""

from collections import Counter
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_errors import auth_error
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
EXPECTED_OWNER_PARAMETERS = [
    {
        "name": "X-Expected-User-ID",
        "in": "header",
        "required": False,
        "description": "Optional with X-Expected-Role; checked against this request's user.",
        "schema": {"type": "string", "format": "uuid"},
    },
    {
        "name": "X-Expected-Role",
        "in": "header",
        "required": False,
        "description": "Optional with X-Expected-User-ID; checked against this request's role.",
        "schema": {"type": "string", "enum": ["STUDENT", "STAFF"]},
    },
]


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


def check_expected_notification_owner(request: Request, context: AuthContext) -> None:
    """Bind a rendered inbox identity to this request's authenticated context."""
    user_headers = request.headers.getlist("x-expected-user-id")
    role_headers = request.headers.getlist("x-expected-role")
    if not user_headers and not role_headers:
        return
    if len(user_headers) != 1 or len(role_headers) != 1:
        raise catalog_error("VALIDATION_ERROR")
    try:
        expected_user_id = UUID(user_headers[0])
    except ValueError as exc:
        raise catalog_error("VALIDATION_ERROR") from exc
    expected_role = role_headers[0]
    if expected_role not in {"STUDENT", "STAFF"}:
        raise catalog_error("VALIDATION_ERROR")
    if expected_user_id != context.user_id or expected_role != context.role:
        raise auth_error("ACCOUNT_CHANGED")


@router.get(
    "/notifications",
    response_model=PageResponse[NotificationListItem],
    responses=_responses({200: PageResponse[NotificationListItem]}, 401, 403, 409, 422, 500, 503),
    summary="List the authenticated user's notifications",
    description=(
        "Returns only the current STUDENT or STAFF recipient's notifications. Pages are "
        "1-based with `page_size` from 1 to 100 and optional `unread_only` filtering. "
        "Rows use deterministic `created_at DESC, id DESC` ordering; `total` counts all "
        "matching rows even when the requested page is empty. A known event shows its safe "
        "summary and appointment link only while that appointment is accessible to this "
        "recipient. An unknown event or inaccessible appointment receives a generic summary "
        "and null appointment fields, with no actionable link. Consultation text and internal "
        "replay data are never included.\n\n"
        "Clients showing a known identity may send both `X-Expected-User-ID` and "
        "`X-Expected-Role`; they are compared with this same request's authenticated "
        "context before any notification data is returned. A mismatch returns "
        "`ACCOUNT_CHANGED` (409); a malformed or incomplete pair returns "
        "`VALIDATION_ERROR` (422). Clients omitting both still receive their own session's "
        "inbox.\n\n"
        "Errors: `INVALID_SESSION` (401), `FORBIDDEN_ROLE` (403), `VALIDATION_ERROR` (422), "
        "`INTERNAL_SERVER_ERROR` (500), `SERVICE_UNAVAILABLE` (503). Responses are "
        "`private, no-store` with a request ID."
    ),
    openapi_extra={"parameters": EXPECTED_OWNER_PARAMETERS},
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
    check_expected_notification_owner(request, context)
    return await notification_service.list_notifications(
        db,
        context=context,
        query=query,
        settings=request.app.state.settings,
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationReadResponse,
    responses=_responses(
        {200: NotificationReadResponse}, 401, 403, 404, 409, 413, 415, 422, 500, 503
    ),
    summary="Persistently mark one owned notification as read",
    description=(
        "Send an empty JSON object to mark one notification owned by the current STUDENT or "
        "STAFF recipient as read. The response returns the persisted `read_at`; repeated or "
        "concurrent calls preserve the first timestamp and create no new business events. "
        "Missing and other recipients' IDs both return `NOT_FOUND` (404). The write requires "
        "the signed CSRF token plus a same-origin Origin or Referer.\n\n"
        "When supplied together, `X-Expected-User-ID` and `X-Expected-Role` must match "
        "this request's authenticated context; otherwise `ACCOUNT_CHANGED` (409) is "
        "returned without marking a notification. An incomplete or malformed pair returns "
        "`VALIDATION_ERROR` (422).\n\n"
        "Other errors: `INVALID_SESSION` (401); `FORBIDDEN_ROLE`, `INVALID_CSRF`, or "
        "`INVALID_ORIGIN` (403); `PAYLOAD_TOO_LARGE` (413); `UNSUPPORTED_MEDIA_TYPE` (415); "
        "`VALIDATION_ERROR` (422); `INTERNAL_SERVER_ERROR` (500); "
        "`SERVICE_UNAVAILABLE` (503). Responses are `private, no-store` with a request ID."
    ),
    openapi_extra={
        "parameters": EXPECTED_OWNER_PARAMETERS
        + [
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
    check_expected_notification_owner(request, context)
    return await notification_service.mark_notification_read(
        db,
        notification_id=notification_id,
        context=context,
        settings=request.app.state.settings,
    )
