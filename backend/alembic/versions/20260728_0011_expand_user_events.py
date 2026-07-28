"""Expand user events with privacy-safe analytics context.

Revision ID: 20260728_0011
Revises: 20260727_0010
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0011"
down_revision: str | None = "20260727_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_events",
        sa.Column("event_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "user_events",
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_events",
        sa.Column("environment", sa.String(length=32), server_default="unknown", nullable=False),
    )
    op.add_column(
        "user_events",
        sa.Column("trace_id", sa.String(length=64), server_default="unavailable", nullable=False),
    )
    op.add_column(
        "user_events",
        sa.Column(
            "request_id",
            sa.String(length=128),
            server_default="unavailable",
            nullable=False,
        ),
    )
    op.add_column("user_events", sa.Column("user_id_hash", sa.String(length=64)))
    op.add_column("user_events", sa.Column("session_id", sa.String(length=64)))
    op.add_column("user_events", sa.Column("client_version", sa.String(length=32)))
    op.add_column(
        "user_events",
        sa.Column("platform", sa.String(length=32), server_default="server", nullable=False),
    )
    op.add_column(
        "user_events",
        sa.Column("app_channel", sa.String(length=32), server_default="server", nullable=False),
    )
    op.create_check_constraint(
        "ck_user_events_event_version_positive",
        "user_events",
        "event_version >= 1",
    )
    op.create_index(
        "ix_user_events_name_occurred",
        "user_events",
        ["event_name", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_events_name_occurred", table_name="user_events")
    op.drop_constraint(
        "ck_user_events_event_version_positive",
        "user_events",
        type_="check",
    )
    op.drop_column("user_events", "app_channel")
    op.drop_column("user_events", "platform")
    op.drop_column("user_events", "client_version")
    op.drop_column("user_events", "session_id")
    op.drop_column("user_events", "user_id_hash")
    op.drop_column("user_events", "request_id")
    op.drop_column("user_events", "trace_id")
    op.drop_column("user_events", "environment")
    op.drop_column("user_events", "occurred_at")
    op.drop_column("user_events", "event_version")
