"""Strict proposal inputs, immutable receipts, resolutions and history contexts."""

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.schemas.appointments import AppointmentStatus
from app.schemas.common import PageResponse, PaginationParams, UtcTimestamp

MAX_PROPOSAL_VERSION = 9007199254740991
ProposalStatus = Literal["PENDING", "ACCEPTED", "DECLINED", "EXPIRED", "SUPERSEDED", "CANCELLED"]
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")


class ProposalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_proposal_version: int = Field(strict=True, ge=0, le=MAX_PROPOSAL_VERSION)
    expected_pending_proposal_id: UUID | None
    proposed_starts_at: AwareDatetime
    proposed_location: str | None = Field(max_length=500, strict=True)

    @field_validator("proposed_starts_at", mode="before")
    @classmethod
    def require_timestamp(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
            raise ValueError("Expected an offset-bearing timestamp with microsecond precision")
        return value

    @field_validator("proposed_starts_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        try:
            return value.astimezone(UTC)
        except OverflowError as error:
            raise ValueError("Timestamp cannot be represented in UTC") from error

    @field_validator("proposed_location")
    @classmethod
    def reject_blank_location(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Location must not be blank")
        return value


class ProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProposalHistoryQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")


class ProposalCreateReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    appointment_id: UUID
    created_version: int
    created_at: UtcTimestamp
    expires_at: UtcTimestamp


class ProposalResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    appointment_id: UUID
    status: Literal["ACCEPTED", "DECLINED"]
    resolved_at: UtcTimestamp
    resolved_by: UUID


class ProposalHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    appointment_id: UUID
    created_by: UUID
    created_version: int | None
    proposed_starts_at: UtcTimestamp
    proposed_ends_at: UtcTimestamp
    proposed_location: str | None
    status: ProposalStatus
    created_at: UtcTimestamp
    expires_at: UtcTimestamp
    resolved_at: UtcTimestamp | None
    resolved_by: UUID | None


class ProposalContext(BaseModel):
    appointment_id: UUID
    appointment_status: AppointmentStatus
    proposal_version: int
    pending_proposal_id: UUID | None


class ProposalHistoryResponse(PageResponse[ProposalHistoryItem]):
    context: ProposalContext
