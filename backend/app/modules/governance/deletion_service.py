from dataclasses import dataclass
from uuid import UUID, uuid4

from app.modules.governance.deletion_repository import DeletionRepository
from app.modules.governance.models import (
    DeletionJob,
    DeletionStatus,
    DeletionType,
)
from app.modules.identity.models import User, UserStatus
from app.modules.jobs.models import JobTaskType
from app.modules.jobs.repository import JobRepository
from app.modules.jobs.service import JobApplicationService, JobServiceError


class DeletionServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CreatedDeletion:
    deletion: DeletionJob
    reused: bool


class DeletionApplicationService:
    def __init__(
        self,
        *,
        deletion_repository: DeletionRepository,
        job_repository: JobRepository,
    ) -> None:
        self._deletions = deletion_repository
        self._jobs = JobApplicationService(job_repository)

    async def request_account_deletion(
        self,
        *,
        user: User,
        idempotency_key: str,
    ) -> CreatedDeletion:
        latest = await self._deletions.latest_account(user_id=user.id)
        if latest is not None and latest.status in {
            DeletionStatus.PENDING,
            DeletionStatus.PROCESSING,
            DeletionStatus.FAILED_RETRYABLE,
            DeletionStatus.COMPLETED,
        }:
            return CreatedDeletion(deletion=latest, reused=True)
        if user.status == UserStatus.DELETED:
            raise DeletionServiceError("ACCOUNT_ALREADY_DELETED")

        try:
            created_job = await self._jobs.create(
                user_id=user.id,
                task_type=JobTaskType.DELETION,
                idempotency_key=idempotency_key,
                request_payload={
                    "deletion_type": DeletionType.ACCOUNT.value,
                    "deletion_contract_version": "account-deletion-v1.0.0",
                },
            )
        except JobServiceError as error:
            raise DeletionServiceError(error.code) from error

        if created_job.reused:
            existing = await self._deletions.get_by_job(
                job_id=created_job.job.id,
                user_id=user.id,
            )
            if existing is None:
                raise DeletionServiceError("DELETION_STATE_INCOMPLETE")
            return CreatedDeletion(deletion=existing, reused=True)

        deletion = await self._deletions.create(
            deletion_id=uuid4(),
            user_id=user.id,
            job_id=created_job.job.id,
            deletion_type=DeletionType.ACCOUNT,
        )
        await self._deletions.mark_user_deletion_pending(user)
        return CreatedDeletion(deletion=deletion, reused=False)

    async def request_asset_deletion(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
        idempotency_key: str,
    ) -> CreatedDeletion:
        latest = await self._deletions.latest_asset(
            user_id=user_id,
            asset_id=asset_id,
        )
        if latest is not None and latest.status in {
            DeletionStatus.PENDING,
            DeletionStatus.PROCESSING,
            DeletionStatus.FAILED_RETRYABLE,
            DeletionStatus.COMPLETED,
        }:
            return CreatedDeletion(deletion=latest, reused=True)

        asset = await self._deletions.get_owned_asset(
            user_id=user_id,
            asset_id=asset_id,
            for_update=True,
        )
        if asset is None:
            raise DeletionServiceError("ASSET_NOT_FOUND")

        try:
            created_job = await self._jobs.create(
                user_id=user_id,
                task_type=JobTaskType.DELETION,
                idempotency_key=idempotency_key,
                request_payload={
                    "deletion_type": DeletionType.ASSET.value,
                    "target_id": str(asset_id),
                    "deletion_contract_version": "asset-deletion-v1.0.0",
                },
            )
        except JobServiceError as error:
            raise DeletionServiceError(error.code) from error

        if created_job.reused:
            existing = await self._deletions.get_by_job(
                job_id=created_job.job.id,
                user_id=user_id,
            )
            if existing is None:
                raise DeletionServiceError("DELETION_STATE_INCOMPLETE")
            return CreatedDeletion(deletion=existing, reused=True)

        deletion = await self._deletions.create(
            deletion_id=uuid4(),
            user_id=user_id,
            job_id=created_job.job.id,
            deletion_type=DeletionType.ASSET,
            target_id=asset_id,
        )
        await self._deletions.mark_asset_deletion_pending(asset)
        return CreatedDeletion(deletion=deletion, reused=False)

    async def account_status(self, *, user_id: UUID) -> DeletionJob:
        deletion = await self._deletions.latest_account(user_id=user_id)
        if deletion is None:
            raise DeletionServiceError("DELETION_NOT_FOUND")
        return deletion

    async def asset_status(self, *, user_id: UUID, asset_id: UUID) -> DeletionJob:
        deletion = await self._deletions.latest_asset(
            user_id=user_id,
            asset_id=asset_id,
        )
        if deletion is None:
            raise DeletionServiceError("ASSET_DELETION_NOT_FOUND")
        return deletion
