"""Strict, bounded student cancellation request contract."""

import pytest
from pydantic import ValidationError

from app.schemas import appointments as schemas


def test_cancel_request_is_strict_optional_and_bounded():
    request = getattr(schemas, "AppointmentCancelRequest", None)
    assert request is not None, "Cancellation request contract is missing"
    assert request().reason is None
    assert request(reason=None).reason is None
    assert request(reason=" " + "x" * 1999).reason == " " + "x" * 1999
    for payload in ({"reason": "x" * 2001}, {"reason": 1}, {"reason": []}, {"status": "CANCELLED"}):
        with pytest.raises(ValidationError):
            request(**payload)
