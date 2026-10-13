"""Initial schema — all 11 application tables.

Revision ID: a1b2c3d4e5f6
Revises: None
Create Date: 2026-10-13 10:00:00.000000

This revision creates the complete initial database schema for the Production
Readiness Lab application.  It was authored by hand from the SQLAlchemy model
definitions in app.models and the database design document
(docs/architecture/database-design.md).  Do NOT regenerate it with
``alembic revision --autogenerate``; edit this file directly for any corrections
and validate against a fresh MySQL 8.4 database before applying to shared
environments.

Table creation order respects FK dependencies:
  1.  users               — no FK dependencies
  2.  purposes            — no FK dependencies
  3.  service_settings    — no FK dependencies
  4.  service_hours       — no FK dependencies
  5.  sessions            — FK → users
  6.  staff_purposes      — FK → users, purposes
  7.  staff_unavailability — FK → users
  8.  appointments        — FK → users (×3), staff_purposes (composite)
  9.  change_proposals    — FK → appointments, users (×2)
  10. notifications       — FK → users, appointments
  11. audit_events        — FK → users (nullable)

DESTRUCTIVE DOWNGRADE WARNING
------------------------------
The downgrade() function drops all 11 application tables in reverse FK order.
It is intended ONLY for disposable test and local development databases.
Running downgrade on a shared or production database is UNSUPPORTED and will
result in permanent data loss.  Recovery requires a verified backup and a
manual schema inspection — see docs/tasks/database-migrations.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Shared table options for all InnoDB/utf8mb4 tables.
_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

# Collation constants matching app.models.base.
_CODE_COLLATION = "utf8mb4_0900_bin"
_EMAIL_COLLATION = "utf8mb4_0900_as_ci"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column(
            "email",
            sa.String(254, collation=_EMAIL_COLLATION),
            nullable=False,
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column(
            "role",
            sa.String(10, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(email)) > 0", name="ck_users_email_not_empty"),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_users_name_not_empty"),
        sa.CheckConstraint(
            "CAST(email AS BINARY) = CAST(LOWER(TRIM(email)) AS BINARY)",
            name="ck_users_email_normalized",
        ),
        sa.CheckConstraint(
            "role IN ('STUDENT', 'STAFF', 'ADMIN')",
            name="ck_users_role",
        ),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_users_is_active"),
        **_TABLE_KW,
    )

    # ------------------------------------------------------------------
    # 2. purposes
    # ------------------------------------------------------------------
    op.create_table(
        "purposes",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column(
            "code",
            sa.String(50, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column(
            "requires_free_text",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_purposes"),
        sa.UniqueConstraint("code", name="uq_purposes_code"),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(code)) > 0", name="ck_purposes_code_not_empty"),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_purposes_name_not_empty"),
        sa.CheckConstraint("duration_minutes > 0", name="ck_purposes_duration"),
        sa.CheckConstraint("requires_free_text IN (0, 1)", name="ck_purposes_requires_free_text"),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_purposes_is_active"),
        **_TABLE_KW,
    )

    # ------------------------------------------------------------------
    # 3. service_settings  (singleton: id = 1 enforced by CHECK)
    # ------------------------------------------------------------------
    op.create_table(
        "service_settings",
        sa.Column(
            "id",
            sa.SmallInteger(),
            autoincrement=False,
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "timezone",
            sa.String(64),
            server_default=sa.text("'Asia/Ho_Chi_Minh'"),
            nullable=False,
        ),
        sa.Column(
            "start_step_minutes",
            sa.SmallInteger(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.Column(
            "booking_horizon_days",
            sa.SmallInteger(),
            server_default=sa.text("7"),
            nullable=False,
        ),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_service_settings"),
        sa.CheckConstraint("id = 1", name="ck_service_settings_singleton"),
        sa.CheckConstraint("start_step_minutes > 0", name="ck_service_settings_start_step"),
        sa.CheckConstraint("booking_horizon_days > 0", name="ck_service_settings_booking_horizon"),
        **_TABLE_KW,
    )

    # ------------------------------------------------------------------
    # 4. service_hours
    # ------------------------------------------------------------------
    op.create_table(
        "service_hours",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("opens_at", mysql.TIME(fsp=0), nullable=False),
        sa.Column("closes_at", mysql.TIME(fsp=0), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_service_hours"),
        sa.UniqueConstraint("weekday", "opens_at", "closes_at", name="uq_service_hours_window"),
        sa.CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_service_hours_weekday"),
        sa.CheckConstraint(
            "opens_at >= '00:00:00' AND opens_at < closes_at AND closes_at < '24:00:00'",
            name="ck_service_hours_interval",
        ),
        **_TABLE_KW,
    )

    # ------------------------------------------------------------------
    # 5. sessions  (FK → users)
    # ------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column("user_id", mysql.BINARY(16), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_sessions_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint("OCTET_LENGTH(token_hash) = 32", name="ck_sessions_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expiry"),
        **_TABLE_KW,
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    # ------------------------------------------------------------------
    # 6. staff_purposes  (composite PK; FK → users, purposes)
    # ------------------------------------------------------------------
    op.create_table(
        "staff_purposes",
        sa.Column("staff_id", mysql.BINARY(16), nullable=False),
        sa.Column("purpose_id", mysql.BINARY(16), nullable=False),
        sa.Column(
            "is_accepting",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("staff_id", "purpose_id", name="pk_staff_purposes"),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["users.id"],
            name="fk_staff_purposes_staff_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purpose_id"],
            ["purposes.id"],
            name="fk_staff_purposes_purpose_id_purposes",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint("is_accepting IN (0, 1)", name="ck_staff_purposes_is_accepting"),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_staff_purposes_accepting",
        "staff_purposes",
        ["purpose_id", "is_accepting", "staff_id"],
    )

    # ------------------------------------------------------------------
    # 7. staff_unavailability  (FK → users)
    # ------------------------------------------------------------------
    op.create_table(
        "staff_unavailability",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column("staff_id", mysql.BINARY(16), nullable=False),
        sa.Column("starts_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("ends_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_staff_unavailability"),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["users.id"],
            name="fk_staff_unavailability_staff_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_staff_unavailability_interval"),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_staff_unavailability_interval",
        "staff_unavailability",
        ["staff_id", "starts_at", "ends_at"],
    )

    # ------------------------------------------------------------------
    # 8. appointments  (FK → users ×3, composite FK → staff_purposes)
    # ------------------------------------------------------------------
    op.create_table(
        "appointments",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column("student_id", mysql.BINARY(16), nullable=False),
        sa.Column("staff_id", mysql.BINARY(16), nullable=False),
        sa.Column("purpose_id", mysql.BINARY(16), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("other_reason", sa.String(2000), nullable=True),
        sa.Column("starts_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("ends_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("location", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.String(20, collation=_CODE_COLLATION),
            server_default=sa.text("'UPCOMING'"),
            nullable=False,
        ),
        sa.Column(
            "creation_request_key",
            sa.String(128, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column("creation_request_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("cancelled_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("cancelled_by", mysql.BINARY(16), nullable=True),
        sa.Column("cancel_reason", sa.String(2000), nullable=True),
        sa.Column("outcome_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("outcome_by", mysql.BINARY(16), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
        sa.UniqueConstraint(
            "student_id", "creation_request_key", name="uq_appointments_creation_request"
        ),
        # Composite FK: (staff_id, purpose_id) → staff_purposes
        sa.ForeignKeyConstraint(
            ["staff_id", "purpose_id"],
            ["staff_purposes.staff_id", "staff_purposes.purpose_id"],
            name="fk_appointments_staff_purpose",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            name="fk_appointments_student_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["users.id"],
            name="fk_appointments_cancelled_by_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outcome_by"],
            ["users.id"],
            name="fk_appointments_outcome_by_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('UPCOMING', 'CHANGE_PENDING', 'CANCELLED', 'COMPLETED', 'NO_SHOW')",
            name="ck_appointments_status",
        ),
        sa.CheckConstraint(
            "duration_minutes > 0 AND ends_at > starts_at "
            "AND DATE_ADD(starts_at, INTERVAL duration_minutes MINUTE) IS NOT NULL "
            "AND ends_at = DATE_ADD(starts_at, INTERVAL duration_minutes MINUTE)",
            name="ck_appointments_interval",
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(creation_request_key)) > 0 "
            "AND OCTET_LENGTH(creation_request_hash) = 32",
            name="ck_appointments_creation_request",
        ),
        sa.CheckConstraint(
            "(status = 'CANCELLED' AND cancelled_at IS NOT NULL) OR "
            "(status <> 'CANCELLED' AND cancelled_at IS NULL AND cancel_reason IS NULL)",
            name="ck_appointments_cancellation",
        ),
        sa.CheckConstraint(
            "(status IN ('COMPLETED', 'NO_SHOW') AND outcome_at IS NOT NULL) OR "
            "(status NOT IN ('COMPLETED', 'NO_SHOW') AND outcome_at IS NULL)",
            name="ck_appointments_outcome",
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_appointments_student_interval",
        "appointments",
        ["student_id", "status", "starts_at", "ends_at"],
    )
    op.create_index(
        "ix_appointments_staff_interval",
        "appointments",
        ["staff_id", "status", "starts_at", "ends_at"],
    )
    op.create_index(
        "ix_appointments_staff_purpose",
        "appointments",
        ["staff_id", "purpose_id"],
    )
    op.create_index(
        "ix_appointments_cancelled_by",
        "appointments",
        ["cancelled_by"],
    )
    op.create_index(
        "ix_appointments_outcome_by",
        "appointments",
        ["outcome_by"],
    )
    op.create_index(
        "ix_appointments_student_history",
        "appointments",
        [
            "student_id",
            sa.literal_column("starts_at DESC"),
            sa.literal_column("id DESC"),
        ],
    )
    op.create_index(
        "ix_appointments_staff_calendar",
        "appointments",
        ["staff_id", "starts_at", "id"],
    )

    # ------------------------------------------------------------------
    # 9. change_proposals  (FK → appointments, users ×2; virtual generated col)
    # ------------------------------------------------------------------
    op.create_table(
        "change_proposals",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column("appointment_id", mysql.BINARY(16), nullable=False),
        sa.Column("created_by", mysql.BINARY(16), nullable=False),
        sa.Column("proposed_starts_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("proposed_ends_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("proposed_location", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.String(20, collation=_CODE_COLLATION),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        # Virtual generated column: CASE WHEN status = 'PENDING' THEN 1 ELSE NULL END
        # Used with the UNIQUE constraint to enforce at-most-one PENDING proposal per
        # appointment.  persisted=False → VIRTUAL in MySQL (not stored on disk).
        sa.Column(
            "pending_guard",
            mysql.TINYINT(),
            sa.Computed("CASE WHEN status = 'PENDING' THEN 1 ELSE NULL END", persisted=False),
            nullable=True,
        ),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("resolved_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("resolved_by", mysql.BINARY(16), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_change_proposals"),
        sa.UniqueConstraint("appointment_id", "pending_guard", name="uq_change_proposals_pending"),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_change_proposals_appointment_id_appointments",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_change_proposals_created_by_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["users.id"],
            name="fk_change_proposals_resolved_by_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'DECLINED', 'EXPIRED', 'SUPERSEDED', 'CANCELLED')",
            name="ck_change_proposals_status",
        ),
        sa.CheckConstraint(
            "proposed_ends_at > proposed_starts_at AND expires_at > created_at",
            name="ck_change_proposals_interval",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND resolved_at IS NULL) OR "
            "(status <> 'PENDING' AND resolved_at IS NOT NULL)",
            name="ck_change_proposals_resolution",
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_change_proposals_expiry",
        "change_proposals",
        ["status", "expires_at", "id"],
    )
    op.create_index(
        "ix_change_proposals_created_by",
        "change_proposals",
        ["created_by"],
    )
    op.create_index(
        "ix_change_proposals_resolved_by",
        "change_proposals",
        ["resolved_by"],
    )
    op.create_index(
        "ix_change_proposals_history",
        "change_proposals",
        [
            "appointment_id",
            sa.literal_column("created_at DESC"),
            sa.literal_column("id DESC"),
        ],
    )

    # ------------------------------------------------------------------
    # 10. notifications  (FK → users, appointments)
    # ------------------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column("recipient_id", mysql.BINARY(16), nullable=False),
        sa.Column("appointment_id", mysql.BINARY(16), nullable=False),
        sa.Column(
            "event_type",
            sa.String(50, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column("read_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["users.id"],
            name="fk_notifications_recipient_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_notifications_appointment_id_appointments",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_notifications_appointment_id",
        "notifications",
        ["appointment_id"],
    )
    op.create_index(
        "ix_notifications_recipient_history",
        "notifications",
        [
            "recipient_id",
            sa.literal_column("created_at DESC"),
            sa.literal_column("id DESC"),
        ],
    )

    # ------------------------------------------------------------------
    # 11. audit_events  (nullable FK → users)
    # ------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", mysql.BINARY(16), nullable=False),
        sa.Column("actor_id", mysql.BINARY(16), nullable=True),
        sa.Column(
            "action",
            sa.String(60, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column(
            "entity_type",
            sa.String(50, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(100, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.String(100, collation=_CODE_COLLATION),
            nullable=False,
        ),
        sa.Column("metadata", mysql.JSON(), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_audit_events_actor_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_audit_events_timeline",
        "audit_events",
        ["entity_type", "entity_id", "created_at", "id"],
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])


def downgrade() -> None:
    # DESTRUCTIVE — drops all 11 application tables in reverse FK dependency order.
    # Permitted ONLY for disposable test and local development databases.
    # Do NOT run on shared or production environments.
    # See docs/tasks/database-migrations.md for recovery and rollback guidance.
    op.drop_table("audit_events")
    op.drop_table("notifications")
    op.drop_table("change_proposals")
    op.drop_table("appointments")
    op.drop_table("staff_unavailability")
    op.drop_table("staff_purposes")
    op.drop_table("sessions")
    op.drop_table("service_hours")
    op.drop_table("service_settings")
    op.drop_table("purposes")
    op.drop_table("users")
