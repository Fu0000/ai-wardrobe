"""Add a fenced execution lease to generation jobs.

Revision ID: 20260726_0004
Revises: 20260726_0003
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0004"
down_revision: str | None = "20260726_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("execution_token", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "execution_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_generation_jobs_status_lease",
        "generation_jobs",
        ["status", "execution_lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_jobs_status_lease",
        table_name="generation_jobs",
    )
    op.drop_column("generation_jobs", "execution_lease_expires_at")
    op.drop_column("generation_jobs", "execution_token")
