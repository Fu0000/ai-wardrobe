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
from app.modules.diagnosis.models import (
    DiagnosisStatus,
    OptimizationStatus,
    StyleDiagnosis,
    StyleOptimizationResult,
)
from app.modules.governance.models import QuotaType
from app.modules.governance.quota import QuotaRepository
from app.modules.growth.models import UserEvent
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.optimization.executor import OptimizationExecutor
from tests.integration.markers import requires_services

pytestmark = requires_services


async def test_optimization_completion_persists_one_funnel_event(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    user_id = uuid4()
    purge_users.append(user_id)
    optimization_job_id = uuid4()
    execution_token = str(uuid4())

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        source_asset = UserAsset(
            id=uuid4(),
            user_id=user_id,
            kind=AssetKind.USER_UPLOAD,
            status=AssetStatus.READY,
            bucket="integration",
            object_key=f"users/{user_id}/optimization-source.jpg",
            content_type="image/jpeg",
        )
        setup.add(source_asset)
        await setup.flush()
        source_photo = SourcePhoto(
            id=uuid4(),
            user_id=user_id,
            asset_id=source_asset.id,
            purpose=PhotoPurpose.OUTFIT_DIAGNOSIS,
        )
        setup.add(source_photo)
        diagnosis_job = GenerationJob(
            id=uuid4(),
            user_id=user_id,
            task_type=JobTaskType.STYLE_DIAGNOSIS,
            status=JobStatus.COMPLETED,
            idempotency_key=f"diagnosis-{uuid4().hex}",
            request_hash="a" * 64,
        )
        optimization_job = GenerationJob(
            id=optimization_job_id,
            user_id=user_id,
            task_type=JobTaskType.STYLE_OPTIMIZATION,
            status=JobStatus.PROCESSING,
            idempotency_key=f"optimization-{uuid4().hex}",
            request_hash="b" * 64,
            started_at=datetime.now(UTC) - timedelta(seconds=2),
            execution_token=execution_token,
            execution_lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        setup.add_all([diagnosis_job, optimization_job])
        await setup.flush()
        diagnosis = StyleDiagnosis(
            id=uuid4(),
            user_id=user_id,
            source_photo_id=source_photo.id,
            job_id=diagnosis_job.id,
            occasion="DAILY",
            status=DiagnosisStatus.COMPLETED,
        )
        setup.add(diagnosis)
        await setup.flush()
        setup.add(
            StyleOptimizationResult(
                id=uuid4(),
                user_id=user_id,
                diagnosis_id=diagnosis.id,
                job_id=optimization_job_id,
                status=OptimizationStatus.PENDING,
                change_level=1,
                change_summary=[],
            )
        )
        await QuotaRepository(setup).reserve(
            user_id=user_id,
            job_id=optimization_job_id,
            quota_type=QuotaType.OPTIMIZATION,
        )
        await setup.commit()

    result_asset_id = uuid4()
    executor = OptimizationExecutor(settings=settings, database=database)

    async def complete() -> bool:
        return await executor._complete(
            job_id=optimization_job_id,
            execution_token=execution_token,
            result_asset_id=result_asset_id,
            object_key=f"private/{user_id}/generated/{result_asset_id}.jpg",
            image_bytes=b"generated-image",
            width=1024,
            height=1536,
            image_model="integration-image-model",
            critic_model="integration-critic-model",
            reports=[{"overall_pass": False}, {"overall_pass": True}],
            accepted_attempt=2,
        )

    assert await complete() is True
    assert await complete() is False

    async with database.session_factory() as verify:
        events = list(
            (
                await verify.execute(
                    select(UserEvent).where(
                        UserEvent.event_name == "optimization.result.completed",
                        UserEvent.entity_id == optimization_job_id,
                    )
                )
            ).scalars()
        )
        generated_asset = await verify.get(UserAsset, result_asset_id)

    assert len(events) == 1
    assert events[0].user_id == user_id
    assert events[0].properties["job_id"] == str(optimization_job_id)
    assert events[0].properties["critic_attempts"] == 2
    latency_ms = events[0].properties["latency_ms"]
    assert isinstance(latency_ms, int)
    assert latency_ms >= 2_000
    assert events[0].request_id == f"job:{optimization_job_id}"
    assert events[0].app_channel == "worker"
    assert generated_asset is not None
    assert generated_asset.user_id == user_id
