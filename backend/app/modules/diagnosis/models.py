from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DiagnosisStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OptimizationStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REJECTED_BY_CRITIC = "REJECTED_BY_CRITIC"
    FAILED = "FAILED"


class StyleDiagnosis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "style_diagnoses"
    __table_args__ = (
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score_range"),
        Index("ix_style_diagnoses_user_created", "user_id", "created_at"),
        Index("ix_style_diagnoses_job_id", "job_id", unique=True),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_photo_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_photos.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    occasion: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[DiagnosisStatus] = mapped_column(
        Enum(
            DiagnosisStatus,
            name="diagnosis_status",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
        default=DiagnosisStatus.PENDING,
        server_default=DiagnosisStatus.PENDING.value,
    )
    input_quality: Mapped[str | None] = mapped_column(String(40))
    input_quality_message: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    strengths: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    issues: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    primary_issue: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    optimization_plan: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    disclaimer: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(160))
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    schema_version: Mapped[str | None] = mapped_column(String(80))


class StyleOptimizationResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "style_optimization_results"
    __table_args__ = (
        CheckConstraint("change_level >= 1 AND change_level <= 3", name="change_level_range"),
        Index("ix_optimization_results_user_created", "user_id", "created_at"),
        Index("ix_optimization_results_diagnosis", "diagnosis_id"),
        Index("ix_optimization_results_job_id", "job_id", unique=True),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    diagnosis_id: Mapped[UUID] = mapped_column(
        ForeignKey("style_diagnoses.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    result_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_assets.id", ondelete="SET NULL"),
    )
    status: Mapped[OptimizationStatus] = mapped_column(
        Enum(
            OptimizationStatus,
            name="optimization_status",
            native_enum=False,
            create_constraint=True,
            length=40,
        ),
        nullable=False,
        default=OptimizationStatus.PENDING,
        server_default=OptimizationStatus.PENDING.value,
    )
    change_level: Mapped[int] = mapped_column(Integer, nullable=False)
    change_summary: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    quality_report: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(160))
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    schema_version: Mapped[str | None] = mapped_column(String(80))
