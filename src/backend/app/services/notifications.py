"""Notification inbox orchestration and persistent read-state behavior."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import Settings
from app.core.errors import ServiceUnavailable
from app.core.transactions import (
    CommitOutcomeUnknown,
    commit_safely,
    read_transaction,
    rollback,
)
from app.domain.auth import AuthContext
from app.repositories import notifications as notification_repository
from app.schemas.common import PageResponse
from app.schemas.notifications import (
    NotificationListItem,
    NotificationListQuery,
    NotificationReadResponse,
)

_EVENT_SUMMARIES = {
    "APPOINTMENT_CREATED": "Lịch hẹn đã được tạo",
    "APPOINTMENT_CANCELLED": "Lịch hẹn đã bị hủy",
    "APPOINTMENT_OUTCOME_RECORDED": "Kết quả lịch hẹn đã được cập nhật",
    "CHANGE_PROPOSAL_CREATED": "Có đề xuất thay đổi lịch hẹn",
    "CHANGE_PROPOSAL_REPLACED": "Đề xuất thay đổi lịch hẹn đã được cập nhật",
    "CHANGE_PROPOSAL_ACCEPTED": "Đề xuất thay đổi lịch hẹn đã được chấp nhận",
    "CHANGE_PROPOSAL_DECLINED": "Đề xuất thay đổi lịch hẹn đã bị từ chối",
    "CHANGE_PROPOSAL_EXPIRED": "Đề xuất thay đổi lịch hẹn đã hết hạn",
}
_GENERIC_SUMMARY = "Bạn có một thông báo mới"


def _not_found() -> AppError:
    return AppError(
        404,
        "NOT_FOUND",
        "Notification not found",
        "input",
        {"Cache-Control": "private, no-store"},
    )


def _require_inbox_role(context: AuthContext) -> None:
    if context.role not in {"STUDENT", "STAFF"}:
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")


async def list_notifications(
    db: AsyncSession,
    *,
    context: AuthContext,
    query: NotificationListQuery,
    settings: Settings,
) -> PageResponse[NotificationListItem]:
    _require_inbox_role(context)
    async with read_transaction(db, settings):
        rows, total = await notification_repository.read_notification_page(
            db,
            recipient_id=context.user_id,
            page=query.page,
            page_size=query.page_size,
            unread_only=query.unread_only,
        )
        items: list[NotificationListItem] = []
        for row in rows:
            notification = row[0]
            known_event = notification.event_type in _EVENT_SUMMARIES
            if context.role == "STUDENT":
                can_view_appointment = row.appointment_student_id == context.user_id
            else:
                can_view_appointment = row.appointment_staff_id == context.user_id
            can_link = known_event and can_view_appointment
            items.append(
                NotificationListItem(
                    id=notification.id,
                    event_type=notification.event_type,
                    summary=(
                        _EVENT_SUMMARIES.get(notification.event_type, _GENERIC_SUMMARY)
                        if can_view_appointment
                        else _GENERIC_SUMMARY
                    ),
                    created_at=notification.created_at,
                    read_at=notification.read_at,
                    appointment_id=notification.appointment_id if can_link else None,
                    appointment_starts_at=row.appointment_starts_at if can_link else None,
                    appointment_status=row.appointment_status if can_link else None,
                )
            )
        return PageResponse[NotificationListItem](
            items=items,
            page=query.page,
            page_size=query.page_size,
            total=total,
        )


async def mark_notification_read(
    db: AsyncSession,
    *,
    notification_id: UUID,
    context: AuthContext,
    settings: Settings,
) -> NotificationReadResponse:
    _require_inbox_role(context)

    try:
        async with asyncio.timeout(settings.db_query_timeout_seconds):
            for attempt in range(2):
                transaction = db.begin()
                await transaction.start()
                try:
                    candidate_read_at = datetime.now(UTC)
                    found, updated, persisted_read_at = (
                        await notification_repository.mark_read_if_unread(
                            db,
                            notification_id=notification_id,
                            recipient_id=context.user_id,
                            read_at=candidate_read_at,
                        )
                    )
                    if not found:
                        await transaction.rollback()
                        raise _not_found()
                    if not updated:
                        await transaction.rollback()
                        if persisted_read_at is None:
                            raise ServiceUnavailable()
                        return NotificationReadResponse(
                            id=notification_id, read_at=persisted_read_at
                        )

                    if persisted_read_at is None:
                        await transaction.rollback()
                        raise ServiceUnavailable()
                    await commit_safely(db, transaction)
                    return NotificationReadResponse(
                        id=notification_id,
                        read_at=persisted_read_at,
                    )
                except CommitOutcomeUnknown:
                    async with read_transaction(db, settings):
                        found, persisted_read_at = (
                            await notification_repository.read_notification_state(
                                db,
                                notification_id=notification_id,
                                recipient_id=context.user_id,
                            )
                        )
                    if not found:
                        raise _not_found() from None
                    if persisted_read_at is not None:
                        return NotificationReadResponse(
                            id=notification_id, read_at=persisted_read_at
                        )
                    if attempt == 0:
                        continue
                    raise ServiceUnavailable() from None
                except AppError:
                    raise
                except SQLAlchemyError:
                    await rollback(db)
                    raise ServiceUnavailable() from None
                except BaseException:
                    await rollback(db)
                    raise
            raise ServiceUnavailable() from None
    except TimeoutError:
        await rollback(db)
        raise ServiceUnavailable() from None
