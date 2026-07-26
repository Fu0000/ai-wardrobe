"""Allow outbox events to reach a dead-letter terminal state.

Revision ID: 20260726_0009
Revises: 20260726_0008
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260726_0009"
down_revision: str | None = "20260726_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 状态列使用 native_enum=False，取值由 CheckConstraint 约束，
# 因此新增枚举值必须重建该约束。
_CONSTRAINT_NAME = "outbox_status"
_OLD_VALUES = ("PENDING", "PROCESSING", "PUBLISHED", "FAILED")
_NEW_VALUES = (*_OLD_VALUES, "DEAD_LETTER")


def _values_clause(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "outbox_events", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "outbox_events",
        _values_clause(_NEW_VALUES),
    )


def downgrade() -> None:
    # 回滚前必须先把死信收敛回 FAILED，否则新约束会拒绝既有行。
    # 这些事件本就无法发布，回到 FAILED 会恢复无限重试，属于已知的回滚代价。
    op.execute(
        "UPDATE outbox_events SET status = 'FAILED', next_retry_at = NULL "
        "WHERE status = 'DEAD_LETTER'"
    )
    op.drop_constraint(_CONSTRAINT_NAME, "outbox_events", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "outbox_events",
        _values_clause(_OLD_VALUES),
    )
