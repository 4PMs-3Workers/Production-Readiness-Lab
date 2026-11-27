"""Join the booking quota and account lifecycle migration branches."""

revision = "20261127_1200_quota_acct_merge"
down_revision = ("20261113_1200_booking_quota", "20261114_1200_soft_delete")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
