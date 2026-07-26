from uuid import UUID, uuid4

import pytest

from app.modules.assets.models import AssetKind, AssetStatus, UserAsset
from app.modules.events.models import OutboxEvent
from app.modules.governance.deletion_service import (
    DeletionApplicationService,
    DeletionServiceError,
)
from app.modules.governance.models import (
    DeletionJob,
    DeletionStatus,
    DeletionType,
)
from app.modules.identity.models import User, UserStatus
from app.modules.jobs.models import GenerationJob, JobTaskType


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: list[GenerationJob] = []
        self.events: list[OutboxEvent] = []

    async def find_idempotent(
        self,
        *,
        user_id: UUID,
        task_type: JobTaskType,
        idempotency_key: str,
    ) -> GenerationJob | None:
        return next(
            (
                job
                for job in self.jobs
                if job.user_id == user_id
                and job.task_type == task_type
                and job.idempotency_key == idempotency_key
            ),
            None,
        )

    async def create_with_outbox(
        self,
        *,
        job: GenerationJob,
        event: OutboxEvent,
    ) -> bool:
        self.jobs.append(job)
        self.events.append(event)
        return True


class FakeDeletionRepository:
    def __init__(self) -> None:
        self.deletions: list[DeletionJob] = []
        self.assets: list[UserAsset] = []

    async def latest_account(self, *, user_id: UUID) -> DeletionJob | None:
        return next(
            (
                deletion
                for deletion in reversed(self.deletions)
                if deletion.user_id == user_id and deletion.deletion_type == DeletionType.ACCOUNT
            ),
            None,
        )

    async def latest_asset(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
    ) -> DeletionJob | None:
        return next(
            (
                deletion
                for deletion in reversed(self.deletions)
                if deletion.user_id == user_id
                and deletion.deletion_type == DeletionType.ASSET
                and deletion.target_id == asset_id
            ),
            None,
        )

    async def get_owned_asset(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
        for_update: bool = False,
    ) -> UserAsset | None:
        return next(
            (asset for asset in self.assets if asset.id == asset_id and asset.user_id == user_id),
            None,
        )

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> DeletionJob | None:
        return next(
            (
                deletion
                for deletion in self.deletions
                if deletion.job_id == job_id and deletion.user_id == user_id
            ),
            None,
        )

    async def create(
        self,
        *,
        deletion_id: UUID,
        user_id: UUID,
        job_id: UUID,
        deletion_type: DeletionType,
        target_id: UUID | None = None,
    ) -> DeletionJob:
        deletion = DeletionJob(
            id=deletion_id,
            user_id=user_id,
            job_id=job_id,
            deletion_type=deletion_type,
            target_id=target_id,
            status=DeletionStatus.PENDING,
        )
        self.deletions.append(deletion)
        return deletion

    async def mark_user_deletion_pending(self, user: User) -> None:
        user.status = UserStatus.DELETION_PENDING

    async def mark_asset_deletion_pending(self, asset: UserAsset) -> None:
        asset.status = AssetStatus.DELETION_PENDING


def service_fixture() -> tuple[
    DeletionApplicationService,
    FakeDeletionRepository,
    FakeJobRepository,
]:
    deletions = FakeDeletionRepository()
    jobs = FakeJobRepository()
    return (
        DeletionApplicationService(
            deletion_repository=deletions,  # type: ignore[arg-type]
            job_repository=jobs,  # type: ignore[arg-type]
        ),
        deletions,
        jobs,
    )


@pytest.mark.asyncio
async def test_account_deletion_is_idempotent_and_freezes_business_access() -> None:
    service, deletions, jobs = service_fixture()
    user = User(id=uuid4(), status=UserStatus.ACTIVE)

    first = await service.request_account_deletion(
        user=user,
        idempotency_key="delete-account-request",
    )
    repeated = await service.request_account_deletion(
        user=user,
        idempotency_key="another-key-is-not-used",
    )

    assert first.reused is False
    assert repeated.reused is True
    assert repeated.deletion.id == first.deletion.id
    assert user.status == UserStatus.DELETION_PENDING
    assert len(deletions.deletions) == 1
    assert len(jobs.jobs) == 1
    assert jobs.jobs[0].task_type == JobTaskType.DELETION
    assert len(jobs.events) == 1


@pytest.mark.asyncio
async def test_failed_final_deletion_can_be_recreated_with_a_new_key() -> None:
    service, deletions, jobs = service_fixture()
    user = User(id=uuid4(), status=UserStatus.DELETION_PENDING)
    failed = DeletionJob(
        id=uuid4(),
        user_id=user.id,
        job_id=uuid4(),
        deletion_type=DeletionType.ACCOUNT,
        status=DeletionStatus.FAILED_FINAL,
    )
    deletions.deletions.append(failed)

    created = await service.request_account_deletion(
        user=user,
        idempotency_key="delete-account-retry",
    )

    assert created.reused is False
    assert created.deletion.id != failed.id
    assert len(jobs.jobs) == 1


@pytest.mark.asyncio
async def test_deleted_account_cannot_create_another_deletion() -> None:
    service, _, _ = service_fixture()
    user = User(id=uuid4(), status=UserStatus.DELETED)

    with pytest.raises(
        DeletionServiceError,
        match="ACCOUNT_ALREADY_DELETED",
    ):
        await service.request_account_deletion(
            user=user,
            idempotency_key="delete-account-request",
        )


@pytest.mark.asyncio
async def test_asset_deletion_is_scoped_idempotent_and_marks_asset_pending() -> None:
    service, deletions, jobs = service_fixture()
    user_id = uuid4()
    asset = UserAsset(
        id=uuid4(),
        user_id=user_id,
        kind=AssetKind.USER_UPLOAD,
        status=AssetStatus.READY,
        bucket="test",
        object_key=f"private/{user_id}/photo.jpg",
    )
    deletions.assets.append(asset)

    first = await service.request_asset_deletion(
        user_id=user_id,
        asset_id=asset.id,
        idempotency_key="delete-photo-request",
    )
    repeated = await service.request_asset_deletion(
        user_id=user_id,
        asset_id=asset.id,
        idempotency_key="unused-after-active-request",
    )

    assert first.reused is False
    assert repeated.reused is True
    assert first.deletion.target_id == asset.id
    assert first.deletion.deletion_type == DeletionType.ASSET
    assert asset.status == AssetStatus.DELETION_PENDING
    assert len(jobs.jobs) == 1
    assert jobs.jobs[0].task_type == JobTaskType.DELETION
    assert len(jobs.events) == 1


@pytest.mark.asyncio
async def test_asset_deletion_does_not_reveal_another_users_asset() -> None:
    service, deletions, jobs = service_fixture()
    owner_id = uuid4()
    asset = UserAsset(
        id=uuid4(),
        user_id=owner_id,
        kind=AssetKind.USER_UPLOAD,
        status=AssetStatus.READY,
        bucket="test",
        object_key=f"private/{owner_id}/photo.jpg",
    )
    deletions.assets.append(asset)

    with pytest.raises(DeletionServiceError, match="ASSET_NOT_FOUND"):
        await service.request_asset_deletion(
            user_id=uuid4(),
            asset_id=asset.id,
            idempotency_key="delete-photo-request",
        )

    assert asset.status == AssetStatus.READY
    assert jobs.jobs == []
