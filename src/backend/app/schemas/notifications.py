"""Public notification inbox query and response contracts."""

from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.appointments import AppointmentStatus
from app.schemas.common import PaginationParams, UtcTimestamp


class NotificationListQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    unread_only: bool = False


class NotificationListItem(BaseModel):
    """A safe event summary and appointment snapshot authorized for this recipient."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    event_type: Annotated[str, Field(min_length=1, max_length=50)]
    summary: Annotated[str, Field(min_length=1, max_length=200)]
    created_at: UtcTimestamp
    read_at: UtcTimestamp | None
    appointment_id: UUID | None
    appointment_starts_at: UtcTimestamp | None
    appointment_status: AppointmentStatus | None


class NotificationReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    read_at: AwareDatetime


class NotificationReadRequest(BaseModel):
    """The mutation has no client-controlled fields; callers send an empty object."""

    model_config = ConfigDict(extra="forbid", strict=True)
