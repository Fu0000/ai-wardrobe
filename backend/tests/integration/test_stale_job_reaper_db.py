"""过期任务回收的数据库级验证。

单元测试用 fake 覆盖分支，无法验证 `skip_locked` 选取、真实配额计数回滚，
以及「未过期任务不被误杀」这类依赖 SQL 谓词的行为，这些在此验证。

被测代码自己开事务并提交，无法靠回滚兜底，因此用 `purge_users` 登记清理。
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import Settings
from app.database.session import Database
from app.modules.governance.models import (
    QuotaReservation,
    QuotaReservationStatus,
    QuotaType,
    UsageCounter,
)
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.jobs.runtime.reaper import STALE_JOB_ERROR_CODE, StaleJobReaper
from tests.integration.markers import requires_services

pytestmark = requires_services


async def test_reaper_times_out_stale_job_and_restores_quota(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    """失联任务被回收，其占用的配额计数真实回滚。"""

    now = datetime.now(UTC)
    stale_at = now - timedelta(seconds=settings.stale_job_grace_seconds + 60)
    period_key = now.strftime("%Y-%m")

    user_id = uuid4()
    purge_users.append(user_id)
    stale_job_id = uuid4()
    live_job_id = uuid4()

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        setup.add(
            UsageCounter(
                id=uuid4(),
                user_id=user_id,
                quota_type=QuotaType.DIAGNOSIS,
                period_key=period_key,
                used=0,
                reserved=1,
            )
        )
        setup.add(
            GenerationJob(
                id=stale_job_id,
                user_id=user_id,
                task_type=JobTaskType.STYLE_DIAGNOSIS,
                status=JobStatus.PROCESSING,
                idempotency_key=f"stale-{uuid4().hex}",
                request_hash="a" * 64,
                execution_token=uuid4().hex,
                execution_lease_expires_at=stale_at,
            )
        )
        # 租约仍然有效，必须不被回收。
        setup.add(
            GenerationJob(
                id=live_job_id,
                user_id=user_id,
                task_type=JobTaskType.STYLE_DIAGNOSIS,
                status=JobStatus.PROCESSING,
                idempotency_key=f"live-{uuid4().hex}",
                request_hash="b" * 64,
                execution_token=uuid4().hex,
                execution_lease_expires_at=now + timedelta(minutes=5),
            )
        )
        await setup.flush()
        setup.add(
            QuotaReservation(
                id=uuid4(),
                user_id=user_id,
                job_id=stale_job_id,
                quota_type=QuotaType.DIAGNOSIS,
                status=QuotaReservationStatus.RESERVED,
                amount=1,
                period_keys=[period_key],
            )
        )
        await setup.commit()

    outcome = await StaleJobReaper(settings=settings, database=database).reap_once()
    assert outcome.reaped == 1

    async with database.session_factory() as verify:
        stale_job = await verify.get(GenerationJob, stale_job_id)
        assert stale_job is not None
        assert stale_job.status is JobStatus.TIMED_OUT
        assert stale_job.error_code == STALE_JOB_ERROR_CODE
        assert stale_job.execution_token is None
        assert stale_job.execution_lease_expires_at is None

        live_job = await verify.get(GenerationJob, live_job_id)
        assert live_job is not None
        assert live_job.status is JobStatus.PROCESSING, "租约有效的任务不得被回收"

        reservation = (
            await verify.execute(
                select(QuotaReservation).where(QuotaReservation.job_id == stale_job_id)
            )
        ).scalar_one()
        assert reservation.status is QuotaReservationStatus.RELEASED

        counter = (
            await verify.execute(
                select(UsageCounter).where(
                    UsageCounter.user_id == user_id,
                    UsageCounter.period_key == period_key,
                )
            )
        ).scalar_one()
        assert counter.reserved == 0, "配额预留必须归还，否则用户额度被静默扣除"
        assert counter.used == 0


async def test_reaper_is_idempotent_across_runs(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    """重复执行不得重复释放配额或触碰终态任务。"""

    now = datetime.now(UTC)
    stale_at = now - timedelta(seconds=settings.stale_job_grace_seconds + 60)

    user_id = uuid4()
    purge_users.append(user_id)
    job_id = uuid4()

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        setup.add(
            GenerationJob(
                id=job_id,
                user_id=user_id,
                task_type=JobTaskType.DELETION,
                status=JobStatus.PROCESSING,
                idempotency_key=f"stale-{uuid4().hex}",
                request_hash="c" * 64,
                execution_token=uuid4().hex,
                execution_lease_expires_at=stale_at,
            )
        )
        await setup.commit()

    reaper = StaleJobReaper(settings=settings, database=database)
    first = await reaper.reap_once()
    second = await reaper.reap_once()

    assert first.reaped == 1
    assert second.reaped == 0, "终态任务不应再次进入回收范围"

    async with database.session_factory() as verify:
        job = await verify.get(GenerationJob, job_id)
        assert job is not None
        assert job.status is JobStatus.TIMED_OUT
