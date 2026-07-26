"""Add asynchronous share asset state.

Revision ID: 20260726_0005
Revises: 20260726_0004
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260726_0005"
down_revision: str | None = "20260726_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_share_records_share_status",
        "share_records",
        type_="check",
    )
    op.alter_column(
        "share_records",
        "status",
        server_default="PENDING",
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.alter_column(
        "share_records",
        "share_asset_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.add_column("share_records", sa.Column("job_id", sa.Uuid()))
    op.add_column(
        "share_records",
        sa.Column(
            "public_payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f("fk_share_records_job_id_generation_jobs"),
        "share_records",
        "generation_jobs",
        ["job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_share_records_job_id",
        "share_records",
        ["job_id"],
        unique=True,
    )
    op.create_check_constraint(
        "share_status",
        "share_records",
        "status IN ('PENDING', 'ACTIVE', 'FAILED', 'EXPIRED', 'REVOKED')",
    )


def downgrade() -> None:
    op.execute("UPDATE share_records SET status = 'REVOKED' WHERE status IN ('PENDING', 'FAILED')")
    op.execute("DELETE FROM share_records WHERE share_asset_id IS NULL")
    op.drop_constraint(
        op.f("ck_share_records_share_status"),
        "share_records",
        type_="check",
    )
    op.drop_index("uq_share_records_job_id", table_name="share_records")
    op.drop_constraint(
        op.f("fk_share_records_job_id_generation_jobs"),
        "share_records",
        type_="foreignkey",
    )
    op.drop_column("share_records", "public_payload")
    op.drop_column("share_records", "job_id")
    op.alter_column(
        "share_records",
        "share_asset_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.alter_column(
        "share_records",
        "status",
        server_default="ACTIVE",
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "share_status",
        "share_records",
        "status IN ('ACTIVE', 'EXPIRED', 'REVOKED')",
    )
