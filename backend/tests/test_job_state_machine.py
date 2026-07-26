from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.jobs.state_machine import (
    InvalidJobTransitionError,
    transition_job,
)


def _job(status: JobStatus = JobStatus.PENDING) -> GenerationJob:
    return GenerationJob(
        id=uuid4(),
        user_id=uuid4(),
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        status=status,
        idempotency_key="idem-1",
        request_hash="a" * 64,
        progress=0,
        retry_count=0,
    )


def _execution_state(job: GenerationJob) -> tuple[str | None, datetime | None]:
    return job.execution_token, job.execution_lease_expires_at


def test_job_happy_path_controls_timestamps_and_progress() -> None:
    job = _job()
    queued_at = datetime(2026, 7, 26, 9, tzinfo=UTC)
    started_at = datetime(2026, 7, 26, 9, 0, 1, tzinfo=UTC)
    completed_at = datetime(2026, 7, 26, 9, 0, 10, tzinfo=UTC)

    transition_job(job, JobStatus.QUEUED, at=queued_at)
    transition_job(job, JobStatus.PROCESSING, at=started_at, progress=35)
    transition_job(job, JobStatus.QUALITY_CHECKING, progress=90)
    transition_job(job, JobStatus.COMPLETED, at=completed_at)

    assert job.status == JobStatus.COMPLETED
    assert job.progress == 100
    assert job.queued_at == queued_at
    assert job.started_at == started_at
    assert job.completed_at == completed_at


def test_terminal_job_cannot_transition_again() -> None:
    job = _job(JobStatus.COMPLETED)
    job.progress = 100

    with pytest.raises(InvalidJobTransitionError):
        transition_job(job, JobStatus.PROCESSING)


def test_retry_transition_increments_count_without_progress_regression() -> None:
    job = _job(JobStatus.PROCESSING)
    job.progress = 60

    transition_job(
        job,
        JobStatus.FAILED_RETRYABLE,
        error_code="PROVIDER_BUSY",
        user_message="服务繁忙，正在自动重试。",
    )
    transition_job(job, JobStatus.QUEUED)

    assert job.retry_count == 1
    assert job.progress == 60
    assert job.error_code is None
    assert job.user_message is None


def test_failure_requires_safe_user_message_before_mutation() -> None:
    job = _job(JobStatus.PROCESSING)

    with pytest.raises(ValueError, match="safe error"):
        transition_job(
            job,
            JobStatus.FAILED_FINAL,
            error_code="INTERNAL_PROVIDER_DETAIL",
        )

    assert job.status == JobStatus.PROCESSING
    assert job.completed_at is None


def test_terminal_transition_fences_out_an_old_worker() -> None:
    job = _job(JobStatus.PROCESSING)
    job.execution_token = "67a4b928-63bc-4a4d-8779-6e40487c285e"
    job.execution_lease_expires_at = datetime(2026, 7, 26, 9, 2, tzinfo=UTC)

    transition_job(job, JobStatus.COMPLETED)
    execution_token, lease_expires_at = _execution_state(job)

    assert execution_token is None
    assert lease_expires_at is None
