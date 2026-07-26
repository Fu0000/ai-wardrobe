from datetime import UTC, datetime

from app.modules.jobs.models import GenerationJob, JobStatus


class InvalidJobTransitionError(Exception):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        super().__init__(f"{current.value} -> {target.value}")
        self.current = current
        self.target = target


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.FAILED_FINAL,
        JobStatus.TIMED_OUT,
        JobStatus.CANCELLED,
    }
)

ALLOWED_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.QUEUED: frozenset(
        {
            JobStatus.PROCESSING,
            JobStatus.FAILED_RETRYABLE,
            JobStatus.FAILED_FINAL,
            JobStatus.TIMED_OUT,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.PROCESSING: frozenset(
        {
            JobStatus.QUALITY_CHECKING,
            JobStatus.COMPLETED,
            JobStatus.FAILED_RETRYABLE,
            JobStatus.FAILED_FINAL,
            JobStatus.TIMED_OUT,
        }
    ),
    JobStatus.QUALITY_CHECKING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.FAILED_RETRYABLE,
            JobStatus.FAILED_FINAL,
            JobStatus.TIMED_OUT,
        }
    ),
    JobStatus.FAILED_RETRYABLE: frozenset(
        {
            JobStatus.QUEUED,
            JobStatus.FAILED_FINAL,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED_FINAL: frozenset(),
    JobStatus.TIMED_OUT: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

DEFAULT_PROGRESS_BY_STATUS: dict[JobStatus, int] = {
    JobStatus.PENDING: 0,
    JobStatus.QUEUED: 5,
    JobStatus.PROCESSING: 20,
    JobStatus.QUALITY_CHECKING: 85,
    JobStatus.COMPLETED: 100,
    JobStatus.FAILED_RETRYABLE: 20,
    JobStatus.FAILED_FINAL: 100,
    JobStatus.TIMED_OUT: 100,
    JobStatus.CANCELLED: 100,
}


def transition_job(
    job: GenerationJob,
    target: JobStatus,
    *,
    at: datetime | None = None,
    progress: int | None = None,
    error_code: str | None = None,
    user_message: str | None = None,
) -> None:
    current = job.status
    if target not in ALLOWED_JOB_TRANSITIONS[current]:
        raise InvalidJobTransitionError(current, target)

    transition_time = at or datetime.now(UTC)
    requested_progress = progress if progress is not None else DEFAULT_PROGRESS_BY_STATUS[target]
    if not 0 <= requested_progress <= 100:
        raise ValueError("job progress must be between 0 and 100")
    if target in {
        JobStatus.FAILED_RETRYABLE,
        JobStatus.FAILED_FINAL,
        JobStatus.TIMED_OUT,
    } and (not error_code or not user_message):
        raise ValueError("failed jobs require a safe error code and user message")

    if target == JobStatus.QUEUED:
        job.queued_at = transition_time
        if current == JobStatus.FAILED_RETRYABLE:
            job.retry_count += 1
        job.error_code = None
        job.user_message = None
    elif target == JobStatus.PROCESSING and job.started_at is None:
        job.started_at = transition_time

    if target in TERMINAL_JOB_STATUSES:
        job.completed_at = transition_time
        job.execution_token = None
        job.execution_lease_expires_at = None

    if target in {
        JobStatus.FAILED_RETRYABLE,
        JobStatus.FAILED_FINAL,
        JobStatus.TIMED_OUT,
    }:
        job.error_code = error_code
        job.user_message = user_message

    job.status = target
    job.progress = max(job.progress, requested_progress)
