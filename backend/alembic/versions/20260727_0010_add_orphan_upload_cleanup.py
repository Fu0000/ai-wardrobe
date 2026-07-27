"""Add recoverable states and indexes for orphan upload cleanup.

Revision ID: 20260727_0010
Revises: 20260726_0009
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260727_0010"
down_revision: str | None = "20260726_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT_NAME = "asset_status"
_FULL_CONSTRAINT_NAME = "ck_user_assets_asset_status"
_OLD_VALUES = (
    "UPLOADING",
    "READY",
    "FAILED",
    "DELETION_PENDING",
    "DELETED",
)
_NEW_VALUES = (
    "UPLOADING",
    "UPLOAD_EXPIRED",
    "UPLOAD_CLEANING",
    "READY",
    "FAILED",
    "DELETION_PENDING",
    "DELETED",
)


def _values_clause(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(op.f(_FULL_CONSTRAINT_NAME), "user_assets", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "user_assets",
        _values_clause(_NEW_VALUES),
    )
    op.create_index(
        "ix_user_assets_status_created",
        "user_assets",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_user_assets_status_updated",
        "user_assets",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    # 恢复旧约束前，把清理中的行收敛为 FAILED，避免回滚被既有新状态阻断。
    op.execute(
        "UPDATE user_assets SET status = 'FAILED' "
        "WHERE status IN ('UPLOAD_EXPIRED', 'UPLOAD_CLEANING')"
    )
    op.drop_index("ix_user_assets_status_updated", table_name="user_assets")
    op.drop_index("ix_user_assets_status_created", table_name="user_assets")
    op.drop_constraint(op.f(_FULL_CONSTRAINT_NAME), "user_assets", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "user_assets",
        _values_clause(_OLD_VALUES),
    )
