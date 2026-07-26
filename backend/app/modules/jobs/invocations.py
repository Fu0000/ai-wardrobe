from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import Database
from app.modules.ai.contracts import (
    ImageEditResponse,
    ModelRoute,
    ProviderErrorCode,
    StructuredVisionResponse,
)
from app.modules.jobs.models import AIInvocation, InvocationStatus


class DatabaseInvocationObserver:
    def __init__(
        self,
        *,
        database: Database,
        job_id: UUID,
        user_id: UUID,
        prompt_version: str,
        schema_version: str,
    ) -> None:
        self._database = database
        self._job_id = job_id
        self._user_id = user_id
        self._prompt_version = prompt_version
        self._schema_version = schema_version

    async def started(self, route: ModelRoute, attempt: int) -> UUID:
        invocation_id = uuid4()
        async with self._database.session_factory() as session:
            session.add(
                AIInvocation(
                    id=invocation_id,
                    job_id=self._job_id,
                    user_id=self._user_id,
                    status=InvocationStatus.STARTED,
                    provider=route.provider,
                    model=route.model,
                    prompt_version=self._prompt_version,
                    schema_version=self._schema_version,
                    attempt=attempt,
                    started_at=datetime.now(UTC),
                )
            )
            await session.commit()
        return invocation_id

    async def succeeded(
        self,
        invocation_id: UUID,
        response: StructuredVisionResponse | ImageEditResponse,
        latency_ms: int,
    ) -> None:
        async with self._database.session_factory() as session:
            invocation = await self._locked(session, invocation_id)
            if invocation is None:
                return
            invocation.status = InvocationStatus.SUCCEEDED
            invocation.input_tokens = response.usage.input_tokens
            invocation.output_tokens = response.usage.output_tokens
            invocation.estimated_cost_microunits = response.usage.estimated_cost_microunits
            invocation.latency_ms = latency_ms
            invocation.completed_at = datetime.now(UTC)
            await session.commit()

    async def failed(
        self,
        invocation_id: UUID,
        error_code: ProviderErrorCode,
        latency_ms: int,
    ) -> None:
        async with self._database.session_factory() as session:
            invocation = await self._locked(session, invocation_id)
            if invocation is None:
                return
            invocation.status = (
                InvocationStatus.TIMED_OUT
                if error_code == ProviderErrorCode.TIMEOUT
                else InvocationStatus.FAILED
            )
            invocation.error_code = error_code.value
            invocation.latency_ms = latency_ms
            invocation.completed_at = datetime.now(UTC)
            await session.commit()

    @staticmethod
    async def _locked(
        session: AsyncSession,
        invocation_id: UUID,
    ) -> AIInvocation | None:
        result = await session.execute(
            select(AIInvocation).where(AIInvocation.id == invocation_id).with_for_update()
        )
        return result.scalar_one_or_none()
