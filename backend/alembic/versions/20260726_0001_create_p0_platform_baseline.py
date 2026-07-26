"""Create the P0 platform baseline.

Revision ID: 20260726_0001
Revises:
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260726_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "outbox_events",
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=160), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "PUBLISHED",
                "FAILED",
                name="outbox_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("locked_by", sa.String(length=120)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_outbox_events_attempt_count_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_events_aggregate",
        "outbox_events",
        ["aggregate_type", "aggregate_id"],
    )
    op.create_index(
        "ix_outbox_events_dispatch",
        "outbox_events",
        ["status", "next_retry_at", "created_at"],
    )

    op.create_table(
        "quota_policies",
        sa.Column("plan", sa.String(length=40), nullable=False),
        sa.Column(
            "quota_type",
            sa.Enum(
                "DIAGNOSIS",
                "OPTIMIZATION",
                name="quota_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "period",
            sa.Enum(
                "DAILY",
                "MONTHLY",
                "LIFETIME",
                name="quota_period",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("limit_value", sa.Integer(), nullable=False),
        sa.Column("active_from", sa.DateTime(timezone=True)),
        sa.Column("active_until", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "limit_value >= 0",
            name=op.f("ck_quota_policies_limit_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quota_policies")),
    )
    op.create_index(
        "ix_quota_policies_lookup",
        "quota_policies",
        ["plan", "quota_type", "period"],
    )

    op.create_table(
        "users",
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DELETION_PENDING",
                "DELETED",
                name="user_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="ACTIVE",
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )

    op.create_table(
        "deletion_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "deletion_type",
            sa.Enum(
                "ACCOUNT",
                "PHOTO",
                "ASSET",
                name="deletion_type",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Uuid()),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "COMPLETED",
                "FAILED_RETRYABLE",
                "FAILED_FINAL",
                name="deletion_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "completed_steps",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
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
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_deletion_jobs_attempt_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_deletion_jobs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deletion_jobs")),
    )
    op.create_index(
        "ix_deletion_jobs_dispatch",
        "deletion_jobs",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "ix_deletion_jobs_user_created",
        "deletion_jobs",
        ["user_id", "created_at"],
    )

    op.create_table(
        "generation_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "task_type",
            sa.Enum(
                "STYLE_DIAGNOSIS",
                "STYLE_OPTIMIZATION",
                "SHARE_ASSET",
                "DELETION",
                name="job_task_type",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "QUEUED",
                "PROCESSING",
                "QUALITY_CHECKING",
                "COMPLETED",
                "FAILED_RETRYABLE",
                "FAILED_FINAL",
                "TIMED_OUT",
                "CANCELLED",
                name="job_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("user_message", sa.String(length=240)),
        sa.Column("model_policy_snapshot", postgresql.JSONB()),
        sa.Column("result_reference_type", sa.String(length=80)),
        sa.Column("result_reference_id", sa.Uuid()),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name=op.f("ck_generation_jobs_progress_range"),
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name=op.f("ck_generation_jobs_retry_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_generation_jobs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_jobs")),
        sa.UniqueConstraint(
            "user_id",
            "task_type",
            "idempotency_key",
            name="uq_generation_jobs_user_task_idempotency",
        ),
    )
    op.create_index(
        "ix_generation_jobs_status_created",
        "generation_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_generation_jobs_user_status",
        "generation_jobs",
        ["user_id", "status"],
    )

    op.create_table(
        "usage_counters",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "quota_type",
            sa.Enum(
                "DIAGNOSIS",
                "OPTIMIZATION",
                name="usage_quota_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("period_key", sa.String(length=20), nullable=False),
        sa.Column("used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
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
        sa.CheckConstraint(
            "used >= 0",
            name=op.f("ck_usage_counters_used_nonnegative"),
        ),
        sa.CheckConstraint(
            "reserved >= 0",
            name=op.f("ck_usage_counters_reserved_nonnegative"),
        ),
        sa.CheckConstraint(
            "version >= 0",
            name=op.f("ck_usage_counters_version_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_usage_counters_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_counters")),
        sa.UniqueConstraint(
            "user_id",
            "quota_type",
            "period_key",
            name="uq_usage_counters_user_quota_period",
        ),
    )

    op.create_table(
        "user_assets",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "USER_UPLOAD",
                "OPTIMIZATION_RESULT",
                "SHARE_DERIVATIVE",
                name="asset_kind",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "UPLOADING",
                "READY",
                "FAILED",
                "DELETION_PENDING",
                "DELETED",
                name="asset_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="UPLOADING",
            nullable=False,
        ),
        sa.Column("bucket", sa.String(length=120), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=120)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("checksum_sha256", sa.String(length=64)),
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
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name=op.f("ck_user_assets_size_nonnegative"),
        ),
        sa.CheckConstraint(
            "width IS NULL OR width > 0",
            name=op.f("ck_user_assets_width_positive"),
        ),
        sa.CheckConstraint(
            "height IS NULL OR height > 0",
            name=op.f("ck_user_assets_height_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_assets_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_assets")),
    )
    op.create_index(
        "ix_user_assets_user_status",
        "user_assets",
        ["user_id", "status"],
    )
    op.create_index(
        "uq_user_assets_object_key",
        "user_assets",
        ["object_key"],
        unique=True,
    )

    op.create_table(
        "user_identities",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "WECHAT",
                name="identity_provider",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("provider_subject_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_subject_encrypted", sa.Text(), nullable=False),
        sa.Column("union_subject_hash", sa.String(length=64)),
        sa.Column("union_subject_encrypted", sa.Text()),
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
            name=op.f("fk_user_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identities")),
    )
    op.create_index(
        "ix_user_identities_user_id",
        "user_identities",
        ["user_id"],
    )
    op.create_index(
        "uq_user_identities_provider_subject_hash",
        "user_identities",
        ["provider", "provider_subject_hash"],
        unique=True,
    )

    op.create_table(
        "ai_invocations",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "STARTED",
                "SUCCEEDED",
                "FAILED",
                "TIMED_OUT",
                name="invocation_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost_microunits", sa.BigInteger()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("error_detail", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "attempt >= 1",
            name=op.f("ck_ai_invocations_attempt_positive"),
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name=op.f("ck_ai_invocations_input_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name=op.f("ck_ai_invocations_output_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "estimated_cost_microunits IS NULL OR estimated_cost_microunits >= 0",
            name=op.f("ck_ai_invocations_cost_nonnegative"),
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name=op.f("ck_ai_invocations_latency_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_ai_invocations_job_id_generation_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ai_invocations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_invocations")),
    )
    op.create_index(
        "ix_ai_invocations_job_id",
        "ai_invocations",
        ["job_id"],
    )
    op.create_index(
        "ix_ai_invocations_user_created",
        "ai_invocations",
        ["user_id", "created_at"],
    )

    op.create_table(
        "share_records",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("scene_code", sa.String(length=64), nullable=False),
        sa.Column("share_asset_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "EXPIRED",
                "REVOKED",
                name="share_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("attribution_source", sa.String(length=120)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
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
            ["share_asset_id"],
            ["user_assets.id"],
            name=op.f("fk_share_records_share_asset_id_user_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_share_records_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share_records")),
    )
    op.create_index(
        "ix_share_records_user_created",
        "share_records",
        ["user_id", "created_at"],
    )
    op.create_index(
        "uq_share_records_scene_code",
        "share_records",
        ["scene_code"],
        unique=True,
    )

    op.create_table(
        "source_photos",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "OUTFIT_DIAGNOSIS",
                name="photo_purpose",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
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
            ["asset_id"],
            ["user_assets.id"],
            name=op.f("fk_source_photos_asset_id_user_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_source_photos_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_photos")),
        sa.UniqueConstraint(
            "asset_id",
            name=op.f("uq_source_photos_asset_id"),
        ),
    )
    op.create_index(
        "ix_source_photos_user_id",
        "source_photos",
        ["user_id"],
    )

    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=80)),
        sa.Column("avatar_asset_id", sa.Uuid()),
        sa.Column("consent_version", sa.String(length=40)),
        sa.Column(
            "has_ai_processing_consent",
            sa.Boolean(),
            server_default="false",
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
            ["avatar_asset_id"],
            ["user_assets.id"],
            name="fk_user_profiles_avatar_asset_id_user_assets",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_profiles")),
        sa.UniqueConstraint(
            "user_id",
            name=op.f("uq_user_profiles_user_id"),
        ),
    )

    op.create_table(
        "style_diagnoses",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_photo_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("occasion", sa.String(length=80), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "COMPLETED",
                "FAILED",
                name="diagnosis_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("score", sa.Integer()),
        sa.Column("strengths", postgresql.JSONB()),
        sa.Column("issues", postgresql.JSONB()),
        sa.Column("primary_issue", sa.Text()),
        sa.Column("optimization_plan", postgresql.JSONB()),
        sa.Column("model_version", sa.String(length=160)),
        sa.Column("prompt_version", sa.String(length=80)),
        sa.Column("schema_version", sa.String(length=80)),
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
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name=op.f("ck_style_diagnoses_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_style_diagnoses_job_id_generation_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_photo_id"],
            ["source_photos.id"],
            name=op.f("fk_style_diagnoses_source_photo_id_source_photos"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_style_diagnoses_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_style_diagnoses")),
    )
    op.create_index(
        "ix_style_diagnoses_job_id",
        "style_diagnoses",
        ["job_id"],
        unique=True,
    )
    op.create_index(
        "ix_style_diagnoses_user_created",
        "style_diagnoses",
        ["user_id", "created_at"],
    )

    op.create_table(
        "vote_records",
        sa.Column("share_id", sa.Uuid(), nullable=False),
        sa.Column("voter_user_id", sa.Uuid()),
        sa.Column("voter_fingerprint_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "choice",
            sa.Enum(
                "BEFORE",
                "AFTER",
                name="vote_choice",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
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
            ["share_id"],
            ["share_records.id"],
            name=op.f("fk_vote_records_share_id_share_records"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["voter_user_id"],
            ["users.id"],
            name=op.f("fk_vote_records_voter_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vote_records")),
        sa.UniqueConstraint(
            "share_id",
            "voter_fingerprint_hash",
            name="uq_vote_records_share_voter",
        ),
    )
    op.create_index(
        "ix_vote_records_share_choice",
        "vote_records",
        ["share_id", "choice"],
    )

    op.create_table(
        "style_optimization_results",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("diagnosis_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("result_asset_id", sa.Uuid()),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "COMPLETED",
                "REJECTED_BY_CRITIC",
                "FAILED",
                name="optimization_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("change_level", sa.Integer(), nullable=False),
        sa.Column("change_summary", postgresql.JSONB(), nullable=False),
        sa.Column("quality_report", postgresql.JSONB()),
        sa.Column("model_version", sa.String(length=160)),
        sa.Column("prompt_version", sa.String(length=80)),
        sa.Column("schema_version", sa.String(length=80)),
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
        sa.CheckConstraint(
            "change_level >= 1 AND change_level <= 3",
            name=op.f("ck_style_optimization_results_change_level_range"),
        ),
        sa.ForeignKeyConstraint(
            ["diagnosis_id"],
            ["style_diagnoses.id"],
            name=op.f("fk_style_optimization_results_diagnosis_id_style_diagnoses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_style_optimization_results_job_id_generation_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_asset_id"],
            ["user_assets.id"],
            name=op.f("fk_style_optimization_results_result_asset_id_user_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_style_optimization_results_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_style_optimization_results"),
        ),
    )
    op.create_index(
        "ix_optimization_results_diagnosis",
        "style_optimization_results",
        ["diagnosis_id"],
    )
    op.create_index(
        "ix_optimization_results_job_id",
        "style_optimization_results",
        ["job_id"],
        unique=True,
    )
    op.create_index(
        "ix_optimization_results_user_created",
        "style_optimization_results",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("style_optimization_results")
    op.drop_table("vote_records")
    op.drop_table("style_diagnoses")
    op.drop_table("user_profiles")
    op.drop_table("source_photos")
    op.drop_table("share_records")
    op.drop_table("ai_invocations")
    op.drop_table("user_identities")
    op.drop_table("user_assets")
    op.drop_table("usage_counters")
    op.drop_table("generation_jobs")
    op.drop_table("deletion_jobs")
    op.drop_table("users")
    op.drop_table("quota_policies")
    op.drop_table("outbox_events")

    # Extensions are provisioned at the database level and may be shared by
    # other schemas, so application migrations intentionally never drop them.
