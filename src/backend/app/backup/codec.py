"""Canonical, typed row records preserve binary IDs and MySQL temporal values."""

import base64
import json
import math
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from app.backup.config import BackupError, Reason

MAX_RECORD_BYTES = 1024 * 1024


def encode_value(value: Any) -> list:
    if value is None:
        return ["null", None]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, datetime):
        utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return ["datetime", utc.isoformat(timespec="microseconds")]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time) and value.tzinfo is None:
        return ["time", value.isoformat(timespec="microseconds")]
    if isinstance(value, timedelta):
        return [
            "duration",
            str((value.days * 86400 + value.seconds) * 1000000 + value.microseconds),
        ]
    if isinstance(value, Decimal) and value.is_finite():
        return ["decimal", str(value)]
    if isinstance(value, float) and math.isfinite(value):
        return ["float", repr(value)]
    raise BackupError(Reason.UNSUPPORTED)


def decode_value(value: Any) -> Any:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError()
    kind, data = value
    if kind == "null" and data is None:
        return None
    if kind == "bool" and type(data) is bool:
        return data
    if not isinstance(data, str):
        raise ValueError()
    if kind == "str":
        return data
    if kind == "int":
        return int(data)
    if kind == "bytes":
        return base64.b64decode(data, validate=True)
    if kind == "datetime":
        instant = datetime.fromisoformat(data)
        if instant.tzinfo != UTC:
            raise ValueError()
        return instant.replace(tzinfo=None)
    if kind == "date":
        return date.fromisoformat(data)
    if kind == "time":
        result = time.fromisoformat(data)
        if result.tzinfo is not None:
            raise ValueError()
        return result
    if kind == "duration":
        return timedelta(microseconds=int(data))
    if kind == "decimal":
        result = Decimal(data)
        if result.is_finite():
            return result
    if kind == "float":
        result = float(data)
        if math.isfinite(result):
            return result
    raise ValueError()


def encode_row(values) -> bytes:
    encoded = (
        json.dumps([encode_value(item) for item in values], separators=(",", ":")) + "\n"
    ).encode()
    if len(encoded) > MAX_RECORD_BYTES:
        raise BackupError(Reason.STORAGE)
    return encoded


def decode_row(raw: bytes, columns: int) -> tuple:
    try:
        if len(raw) > MAX_RECORD_BYTES or not raw.endswith(b"\n"):
            raise ValueError()
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != columns:
            raise ValueError()
        result = tuple(decode_value(value) for value in values)
        if encode_row(result) != raw:
            raise ValueError()
        return result
    except Exception:
        raise BackupError(Reason.INTEGRITY) from None
