from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.models import AssetStatus, SourcePhoto, UserAsset
from app.modules.diagnosis.models import StyleDiagnosis, StyleOptimizationResult
from app.modules.events.models import OutboxEvent
from app.modules.feedback.models import BetaFeedback
from app.modules.governance.models import (
    DeletionJob,
    DeletionType,
    QuotaReservation,
    UsageCounter,
)
from app.modules.growth.models import ShareRecord, UserEvent, VoteRecord
from app.modules.identity.models import User, UserIdentity, UserProfile, UserStatus
from app.modules.jobs.models import AIInvocation, GenerationJob


@dataclass(frozen=True, slots=True)
class DeletionContext:
    deletion: DeletionJob
    job: GenerationJob
    user: User


@dataclass(frozen=True, slots=True)
class AssetDeletionPlan:
    target_asset_id: UUID
    asset_ids: tuple[UUID, ...]
    object_keys: tuple[str, ...]
    source_photo_ids: tuple[UUID, ...]
    diagnosis_ids: tuple[UUID, ...]
    optimization_ids: tuple[UUID, ...]
    share_ids: tuple[UUID, ...]


class DeletionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        deletion_id: UUID,
        user_id: UUID,
        job_id: UUID,
        deletion_type: DeletionType,
        target_id: UUID | None = None,
    ) -> DeletionJob:
        deletion = DeletionJob(
            id=deletion_id,
            user_id=user_id,
            job_id=job_id,
            deletion_type=deletion_type,
            target_id=target_id,
        )
        self._session.add(deletion)
        await self._session.flush()
        return deletion

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> DeletionJob | None:
        result = await self._session.execute(
            select(DeletionJob).where(
                DeletionJob.job_id == job_id,
                DeletionJob.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def latest_account(self, *, user_id: UUID) -> DeletionJob | None:
        result = await self._session.execute(
            select(DeletionJob)
            .where(
                DeletionJob.user_id == user_id,
                DeletionJob.deletion_type == DeletionType.ACCOUNT,
            )
            .order_by(DeletionJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_asset(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
    ) -> DeletionJob | None:
        result = await self._session.execute(
            select(DeletionJob)
            .where(
                DeletionJob.user_id == user_id,
                DeletionJob.deletion_type == DeletionType.ASSET,
                DeletionJob.target_id == asset_id,
            )
            .order_by(DeletionJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_owned_asset(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
        for_update: bool = False,
    ) -> UserAsset | None:
        statement = select(UserAsset).where(
            UserAsset.id == asset_id,
            UserAsset.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def mark_asset_deletion_pending(self, asset: UserAsset) -> None:
        asset.status = AssetStatus.DELETION_PENDING
        await self._session.flush()

    async def context(
        self,
        *,
        job_id: UUID,
        for_update: bool = False,
    ) -> DeletionContext | None:
        statement = (
            select(DeletionJob, GenerationJob, User)
            .join(GenerationJob, GenerationJob.id == DeletionJob.job_id)
            .join(User, User.id == DeletionJob.user_id)
            .where(
                DeletionJob.job_id == job_id,
                DeletionJob.user_id == GenerationJob.user_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=(DeletionJob, GenerationJob, User))
        result = await self._session.execute(statement)
        row = result.one_or_none()
        return DeletionContext(*row) if row else None

    async def account_object_keys(self, *, user_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(UserAsset.object_key).where(UserAsset.user_id == user_id)
        )
        return list(result.scalars())

    async def asset_deletion_plan(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
    ) -> AssetDeletionPlan | None:
        target = await self.get_owned_asset(
            user_id=user_id,
            asset_id=asset_id,
            for_update=True,
        )
        if target is None:
            return None

        source_photo_result = await self._session.execute(
            select(SourcePhoto.id)
            .where(
                SourcePhoto.user_id == user_id,
                SourcePhoto.asset_id == asset_id,
            )
            .with_for_update()
        )
        source_photo_ids = set(source_photo_result.scalars())

        diagnosis_ids: set[UUID] = set()
        if source_photo_ids:
            diagnosis_result = await self._session.execute(
                select(StyleDiagnosis.id)
                .where(
                    StyleDiagnosis.user_id == user_id,
                    StyleDiagnosis.source_photo_id.in_(source_photo_ids),
                )
                .with_for_update()
            )
            diagnosis_ids.update(diagnosis_result.scalars())

        optimization_scope = StyleOptimizationResult.result_asset_id == asset_id
        if diagnosis_ids:
            optimization_scope = or_(
                optimization_scope,
                StyleOptimizationResult.diagnosis_id.in_(diagnosis_ids),
            )
        optimization_result = await self._session.execute(
            select(
                StyleOptimizationResult.id,
                StyleOptimizationResult.result_asset_id,
            )
            .where(
                StyleOptimizationResult.user_id == user_id,
                optimization_scope,
            )
            .with_for_update()
        )
        optimization_rows = optimization_result.all()
        optimization_ids = {row.id for row in optimization_rows}

        share_scope = ShareRecord.share_asset_id == asset_id
        if optimization_ids:
            share_scope = or_(
                share_scope,
                (
                    (ShareRecord.target_type == "StyleOptimizationResult")
                    & ShareRecord.target_id.in_(optimization_ids)
                ),
            )
        share_result = await self._session.execute(
            select(ShareRecord.id, ShareRecord.share_asset_id)
            .where(
                ShareRecord.user_id == user_id,
                share_scope,
            )
            .with_for_update()
        )
        share_rows = share_result.all()
        share_ids = {row.id for row in share_rows}

        asset_ids = {asset_id}
        asset_ids.update(
            row.result_asset_id for row in optimization_rows if row.result_asset_id is not None
        )
        asset_ids.update(row.share_asset_id for row in share_rows if row.share_asset_id is not None)
        asset_result = await self._session.execute(
            select(UserAsset.id, UserAsset.object_key)
            .where(
                UserAsset.user_id == user_id,
                UserAsset.id.in_(asset_ids),
            )
            .with_for_update()
        )
        asset_rows = asset_result.all()
        return AssetDeletionPlan(
            target_asset_id=asset_id,
            asset_ids=tuple(sorted((row.id for row in asset_rows), key=str)),
            object_keys=tuple(sorted({row.object_key for row in asset_rows})),
            source_photo_ids=tuple(sorted(source_photo_ids, key=str)),
            diagnosis_ids=tuple(sorted(diagnosis_ids, key=str)),
            optimization_ids=tuple(sorted(optimization_ids, key=str)),
            share_ids=tuple(sorted(share_ids, key=str)),
        )

    async def purge_asset_plan(self, plan: AssetDeletionPlan) -> None:
        if plan.share_ids:
            await self._session.execute(
                delete(UserEvent).where(
                    UserEvent.entity_type == "ShareRecord",
                    UserEvent.entity_id.in_(plan.share_ids),
                )
            )
            await self._session.execute(
                delete(ShareRecord).where(ShareRecord.id.in_(plan.share_ids))
            )
        if plan.optimization_ids:
            await self._session.execute(
                delete(StyleOptimizationResult).where(
                    StyleOptimizationResult.id.in_(plan.optimization_ids)
                )
            )
        if plan.diagnosis_ids:
            await self._session.execute(
                delete(StyleDiagnosis).where(StyleDiagnosis.id.in_(plan.diagnosis_ids))
            )
        if plan.source_photo_ids:
            await self._session.execute(
                delete(SourcePhoto).where(SourcePhoto.id.in_(plan.source_photo_ids))
            )
        if plan.asset_ids:
            await self._session.execute(delete(UserAsset).where(UserAsset.id.in_(plan.asset_ids)))

    async def purge_account(
        self,
        *,
        user_id: UUID,
        keep_deletion_id: UUID,
        keep_generation_job_id: UUID,
    ) -> None:
        share_ids_result = await self._session.execute(
            select(ShareRecord.id).where(ShareRecord.user_id == user_id)
        )
        share_ids = list(share_ids_result.scalars())
        job_ids_result = await self._session.execute(
            select(GenerationJob.id).where(GenerationJob.user_id == user_id)
        )
        all_job_ids = list(job_ids_result.scalars())
        removable_job_ids = [job_id for job_id in all_job_ids if job_id != keep_generation_job_id]

        event_scope = UserEvent.user_id == user_id
        if share_ids:
            event_scope = or_(
                event_scope,
                ((UserEvent.entity_type == "ShareRecord") & UserEvent.entity_id.in_(share_ids)),
            )
        await self._session.execute(delete(UserEvent).where(event_scope))
        await self._session.execute(delete(VoteRecord).where(VoteRecord.voter_user_id == user_id))
        await self._session.execute(delete(ShareRecord).where(ShareRecord.user_id == user_id))
        await self._session.execute(
            delete(StyleOptimizationResult).where(StyleOptimizationResult.user_id == user_id)
        )
        await self._session.execute(delete(StyleDiagnosis).where(StyleDiagnosis.user_id == user_id))
        await self._session.execute(delete(SourcePhoto).where(SourcePhoto.user_id == user_id))
        await self._session.execute(delete(BetaFeedback).where(BetaFeedback.user_id == user_id))
        await self._session.execute(delete(UserProfile).where(UserProfile.user_id == user_id))
        await self._session.execute(delete(UserAsset).where(UserAsset.user_id == user_id))
        await self._session.execute(delete(UserIdentity).where(UserIdentity.user_id == user_id))
        await self._session.execute(delete(UsageCounter).where(UsageCounter.user_id == user_id))
        # ai_invocations 与 quota_reservations 虽有 generation_jobs 级联，但本次注销
        # 任务对应的 Job 需存活至执行结束，其关联行不会被级联带走，因此显式按用户清理。
        await self._session.execute(delete(AIInvocation).where(AIInvocation.user_id == user_id))
        await self._session.execute(
            delete(QuotaReservation).where(QuotaReservation.user_id == user_id)
        )
        await self._session.execute(
            delete(DeletionJob).where(
                DeletionJob.user_id == user_id,
                DeletionJob.id != keep_deletion_id,
            )
        )
        if all_job_ids:
            await self._session.execute(
                delete(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "GenerationJob",
                    OutboxEvent.aggregate_id.in_(all_job_ids),
                )
            )
        if removable_job_ids:
            await self._session.execute(
                delete(GenerationJob).where(GenerationJob.id.in_(removable_job_ids))
            )

        user = await self._session.get(User, user_id)
        if user is not None:
            user.status = UserStatus.DELETED

    async def mark_user_deletion_pending(self, user: User) -> None:
        user.status = UserStatus.DELETION_PENDING

    async def flush(self) -> None:
        await self._session.flush()
