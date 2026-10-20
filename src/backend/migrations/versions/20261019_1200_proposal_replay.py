"""Persist proposal creation receipts and optimistic creation contexts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261019_1200_proposal_replay"
down_revision = "20261017_2350_student_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("proposal_version", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        op.f("ck_appointments_proposal_version"),
        "appointments",
        "proposal_version BETWEEN 0 AND 9007199254740991",
    )
    op.add_column(
        "change_proposals",
        sa.Column(
            "creation_request_key", sa.String(128, collation="utf8mb4_0900_bin"), nullable=True
        ),
    )
    op.add_column(
        "change_proposals", sa.Column("creation_request_hash", mysql.VARBINARY(32), nullable=True)
    )
    op.add_column("change_proposals", sa.Column("created_version", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_change_proposals_creation_request"),
        "change_proposals",
        "(creation_request_key IS NULL AND creation_request_hash IS NULL "
        "AND created_version IS NULL) OR "
        "(creation_request_key IS NOT NULL AND creation_request_hash IS NOT NULL "
        "AND created_version IS NOT NULL AND CHAR_LENGTH(TRIM(creation_request_key)) > 0 "
        "AND OCTET_LENGTH(creation_request_hash) = 32 "
        "AND created_version BETWEEN 1 AND 9007199254740991)",
    )
    op.create_unique_constraint(
        "uq_change_proposals_creation_request",
        "change_proposals",
        ["appointment_id", "creation_request_key"],
    )
    op.create_unique_constraint(
        "uq_change_proposals_created_version",
        "change_proposals",
        ["appointment_id", "created_version"],
    )


def downgrade() -> None:
    used = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT COUNT(*) FROM appointments WHERE proposal_version <> 0) + "
                "(SELECT COUNT(*) FROM change_proposals WHERE creation_request_key IS NOT NULL "
                "OR creation_request_hash IS NOT NULL OR created_version IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    if used:
        raise RuntimeError("Cannot downgrade proposal replay: durable receipts would be lost")
    op.drop_constraint("uq_change_proposals_created_version", "change_proposals", type_="unique")
    op.drop_constraint("uq_change_proposals_creation_request", "change_proposals", type_="unique")
    op.drop_constraint(
        op.f("ck_change_proposals_creation_request"), "change_proposals", type_="check"
    )
    for column in ("created_version", "creation_request_hash", "creation_request_key"):
        op.drop_column("change_proposals", column)
    op.drop_constraint(op.f("ck_appointments_proposal_version"), "appointments", type_="check")
    op.drop_column("appointments", "proposal_version")
