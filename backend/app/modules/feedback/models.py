from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FeedbackCategory(StrEnum):
    AI_QUALITY = "AI_QUALITY"
    BUG = "BUG"
    EXPERIENCE = "EXPERIENCE"
    PRIVACY = "PRIVACY"
    OTHER = "OTHER"


class FeedbackStatus(StrEnum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    RESOLVED = "RESOLVED"


class BetaFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "beta_feedback"
    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="rating_range",
        ),
        Index(
            "uq_beta_feedback_user_idempotency",
            "user_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_beta_feedback_status_created", "status", "created_at"),
        Index("ix_beta_feedback_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    related_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"),
    )
    category: Mapped[FeedbackCategory] = mapped_column(
        Enum(
            FeedbackCategory,
            name="feedback_category",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
    )
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(
            FeedbackStatus,
            name="feedback_status",
            native_enum=False,
            create_constraint=True,
            length=24,
        ),
        nullable=False,
        default=FeedbackStatus.NEW,
        server_default=FeedbackStatus.NEW.value,
    )
    rating: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[str | None] = mapped_column(String(120))
    app_version: Mapped[str | None] = mapped_column(String(40))
    platform: Mapped[str | None] = mapped_column(String(40))
    system_version: Mapped[str | None] = mapped_column(String(80))
    wechat_version: Mapped[str | None] = mapped_column(String(40))
    network_type: Mapped[str | None] = mapped_column(String(24))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
