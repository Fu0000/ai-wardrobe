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
from app.modules.diagnosis.schema import DiagnosisOutput
from app.modules.governance.models import (
    QuotaReservation,
    QuotaReservationStatus,
    QuotaType,
    UsageCounter,
)
from app.modules.governance.quota import QuotaRepository
from app.modules.growth.models import UserEvent
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from tests.integration.markers import requires_services

pytestmark = requires_services


def _completed_diagnosis_output() -> DiagnosisOutput:
    return DiagnosisOutput.model_validate(
        {
            "input_quality": "ACCEPTABLE",
            "input_quality_message": None,
            "score": 82,
            "summary": "整体清爽，调整上下身比例后会更利落。",
            "strengths": [
                {
                    "title": "配色克制",
                    "explanation": "上衣与裤装使用低饱和色，视觉上比较统一。",
                    "visual_evidence": "上下装没有突兀的大面积撞色。",
                    "confidence": "HIGH",
                }
            ],
            "issues": [
                {
                    "title": "腰线偏低",
                    "explanation": "上衣下摆覆盖较多，缩短了下半身视觉比例。",
                    "visual_evidence": "上衣下摆遮住裤腰位置。",
                    "confidence": "HIGH",
                }
            ],
            "primary_issue": {
                "category": "PROPORTION",
                "title": "先恢复腰线",
                "explanation": "当前上衣长度让上下身比例显得平均，缺少明确重心。",
                "expected_impact": "让整体轮廓更利落并拉长下半身。",
                "confidence": "HIGH",
            },
            "optimization_plan": [
                {
                    "priority": 1,
                    "action": "ADJUST_WEARING",
                    "instruction": "把上衣前摆轻塞进裤腰。",
                    "reason": "用最小改变露出腰线，不需要替换现有单品。",
                    "preserves": "现有上衣、裤装和配色",
                }
            ],
            "disclaimer": "建议仅基于照片中可见的穿搭信息。",
        }
    )


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


async def test_diagnosis_completion_persists_one_funnel_event(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    user_id = uuid4()
    purge_users.append(user_id)
    job_id = uuid4()
    execution_token = str(uuid4())

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        asset = UserAsset(
            id=uuid4(),
            user_id=user_id,
            kind=AssetKind.USER_UPLOAD,
            status=AssetStatus.READY,
            bucket="integration",
            object_key=f"users/{user_id}/completion-event.jpg",
            content_type="image/jpeg",
        )
        setup.add(asset)
        await setup.flush()
        source_photo = SourcePhoto(
            id=uuid4(),
            user_id=user_id,
            asset_id=asset.id,
            purpose=PhotoPurpose.OUTFIT_DIAGNOSIS,
        )
        setup.add(source_photo)
        await setup.flush()
        job = GenerationJob(
            id=job_id,
            user_id=user_id,
            task_type=JobTaskType.STYLE_DIAGNOSIS,
            status=JobStatus.PROCESSING,
            idempotency_key=f"completion-{uuid4().hex}",
            request_hash="e" * 64,
            started_at=datetime.now(UTC) - timedelta(seconds=1),
            execution_token=execution_token,
            execution_lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        setup.add(job)
        await setup.flush()
        setup.add(
            StyleDiagnosis(
                id=uuid4(),
                user_id=user_id,
                source_photo_id=source_photo.id,
                job_id=job_id,
                occasion="DAILY",
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
    assert (
        await executor._complete(
            job_id=job_id,
            output=_completed_diagnosis_output(),
            provider="integration",
            model="diagnosis-model",
            execution_token=execution_token,
        )
        is True
    )
    assert (
        await executor._complete(
            job_id=job_id,
            output=_completed_diagnosis_output(),
            provider="integration",
            model="diagnosis-model",
            execution_token=execution_token,
        )
        is False
    )

    async with database.session_factory() as verify:
        events = list(
            (
                await verify.execute(
                    select(UserEvent).where(
                        UserEvent.event_name == "diagnosis.text.completed",
                        UserEvent.entity_id == job_id,
                    )
                )
            ).scalars()
        )

    assert len(events) == 1
    assert events[0].user_id == user_id
    assert events[0].properties["job_id"] == str(job_id)
    latency_ms = events[0].properties["latency_ms"]
    assert isinstance(latency_ms, int)
    assert latency_ms >= 1_000
    assert events[0].request_id == f"job:{job_id}"
    assert events[0].app_channel == "worker"
