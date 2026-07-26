"""Add privacy-minimized closed-beta feedback.

Revision ID: 20260726_0008
Revises: 20260726_0007
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0008"
down_revision: str | None = "20260726_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_feedback",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("related_job_id", sa.Uuid()),
        sa.Column(
            "category",
            sa.Enum(
                "AI_QUALITY",
                "BUG",
                "EXPERIENCE",
                "PRIVACY",
                "OTHER",
                name="feedback_category",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "NEW",
                "TRIAGED",
                "RESOLVED",
                name="feedback_status",
                native_enum=False,
                length=24,
            ),
            server_default="NEW",
            nullable=False,
        ),
        sa.Column("rating", sa.Integer()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("page", sa.String(length=120)),
        sa.Column("app_version", sa.String(length=40)),
        sa.Column("platform", sa.String(length=40)),
        sa.Column("system_version", sa.String(length=80)),
        sa.Column("wechat_version", sa.String(length=40)),
        sa.Column("network_type", sa.String(length=24)),
        sa.Column("trace_id", sa.String(length=32)),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name=op.f("ck_beta_feedback_rating_range"),
        ),
        sa.ForeignKeyConstraint(
            ["related_job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_beta_feedback_related_job_id_generation_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_beta_feedback_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_beta_feedback")),
    )
    op.create_index(
        "ix_beta_feedback_status_created",
        "beta_feedback",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_beta_feedback_user_created",
        "beta_feedback",
        ["user_id", "created_at"],
    )
    op.create_index(
        "uq_beta_feedback_user_idempotency",
        "beta_feedback",
        ["user_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_beta_feedback_user_idempotency", table_name="beta_feedback")
    op.drop_index("ix_beta_feedback_user_created", table_name="beta_feedback")
    op.drop_index("ix_beta_feedback_status_created", table_name="beta_feedback")
    op.drop_table("beta_feedback")
