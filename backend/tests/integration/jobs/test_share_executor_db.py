from dataclasses import dataclass
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
from app.modules.growth.executor import ShareAssetExecutor
from app.modules.growth.images import ShareImage
from app.modules.growth.models import ShareRecord, ShareStatus, UserEvent
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from tests.integration.markers import requires_services

pytestmark = requires_services


@dataclass(frozen=True, slots=True)
class ShareFixture:
    user_id: UUID
    job_id: UUID
    share_id: UUID
    execution_token: str


async def create_share_fixture(database: Database) -> ShareFixture:
    fixture = ShareFixture(
        user_id=uuid4(),
        job_id=uuid4(),
        share_id=uuid4(),
        execution_token=str(uuid4()),
    )
    async with database.session_factory() as setup:
        setup.add(User(id=fixture.user_id))
        await setup.flush()
        before_asset = UserAsset(
            id=uuid4(),
            user_id=fixture.user_id,
            kind=AssetKind.USER_UPLOAD,
            status=AssetStatus.READY,
            bucket="integration",
            object_key=f"users/{fixture.user_id}/share-before.jpg",
            content_type="image/jpeg",
        )
        after_asset = UserAsset(
            id=uuid4(),
            user_id=fixture.user_id,
            kind=AssetKind.OPTIMIZATION_RESULT,
            status=AssetStatus.READY,
            bucket="integration",
            object_key=f"users/{fixture.user_id}/share-after.jpg",
            content_type="image/jpeg",
        )
        setup.add_all([before_asset, after_asset])
        await setup.flush()
        source_photo = SourcePhoto(
            id=uuid4(),
            user_id=fixture.user_id,
            asset_id=before_asset.id,
            purpose=PhotoPurpose.OUTFIT_DIAGNOSIS,
        )
        setup.add(source_photo)
        diagnosis_job = GenerationJob(
            id=uuid4(),
            user_id=fixture.user_id,
            task_type=JobTaskType.STYLE_DIAGNOSIS,
            status=JobStatus.COMPLETED,
            idempotency_key=f"share-diagnosis-{uuid4().hex}",
            request_hash="a" * 64,
        )
        optimization_job = GenerationJob(
            id=uuid4(),
            user_id=fixture.user_id,
            task_type=JobTaskType.STYLE_OPTIMIZATION,
            status=JobStatus.COMPLETED,
            idempotency_key=f"share-optimization-{uuid4().hex}",
            request_hash="b" * 64,
        )
        share_job = GenerationJob(
            id=fixture.job_id,
            user_id=fixture.user_id,
            task_type=JobTaskType.SHARE_ASSET,
            status=JobStatus.PROCESSING,
            idempotency_key=f"share-{uuid4().hex}",
            request_hash="c" * 64,
            started_at=datetime.now(UTC) - timedelta(seconds=2),
            execution_token=fixture.execution_token,
            execution_lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        setup.add_all([diagnosis_job, optimization_job, share_job])
        await setup.flush()
        diagnosis = StyleDiagnosis(
            id=uuid4(),
            user_id=fixture.user_id,
            source_photo_id=source_photo.id,
            job_id=diagnosis_job.id,
            occasion="DAILY",
            status=DiagnosisStatus.COMPLETED,
        )
        setup.add(diagnosis)
        await setup.flush()
        optimization = StyleOptimizationResult(
            id=uuid4(),
            user_id=fixture.user_id,
            diagnosis_id=diagnosis.id,
            job_id=optimization_job.id,
            result_asset_id=after_asset.id,
            status=OptimizationStatus.COMPLETED,
            change_level=1,
            change_summary=[],
        )
        setup.add(optimization)
        await setup.flush()
        setup.add(
            ShareRecord(
                id=fixture.share_id,
                user_id=fixture.user_id,
                target_type="StyleOptimizationResult",
                target_id=optimization.id,
                job_id=fixture.job_id,
                scene_code=f"scene_{uuid4().hex}",
                public_payload={"score": 82, "template_version": "v1"},
                status=ShareStatus.PENDING,
            )
        )
        await setup.commit()
    return fixture


async def test_share_completion_is_idempotent_and_transactional(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    fixture = await create_share_fixture(database)
    purge_users.append(fixture.user_id)
    executor = ShareAssetExecutor(settings=settings, database=database)
    rendered = ShareImage(data=b"redacted-share-card", width=1_200, height=1_500)
    object_key = f"share-derivatives/{fixture.share_id}.jpg"

    async def complete() -> bool:
        return await executor._complete(
            job_id=fixture.job_id,
            execution_token=fixture.execution_token,
            object_key=object_key,
            rendered=rendered,
        )

    assert await complete() is True
    assert await complete() is False

    async with database.session_factory() as verify:
        job = await verify.get(GenerationJob, fixture.job_id)
        share = await verify.get(ShareRecord, fixture.share_id)
        derivative = (
            await verify.execute(select(UserAsset).where(UserAsset.object_key == object_key))
        ).scalar_one()
        events = list(
            (
                await verify.execute(
                    select(UserEvent).where(
                        UserEvent.event_name == "share.asset.created",
                        UserEvent.entity_id == fixture.share_id,
                    )
                )
            ).scalars()
        )

    assert job is not None
    assert job.status is JobStatus.COMPLETED
    assert job.result_reference_id == fixture.share_id
    assert share is not None
    assert share.status is ShareStatus.ACTIVE
    assert share.share_asset_id == derivative.id
    assert share.expires_at is not None
    assert derivative.kind is AssetKind.SHARE_DERIVATIVE
    assert derivative.user_id == fixture.user_id
    assert len(events) == 1
    assert events[0].properties["template_version"] == "v1"
    assert events[0].request_id == f"job:{fixture.job_id}"


async def test_share_failure_is_execution_token_fenced(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    fixture = await create_share_fixture(database)
    purge_users.append(fixture.user_id)
    executor = ShareAssetExecutor(settings=settings, database=database)

    assert (
        await executor.finalize_failure(
            fixture.job_id,
            code="STALE_SHARE_WORKER",
            user_message="不应写入",
            expected_execution_token=str(uuid4()),
        )
        is False
    )
    assert (
        await executor.finalize_failure(
            fixture.job_id,
            code="SHARE_RETRY_EXHAUSTED",
            user_message="分享卡片未生成，请重新创建。",
            expected_execution_token=fixture.execution_token,
        )
        is True
    )
    assert (
        await executor.finalize_failure(
            fixture.job_id,
            code="LATE_FINALIZER",
            user_message="不应覆盖",
            expected_execution_token=fixture.execution_token,
        )
        is False
    )

    async with database.session_factory() as verify:
        job = await verify.get(GenerationJob, fixture.job_id)
        share = await verify.get(ShareRecord, fixture.share_id)

    assert job is not None
    assert job.status is JobStatus.FAILED_FINAL
    assert job.error_code == "SHARE_RETRY_EXHAUSTED"
    assert job.execution_token is None
    assert share is not None
    assert share.status is ShareStatus.FAILED
