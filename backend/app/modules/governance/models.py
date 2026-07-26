from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class QuotaType(StrEnum):
    DIAGNOSIS = "DIAGNOSIS"
    OPTIMIZATION = "OPTIMIZATION"


class QuotaPeriod(StrEnum):
    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    LIFETIME = "LIFETIME"


class DeletionType(StrEnum):
    ACCOUNT = "ACCOUNT"
    PHOTO = "PHOTO"
    ASSET = "ASSET"


class DeletionStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"


class QuotaReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"


class QuotaPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "quota_policies"
    __table_args__ = (
        CheckConstraint("limit_value >= 0", name="limit_nonnegative"),
        Index("ix_quota_policies_lookup", "plan", "quota_type", "period"),
    )

    plan: Mapped[str] = mapped_column(String(40), nullable=False)
    quota_type: Mapped[QuotaType] = mapped_column(
        Enum(
            QuotaType,
            name="quota_type",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
    )
    period: Mapped[QuotaPeriod] = mapped_column(
        Enum(
            QuotaPeriod,
            name="quota_period",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        nullable=False,
    )
    limit_value: Mapped[int] = mapped_column(Integer, nullable=False)
    active_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageCounter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_counters"
    __table_args__ = (
        CheckConstraint("used >= 0", name="used_nonnegative"),
        CheckConstraint("reserved >= 0", name="reserved_nonnegative"),
        CheckConstraint("version >= 0", name="version_nonnegative"),
        UniqueConstraint(
            "user_id",
            "quota_type",
            "period_key",
            name="uq_usage_counters_user_quota_period",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    quota_type: Mapped[QuotaType] = mapped_column(
        Enum(
            QuotaType,
            name="usage_quota_type",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
    )
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class QuotaReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "quota_reservations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_quota_reservations_user_created", "user_id", "created_at"),
        Index("ix_quota_reservations_status_created", "status", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    quota_type: Mapped[QuotaType] = mapped_column(
        Enum(
            QuotaType,
            name="reservation_quota_type",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
    )
    status: Mapped[QuotaReservationStatus] = mapped_column(
        Enum(
            QuotaReservationStatus,
            name="quota_reservation_status",
            native_enum=False,
            create_constraint=True,
            length=24,
        ),
        nullable=False,
        default=QuotaReservationStatus.RESERVED,
        server_default=QuotaReservationStatus.RESERVED.value,
    )
    amount: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    period_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeletionJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deletion_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index("ix_deletion_jobs_user_created", "user_id", "created_at"),
        Index("ix_deletion_jobs_dispatch", "status", "next_retry_at"),
        Index("uq_deletion_jobs_job_id", "job_id", unique=True),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
    )
    deletion_type: Mapped[DeletionType] = mapped_column(
        Enum(
            DeletionType,
            name="deletion_type",
            native_enum=False,
            create_constraint=True,
            length=24,
        ),
        nullable=False,
    )
    target_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[DeletionStatus] = mapped_column(
        Enum(
            DeletionStatus,
            name="deletion_status",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
        default=DeletionStatus.PENDING,
        server_default=DeletionStatus.PENDING.value,
    )
    completed_steps: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
