"""Add privacy-safe attribution events.

Revision ID: 20260726_0006
Revises: 20260726_0005
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260726_0006"
down_revision: str | None = "20260726_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_events",
        sa.Column("user_id", sa.Uuid()),
        sa.Column("event_name", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_events_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_events")),
        sa.UniqueConstraint(
            "event_name",
            "dedupe_key",
            name="uq_user_events_name_dedupe",
        ),
    )
    op.create_index(
        "ix_user_events_name_created",
        "user_events",
        ["event_name", "created_at"],
    )
    op.create_index(
        "ix_user_events_entity",
        "user_events",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_user_events_user_created",
        "user_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_events_user_created", table_name="user_events")
    op.drop_index("ix_user_events_entity", table_name="user_events")
    op.drop_index("ix_user_events_name_created", table_name="user_events")
    op.drop_table("user_events")
