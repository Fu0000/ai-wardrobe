"""Add the complete structured diagnosis contract fields.

Revision ID: 20260726_0003
Revises: 20260726_0002
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260726_0003"
down_revision: str | None = "20260726_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "style_diagnoses",
        "primary_issue",
        existing_type=sa.Text(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="""
        CASE
          WHEN primary_issue IS NULL THEN NULL
          ELSE jsonb_build_object(
            'category', 'UNKNOWN',
            'title', '主要问题',
            'explanation', primary_issue,
            'expected_impact', '改善整体协调感',
            'confidence', 'LOW'
          )
        END
        """,
    )
    op.add_column(
        "style_diagnoses",
        sa.Column("input_quality", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "style_diagnoses",
        sa.Column("input_quality_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "style_diagnoses",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "style_diagnoses",
        sa.Column("disclaimer", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("style_diagnoses", "disclaimer")
    op.drop_column("style_diagnoses", "summary")
    op.drop_column("style_diagnoses", "input_quality_message")
    op.drop_column("style_diagnoses", "input_quality")
    op.alter_column(
        "style_diagnoses",
        "primary_issue",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.Text(),
        postgresql_using="primary_issue ->> 'explanation'",
    )
