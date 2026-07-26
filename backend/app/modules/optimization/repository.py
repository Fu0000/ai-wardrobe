from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.assets.models import SourcePhoto, UserAsset
from app.modules.diagnosis.models import StyleDiagnosis, StyleOptimizationResult
from app.modules.jobs.models import GenerationJob


@dataclass(frozen=True, slots=True)
class OptimizationRecord:
    optimization: StyleOptimizationResult
    job: GenerationJob
    diagnosis: StyleDiagnosis
    source_asset: UserAsset
    result_asset: UserAsset | None


class OptimizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        optimization_id: UUID,
        user_id: UUID,
        diagnosis_id: UUID,
        job_id: UUID,
        change_level: int,
        change_summary: list[dict[str, object]],
    ) -> StyleOptimizationResult:
        optimization = StyleOptimizationResult(
            id=optimization_id,
            user_id=user_id,
            diagnosis_id=diagnosis_id,
            job_id=job_id,
            change_level=change_level,
            change_summary=change_summary,
        )
        self._session.add(optimization)
        await self._session.flush()
        return optimization

    async def get_owned(
        self,
        *,
        optimization_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> OptimizationRecord | None:
        return await self._record(
            user_id=user_id,
            optimization_id=optimization_id,
            for_update=for_update,
        )

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> StyleOptimizationResult | None:
        result = await self._session.execute(
            select(StyleOptimizationResult).where(
                StyleOptimizationResult.job_id == job_id,
                StyleOptimizationResult.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def execution_context(
        self,
        *,
        job_id: UUID,
        for_update: bool = False,
    ) -> OptimizationRecord | None:
        return await self._record(job_id=job_id, for_update=for_update)

    async def _record(
        self,
        *,
        user_id: UUID | None = None,
        optimization_id: UUID | None = None,
        job_id: UUID | None = None,
        for_update: bool,
    ) -> OptimizationRecord | None:
        source_asset = aliased(UserAsset)
        result_asset = aliased(UserAsset)
        statement = (
            select(
                StyleOptimizationResult,
                GenerationJob,
                StyleDiagnosis,
                source_asset,
                result_asset,
            )
            .join(
                GenerationJob,
                GenerationJob.id == StyleOptimizationResult.job_id,
            )
            .join(
                StyleDiagnosis,
                StyleDiagnosis.id == StyleOptimizationResult.diagnosis_id,
            )
            .join(SourcePhoto, SourcePhoto.id == StyleDiagnosis.source_photo_id)
            .join(source_asset, source_asset.id == SourcePhoto.asset_id)
            .outerjoin(
                result_asset,
                and_(
                    result_asset.id == StyleOptimizationResult.result_asset_id,
                    result_asset.user_id == StyleOptimizationResult.user_id,
                ),
            )
            .where(
                StyleOptimizationResult.user_id == GenerationJob.user_id,
                StyleOptimizationResult.user_id == StyleDiagnosis.user_id,
                StyleOptimizationResult.user_id == source_asset.user_id,
            )
        )
        if user_id is not None:
            statement = statement.where(
                StyleOptimizationResult.user_id == user_id,
            )
        if optimization_id is not None:
            statement = statement.where(
                StyleOptimizationResult.id == optimization_id,
            )
        if job_id is not None:
            statement = statement.where(StyleOptimizationResult.job_id == job_id)
        if for_update:
            statement = statement.with_for_update(
                of=(StyleOptimizationResult, GenerationJob),
            )
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return OptimizationRecord(*row) if row else None
