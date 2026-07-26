from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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


class JobStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    QUALITY_CHECKING = "QUALITY_CHECKING"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class JobTaskType(StrEnum):
    STYLE_DIAGNOSIS = "STYLE_DIAGNOSIS"
    STYLE_OPTIMIZATION = "STYLE_OPTIMIZATION"
    SHARE_ASSET = "SHARE_ASSET"
    DELETION = "DELETION"


class InvocationStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class GenerationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        UniqueConstraint(
            "user_id",
            "task_type",
            "idempotency_key",
            name="uq_generation_jobs_user_task_idempotency",
        ),
        Index("ix_generation_jobs_user_status", "user_id", "status"),
        Index("ix_generation_jobs_status_created", "status", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[JobTaskType] = mapped_column(
        Enum(
            JobTaskType,
            name="job_task_type",
            native_enum=False,
            create_constraint=True,
            length=40,
        ),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            native_enum=False,
            create_constraint=True,
            length=40,
        ),
        nullable=False,
        default=JobStatus.PENDING,
        server_default=JobStatus.PENDING.value,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(80))
    user_message: Mapped[str | None] = mapped_column(String(240))
    model_policy_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    result_reference_type: Mapped[str | None] = mapped_column(String(80))
    result_reference_id: Mapped[UUID | None] = mapped_column()
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_token: Mapped[str | None] = mapped_column(String(36))
    execution_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_invocations"
    __table_args__ = (
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="input_tokens_nonnegative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="output_tokens_nonnegative",
        ),
        CheckConstraint(
            "estimated_cost_microunits IS NULL OR estimated_cost_microunits >= 0",
            name="cost_nonnegative",
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"),
        Index("ix_ai_invocations_job_id", "job_id"),
        Index("ix_ai_invocations_user_created", "user_id", "created_at"),
    )

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[InvocationStatus] = mapped_column(
        Enum(
            InvocationStatus,
            name="invocation_status",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_microunits: Mapped[int | None] = mapped_column(BigInteger)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
