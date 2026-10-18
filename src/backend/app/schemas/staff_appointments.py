"""Strict DTOs for assigned staff appointment management."""

from datetime import date as Date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.appointments import AppointmentStatus
from app.schemas.common import PaginationParams, UtcTimestamp

StaffAppointmentOutcomeStatus = Literal["COMPLETED", "NO_SHOW"]


class StaffAppointmentListQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: AppointmentStatus | None = None
    date: Date | None = None


class StaffAppointmentListItem(BaseModel):
    """Assigned appointment summary without free-text fields."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    purpose_id: UUID
    purpose_code: str
    purpose_name: str
    student_id: UUID
    student_name: str
    duration_minutes: int
    starts_at: UtcTimestamp
    ends_at: UtcTimestamp
    location: str | None
    status: AppointmentStatus
    created_at: UtcTimestamp
    cancelled_at: UtcTimestamp | None
    outcome_at: UtcTimestamp | None


class StaffAppointmentDetailResponse(StaffAppointmentListItem):
    """Authorized detail including consultation and transition metadata."""

    description: str | None
    cancelled_by: UUID | None
    cancel_reason: str | None
    outcome_by: UUID | None


class StaffAppointmentOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: StaffAppointmentOutcomeStatus


class StaffAppointmentOutcomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: StaffAppointmentOutcomeStatus
    outcome_at: UtcTimestamp
