from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.feedback.models import BetaFeedback
from app.modules.jobs.models import GenerationJob


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_idempotent(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> BetaFeedback | None:
        result = await self._session.execute(
            select(BetaFeedback).where(
                BetaFeedback.user_id == user_id,
                BetaFeedback.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def owned_job_exists(self, *, user_id: UUID, job_id: UUID) -> bool:
        result = await self._session.execute(
            select(GenerationJob.id).where(
                GenerationJob.id == job_id,
                GenerationJob.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def create(self, feedback: BetaFeedback) -> bool:
        try:
            async with self._session.begin_nested():
                self._session.add(feedback)
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    async def list_owned(
        self,
        *,
        user_id: UUID,
        cursor_created_at: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> list[BetaFeedback]:
        statement = select(BetaFeedback).where(BetaFeedback.user_id == user_id)
        if cursor_created_at is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    BetaFeedback.created_at < cursor_created_at,
                    and_(
                        BetaFeedback.created_at == cursor_created_at,
                        BetaFeedback.id < cursor_id,
                    ),
                )
            )
        result = await self._session.execute(
            statement.order_by(desc(BetaFeedback.created_at), desc(BetaFeedback.id)).limit(limit)
        )
        return list(result.scalars())
