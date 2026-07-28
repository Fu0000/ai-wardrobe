"""配额预留账本的数据库级验证。

`test_quota.py` 只覆盖 period_key 的字符串拼接，`QuotaRepository` 的
reserve/commit/release 真实实现零覆盖——而这是直接管用户免费额度的路径。
其行为依赖 with_for_update、upsert 与多周期计数器，无法用 fake 验证。
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.governance.models import (
    QuotaReservationStatus,
    QuotaType,
    UsageCounter,
)
from app.modules.governance.quota import QuotaError, QuotaRepository
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from tests.integration.markers import requires_services

pytestmark = requires_services


async def _seed_job(session: AsyncSession, *, user_id: UUID) -> UUID:
    job_id = uuid4()
    session.add(
        GenerationJob(
            id=job_id,
            user_id=user_id,
            task_type=JobTaskType.STYLE_DIAGNOSIS,
            status=JobStatus.PENDING,
            idempotency_key=f"quota-{uuid4().hex}",
            request_hash="a" * 64,
        )
    )
    await session.flush()
    return job_id


async def _counters(session: AsyncSession, *, user_id: UUID) -> list[UsageCounter]:
    result = await session.execute(
        select(UsageCounter)
        .where(UsageCounter.user_id == user_id)
        .order_by(UsageCounter.period_key)
    )
    return list(result.scalars())


async def test_reserve_increments_every_period_counter(session: AsyncSession) -> None:
    """FREE 计划同时受日与月限额约束，两个计数器都要加。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    result = await QuotaRepository(session).reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )
    await session.flush()

    assert result.status is QuotaReservationStatus.RESERVED
    counters = await _counters(session, user_id=user.id)
    assert len(counters) == 2, "日与月两个周期都应产生计数器"
    assert all(counter.reserved == 1 for counter in counters)
    assert all(counter.used == 0 for counter in counters)


async def test_reserve_is_idempotent_per_job(session: AsyncSession) -> None:
    """同一 Job 重复预留不得重复扣额度——弱网重试会走到这里。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    repository = QuotaRepository(session)
    first = await repository.reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )
    second = await repository.reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )
    await session.flush()

    assert first.reservation_id == second.reservation_id
    counters = await _counters(session, user_id=user.id)
    assert all(counter.reserved == 1 for counter in counters), "重复预留不得叠加"


async def test_commit_moves_reserved_into_used(session: AsyncSession) -> None:
    """结算把预留转为已用，总占用量不变。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    repository = QuotaRepository(session)
    await repository.reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )
    outcome = await repository.commit(job_id=job_id)
    await session.flush()

    assert outcome.status is QuotaReservationStatus.COMMITTED
    for counter in await _counters(session, user_id=user.id):
        assert counter.reserved == 0
        assert counter.used == 1


async def test_release_returns_the_quota(session: AsyncSession) -> None:
    """任务失败后释放，额度必须完整归还。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    repository = QuotaRepository(session)
    await repository.reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )
    outcome = await repository.release(job_id=job_id)
    await session.flush()

    assert outcome.status is QuotaReservationStatus.RELEASED
    for counter in await _counters(session, user_id=user.id):
        assert counter.reserved == 0
        assert counter.used == 0, "释放不应计入已用"


async def test_settlement_is_idempotent(session: AsyncSession) -> None:
    """重复结算不得二次扣减——Worker 重试会走到这里。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    repository = QuotaRepository(session)
    await repository.reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )
    await repository.commit(job_id=job_id)
    await repository.commit(job_id=job_id)
    await session.flush()

    for counter in await _counters(session, user_id=user.id):
        assert counter.used == 1, "重复 commit 不得叠加已用量"


