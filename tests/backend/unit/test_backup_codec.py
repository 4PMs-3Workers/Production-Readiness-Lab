from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.backup.codec import MAX_RECORD_BYTES, decode_row, encode_row
from app.backup.config import BackupError


def test_rows_roundtrip_all_application_scalar_types_without_loss():
    values = (
        None,
        True,
        2**63,
        "fictional\n\u1ee9",
        bytes(range(16)),
        datetime(2026, 10, 28, 1, 2, 3, 456, UTC),
        date(2026, 10, 28),
        time(8, 30),
        timedelta(hours=-1, microseconds=1),
        Decimal("1.2300"),
        0.125,
    )
    raw = encode_row(values)
    decoded = decode_row(raw, len(values))
    assert encode_row(decoded) == raw
    assert decoded[4] == values[4] and decoded[5] == values[5].replace(tzinfo=None)


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"[]\n",
        b'[["int","01"]]\n',
        b'[["bytes","???"]]\n',
        b'[["float","nan"]]\n',
        b'[["str",1]]\n',
        b'[["datetime","2026-10-28T00:00:00"]]\n',
        b'[["unknown","x"]]\n',
    ],
)
def test_invalid_truncated_or_noncanonical_rows_fail_closed(raw):
    with pytest.raises(BackupError):
        decode_row(raw, 1)


def test_record_size_is_bounded():
    with pytest.raises(BackupError):
        encode_row(["x" * MAX_RECORD_BYTES])
