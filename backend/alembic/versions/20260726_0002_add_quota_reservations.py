"""Add an exactly-once quota reservation ledger.

Revision ID: 20260726_0002
Revises: 20260726_0001
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260726_0002"
down_revision: str | None = "20260726_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quota_reservations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "quota_type",
            sa.Enum(
                "DIAGNOSIS",
                "OPTIMIZATION",
                name="reservation_quota_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "RESERVED",
                "COMMITTED",
                "RELEASED",
                name="quota_reservation_status",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            server_default="RESERVED",
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), server_default="1", nullable=False),
        sa.Column("period_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount > 0",
            name=op.f("ck_quota_reservations_amount_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_quota_reservations_job_id_generation_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_quota_reservations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quota_reservations")),
        sa.UniqueConstraint("job_id", name=op.f("uq_quota_reservations_job_id")),
    )
    op.create_index(
        "ix_quota_reservations_status_created",
        "quota_reservations",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_quota_reservations_user_created",
        "quota_reservations",
        ["user_id", "created_at"],
    )

    quota_policy = sa.table(
        "quota_policies",
        sa.column("id", sa.Uuid()),
        sa.column("plan", sa.String()),
        sa.column("quota_type", sa.String()),
        sa.column("period", sa.String()),
        sa.column("limit_value", sa.Integer()),
    )
    op.bulk_insert(
        quota_policy,
        [
            {
                "id": "10000000-0000-0000-0000-000000000001",
                "plan": "FREE",
                "quota_type": "DIAGNOSIS",
                "period": "DAILY",
                "limit_value": 5,
            },
            {
                "id": "10000000-0000-0000-0000-000000000002",
                "plan": "FREE",
                "quota_type": "DIAGNOSIS",
                "period": "MONTHLY",
                "limit_value": 50,
            },
            {
                "id": "10000000-0000-0000-0000-000000000003",
                "plan": "FREE",
                "quota_type": "OPTIMIZATION",
                "period": "DAILY",
                "limit_value": 2,
            },
            {
                "id": "10000000-0000-0000-0000-000000000004",
                "plan": "FREE",
                "quota_type": "OPTIMIZATION",
                "period": "MONTHLY",
                "limit_value": 12,
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM quota_policies
            WHERE id IN (
                '10000000-0000-0000-0000-000000000001',
                '10000000-0000-0000-0000-000000000002',
                '10000000-0000-0000-0000-000000000003',
                '10000000-0000-0000-0000-000000000004'
            )
            """
        )
    )
    op.drop_index(
        "ix_quota_reservations_user_created",
        table_name="quota_reservations",
    )
    op.drop_index(
        "ix_quota_reservations_status_created",
        table_name="quota_reservations",
    )
    op.drop_table("quota_reservations")
