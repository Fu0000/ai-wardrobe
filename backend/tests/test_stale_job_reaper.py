"""过期任务回收的逻辑覆盖。

真实 SQL 与配额结算在 tests/integration/test_stale_job_reaper.py 中验证；
这里聚焦不依赖数据库的分支：状态转换、配额释放的调用与豁免、宽限期计算。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.modules.governance.quota import QuotaError
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.jobs.reaper import (
    REAPABLE_JOB_STATUSES,
    STALE_JOB_ERROR_CODE,
    StaleJobReaper,
)
from app.modules.jobs.state_machine import TERMINAL_JOB_STATUSES


def _job(
    *,
    status: JobStatus = JobStatus.PROCESSING,
    task_type: JobTaskType = JobTaskType.STYLE_DIAGNOSIS,
    lease_expires_at: datetime | None = None,
) -> GenerationJob:
    return GenerationJob(
        id=uuid4(),
        user_id=uuid4(),
        task_type=task_type,
        status=status,
        idempotency_key=f"reaper-{uuid4().hex}",
        request_hash="a" * 64,
        execution_token=uuid4().hex,
        execution_lease_expires_at=lease_expires_at,
        progress=20,
    )


class _FakeSession:
    """只承载回收器实际用到的会话行为。"""

    def __init__(self, jobs: list[GenerationJob]) -> None:
        self._jobs = jobs
        self.committed = False

    async def execute(self, _statement: Any) -> Any:
        jobs = self._jobs

        class _Result:
            def scalars(self) -> list[GenerationJob]:
                return jobs

        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeDatabase:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def session_factory(self) -> _FakeSession:
        return self._session


class _RecordingQuota:
    """记录配额释放调用，并可模拟「无预留」。"""

    released: ClassVar[list[UUID]] = []
    raise_not_found: ClassVar[bool] = False

    def __init__(self, _session: object) -> None:
        return None

    async def release(self, *, job_id: UUID, now: datetime | None = None) -> object:
        if _RecordingQuota.raise_not_found:
            raise QuotaError("QUOTA_RESERVATION_NOT_FOUND")
        _RecordingQuota.released.append(job_id)
        return SimpleNamespace(reservation_id=uuid4())


@pytest.fixture(autouse=True)
def _reset_quota_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingQuota.released = []
    _RecordingQuota.raise_not_found = False
    monkeypatch.setattr("app.modules.jobs.reaper.QuotaRepository", _RecordingQuota)


def _reaper(jobs: list[GenerationJob]) -> tuple[StaleJobReaper, _FakeSession]:
    session = _FakeSession(jobs)
    reaper = StaleJobReaper(
        settings=Settings(),
        database=_FakeDatabase(session),  # type: ignore[arg-type]
    )
    return reaper, session


async def test_stale_job_is_timed_out_and_quota_released() -> None:
    """核心路径：失联任务转入 TIMED_OUT，其配额被释放。"""

    job = _job(lease_expires_at=datetime.now(UTC) - timedelta(hours=2))
    reaper, session = _reaper([job])

    outcome = await reaper.reap_once()

    assert outcome.reaped == 1
    assert job.status is JobStatus.TIMED_OUT
    assert job.error_code == STALE_JOB_ERROR_CODE
    assert _RecordingQuota.released == [job.id]
    assert session.committed


async def test_timed_out_job_clears_execution_lease() -> None:
    """回收后必须清空租约，避免被后续投递误认为仍在执行。"""

    job = _job(lease_expires_at=datetime.now(UTC) - timedelta(hours=2))
    reaper, _ = _reaper([job])

    await reaper.reap_once()

    assert job.execution_token is None
    assert job.execution_lease_expires_at is None
    assert job.completed_at is not None


async def test_user_message_does_not_leak_internal_reason() -> None:
    """失败文案面向用户，不得暴露租约、Worker 或 Broker 细节。"""

    job = _job(lease_expires_at=datetime.now(UTC) - timedelta(hours=2))
    reaper, _ = _reaper([job])

    await reaper.reap_once()

    assert job.user_message is not None
    for leaked in ("lease", "worker", "broker", "redis", "租约", "Celery"):
        assert leaked.lower() not in job.user_message.lower()


async def test_missing_reservation_does_not_abort_the_batch() -> None:
    """删除类任务不占配额，无预留是正常情况，不能中断整批回收。"""

    _RecordingQuota.raise_not_found = True
    first = _job(
        task_type=JobTaskType.DELETION,
        lease_expires_at=datetime.now(UTC) - timedelta(hours=2),
    )
    second = _job(lease_expires_at=datetime.now(UTC) - timedelta(hours=3))
    reaper, _ = _reaper([first, second])

    outcome = await reaper.reap_once()

    assert outcome.reaped == 2
    assert first.status is JobStatus.TIMED_OUT
    assert second.status is JobStatus.TIMED_OUT


async def test_unexpected_quota_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只豁免「无预留」，其余配额异常必须上抛而非静默吞掉。"""

    class _Underflow(_RecordingQuota):
        async def release(self, *, job_id: UUID, now: datetime | None = None) -> object:
            raise QuotaError("QUOTA_COUNTER_UNDERFLOW")

    job = _job(lease_expires_at=datetime.now(UTC) - timedelta(hours=2))
    reaper, _ = _reaper([job])
    monkeypatch.setattr("app.modules.jobs.reaper.QuotaRepository", _Underflow)

    with pytest.raises(QuotaError) as excinfo:
        await reaper.reap_once()
    assert excinfo.value.code == "QUOTA_COUNTER_UNDERFLOW"


async def test_empty_batch_is_a_no_op() -> None:
    """无候选任务时不应产生任何副作用。"""

    reaper, _ = _reaper([])

    outcome = await reaper.reap_once()

    assert outcome == type(outcome)(scanned=0, reaped=0)
    assert _RecordingQuota.released == []


def test_reapable_statuses_exclude_terminal_and_pending() -> None:
    """回收范围必须与状态机一致：终态不可回收，PENDING 交由投递重试。"""

    reapable = set(REAPABLE_JOB_STATUSES)
    assert not reapable & TERMINAL_JOB_STATUSES
    assert JobStatus.PENDING not in reapable
    assert JobStatus.FAILED_RETRYABLE not in reapable


def test_every_reapable_status_can_transition_to_timed_out() -> None:
    """守卫：若状态机收紧了 TIMED_OUT 的入边，回收器会抛异常，这里先失败。"""

    from app.modules.jobs.state_machine import ALLOWED_JOB_TRANSITIONS

    for status in REAPABLE_JOB_STATUSES:
        assert JobStatus.TIMED_OUT in ALLOWED_JOB_TRANSITIONS[status], (
            f"{status.value} 无法转入 TIMED_OUT，回收器将在运行时失败"
        )


def test_grace_period_must_exceed_longest_lease() -> None:
    """配置校验：宽限期短于最长租约会误杀正在执行的任务。"""

    with pytest.raises(ValueError, match="grace period"):
        Settings(stale_job_grace_seconds=60)
