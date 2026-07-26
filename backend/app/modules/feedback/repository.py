from uuid import UUID

from sqlalchemy import desc, select
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
        limit: int,
    ) -> list[BetaFeedback]:
        result = await self._session.execute(
            select(BetaFeedback)
            .where(BetaFeedback.user_id == user_id)
            .order_by(desc(BetaFeedback.created_at), desc(BetaFeedback.id))
            .limit(limit)
        )
        return list(result.scalars())
