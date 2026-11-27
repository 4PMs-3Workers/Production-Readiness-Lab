"""Add a reversible soft-delete lifecycle to user accounts."""

import sqlalchemy as sa
from alembic import op

revision = "20261114_1200_soft_delete"
down_revision = "20261019_1200_proposal_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # InnoDB commits MySQL DDL implicitly. Keep the table change in one atomic ALTER.
    op.execute("""
        ALTER TABLE users
            ADD COLUMN deleted_at DATETIME(6) NULL,
            ADD COLUMN is_active_before_delete BOOLEAN NULL,
            ADD CONSTRAINT ck_users_soft_delete_state CHECK (
                (deleted_at IS NULL AND is_active_before_delete IS NULL)
                OR (
                    deleted_at IS NOT NULL
                    AND is_active_before_delete IS NOT NULL
                    AND is_active = 0
                )
            ),
            ADD INDEX ix_users_deleted_at_role (deleted_at, role)
        """)


def downgrade() -> None:
    used = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM users "
                "WHERE deleted_at IS NOT NULL OR is_active_before_delete IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    if used:
        raise RuntimeError("Cannot downgrade account soft delete: lifecycle state would be lost")
    op.execute("""
        ALTER TABLE users
            DROP CHECK ck_users_soft_delete_state,
            DROP INDEX ix_users_deleted_at_role,
            DROP COLUMN is_active_before_delete,
            DROP COLUMN deleted_at
        """)
