from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.modules.jobs.models import GenerationJob, JobStatus
from app.modules.jobs.state_machine import TERMINAL_JOB_STATUSES, transition_job

ExecutionTokenFactory = Callable[[], str]
ACTIVE_EXECUTION_STATUSES = frozenset(
    {
        JobStatus.PROCESSING,
        JobStatus.QUALITY_CHECKING,
    }
)


def _new_execution_token() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    execution_token: str
    recovered_stale_execution: bool


@dataclass(frozen=True, slots=True)
class JobExecutionHarness:
    lease_seconds: int
    stale_user_message: str
    token_factory: ExecutionTokenFactory = field(
        default=_new_execution_token,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0:
            raise ValueError("execution lease must be positive")

    def claim(
        self,
        job: GenerationJob,
        *,
        now: datetime | None = None,
    ) -> LeaseClaim | None:
        claim_time = now or datetime.now(UTC)
        if job.status in TERMINAL_JOB_STATUSES:
            return None

        recovered_stale = False
        if job.status in ACTIVE_EXECUTION_STATUSES:
            if (
                job.execution_lease_expires_at is not None
                and job.execution_lease_expires_at > claim_time
            ):
                return None
            transition_job(
                job,
                JobStatus.FAILED_RETRYABLE,
                at=claim_time,
                error_code="STALE_EXECUTION_RECOVERED",
                user_message=self.stale_user_message,
            )
            recovered_stale = True

        if job.status in {JobStatus.PENDING, JobStatus.FAILED_RETRYABLE}:
            transition_job(job, JobStatus.QUEUED, at=claim_time)
        transition_job(job, JobStatus.PROCESSING, at=claim_time)
        execution_token = self.token_factory()
        job.execution_token = execution_token
        job.execution_lease_expires_at = claim_time + timedelta(seconds=self.lease_seconds)
        return LeaseClaim(
            execution_token=execution_token,
            recovered_stale_execution=recovered_stale,
        )

    @staticmethod
    def finalize_failure(
        job: GenerationJob,
        *,
        code: str,
        user_message: str,
        expected_execution_token: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        failure_time = now or datetime.now(UTC)
        if job.status in TERMINAL_JOB_STATUSES:
            return False
        if expected_execution_token is not None and job.execution_token != expected_execution_token:
            return False
        if (
            expected_execution_token is None
            and job.status in ACTIVE_EXECUTION_STATUSES
            and job.execution_lease_expires_at is not None
            and job.execution_lease_expires_at > failure_time
        ):
            return False
        if job.status == JobStatus.PENDING:
            transition_job(job, JobStatus.QUEUED, at=failure_time)
        transition_job(
            job,
            JobStatus.FAILED_FINAL,
            at=failure_time,
            error_code=code,
            user_message=user_message,
        )
        return True

    @staticmethod
    def mark_retryable(
        job: GenerationJob,
        *,
        code: str,
        user_message: str,
        execution_token: str,
        now: datetime | None = None,
    ) -> bool:
        if job.execution_token != execution_token or job.status not in ACTIVE_EXECUTION_STATUSES:
            return False
        transition_job(
            job,
            JobStatus.FAILED_RETRYABLE,
            at=now,
            error_code=code,
            user_message=user_message,
        )
        return True

    @staticmethod
    def complete(
        job: GenerationJob,
        *,
        execution_token: str,
        now: datetime | None = None,
    ) -> bool:
        if not JobExecutionHarness.is_current(
            job,
            execution_token=execution_token,
            allowed_statuses=(JobStatus.QUALITY_CHECKING,),
        ):
            return False
        transition_job(job, JobStatus.COMPLETED, at=now)
        return True

    @staticmethod
    def is_current(
        job: GenerationJob,
        *,
        execution_token: str,
        allowed_statuses: Collection[JobStatus] = (JobStatus.PROCESSING,),
    ) -> bool:
        return job.execution_token == execution_token and job.status in allowed_statuses
