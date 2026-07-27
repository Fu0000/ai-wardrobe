import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.config import Settings
from app.modules.assets.models import AssetStatus
from app.modules.diagnosis.models import OptimizationStatus
from app.modules.growth.models import ShareRecord, ShareStatus, VoteChoice
from app.modules.growth.repository import GrowthRepository, ShareView, VoteTally
from app.modules.jobs.models import JobTaskType
from app.modules.jobs.repository import JobRepository
from app.modules.jobs.service import JobApplicationService, JobServiceError
from app.modules.optimization.repository import OptimizationRepository
from app.modules.optimization.schema import ChangeInstruction


class GrowthServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CreatedShare:
    share: ShareRecord
    reused: bool


@dataclass(frozen=True, slots=True)
class ShareDetails:
    view: ShareView
    tally: VoteTally
    viewer_choice: VoteChoice | None


@dataclass(frozen=True, slots=True)
class VoteResult:
    share: ShareRecord
    tally: VoteTally
    choice: VoteChoice
    reused: bool


def vote_fingerprint(
    settings: Settings,
    *,
    share_id: UUID,
    user_id: UUID,
) -> str:
    message = f"vote:{share_id}:{user_id}".encode()
    return hmac.new(
        settings.identity_hmac_key.get_secret_value().encode(),
        message,
        hashlib.sha256,
    ).hexdigest()


