from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import Settings
from app.database.session import Database
from app.modules.assets.models import AssetKind, AssetStatus, UserAsset
from app.modules.governance.deletion_executor import DeletionExecutor
from app.modules.governance.models import DeletionJob, DeletionStatus, DeletionType
from app.modules.growth.models import UserEvent
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from tests.integration.markers import requires_services

pytestmark = requires_services


async def test_asset_deletion_completion_purges_target_and_is_idempotent(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    user_id = uuid4()
    purge_users.append(user_id)
    asset_id = uuid4()
    job_id = uuid4()
    deletion_id = uuid4()
    execution_token = str(uuid4())
    object_key = f"users/{user_id}/delete-me.jpg"

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        setup.add(
            UserAsset(
                id=asset_id,
                user_id=user_id,
                kind=AssetKind.USER_UPLOAD,
                status=AssetStatus.DELETION_PENDING,
                bucket="integration",
                object_key=object_key,
                content_type="image/jpeg",
            )
        )
        job = GenerationJob(
            id=job_id,
            user_id=user_id,
            task_type=JobTaskType.DELETION,
            status=JobStatus.PROCESSING,
            idempotency_key=f"asset-deletion-{uuid4().hex}",
            request_hash="d" * 64,
            started_at=datetime.now(UTC) - timedelta(seconds=2),
            execution_token=execution_token,
            execution_lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        setup.add(job)
        await setup.flush()
        setup.add(
            DeletionJob(
                id=deletion_id,
                user_id=user_id,
                job_id=job_id,
                deletion_type=DeletionType.ASSET,
                target_id=asset_id,
                status=DeletionStatus.PROCESSING,
                attempt_count=1,
            )
        )
        await setup.commit()

    executor = DeletionExecutor(settings=settings, database=database)
    decision = await executor._complete_if_stable(
        job_id=job_id,
        execution_token=execution_token,
        deleted_keys={object_key},
    )
    repeated = await executor._complete_if_stable(
        job_id=job_id,
        execution_token=execution_token,
        deleted_keys={object_key},
    )

    assert decision.completed is True
    assert decision.stale is False
    assert repeated.completed is False
    assert repeated.stale is True

    async with database.session_factory() as verify:
        verified_job = await verify.get(GenerationJob, job_id)
        deletion = await verify.get(DeletionJob, deletion_id)
        asset = await verify.get(UserAsset, asset_id)
        events = list(
            (
                await verify.execute(
                    select(UserEvent).where(
                        UserEvent.event_name == "privacy.deletion.completed",
                        UserEvent.entity_id == deletion_id,
                    )
                )
            ).scalars()
        )

    assert asset is None
    assert verified_job is not None
    assert verified_job.status is JobStatus.COMPLETED
    assert verified_job.result_reference_id == deletion_id
    assert deletion is not None
    assert deletion.status is DeletionStatus.COMPLETED
    assert deletion.completed_steps == [
        "COS_OBJECTS_DELETED",
        "BUSINESS_DATA_PURGED",
        "OBJECT_ACCESS_REVOKED",
    ]
    assert len(events) == 1
    assert events[0].properties["deletion_type"] == "ASSET"
    assert events[0].request_id == f"job:{job_id}"
