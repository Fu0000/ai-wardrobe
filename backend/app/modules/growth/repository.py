from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.assets.models import SourcePhoto, UserAsset
from app.modules.diagnosis.models import StyleDiagnosis, StyleOptimizationResult
from app.modules.growth.models import (
    ShareRecord,
    UserEvent,
    VoteChoice,
    VoteRecord,
)
from app.modules.jobs.models import GenerationJob


@dataclass(frozen=True, slots=True)
class ShareView:
    share: ShareRecord
    job: GenerationJob | None
    asset: UserAsset | None


@dataclass(frozen=True, slots=True)
class ShareExecutionContext:
    share: ShareRecord
    job: GenerationJob
    optimization: StyleOptimizationResult
    before_asset: UserAsset
    after_asset: UserAsset


@dataclass(frozen=True, slots=True)
class ShareJobContext:
    share: ShareRecord
    job: GenerationJob


@dataclass(frozen=True, slots=True)
class VoteTally:
    before: int
    after: int


class GrowthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_share(
        self,
        *,
        share_id: UUID,
        user_id: UUID,
        job_id: UUID,
        optimization_id: UUID,
        scene_code: str,
        public_payload: dict[str, object],
        attribution_source: str,
    ) -> ShareRecord:
        share = ShareRecord(
            id=share_id,
            user_id=user_id,
            job_id=job_id,
            target_type="StyleOptimizationResult",
            target_id=optimization_id,
            scene_code=scene_code,
            public_payload=public_payload,
            attribution_source=attribution_source,
        )
        self._session.add(share)
        await self._session.flush()
        return share

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> ShareRecord | None:
        result = await self._session.execute(
            select(ShareRecord).where(
                ShareRecord.job_id == job_id,
                ShareRecord.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_scene(self, *, scene_code: str) -> ShareView | None:
        result = await self._session.execute(
            select(ShareRecord, GenerationJob, UserAsset)
            .outerjoin(
                GenerationJob,
                and_(
                    GenerationJob.id == ShareRecord.job_id,
                    GenerationJob.user_id == ShareRecord.user_id,
                ),
            )
            .outerjoin(
                UserAsset,
                and_(
                    UserAsset.id == ShareRecord.share_asset_id,
                    UserAsset.user_id == ShareRecord.user_id,
                ),
            )
            .where(ShareRecord.scene_code == scene_code)
        )
        row = result.one_or_none()
        return ShareView(*row) if row else None

    async def execution_context(
        self,
        *,
        job_id: UUID,
        for_update: bool = False,
    ) -> ShareExecutionContext | None:
        before_asset = aliased(UserAsset)
        after_asset = aliased(UserAsset)
        statement = (
            select(
                ShareRecord,
                GenerationJob,
                StyleOptimizationResult,
                before_asset,
                after_asset,
            )
            .join(GenerationJob, GenerationJob.id == ShareRecord.job_id)
            .join(
                StyleOptimizationResult,
                StyleOptimizationResult.id == ShareRecord.target_id,
            )
            .join(
                StyleDiagnosis,
                StyleDiagnosis.id == StyleOptimizationResult.diagnosis_id,
            )
            .join(SourcePhoto, SourcePhoto.id == StyleDiagnosis.source_photo_id)
            .join(before_asset, before_asset.id == SourcePhoto.asset_id)
            .join(after_asset, after_asset.id == StyleOptimizationResult.result_asset_id)
            .where(
                ShareRecord.job_id == job_id,
                ShareRecord.target_type == "StyleOptimizationResult",
                ShareRecord.user_id == GenerationJob.user_id,
                ShareRecord.user_id == StyleOptimizationResult.user_id,
                ShareRecord.user_id == before_asset.user_id,
                ShareRecord.user_id == after_asset.user_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=(ShareRecord, GenerationJob))
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return ShareExecutionContext(*row) if row else None

    async def job_context(
        self,
        *,
        job_id: UUID,
        for_update: bool = False,
    ) -> ShareJobContext | None:
        statement = (
            select(ShareRecord, GenerationJob)
            .join(GenerationJob, GenerationJob.id == ShareRecord.job_id)
            .where(
                ShareRecord.job_id == job_id,
                ShareRecord.user_id == GenerationJob.user_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=(ShareRecord, GenerationJob))
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return ShareJobContext(*row) if row else None

    async def upsert_vote(
        self,
        *,
        vote_id: UUID,
        share_id: UUID,
        voter_user_id: UUID,
        voter_fingerprint_hash: str,
        choice: VoteChoice,
    ) -> bool:
        existing = await self._session.execute(
            select(VoteRecord.choice).where(
                VoteRecord.share_id == share_id,
                VoteRecord.voter_fingerprint_hash == voter_fingerprint_hash,
            )
        )
        previous = existing.scalar_one_or_none()
        statement = (
            insert(VoteRecord)
            .values(
                id=vote_id,
                share_id=share_id,
                voter_user_id=voter_user_id,
                voter_fingerprint_hash=voter_fingerprint_hash,
                choice=choice,
            )
            .on_conflict_do_update(
                constraint="uq_vote_records_share_voter",
                set_={
                    "choice": choice,
                    "voter_user_id": voter_user_id,
                    "updated_at": func.now(),
                },
            )
        )
        await self._session.execute(statement)
        return previous == choice

    async def vote_tally(self, *, share_id: UUID) -> VoteTally:
        result = await self._session.execute(
            select(VoteRecord.choice, func.count(VoteRecord.id))
            .where(VoteRecord.share_id == share_id)
            .group_by(VoteRecord.choice)
        )
        counts = {choice: int(count) for choice, count in result.all()}
        return VoteTally(
            before=counts.get(VoteChoice.BEFORE, 0),
            after=counts.get(VoteChoice.AFTER, 0),
        )

    async def viewer_choice(
        self,
        *,
        share_id: UUID,
        voter_fingerprint_hash: str,
    ) -> VoteChoice | None:
        result = await self._session.execute(
            select(VoteRecord.choice).where(
                VoteRecord.share_id == share_id,
                VoteRecord.voter_fingerprint_hash == voter_fingerprint_hash,
            )
        )
        return result.scalar_one_or_none()

    async def record_event(
        self,
        *,
        event_id: UUID,
        user_id: UUID | None,
        event_name: str,
        entity_type: str,
        entity_id: UUID,
        dedupe_key: str,
        properties: dict[str, object],
    ) -> bool:
        statement = (
            insert(UserEvent)
            .values(
                id=event_id,
                user_id=user_id,
                event_name=event_name,
                entity_type=entity_type,
                entity_id=entity_id,
                dedupe_key=dedupe_key,
                properties=properties,
            )
            .on_conflict_do_nothing(constraint="uq_user_events_name_dedupe")
            .returning(UserEvent.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None
