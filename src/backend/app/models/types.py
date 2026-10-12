"""MySQL adapters for values exposed by the application."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from sqlalchemy.dialects.mysql import BINARY, DATETIME, TIME, VARBINARY
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class BinaryUUID(TypeDecorator[UUID]):
    """Store UUID bytes in standard order, without MySQL UUID byte swapping."""

    impl = BINARY(16)
    cache_ok = True

    def process_bind_param(self, value: UUID | str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = UUID(value)
        if not isinstance(value, UUID):
            raise TypeError("Expected UUID or canonical UUID string")
        return value.bytes

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> UUID | None:
        return UUID(bytes=value) if value is not None else None


class UTCDateTime(TypeDecorator[datetime]):
    """Reject naive input; store UTC without tzinfo and restore it on reads."""

    impl = DATETIME(fsp=6)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError("Expected datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("A timezone-aware datetime is required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class TimeOfDay(TypeDecorator[time]):
    """MySQL TIME(0) used as a local wall-clock time, never as a duration."""

    impl = TIME(fsp=0)
    cache_ok = True

    def process_bind_param(self, value: time | None, dialect: Dialect) -> time | None:
        if value is None:
            return None
        if not isinstance(value, time):
            raise TypeError("Expected datetime.time")
        if value.tzinfo is not None or value.microsecond:
            raise ValueError("Time of day must have no timezone or fractional seconds")
        return value

    def process_result_value(self, value: time | timedelta | None, dialect: Dialect) -> time | None:
        if isinstance(value, timedelta):
            if not timedelta(0) <= value < timedelta(days=1) or value.microseconds:
                raise ValueError("Stored TIME is outside the supported wall-clock range")
            seconds = value.seconds
            return time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)
        return value


class SHA256Hash(TypeDecorator[bytes]):
    """A full 32-byte SHA-256 digest, with database CHECK as a second guard."""

    impl = VARBINARY(32)
    cache_ok = True

    def process_bind_param(self, value: bytes | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if not isinstance(value, bytes):
            raise TypeError("Expected raw SHA-256 digest bytes")
        if len(value) != 32:
            raise ValueError("SHA-256 digest must contain exactly 32 bytes")
        return value
