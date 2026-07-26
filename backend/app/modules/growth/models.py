from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ShareStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class VoteChoice(StrEnum):
    BEFORE = "BEFORE"
    AFTER = "AFTER"


class ShareRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "share_records"
    __table_args__ = (
        Index("uq_share_records_scene_code", "scene_code", unique=True),
        Index("ix_share_records_user_created", "user_id", "created_at"),
        Index("uq_share_records_job_id", "job_id", unique=True),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
    )
    scene_code: Mapped[str] = mapped_column(String(64), nullable=False)
    share_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_assets.id", ondelete="RESTRICT"),
    )
    public_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[ShareStatus] = mapped_column(
        Enum(
            ShareStatus,
            name="share_status",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
        default=ShareStatus.PENDING,
        server_default=ShareStatus.PENDING.value,
    )
    attribution_source: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoteRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vote_records"
    __table_args__ = (
        UniqueConstraint(
            "share_id",
            "voter_fingerprint_hash",
            name="uq_vote_records_share_voter",
        ),
        Index("ix_vote_records_share_choice", "share_id", "choice"),
    )

    share_id: Mapped[UUID] = mapped_column(
        ForeignKey("share_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    voter_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    voter_fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    choice: Mapped[VoteChoice] = mapped_column(
        Enum(
            VoteChoice,
            name="vote_choice",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        nullable=False,
    )


class UserEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_events"
    __table_args__ = (
        UniqueConstraint(
            "event_name",
            "dedupe_key",
            name="uq_user_events_name_dedupe",
        ),
        Index("ix_user_events_name_created", "event_name", "created_at"),
        Index("ix_user_events_entity", "entity_type", "entity_id"),
        Index("ix_user_events_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    event_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    properties: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
