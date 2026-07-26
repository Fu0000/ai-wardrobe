from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.models import PhotoPurpose, SourcePhoto, UserAsset
from app.modules.diagnosis.models import DiagnosisStatus, StyleDiagnosis
from app.modules.jobs.models import GenerationJob


@dataclass(frozen=True, slots=True)
class DiagnosisRecord:
    diagnosis: StyleDiagnosis
    job: GenerationJob


@dataclass(frozen=True, slots=True)
class DiagnosisExecutionContext:
    diagnosis: StyleDiagnosis
    job: GenerationJob
    asset: UserAsset


class DiagnosisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_source_photo(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
    ) -> SourcePhoto:
        existing = await self._source_photo(user_id=user_id, asset_id=asset_id)
        if existing is not None:
            return existing
        source_photo = SourcePhoto(
            id=uuid4(),
            user_id=user_id,
            asset_id=asset_id,
            purpose=PhotoPurpose.OUTFIT_DIAGNOSIS,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(source_photo)
                await self._session.flush()
            return source_photo
        except IntegrityError:
            concurrent = await self._source_photo(
                user_id=user_id,
                asset_id=asset_id,
            )
            if concurrent is None:
                raise
            return concurrent

    async def create(
        self,
        *,
        diagnosis_id: UUID,
        user_id: UUID,
        source_photo_id: UUID,
        job_id: UUID,
        occasion: str,
    ) -> StyleDiagnosis:
        diagnosis = StyleDiagnosis(
            id=diagnosis_id,
            user_id=user_id,
            source_photo_id=source_photo_id,
            job_id=job_id,
            occasion=occasion,
            status=DiagnosisStatus.PENDING,
        )
        self._session.add(diagnosis)
        await self._session.flush()
        return diagnosis

    async def get_owned(
        self,
        *,
        diagnosis_id: UUID,
        user_id: UUID,
    ) -> DiagnosisRecord | None:
        result = await self._session.execute(
            select(StyleDiagnosis, GenerationJob)
            .join(GenerationJob, GenerationJob.id == StyleDiagnosis.job_id)
            .where(
                StyleDiagnosis.id == diagnosis_id,
                StyleDiagnosis.user_id == user_id,
                GenerationJob.user_id == user_id,
            )
        )
        row = result.one_or_none()
        return DiagnosisRecord(*row) if row else None

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> StyleDiagnosis | None:
        result = await self._session.execute(
            select(StyleDiagnosis).where(
                StyleDiagnosis.job_id == job_id,
                StyleDiagnosis.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_owned_source_asset(
        self,
        *,
        diagnosis_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> UserAsset | None:
        statement = (
            select(UserAsset)
            .join(SourcePhoto, SourcePhoto.asset_id == UserAsset.id)
            .join(StyleDiagnosis, StyleDiagnosis.source_photo_id == SourcePhoto.id)
            .where(
                StyleDiagnosis.id == diagnosis_id,
                StyleDiagnosis.user_id == user_id,
                SourcePhoto.user_id == user_id,
                UserAsset.user_id == user_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=UserAsset)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def execution_context(
        self,
        *,
        job_id: UUID,
        for_update: bool = False,
    ) -> DiagnosisExecutionContext | None:
        statement = (
            select(StyleDiagnosis, GenerationJob, UserAsset)
            .join(GenerationJob, GenerationJob.id == StyleDiagnosis.job_id)
            .join(SourcePhoto, SourcePhoto.id == StyleDiagnosis.source_photo_id)
            .join(UserAsset, UserAsset.id == SourcePhoto.asset_id)
            .where(
                StyleDiagnosis.job_id == job_id,
                StyleDiagnosis.user_id == GenerationJob.user_id,
                StyleDiagnosis.user_id == UserAsset.user_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return DiagnosisExecutionContext(*row) if row else None

    async def _source_photo(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
    ) -> SourcePhoto | None:
        result = await self._session.execute(
            select(SourcePhoto).where(
                SourcePhoto.user_id == user_id,
                SourcePhoto.asset_id == asset_id,
            )
        )
        return result.scalar_one_or_none()
