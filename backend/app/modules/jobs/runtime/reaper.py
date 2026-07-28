"""过期任务回收。

Job 的租约恢复只在任务被重新投递时才生效。Broker 为 Redis，若 Worker 被强制
终止且消息丢失，Job 会永久停留在 PROCESSING —— 用户看到进度条卡死，配额停在
RESERVED 永不释放，免费额度被静默扣除且无法恢复。

回收器兜住这条路径：扫描租约已过期且超出宽限期的非终态 Job，转入 TIMED_OUT
并释放配额。宽限期严格长于最长的执行租约（见 Settings 校验），因此不会与正常
的租约续期竞争。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.database.session import Database
from app.modules.governance.quota import QuotaError, QuotaRepository
from app.modules.jobs.models import GenerationJob, JobStatus
from app.modules.jobs.state_machine import transition_job

logger = structlog.get_logger(__name__)

# 用户可见文案不得暴露内部原因，仅说明可重试。
STALE_JOB_ERROR_CODE = "JOB_EXECUTION_TIMED_OUT"
STALE_JOB_USER_MESSAGE = "任务处理超时，请重新发起。"

# 只有这些状态可能因 Worker 失联而卡住；PENDING 尚未进入执行，交由投递重试。
REAPABLE_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.PROCESSING,
    JobStatus.QUALITY_CHECKING,
)


@dataclass(frozen=True, slots=True)
class ReapOutcome:
    scanned: int
    reaped: int


class StaleJobReaper:
    def __init__(self, *, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database

    async def reap_once(self, *, now: datetime | None = None) -> ReapOutcome:
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(seconds=self._settings.stale_job_grace_seconds)

        async with self._database.session_factory() as session:
            candidates = await self._claim_candidates(session, cutoff=cutoff)
            reaped = 0
            for job in candidates:
                if await self._reap_one(session, job=job, at=moment):
                    reaped += 1
            await session.commit()

        return ReapOutcome(scanned=len(candidates), reaped=reaped)

    async def _claim_candidates(
        self,
        session: AsyncSession,
        *,
        cutoff: datetime,
    ) -> list[GenerationJob]:
        """取出租约已过期且超出宽限期的任务。

        `skip_locked` 让多个 Beat 实例可以并行回收而不互相阻塞；行锁保证同一个
        Job 不会被两个实例同时转换状态。
        """

        statement = (
            select(GenerationJob)
            .where(
                GenerationJob.status.in_(REAPABLE_JOB_STATUSES),
                GenerationJob.execution_lease_expires_at.is_not(None),
                GenerationJob.execution_lease_expires_at < cutoff,
            )
            .order_by(GenerationJob.execution_lease_expires_at)
            .limit(self._settings.stale_job_batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(statement)
        return list(result.scalars())

    async def _reap_one(
        self,
        session: AsyncSession,
        *,
        job: GenerationJob,
        at: datetime,
    ) -> bool:
        job_id: UUID = job.id
        transition_job(
            job,
            JobStatus.TIMED_OUT,
            at=at,
            error_code=STALE_JOB_ERROR_CODE,
            user_message=STALE_JOB_USER_MESSAGE,
        )
        # 释放必须与状态转换同事务，否则回收成功但配额仍被占用。
        # 删除类任务不占配额，无预留是正常情况，不能让它中断整批回收。
        try:
            await QuotaRepository(session).release(job_id=job_id, now=at)
        except QuotaError as error:
            if error.code != "QUOTA_RESERVATION_NOT_FOUND":
                raise
        # 回收是系统可靠性事件而非用户产品动作，走结构化日志与告警，
        # 不混入 record_product_action 的产品指标口径。
        await logger.awarning(
            "stale_job_reaped",
            job_id=str(job_id),
            task_type=job.task_type.value,
            retry_count=job.retry_count,
        )
        return True
