"""Bounded, owned CSV history export without persistent export jobs."""

import csv
import io
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import BusinessConflict
from app.core.transactions import read_transaction
from app.repositories import appointments
from app.schemas.appointments import AppointmentExportQuery, AppointmentListItem
from app.services.student_appointments import _list_item

MAX_EXPORT_ROWS = 1000
CSV_COLUMNS = (
    "id",
    "purpose_code",
    "purpose_name",
    "staff_name",
    "starts_at",
    "ends_at",
    "duration_minutes",
    "status",
    "location",
)


def render_csv(items: Sequence[AppointmentListItem]) -> str:
    """Quote CSV syntax and prefix spreadsheet formula-like text with an apostrophe."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for item in items:
        values = item.model_dump(mode="json")
        row = []
        for column in CSV_COLUMNS:
            value = values[column]
            if isinstance(value, str) and (
                value.startswith(("\t", "\r", "\n"))
                or value.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠"))
            ):
                value = "'" + value
            row.append(value)
        writer.writerow(row)
    return output.getvalue()


async def export_student_appointments(
    db: AsyncSession, query: AppointmentExportQuery, *, student_id: UUID, settings: Settings
) -> str:
    async with read_transaction(db, settings):
        records = await appointments.read_student_appointment_export(
            db, student_id=student_id, status=query.status, limit=MAX_EXPORT_ROWS + 1
        )
        if len(records) > MAX_EXPORT_ROWS:
            raise BusinessConflict("EXPORT_TOO_LARGE", "Export exceeds 1000 rows; filter by status")
        items = [_list_item(record) for record in records]
    return render_csv(items)
