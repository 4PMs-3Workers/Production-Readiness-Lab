from datetime import UTC, datetime, time, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects.mysql import dialect

from app.models import User
from app.models.types import BinaryUUID, SHA256Hash, TimeOfDay, UTCDateTime

MYSQL = dialect()


def test_uuid_uses_standard_bytes_without_swapping():
    value = UUID("00112233-4455-6677-8899-aabbccddeeff")
    adapter = BinaryUUID()
    assert adapter.process_bind_param(value, MYSQL) == bytes.fromhex(
        "00112233445566778899aabbccddeeff"
    )
    assert adapter.process_bind_param(str(value), MYSQL) == value.bytes
    assert adapter.process_result_value(value.bytes, MYSQL) == value


@pytest.mark.parametrize("value", [b"not-a-uuid", 1])
def test_uuid_rejects_ambiguous_values(value):
    with pytest.raises(TypeError):
        BinaryUUID().process_bind_param(value, MYSQL)


def test_uuid_rejects_invalid_strings():
    with pytest.raises(ValueError):
        BinaryUUID().process_bind_param("invalid", MYSQL)


def test_datetime_preserves_instant_and_microseconds():
    value = datetime(2026, 10, 11, 9, 30, 0, 123456, tzinfo=timezone(timedelta(hours=7)))
    adapter = UTCDateTime()
    stored = adapter.process_bind_param(value, MYSQL)
    assert stored == datetime(2026, 10, 11, 2, 30, 0, 123456)
    assert adapter.process_result_value(stored, MYSQL) == value.astimezone(UTC)


def test_datetime_rejects_naive_input():
    with pytest.raises(ValueError, match="timezone-aware"):
        UTCDateTime().process_bind_param(datetime(2026, 10, 11), MYSQL)


@pytest.mark.parametrize("adapter", [BinaryUUID(), UTCDateTime(), TimeOfDay(), SHA256Hash()])
def test_nullable_adapters_preserve_none(adapter):
    assert adapter.process_bind_param(None, MYSQL) is None
    if not isinstance(adapter, SHA256Hash):
        assert adapter.process_result_value(None, MYSQL) is None


def test_time_of_day_handles_driver_timedelta():
    adapter = TimeOfDay()
    assert adapter.process_bind_param(time(9, 30), MYSQL) == time(9, 30)
    assert adapter.process_result_value(timedelta(hours=9, minutes=30), MYSQL) == time(9, 30)


@pytest.mark.parametrize("value", [time(9, tzinfo=UTC), time(9, microsecond=1)])
def test_time_of_day_rejects_lossy_input(value):
    with pytest.raises(ValueError):
        TimeOfDay().process_bind_param(value, MYSQL)


@pytest.mark.parametrize("value", [timedelta(seconds=-1), timedelta(days=1)])
def test_time_of_day_rejects_duration_results(value):
    with pytest.raises(ValueError):
        TimeOfDay().process_result_value(value, MYSQL)


@pytest.mark.parametrize("value", [b"", b"x" * 31, b"x" * 33])
def test_hash_rejects_wrong_length(value):
    with pytest.raises(ValueError):
        SHA256Hash().process_bind_param(value, MYSQL)


def test_hash_preserves_raw_digest():
    digest = uuid4().bytes * 2
    assert SHA256Hash().process_bind_param(digest, MYSQL) == digest


def test_email_normalizes_on_assignment():
    user = User(email="  STUDENT@Example.COM  ")
    assert user.email == "student@example.com"
    user.email = "  NEXT@Example.COM "
    assert user.email == "next@example.com"
