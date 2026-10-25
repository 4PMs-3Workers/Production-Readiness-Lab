from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.common import PageResponse, UtcTimestamp
from app.schemas.notifications import NotificationListItem, NotificationListQuery


def test_notification_list_query_has_bounded_pagination_and_unread_default():
    query = NotificationListQuery()

    assert (query.page, query.page_size, query.unread_only) == (1, 20, False)
    assert NotificationListQuery(page=2, page_size=100, unread_only=True).unread_only is True


@pytest.mark.parametrize(
    "values",
    [
        {"page": 0},
        {"page_size": 101},
        {"unread_only": "perhaps"},
        {"recipient_id": str(uuid4())},
    ],
)
def test_notification_list_query_rejects_invalid_or_client_owned_fields(values):
    with pytest.raises(ValidationError):
        NotificationListQuery.model_validate(values)


def test_notification_page_uses_shared_total_pagination_contract():
    item = NotificationListItem(
        id=uuid4(),
        event_type="APPOINTMENT_CREATED",
        summary="Lịch hẹn đã được tạo",
        created_at=UtcTimestamp(datetime(2026, 10, 22, tzinfo=UTC)),
        read_at=None,
        appointment_id=uuid4(),
        appointment_starts_at=UtcTimestamp(datetime(2026, 10, 23, tzinfo=UTC)),
        appointment_status="UPCOMING",
    )

    page = PageResponse[NotificationListItem](items=[item], page=1, page_size=20, total=1)
    assert page.items[0].read_at is None
    assert "recipient_id" not in page.model_dump()
    assert "description" not in page.model_dump()
