import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.jobs.repository import JobRepository, new_job_created_event


class JobServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CreatedJob:
    job: GenerationJob
    reused: bool


def canonical_request_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class JobApplicationService:
    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        user_id: UUID,
        task_type: JobTaskType,
        idempotency_key: str,
        request_payload: dict[str, object],
        model_policy_snapshot: dict[str, object] | None = None,
    ) -> CreatedJob:
        normalized_key = idempotency_key.strip()
        if not normalized_key or len(normalized_key) > 128:
            raise JobServiceError("INVALID_IDEMPOTENCY_KEY")

        request_hash = canonical_request_hash(request_payload)
        existing = await self._repository.find_idempotent(
            user_id=user_id,
            task_type=task_type,
            idempotency_key=normalized_key,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise JobServiceError("IDEMPOTENCY_KEY_REUSED")
            return CreatedJob(job=existing, reused=True)

        job = GenerationJob(
            id=uuid4(),
            user_id=user_id,
            task_type=task_type,
            status=JobStatus.PENDING,
            idempotency_key=normalized_key,
            request_hash=request_hash,
            progress=0,
            retry_count=0,
            model_policy_snapshot=model_policy_snapshot,
        )
        created = await self._repository.create_with_outbox(
            job=job,
            event=new_job_created_event(job),
        )
        if not created:
            concurrent = await self._repository.find_idempotent(
                user_id=user_id,
                task_type=task_type,
                idempotency_key=normalized_key,
            )
            if concurrent is None:
                raise JobServiceError("JOB_CREATE_CONFLICT")
            if concurrent.request_hash != request_hash:
                raise JobServiceError("IDEMPOTENCY_KEY_REUSED")
            return CreatedJob(job=concurrent, reused=True)
        return CreatedJob(job=job, reused=False)

    async def get_owned(self, *, job_id: UUID, user_id: UUID) -> GenerationJob:
        job = await self._repository.get_owned(job_id=job_id, user_id=user_id)
        if job is None or job.user_id != user_id:
            raise JobServiceError("JOB_NOT_FOUND")
        return job
