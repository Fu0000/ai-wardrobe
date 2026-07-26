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
        now: datetime | None = None,
    ) -> None:
        event = await self.get_for_update(event_id)
        if event is None:
            return
        failed_at = now or datetime.now(UTC)
        event.attempt_count += 1
        event.status = OutboxStatus.FAILED
        event.last_error = safe_error[:1_000]
        event.next_retry_at = failed_at + retry_delay(event.attempt_count)
        event.locked_by = None
        event.locked_at = None
        await self._session.flush()

    async def metrics(self, *, now: datetime | None = None) -> OutboxMetrics:
        measured_at = now or datetime.now(UTC)
        counts = await self._session.execute(
            select(
                func.count().filter(
                    OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.PROCESSING])
                ),
                func.count().filter(OutboxEvent.status == OutboxStatus.FAILED),
                func.min(OutboxEvent.created_at).filter(
                    OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.PROCESSING])
                ),
            )
        )
        pending_count, failed_count, oldest_created_at = counts.one()
        oldest_age = (
            max(0.0, (measured_at - oldest_created_at).total_seconds())
            if oldest_created_at is not None
            else 0.0
        )
        return OutboxMetrics(
            pending_count=int(pending_count),
            failed_count=int(failed_count),
            oldest_pending_age_seconds=oldest_age,
        )
