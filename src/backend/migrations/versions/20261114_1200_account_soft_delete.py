"""Add a reversible soft-delete lifecycle to user accounts."""

import sqlalchemy as sa
from alembic import op

revision = "20261114_1200_soft_delete"
down_revision = "20261019_1200_proposal_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("is_active_before_delete", sa.Boolean(), nullable=True))
    op.create_check_constraint(
        op.f("ck_users_soft_delete_state"),
        "users",
        "(deleted_at IS NULL AND is_active_before_delete IS NULL) OR "
        "(deleted_at IS NOT NULL AND is_active_before_delete IS NOT NULL AND is_active = 0)",
    )
    op.create_index("ix_users_deleted_at_role", "users", ["deleted_at", "role"])


def downgrade() -> None:
    used = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM users "
                "WHERE deleted_at IS NOT NULL OR is_active_before_delete IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if used:
        raise RuntimeError("Cannot downgrade account soft delete: lifecycle state would be lost")
    op.drop_index("ix_users_deleted_at_role", table_name="users")
    op.drop_constraint(op.f("ck_users_soft_delete_state"), "users", type_="check")
    op.drop_column("users", "is_active_before_delete")
    op.drop_column("users", "deleted_at")
