"""诊断 Executor 的租约隔离与失败退款数据库级验证。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import Settings
from app.database.session import Database
from app.modules.assets.models import (
    AssetKind,
    AssetStatus,
    PhotoPurpose,
    SourcePhoto,
    UserAsset,
)
from app.modules.diagnosis.executor import DiagnosisExecutor
from app.modules.diagnosis.models import DiagnosisStatus, StyleDiagnosis
from app.modules.governance.models import (
    QuotaReservation,
    QuotaReservationStatus,
    QuotaType,
    UsageCounter,
)
from app.modules.governance.quota import QuotaRepository
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from tests.integration.markers import requires_services

pytestmark = requires_services


async def test_active_lease_is_fenced_and_retry_exhaustion_restores_quota(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    user_id = uuid4()
    purge_users.append(user_id)
    job_id = uuid4()
    asset_id = uuid4()
    source_photo_id = uuid4()
    execution_token = str(uuid4())

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        setup.add(
            UserAsset(
                id=asset_id,
                user_id=user_id,
                kind=AssetKind.USER_UPLOAD,
                status=AssetStatus.READY,
                bucket="integration",
                object_key=f"users/{user_id}/executor-test.jpg",
                content_type="image/jpeg",
            )
        )
        await setup.flush()
        setup.add(
            SourcePhoto(
                id=source_photo_id,
                user_id=user_id,
                asset_id=asset_id,
                purpose=PhotoPurpose.OUTFIT_DIAGNOSIS,
            )
        )
        setup.add(
            GenerationJob(
                id=job_id,
                user_id=user_id,
                task_type=JobTaskType.STYLE_DIAGNOSIS,
                status=JobStatus.PROCESSING,
                idempotency_key=f"executor-{uuid4().hex}",
                request_hash="d" * 64,
                execution_token=execution_token,
                execution_lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await setup.flush()
        setup.add(
            StyleDiagnosis(
                id=uuid4(),
                user_id=user_id,
                source_photo_id=source_photo_id,
                job_id=job_id,
                occasion="daily",
                status=DiagnosisStatus.PENDING,
            )
        )
        await QuotaRepository(setup).reserve(
            user_id=user_id,
            job_id=job_id,
            quota_type=QuotaType.DIAGNOSIS,
        )
        await setup.commit()

    executor = DiagnosisExecutor(settings=settings, database=database)

    assert await executor.run(job_id) == "SKIPPED", "有效租约不得被第二个 Worker 抢占"
    assert (
        await executor.finalize_failure(
            job_id,
            code="STALE_WORKER",
            user_message="不应写入",
            expected_execution_token=str(uuid4()),
        )
        is False
    )
    assert (
        await executor.finalize_failure(
            job_id,
            code="UNFENCED_FINALIZER",
            user_message="不应写入",
        )
        is False
    ), "未携带 token 的旧 Worker 不得终结仍在有效租约内的任务"

    async with database.session_factory() as before_finalize:
        job = await before_finalize.get(GenerationJob, job_id)
        reservation = (
            await before_finalize.execute(
                select(QuotaReservation).where(QuotaReservation.job_id == job_id)
            )
        ).scalar_one()
        assert job is not None
        assert job.status is JobStatus.PROCESSING
        assert job.execution_token == execution_token
        assert reservation.status is QuotaReservationStatus.RESERVED

    assert (
        await executor.finalize_failure(
            job_id,
            code="DIAGNOSIS_RETRY_EXHAUSTED",
            user_message="这次诊断没有成功，免费次数已退回。",
            expected_execution_token=execution_token,
        )
        is True
    )

    async with database.session_factory() as verify:
        job = await verify.get(GenerationJob, job_id)
        diagnosis = (
            await verify.execute(select(StyleDiagnosis).where(StyleDiagnosis.job_id == job_id))
        ).scalar_one()
        reservation = (
            await verify.execute(select(QuotaReservation).where(QuotaReservation.job_id == job_id))
        ).scalar_one()
        counters = list(
            (
                await verify.execute(select(UsageCounter).where(UsageCounter.user_id == user_id))
            ).scalars()
        )

        assert job is not None
        assert job.status is JobStatus.FAILED_FINAL
        assert job.error_code == "DIAGNOSIS_RETRY_EXHAUSTED"
        assert job.execution_token is None
        assert job.execution_lease_expires_at is None
        assert diagnosis.status is DiagnosisStatus.FAILED
        assert reservation.status is QuotaReservationStatus.RELEASED
        assert counters
        assert all(counter.reserved == 0 for counter in counters)
        assert all(counter.used == 0 for counter in counters)
