from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.jobs.execution import JobExecutionHarness
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType


def job(status: JobStatus) -> GenerationJob:
    return GenerationJob(
        id=uuid4(),
        user_id=uuid4(),
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        status=status,
        idempotency_key=f"job-{uuid4().hex}",
        request_hash="a" * 64,
        progress=0,
        retry_count=0,
    )


def harness() -> JobExecutionHarness:
    return JobExecutionHarness(
        lease_seconds=120,
        stale_user_message="任务已恢复，正在继续处理。",
        token_factory=lambda: "new-execution-token",
    )


def assert_lease_released(current: GenerationJob) -> None:
    assert current.execution_token is None
    assert current.execution_lease_expires_at is None


def test_claim_moves_pending_job_to_processing_with_fenced_lease() -> None:
    current = job(JobStatus.PENDING)
    now = datetime.now(UTC)

    claim = harness().claim(current, now=now)

    assert claim is not None
    assert claim.execution_token == "new-execution-token"
    assert claim.recovered_stale_execution is False
    assert current.status == JobStatus.PROCESSING
    assert current.execution_token == claim.execution_token
    assert current.execution_lease_expires_at == now + timedelta(seconds=120)
    assert current.queued_at == now
    assert current.started_at == now


def test_claim_skips_an_active_lease_and_recovers_an_expired_lease() -> None:
    now = datetime.now(UTC)
    current = job(JobStatus.PROCESSING)
    current.execution_token = "old-execution-token"
    current.execution_lease_expires_at = now + timedelta(seconds=1)

    assert harness().claim(current, now=now) is None
    assert current.execution_token == "old-execution-token"

    current.execution_lease_expires_at = now - timedelta(microseconds=1)
    claim = harness().claim(current, now=now)

    assert claim is not None
    assert claim.recovered_stale_execution is True
    assert current.status == JobStatus.PROCESSING
    assert current.execution_token == "new-execution-token"
    assert current.retry_count == 1


@pytest.mark.parametrize(
    "terminal_status",
    [
        JobStatus.COMPLETED,
        JobStatus.FAILED_FINAL,
        JobStatus.TIMED_OUT,
        JobStatus.CANCELLED,
    ],
)
def test_claim_never_revives_terminal_jobs(terminal_status: JobStatus) -> None:
    current = job(terminal_status)

    assert harness().claim(current) is None
    assert current.status == terminal_status


def test_finalize_failure_respects_execution_token_and_active_lease() -> None:
    now = datetime.now(UTC)
    current = job(JobStatus.PROCESSING)
    current.execution_token = "current-token"
    current.execution_lease_expires_at = now + timedelta(minutes=1)

    assert not harness().finalize_failure(
        current,
        code="FAILED",
        user_message="任务失败。",
        expected_execution_token="stale-token",
        now=now,
    )
    assert not harness().finalize_failure(
        current,
        code="FAILED",
        user_message="任务失败。",
        now=now,
    )
    assert harness().finalize_failure(
        current,
        code="FAILED",
        user_message="任务失败。",
        expected_execution_token="current-token",
        now=now,
    )
    assert current.status == JobStatus.FAILED_FINAL
    assert_lease_released(current)


def test_finalize_failure_without_token_can_reap_an_expired_execution() -> None:
    now = datetime.now(UTC)
    current = job(JobStatus.QUALITY_CHECKING)
    current.execution_token = "expired-token"
    current.execution_lease_expires_at = now - timedelta(seconds=1)

    assert harness().finalize_failure(
        current,
        code="RETRIES_EXHAUSTED",
        user_message="自动重试次数已用完。",
        now=now,
    )
    assert current.status == JobStatus.FAILED_FINAL


def test_finalize_failure_never_rewrites_a_timed_out_job() -> None:
    current = job(JobStatus.TIMED_OUT)

    assert not harness().finalize_failure(
        current,
        code="LATE_FINALIZER",
        user_message="不应写入。",
    )
    assert current.status == JobStatus.TIMED_OUT


def test_mark_retryable_and_current_checks_share_the_same_fence() -> None:
    current = job(JobStatus.PROCESSING)
    current.execution_token = "current-token"

    assert harness().is_current(
        current,
        execution_token="current-token",
    )
    assert not harness().mark_retryable(
        current,
        code="TEMPORARY_FAILURE",
        user_message="正在自动重试。",
        execution_token="stale-token",
    )
    assert harness().mark_retryable(
        current,
        code="TEMPORARY_FAILURE",
        user_message="正在自动重试。",
        execution_token="current-token",
    )
    assert current.status == JobStatus.FAILED_RETRYABLE
    assert not harness().is_current(
        current,
        execution_token="current-token",
    )


def test_complete_requires_quality_checking_and_the_current_token() -> None:
    current = job(JobStatus.PROCESSING)
    current.execution_token = "current-token"

    assert not harness().complete(
        current,
        execution_token="current-token",
    )
    current.status = JobStatus.QUALITY_CHECKING
    assert not harness().complete(
        current,
        execution_token="stale-token",
    )
    assert harness().complete(
        current,
        execution_token="current-token",
    )
    assert current.status == JobStatus.COMPLETED
    assert_lease_released(current)