async def test_release_after_commit_is_rejected(session: AsyncSession) -> None:
    """已结算的预留不能再改判，否则用户能凭失败重试白拿额度。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    repository = QuotaRepository(session)
    await repository.reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )
    await repository.commit(job_id=job_id)

    with pytest.raises(QuotaError) as excinfo:
        await repository.release(job_id=job_id)
    assert excinfo.value.code == "QUOTA_RESERVATION_ALREADY_SETTLED"


async def test_conflicting_reservation_for_same_job_is_rejected(
    session: AsyncSession,
) -> None:
    """同一 Job 换个额度类型再预留必须报冲突，而不是静默多扣一份。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    repository = QuotaRepository(session)
    await repository.reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )

    with pytest.raises(QuotaError) as excinfo:
        await repository.reserve(
            user_id=user.id,
            job_id=job_id,
            quota_type=QuotaType.OPTIMIZATION,
        )
    assert excinfo.value.code == "QUOTA_RESERVATION_CONFLICT"


async def test_daily_limit_is_enforced(session: AsyncSession) -> None:
    """FREE 计划每日 5 次诊断，第 6 次必须被拒。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()

    repository = QuotaRepository(session)
    for _ in range(5):
        job_id = await _seed_job(session, user_id=user.id)
        await repository.reserve(
            user_id=user.id,
            job_id=job_id,
            quota_type=QuotaType.DIAGNOSIS,
        )

    overflow_job_id = await _seed_job(session, user_id=user.id)
    with pytest.raises(QuotaError) as excinfo:
        await repository.reserve(
            user_id=user.id,
            job_id=overflow_job_id,
            quota_type=QuotaType.DIAGNOSIS,
        )
    assert excinfo.value.code == "QUOTA_EXCEEDED"


async def test_released_quota_can_be_reused(session: AsyncSession) -> None:
    """释放后额度应可再次使用，否则失败一次就永久损失一格。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()

    repository = QuotaRepository(session)
    for _ in range(5):
        job_id = await _seed_job(session, user_id=user.id)
        await repository.reserve(
            user_id=user.id,
            job_id=job_id,
            quota_type=QuotaType.DIAGNOSIS,
        )
        last_job_id = job_id

    await repository.release(job_id=last_job_id)

    retry_job_id = await _seed_job(session, user_id=user.id)
    result = await repository.reserve(
        user_id=user.id,
        job_id=retry_job_id,
        quota_type=QuotaType.DIAGNOSIS,
    )

    assert result.status is QuotaReservationStatus.RESERVED


async def test_reservation_requires_an_owned_job(session: AsyncSession) -> None:
    """不能为他人的 Job 预留额度。"""

    owner = User(id=uuid4())
    other = User(id=uuid4())
    session.add_all([owner, other])
    await session.flush()
    job_id = await _seed_job(session, user_id=owner.id)

    with pytest.raises(QuotaError) as excinfo:
        await QuotaRepository(session).reserve(
            user_id=other.id,
            job_id=job_id,
            quota_type=QuotaType.DIAGNOSIS,
        )
    assert excinfo.value.code == "JOB_NOT_FOUND"


async def test_settling_an_unknown_job_is_rejected(session: AsyncSession) -> None:
    """无预留时结算必须显式报错，避免静默成功掩盖调用方缺陷。"""

    with pytest.raises(QuotaError) as excinfo:
        await QuotaRepository(session).commit(job_id=uuid4())
    assert excinfo.value.code == "QUOTA_RESERVATION_NOT_FOUND"


async def test_counters_are_scoped_to_the_current_period(session: AsyncSession) -> None:
    """计数器按周期键隔离，跨月不应互相影响。"""

    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = await _seed_job(session, user_id=user.id)

    now = datetime.now(UTC)
    await QuotaRepository(session).reserve(
        user_id=user.id,
        job_id=job_id,
        quota_type=QuotaType.DIAGNOSIS,
        now=now,
    )
    await session.flush()

    keys = {counter.period_key for counter in await _counters(session, user_id=user.id)}
    assert now.strftime("%Y-%m-%d") in keys
    assert now.strftime("%Y-%m") in keys
