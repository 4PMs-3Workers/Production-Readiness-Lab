"""Import this registry before migrations or metadata-based schema operations."""

from app.models.appointment import Appointment
from app.models.audit_event import AuditEvent
from app.models.base import Base
from app.models.change_proposal import ChangeProposal
from app.models.notification import Notification
from app.models.purpose import Purpose
from app.models.service_hour import ServiceHour
from app.models.service_setting import ServiceSetting
from app.models.session import Session
from app.models.staff_purpose import StaffPurpose
from app.models.staff_unavailability import StaffUnavailability
from app.models.user import User

__all__ = [
    "Appointment",
    "AuditEvent",
    "Base",
    "ChangeProposal",
    "Notification",
    "Purpose",
    "ServiceHour",
    "ServiceSetting",
    "Session",
    "StaffPurpose",
    "StaffUnavailability",
    "User",
]