class GrowthApplicationService:
    def __init__(
        self,
        *,
        growth_repository: GrowthRepository,
        optimization_repository: OptimizationRepository,
        job_repository: JobRepository,
        settings: Settings,
    ) -> None:
        self._growth = growth_repository
        self._optimizations = optimization_repository
        self._jobs = JobApplicationService(job_repository)
        self._settings = settings

    async def create_share(
        self,
        *,
        user_id: UUID,
        optimization_id: UUID,
        idempotency_key: str,
        display_score: bool,
        attribution_source: str,
    ) -> CreatedShare:
        record = await self._optimizations.get_owned(
            optimization_id=optimization_id,
            user_id=user_id,
            for_update=True,
        )
        if (
            record is None
            or record.optimization.status != OptimizationStatus.COMPLETED
            or record.result_asset is None
            or record.source_asset.status != AssetStatus.READY
            or record.result_asset.status != AssetStatus.READY
        ):
            raise GrowthServiceError("OPTIMIZATION_NOT_SHAREABLE")

        request_payload: dict[str, object] = {
            "optimization_id": str(optimization_id),
            "display_score": display_score,
            "attribution_source": attribution_source,
            "share_template_version": "minimal-change-card-v1.0.0",
        }
        try:
            created_job = await self._jobs.create(
                user_id=user_id,
                task_type=JobTaskType.SHARE_ASSET,
                idempotency_key=idempotency_key,
                request_payload=request_payload,
            )
        except JobServiceError as error:
            raise GrowthServiceError(error.code) from error

        if created_job.reused:
            existing = await self._growth.get_by_job(
                job_id=created_job.job.id,
                user_id=user_id,
            )
            if existing is None:
                raise GrowthServiceError("SHARE_STATE_INCOMPLETE")
            return CreatedShare(share=existing, reused=True)

        changes = [
            ChangeInstruction.model_validate(item) for item in record.optimization.change_summary
        ]
        public_payload: dict[str, object] = {
            "change_level": record.optimization.change_level,
            "changes": [
                {
                    "priority": change.priority,
                    "instruction": change.instruction,
                    "reason": change.reason,
                }
                for change in changes
            ],
            "ai_edited": True,
            "template_version": "minimal-change-card-v1.0.0",
        }
        if display_score and record.diagnosis.score is not None:
            public_payload["score"] = record.diagnosis.score
        share = await self._growth.create_share(
            share_id=uuid4(),
            user_id=user_id,
            job_id=created_job.job.id,
            optimization_id=optimization_id,
            scene_code=secrets.token_urlsafe(18),
            public_payload=public_payload,
            attribution_source=attribution_source,
        )
        return CreatedShare(share=share, reused=False)

    async def get_share(
        self,
        *,
        scene_code: str,
        viewer_user_id: UUID,
        attribution_source: str | None = None,
    ) -> ShareDetails:
        view = await self._growth.get_scene(scene_code=scene_code)
        if view is None:
            raise GrowthServiceError("SHARE_NOT_FOUND")
        if view.share.status in {ShareStatus.EXPIRED, ShareStatus.REVOKED} or (
            view.share.expires_at is not None and view.share.expires_at <= datetime.now(UTC)
        ):
            raise GrowthServiceError("SHARE_EXPIRED")
        fingerprint = vote_fingerprint(
            self._settings,
            share_id=view.share.id,
            user_id=viewer_user_id,
        )
        if view.share.status == ShareStatus.ACTIVE and viewer_user_id != view.share.user_id:
            await self._growth.record_event(
                event_id=uuid4(),
                user_id=viewer_user_id,
                event_name="share.scene.opened",
                entity_type="ShareRecord",
                entity_id=view.share.id,
                dedupe_key=f"{view.share.id}:{fingerprint}",
                properties={
                    "attribution_source": (
                        attribution_source or view.share.attribution_source or "UNKNOWN"
                    )
                },
            )
        return ShareDetails(
            view=view,
            tally=await self._growth.vote_tally(share_id=view.share.id),
            viewer_choice=await self._growth.viewer_choice(
                share_id=view.share.id,
                voter_fingerprint_hash=fingerprint,
            ),
        )

    async def vote(
        self,
        *,
        scene_code: str,
        user_id: UUID,
        choice: VoteChoice,
    ) -> VoteResult:
        view = await self._growth.get_scene(scene_code=scene_code)
        if view is None:
            raise GrowthServiceError("SHARE_NOT_FOUND")
        if (
            view.share.status != ShareStatus.ACTIVE
            or view.asset is None
            or (view.share.expires_at is not None and view.share.expires_at <= datetime.now(UTC))
        ):
            raise GrowthServiceError("SHARE_NOT_ACTIVE")
        fingerprint = vote_fingerprint(
            self._settings,
            share_id=view.share.id,
            user_id=user_id,
        )
        reused = await self._growth.upsert_vote(
            vote_id=uuid4(),
            share_id=view.share.id,
            voter_user_id=user_id,
            voter_fingerprint_hash=fingerprint,
            choice=choice,
        )
        if not reused:
            await self._growth.record_event(
                event_id=uuid4(),
                user_id=user_id,
                event_name="vote.choice.submitted",
                entity_type="ShareRecord",
                entity_id=view.share.id,
                dedupe_key=f"{view.share.id}:{fingerprint}:{choice.value}",
                properties={"choice": choice.value},
            )
        return VoteResult(
            share=view.share,
            tally=await self._growth.vote_tally(share_id=view.share.id),
            choice=choice,
            reused=reused,
        )

    async def record_share_invocation(
        self,
        *,
        scene_code: str,
        user_id: UUID,
        attribution_source: str,
    ) -> bool:
        view = await self._growth.get_scene(scene_code=scene_code)
        if (
            view is None
            or view.share.status != ShareStatus.ACTIVE
            or view.asset is None
            or (view.share.expires_at is not None and view.share.expires_at <= datetime.now(UTC))
        ):
            raise GrowthServiceError("SHARE_NOT_ACTIVE")
        fingerprint = vote_fingerprint(
            self._settings,
            share_id=view.share.id,
            user_id=user_id,
        )
        return await self._growth.record_event(
            event_id=uuid4(),
            user_id=user_id,
            event_name="share.wechat.invoked",
            entity_type="ShareRecord",
            entity_id=view.share.id,
            dedupe_key=f"{view.share.id}:{fingerprint}:{attribution_source}",
            properties={"attribution_source": attribution_source},
        )

    async def record_continue(
        self,
        *,
        scene_code: str,
        user_id: UUID,
    ) -> bool:
        view = await self._growth.get_scene(scene_code=scene_code)
        if (
            view is None
            or view.share.status != ShareStatus.ACTIVE
            or view.asset is None
            or (view.share.expires_at is not None and view.share.expires_at <= datetime.now(UTC))
        ):
            raise GrowthServiceError("SHARE_NOT_ACTIVE")
        fingerprint = vote_fingerprint(
            self._settings,
            share_id=view.share.id,
            user_id=user_id,
        )
        return await self._growth.record_event(
            event_id=uuid4(),
            user_id=user_id,
            event_name="growth.continue.clicked",
            entity_type="ShareRecord",
            entity_id=view.share.id,
            dedupe_key=f"{view.share.id}:{fingerprint}",
            properties={"attribution_source": view.share.attribution_source or "UNKNOWN"},
        )
