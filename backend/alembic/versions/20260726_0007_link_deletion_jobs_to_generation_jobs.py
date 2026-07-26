"""Link deletion jobs to durable generation jobs.

Revision ID: 20260726_0007
Revises: 20260726_0006
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0007"
down_revision: str | None = "20260726_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deletion_jobs", sa.Column("job_id", sa.Uuid()))
    op.create_foreign_key(
        op.f("fk_deletion_jobs_job_id_generation_jobs"),
        "deletion_jobs",
        "generation_jobs",
        ["job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_deletion_jobs_job_id",
        "deletion_jobs",
        ["job_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_deletion_jobs_job_id", table_name="deletion_jobs")
    op.drop_constraint(
        op.f("fk_deletion_jobs_job_id_generation_jobs"),
        "deletion_jobs",
        type_="foreignkey",
    )
    op.drop_column("deletion_jobs", "job_id")
