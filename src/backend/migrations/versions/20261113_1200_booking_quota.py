"""Configure the maximum number of active bookings per student."""

import sqlalchemy as sa
from alembic import op

revision = "20261113_1200_booking_quota"
down_revision = "20261019_1200_proposal_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_settings",
        sa.Column(
            "max_active_appointments",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_service_settings_max_active_appointments"),
        "service_settings",
        "max_active_appointments > 0",
    )


def downgrade() -> None:
    value = (
        op.get_bind()
        .execute(sa.text("SELECT max_active_appointments FROM service_settings WHERE id = 1"))
        .scalar_one_or_none()
    )
    if value not in (None, 3):
        raise RuntimeError("Cannot downgrade booking quota: configured value would be lost")
    op.drop_constraint(
        op.f("ck_service_settings_max_active_appointments"),
        "service_settings",
        type_="check",
    )
    op.drop_column("service_settings", "max_active_appointments")
