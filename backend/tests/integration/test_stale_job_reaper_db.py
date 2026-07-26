"""过期任务回收的数据库级验证。

单元测试用 fake 覆盖分支，无法验证 `skip_locked` 选取、真实配额计数回滚，
以及「未过期任务不被误杀」这类依赖 SQL 谓词的行为，这些在此验证。
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.database import models as database_models  # noqa: F401
from app.database.session import Database
from app.modules.governance.models import (
    QuotaReservation,
    QuotaReservationStatus,
    QuotaType,
    UsageCounter,
)
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.jobs.reaper import STALE_JOB_ERROR_CODE, StaleJobReaper

RUN_INTEGRATION_TESTS = os.getenv("AIW_RUN_INTEGRATION_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION_TESTS,
        reason="set AIW_RUN_INTEGRATION_TESTS=1 with disposable PostgreSQL and Redis",
    ),
]


async def test_reaper_times_out_stale_job_and_restores_quota() -> None:
    """失联任务被回收，其占用的配额计数真实回滚。"""

    settings = Settings()
    database = Database(settings)
    now = datetime.now(UTC)
    stale_at = now - timedelta(seconds=settings.stale_job_grace_seconds + 60)
    period_key = now.strftime("%Y-%m")

    user_id = uuid4()
    stale_job_id = uuid4()
    live_job_id = uuid4()

    try:
        async with database.session_factory() as session:
            session.add(User(id=user_id))
            await session.flush()

            session.add(
                UsageCounter(
                    id=uuid4(),
                    user_id=user_id,
                    quota_type=QuotaType.DIAGNOSIS,
                    period_key=period_key,
                    used=0,
                    reserved=1,
                )
            )
            session.add(
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
            session.add(
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
            await session.flush()

            session.add(
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
            await session.commit()

        outcome = await StaleJobReaper(settings=settings, database=database).reap_once()
        assert outcome.reaped == 1

        async with database.session_factory() as session:
            stale_job = await session.get(GenerationJob, stale_job_id)
            assert stale_job is not None
            assert stale_job.status is JobStatus.TIMED_OUT
            assert stale_job.error_code == STALE_JOB_ERROR_CODE
            assert stale_job.execution_token is None
            assert stale_job.execution_lease_expires_at is None

            live_job = await session.get(GenerationJob, live_job_id)
            assert live_job is not None
            assert live_job.status is JobStatus.PROCESSING, "租约有效的任务不得被回收"

            reservation = (
                await session.execute(
                    select(QuotaReservation).where(QuotaReservation.job_id == stale_job_id)
                )
            ).scalar_one()
            assert reservation.status is QuotaReservationStatus.RELEASED

            counter = (
                await session.execute(
                    select(UsageCounter).where(
                        UsageCounter.user_id == user_id,
                        UsageCounter.period_key == period_key,
                    )
                )
            ).scalar_one()
            assert counter.reserved == 0, "配额预留必须归还，否则用户额度被静默扣除"
            assert counter.used == 0
    finally:
        await _cleanup(database, user_id=user_id)
        await database.dispose()


async def test_reaper_is_idempotent_across_runs() -> None:
    """重复执行不得重复释放配额或触碰终态任务。"""

    settings = Settings()
    database = Database(settings)
    now = datetime.now(UTC)
    stale_at = now - timedelta(seconds=settings.stale_job_grace_seconds + 60)

    user_id = uuid4()
    job_id = uuid4()

    try:
        async with database.session_factory() as session:
            session.add(User(id=user_id))
            await session.flush()
            session.add(
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
            await session.commit()

        reaper = StaleJobReaper(settings=settings, database=database)
        first = await reaper.reap_once()
        second = await reaper.reap_once()

        assert first.reaped == 1
        assert second.reaped == 0, "终态任务不应再次进入回收范围"

        async with database.session_factory() as session:
            job = await session.get(GenerationJob, job_id)
            assert job is not None
            assert job.status is JobStatus.TIMED_OUT
    finally:
        await _cleanup(database, user_id=user_id)
        await database.dispose()


async def _cleanup(database: Database, *, user_id: UUID) -> None:
    async with database.session_factory() as session:
        await _delete_scoped(session, user_id=user_id)
        await session.commit()


async def _delete_scoped(session: AsyncSession, *, user_id: UUID) -> None:
    await session.execute(delete(QuotaReservation).where(QuotaReservation.user_id == user_id))
    await session.execute(delete(UsageCounter).where(UsageCounter.user_id == user_id))
    await session.execute(delete(GenerationJob).where(GenerationJob.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))


async def test_engine_is_reachable() -> None:
    """前置自检：数据库不可达时先失败，避免上面的断言被误读为通过。"""

    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(select(1))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
