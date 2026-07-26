from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.events.models import OutboxEvent, OutboxStatus


@dataclass(frozen=True, slots=True)
class OutboxMetrics:
    pending_count: int
    failed_count: int
    # 与 failed_count 分开上报：死信不会自愈，混在一起会让「重试积压」告警
    # 被永久钉住，等同于关闭该告警。
    dead_letter_count: int
    oldest_pending_age_seconds: float


def retry_delay(attempt_count: int) -> timedelta:
    seconds = min(900, 5 * (2 ** max(0, attempt_count - 1)))
    return timedelta(seconds=seconds)


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_batch(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lock_timeout_seconds: int,
        now: datetime | None = None,
    ) -> list[OutboxEvent]:
        claimed_at = now or datetime.now(UTC)
        stale_before = claimed_at - timedelta(seconds=lock_timeout_seconds)
        due = or_(
            OutboxEvent.status == OutboxStatus.PENDING,
            and_(
                OutboxEvent.status == OutboxStatus.FAILED,
                or_(
                    OutboxEvent.next_retry_at.is_(None),
                    OutboxEvent.next_retry_at <= claimed_at,
                ),
            ),
            and_(
                OutboxEvent.status == OutboxStatus.PROCESSING,
                OutboxEvent.locked_at < stale_before,
            ),
        )
        result = await self._session.execute(
            select(OutboxEvent)
            .where(due)
            .order_by(OutboxEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars())
        for event in events:
            event.status = OutboxStatus.PROCESSING
            event.locked_by = worker_id
            event.locked_at = claimed_at
        await self._session.flush()
        return events

    async def get_for_update(self, event_id: UUID) -> OutboxEvent | None:
        result = await self._session.execute(
            select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def mark_published(
        self,
        event_id: UUID,
        *,
        now: datetime | None = None,
    ) -> None:
        event = await self.get_for_update(event_id)
        if event is None:
            return
        event.status = OutboxStatus.PUBLISHED
        event.published_at = now or datetime.now(UTC)
        event.last_error = None
        event.next_retry_at = None
        event.locked_by = None
        event.locked_at = None
        await self._session.flush()

    async def mark_failed(
        self,
        event_id: UUID,
        *,
        safe_error: str,
        max_attempts: int,
        now: datetime | None = None,
    ) -> OutboxStatus | None:
        """记录一次发布失败，重试次数耗尽后转入死信。

        返回落定后的状态供调用方区分「还会重试」与「已放弃」；事件不存在时返回 None。
        """

        event = await self.get_for_update(event_id)
        if event is None:
            return None
        failed_at = now or datetime.now(UTC)
        event.attempt_count += 1
        event.last_error = safe_error[:1_000]
        event.locked_by = None
        event.locked_at = None
        if event.attempt_count >= max_attempts:
            # 不再排程：next_retry_at 置空可让 claim_batch 的 due 条件失效，
            # 即使状态判定被后续改动放宽，死信也不会被重新捞回。
            event.status = OutboxStatus.DEAD_LETTER
            event.next_retry_at = None
        else:
            event.status = OutboxStatus.FAILED
            event.next_retry_at = failed_at + retry_delay(event.attempt_count)
        await self._session.flush()
        return event.status

    async def metrics(self, *, now: datetime | None = None) -> OutboxMetrics:
        measured_at = now or datetime.now(UTC)
        counts = await self._session.execute(
            select(
                func.count().filter(
                    OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.PROCESSING])
                ),
                func.count().filter(OutboxEvent.status == OutboxStatus.FAILED),
                func.count().filter(OutboxEvent.status == OutboxStatus.DEAD_LETTER),
                func.min(OutboxEvent.created_at).filter(
                    OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.PROCESSING])
                ),
            )
        )
        pending_count, failed_count, dead_letter_count, oldest_created_at = counts.one()
        oldest_age = (
            max(0.0, (measured_at - oldest_created_at).total_seconds())
            if oldest_created_at is not None
            else 0.0
        )
        return OutboxMetrics(
            pending_count=int(pending_count),
            failed_count=int(failed_count),
            dead_letter_count=int(dead_letter_count),
            oldest_pending_age_seconds=oldest_age,
        )
