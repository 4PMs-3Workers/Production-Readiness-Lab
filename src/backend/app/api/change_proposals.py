"""Staff proposal history/creation and owned student proposal decisions."""

import asyncio
from collections import Counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.appointments import STUDENT_WRITE_OPENAPI, appointment_responses
from app.api.errors import AppError
from app.dependencies.auth import require_csrf
from app.dependencies.catalog import require_student
from app.dependencies.database import get_db_session
from app.dependencies.staff import require_staff
from app.domain.auth import AuthContext
from app.schemas.appointments import IdempotencyKey
from app.schemas.change_proposals import (
    ProposalCreateReceipt,
    ProposalCreateRequest,
    ProposalDecisionRequest,
    ProposalHistoryQuery,
    ProposalHistoryResponse,
    ProposalResolution,
)
from app.services import change_proposals as service

router = APIRouter(tags=["change proposals"])
ERROR_STATUSES = (401, 403, 404, 409, 422, 500, 503)


def _guard(request: Request, *, write: bool) -> None:
    keys = [key for key, _ in request.query_params.multi_items()]
    if (write and keys) or any(count > 1 for count in Counter(keys).values()):
        raise AppError(422, "VALIDATION_ERROR", "Invalid request", "input")
    if write and len(request.headers.getlist("Idempotency-Key")) > 1:
        raise AppError(422, "VALIDATION_ERROR", "Invalid request", "input")


def _deadline(request: Request) -> float:
    return getattr(request.state, "request_deadline", None) or (
        asyncio.get_running_loop().time() + request.app.state.settings.request_timeout_seconds
    )


@router.get(
    "/staff/me/appointments/{appointment_id}/change-proposals",
    response_model=ProposalHistoryResponse,
    responses=appointment_responses({200: ProposalHistoryResponse}, *ERROR_STATUSES),
    summary="Read assigned appointment proposal history and creation context",
)
async def staff_history(
    appointment_id: UUID,
    request: Request,
    query: Annotated[ProposalHistoryQuery, Query()],
    context: Annotated[AuthContext, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProposalHistoryResponse:
    _guard(request, write=False)
    return await service.list_proposals(
        db,
        appointment_id=appointment_id,
        query=query,
        context=context,
        settings=request.app.state.settings,
    )


@router.get(
    "/appointments/{appointment_id}/change-proposals",
    response_model=ProposalHistoryResponse,
    responses=appointment_responses({200: ProposalHistoryResponse}, *ERROR_STATUSES),
    summary="Read owned appointment proposal history",
)
async def student_history(
    appointment_id: UUID,
    request: Request,
    query: Annotated[ProposalHistoryQuery, Query()],
    context: Annotated[AuthContext, Depends(require_student)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProposalHistoryResponse:
    _guard(request, write=False)
    return await service.list_proposals(
        db,
        appointment_id=appointment_id,
        query=query,
        context=context,
        settings=request.app.state.settings,
    )


@router.post(
    "/staff/me/appointments/{appointment_id}/change-proposals",
    response_model=ProposalCreateReceipt,
    status_code=201,
    responses=appointment_responses(
        {200: ProposalCreateReceipt, 201: ProposalCreateReceipt}, *ERROR_STATUSES
    ),
    openapi_extra=STUDENT_WRITE_OPENAPI,
    summary="Create or replace a proposal using a durable key and expected context",
)
async def create_proposal(
    appointment_id: UUID,
    request: Request,
    response: Response,
    payload: ProposalCreateRequest,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
    context: Annotated[AuthContext, Depends(require_staff)],
    _csrf: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProposalCreateReceipt:
    """Matching key/body replays the original receipt before mutable business checks.

    Lost responses must be retried with the original key and both original
    preconditions. A stale new intent returns PROPOSAL_PRECONDITION_FAILED;
    clients refresh and confirm before creating a new intent. No proposed slot
    is reserved. Read history/detail for current state after receiving a receipt.
    """
    del _csrf
    _guard(request, write=True)
    receipt, replayed = await service.create_proposal(
        db,
        appointment_id=appointment_id,
        payload=payload,
        context=context,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
        settings=request.app.state.settings,
        deadline=_deadline(request),
    )
    if replayed:
        response.status_code = 200
    return receipt


@router.post(
    "/appointments/{appointment_id}/change-proposals/{proposal_id}/accept",
    response_model=ProposalResolution,
    responses=appointment_responses({200: ProposalResolution}, *ERROR_STATUSES),
    openapi_extra=STUDENT_WRITE_OPENAPI,
    summary="Accept an owned pending proposal, rechecking the complete interval",
)
async def accept_proposal(
    appointment_id: UUID,
    proposal_id: UUID,
    request: Request,
    payload: ProposalDecisionRequest,
    context: Annotated[AuthContext, Depends(require_student)],
    _csrf: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProposalResolution:
    """A repeated accept returns the saved resolution even after later transitions."""
    del _csrf, payload
    _guard(request, write=True)
    return await service.decide_proposal(
        db,
        appointment_id=appointment_id,
        proposal_id=proposal_id,
        decision="ACCEPTED",
        context=context,
        request_id=request.state.request_id,
        settings=request.app.state.settings,
        deadline=_deadline(request),
    )


@router.post(
    "/appointments/{appointment_id}/change-proposals/{proposal_id}/decline",
    response_model=ProposalResolution,
    responses=appointment_responses({200: ProposalResolution}, *ERROR_STATUSES),
    openapi_extra=STUDENT_WRITE_OPENAPI,
    summary="Decline an owned pending proposal and cancel the appointment",
)
async def decline_proposal(
    appointment_id: UUID,
    proposal_id: UUID,
    request: Request,
    payload: ProposalDecisionRequest,
    context: Annotated[AuthContext, Depends(require_student)],
    _csrf: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProposalResolution:
    """Decline needs no new-booking eligibility; same-action retries have no side effects."""
    del _csrf, payload
    _guard(request, write=True)
    return await service.decide_proposal(
        db,
        appointment_id=appointment_id,
        proposal_id=proposal_id,
        decision="DECLINED",
        context=context,
        request_id=request.state.request_id,
        settings=request.app.state.settings,
        deadline=_deadline(request),
    )
