"""Add optional student profile fields without changing existing account data."""

import sqlalchemy as sa
from alembic import op

revision = "20261017_2350_student_profile"
down_revision = "20261016_1200_audit_reqid128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("student_code", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("faculty", sa.String(150), nullable=True))
    op.add_column("users", sa.Column("major", sa.String(150), nullable=True))
    op.add_column(
        "users", sa.Column("gender", sa.String(20, collation="utf8mb4_0900_bin"), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_users_gender"),
        "users",
        "gender IS NULL OR gender IN ('MALE', 'FEMALE', 'OTHER', 'UNDISCLOSED')",
    )


def downgrade() -> None:
    populated = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM users WHERE student_code IS NOT NULL "
                "OR faculty IS NOT NULL OR major IS NOT NULL OR gender IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if populated:
        raise RuntimeError("Cannot downgrade student profile: populated fields would be lost")
    op.drop_constraint(op.f("ck_users_gender"), "users", type_="check")
    for name in ("gender", "major", "faculty", "student_code"):
        op.drop_column("users", name)
