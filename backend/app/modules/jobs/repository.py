from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telemetry import inject_trace_context
from app.modules.events.models import OutboxEvent
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_idempotent(
        self,
        *,
        user_id: UUID,
        task_type: JobTaskType,
        idempotency_key: str,
    ) -> GenerationJob | None:
        result = await self._session.execute(
            select(GenerationJob).where(
                GenerationJob.user_id == user_id,
                GenerationJob.task_type == task_type,
                GenerationJob.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_owned(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> GenerationJob | None:
        result = await self._session.execute(
            select(GenerationJob).where(
                GenerationJob.id == job_id,
                GenerationJob.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_with_outbox(
        self,
        *,
        job: GenerationJob,
        event: OutboxEvent,
    ) -> bool:
        try:
            async with self._session.begin_nested():
                self._session.add_all([job, event])
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    async def flush(self) -> None:
        await self._session.flush()


def new_job_created_event(job: GenerationJob) -> OutboxEvent:
    trace_context = inject_trace_context()
    return OutboxEvent(
        aggregate_type="GenerationJob",
        aggregate_id=job.id,
        event_type="GenerationJobCreated",
        payload={
            "job_id": str(job.id),
            "user_id": str(job.user_id),
            "task_type": job.task_type.value,
            "status": JobStatus.PENDING.value,
            **({"trace_context": trace_context} if trace_context else {}),
        },
    )
