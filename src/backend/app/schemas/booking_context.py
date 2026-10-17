"""Server-defined business calendar for student booking."""

from datetime import date

from pydantic import BaseModel


class BookingContextResponse(BaseModel):
    timezone: str
    business_date: date
    min_booking_date: date
    max_booking_date: date
