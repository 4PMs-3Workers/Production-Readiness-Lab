"""Business calendar context for the authenticated staff portal."""

from datetime import date

from pydantic import BaseModel, ConfigDict


class StaffContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    business_date: date
