"""Compatibility facade for the shared best-effort publisher."""

from app.core.publishing import (
    CLEANUP_TIMEOUT_SECONDS,
    MAX_RECORD_BYTES,
    PUBLISH_TIMEOUT_SECONDS,
    QUEUE_CAPACITY,
    Publisher,
    Record,
)

__all__ = [
    "CLEANUP_TIMEOUT_SECONDS",
    "MAX_RECORD_BYTES",
    "PUBLISH_TIMEOUT_SECONDS",
    "QUEUE_CAPACITY",
    "Publisher",
    "Record",
]
