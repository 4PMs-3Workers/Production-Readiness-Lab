from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.common import PaginationParams, UtcTimestamp


def test_pagination_defaults_and_inclusive_limits():
    assert PaginationParams().model_dump() == {"page": 1, "page_size": 20}
    assert PaginationParams(page=1, page_size=100).page_size == 100


@pytest.mark.parametrize("data", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
def test_pagination_rejects_out_of_range(data):
    with pytest.raises(ValidationError):
        PaginationParams(**data)


def test_timestamp_rejects_naive_and_preserves_utc_microseconds():
    with pytest.raises(ValidationError):
        UtcTimestamp.model_validate(datetime(2026, 10, 13, 12, 0))
    value = datetime(2026, 10, 13, 19, 0, 0, 123456, tzinfo=timezone(timedelta(hours=7)))
    result = UtcTimestamp.model_validate(value)
    assert result.model_dump_json() == '"2026-10-13T12:00:00.123456Z"'
