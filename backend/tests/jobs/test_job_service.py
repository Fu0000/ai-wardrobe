from uuid import UUID, uuid4

import pytest

from app.modules.events.models import OutboxEvent
from app.modules.jobs.models import GenerationJob, JobTaskType
from app.modules.jobs.service import (
    JobApplicationService,
    JobServiceError,
    canonical_request_hash,
)


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

    async def get_owned(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> GenerationJob | None:
        return next(
            (job for job in self.jobs if job.id == job_id and job.user_id == user_id),
            None,
        )


def test_request_hash_is_stable_for_key_order() -> None:
    assert canonical_request_hash({"asset_id": "1", "occasion": "work"}) == (
        canonical_request_hash({"occasion": "work", "asset_id": "1"})
    )


@pytest.mark.asyncio
async def test_job_creation_writes_outbox_and_reuses_same_request() -> None:
    repository = FakeJobRepository()
    service = JobApplicationService(repository)  # type: ignore[arg-type]
    user_id = uuid4()
    payload: dict[str, object] = {"asset_id": str(uuid4()), "occasion": "work"}

    created = await service.create(
        user_id=user_id,
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        idempotency_key="request-1",
        request_payload=payload,
    )
    reused = await service.create(
        user_id=user_id,
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        idempotency_key="request-1",
        request_payload=payload,
    )

    assert created.reused is False
    assert reused.reused is True
    assert reused.job.id == created.job.id
    assert len(repository.jobs) == 1
    assert len(repository.events) == 1
    assert repository.events[0].aggregate_id == created.job.id


@pytest.mark.asyncio
async def test_idempotency_key_rejects_different_request() -> None:
    repository = FakeJobRepository()
    service = JobApplicationService(repository)  # type: ignore[arg-type]
    user_id = uuid4()
    await service.create(
        user_id=user_id,
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        idempotency_key="request-1",
        request_payload={"occasion": "work"},
    )

    with pytest.raises(JobServiceError, match="IDEMPOTENCY_KEY_REUSED"):
        await service.create(
            user_id=user_id,
            task_type=JobTaskType.STYLE_DIAGNOSIS,
            idempotency_key="request-1",
            request_payload={"occasion": "date"},
        )


@pytest.mark.asyncio
async def test_job_lookup_hides_another_users_job() -> None:
    repository = FakeJobRepository()
    service = JobApplicationService(repository)  # type: ignore[arg-type]
    created = await service.create(
        user_id=uuid4(),
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        idempotency_key="request-1",
        request_payload={"occasion": "work"},
    )

    with pytest.raises(JobServiceError, match="JOB_NOT_FOUND"):
        await service.get_owned(job_id=created.job.id, user_id=uuid4())
