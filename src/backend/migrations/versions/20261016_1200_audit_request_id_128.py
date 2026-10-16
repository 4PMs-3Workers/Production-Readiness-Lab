"""Expand audit request IDs to match the gateway contract.

Revision ID: 20261016_1200_audit_request_id_128
Revises: a1b2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_1200_audit_request_id_128"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "audit_events",
        "request_id",
        existing_type=sa.String(length=100, collation="utf8mb4_0900_bin"),
        type_=sa.String(length=128, collation="utf8mb4_0900_bin"),
        existing_nullable=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    too_long = connection.execute(
        sa.text("SELECT COUNT(*) FROM audit_events WHERE CHAR_LENGTH(request_id) > 100")
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            "Cannot downgrade audit_events.request_id: stored request_id value is longer "
            "than 100 characters"
        )
    op.alter_column(
        "audit_events",
        "request_id",
        existing_type=sa.String(length=128, collation="utf8mb4_0900_bin"),
        type_=sa.String(length=100, collation="utf8mb4_0900_bin"),
        existing_nullable=False,
    )
