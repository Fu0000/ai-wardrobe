from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.modules.assets.models import AssetKind, AssetStatus, UserAsset
from app.modules.diagnosis.models import (
    DiagnosisStatus,
    OptimizationStatus,
    StyleDiagnosis,
    StyleOptimizationResult,
)
from app.modules.events.models import OutboxEvent
from app.modules.growth.api import _response
from app.modules.growth.models import ShareRecord, ShareStatus, VoteChoice
from app.modules.growth.repository import ShareView, VoteTally
from app.modules.growth.service import (
    GrowthApplicationService,
    GrowthServiceError,
    vote_fingerprint,
)
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.optimization.repository import OptimizationRecord


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: list[GenerationJob] = []
        self.events: list[OutboxEvent] = []

    async def find_idempotent(
        self,
        *,
        user_id: UUID,
        task_type: JobTaskType,
        idempotency_key: str,
    ) -> GenerationJob | None:
        return next(
            (
                job
                for job in self.jobs
                if job.user_id == user_id
                and job.task_type == task_type
                and job.idempotency_key == idempotency_key
            ),
            None,
        )

    async def create_with_outbox(
        self,
        *,
        job: GenerationJob,
        event: OutboxEvent,
    ) -> bool:
        self.jobs.append(job)
        self.events.append(event)
        return True


class FakeOptimizationRepository:
    def __init__(self, record: OptimizationRecord | None) -> None:
        self.record = record

    async def get_owned(
        self,
        *,
        optimization_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> OptimizationRecord | None:
        if (
            self.record
            and self.record.optimization.id == optimization_id
            and self.record.optimization.user_id == user_id
        ):
            return self.record
        return None


class FakeGrowthRepository:
    def __init__(self) -> None:
        self.shares: list[ShareRecord] = []
        self.votes: dict[tuple[UUID, str], VoteChoice] = {}
        self.event_keys: set[tuple[str, str]] = set()
        self.events: list[dict[str, object]] = []

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
        self.shares.append(share)
        return share

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> ShareRecord | None:
        return next(
            (share for share in self.shares if share.job_id == job_id and share.user_id == user_id),
            None,
        )

    async def get_scene(self, *, scene_code: str) -> ShareView | None:
        share = next(
            (item for item in self.shares if item.scene_code == scene_code),
            None,
        )
        if share is None:
            return None
        asset = UserAsset(
            id=share.share_asset_id or uuid4(),
            user_id=share.user_id,
            kind=AssetKind.SHARE_DERIVATIVE,
            status=AssetStatus.READY,
            bucket="test",
            object_key="share-derivatives/card.jpg",
        )
        return ShareView(share=share, job=None, asset=asset)

    async def upsert_vote(
        self,
        *,
        vote_id: UUID,
        share_id: UUID,
        voter_user_id: UUID,
        voter_fingerprint_hash: str,
        choice: VoteChoice,
    ) -> bool:
        key = (share_id, voter_fingerprint_hash)
        reused = self.votes.get(key) == choice
        self.votes[key] = choice
        return reused

    async def vote_tally(self, *, share_id: UUID) -> VoteTally:
        choices = [
            choice
            for (stored_share_id, _), choice in self.votes.items()
            if stored_share_id == share_id
        ]
        return VoteTally(
            before=choices.count(VoteChoice.BEFORE),
            after=choices.count(VoteChoice.AFTER),
        )

    async def viewer_choice(
        self,
        *,
        share_id: UUID,
        voter_fingerprint_hash: str,
    ) -> VoteChoice | None:
        return self.votes.get((share_id, voter_fingerprint_hash))

    async def record_event(
        self,
        *,
        event_id: UUID,
        user_id: UUID,
        event_name: str,
        entity_type: str,
        entity_id: UUID,
        dedupe_key: str,
        properties: dict[str, object],
    ) -> bool:
        key = (event_name, dedupe_key)
        if key in self.event_keys:
            return False
        self.event_keys.add(key)
        self.events.append(
            {
                "event_name": event_name,
                "entity_id": entity_id,
                "properties": properties,
                "user_id": user_id,
            }
        )
        return True


def optimization_record(
    user_id: UUID,
    *,
    status: OptimizationStatus = OptimizationStatus.COMPLETED,
) -> OptimizationRecord:
    diagnosis = StyleDiagnosis(
        id=uuid4(),
        user_id=user_id,
        source_photo_id=uuid4(),
        job_id=uuid4(),
        occasion="WORK",
        status=DiagnosisStatus.COMPLETED,
        score=82,
    )
    job = GenerationJob(
        id=uuid4(),
        user_id=user_id,
        task_type=JobTaskType.STYLE_OPTIMIZATION,
        status=JobStatus.COMPLETED,
        idempotency_key="optimization-job",
        request_hash="a" * 64,
        progress=100,
        retry_count=0,
    )
    result_asset = UserAsset(
        id=uuid4(),
        user_id=user_id,
        kind=AssetKind.OPTIMIZATION_RESULT,
        status=AssetStatus.READY,
        bucket="private",
        object_key=f"private/{user_id}/after.jpg",
    )
    optimization = StyleOptimizationResult(
        id=uuid4(),
        user_id=user_id,
        diagnosis_id=diagnosis.id,
        job_id=job.id,
        result_asset_id=result_asset.id,
        status=status,
        change_level=1,
        change_summary=[
            {
                "priority": 1,
                "action": "ADJUST_WEARING",
                "instruction": "把上衣前摆轻收进裤腰。",
                "reason": "只调整穿法即可抬高视觉腰线。",
                "preserves": "原有上衣、裤子和配色",
            }
        ],
    )
    source_asset = UserAsset(
        id=uuid4(),
        user_id=user_id,
        kind=AssetKind.USER_UPLOAD,
        status=AssetStatus.READY,
        bucket="private",
        object_key=f"private/{user_id}/before.jpg",
    )
    return OptimizationRecord(
        optimization=optimization,
        job=job,
        diagnosis=diagnosis,
        source_asset=source_asset,
        result_asset=result_asset,
    )


def service_fixture(
    record: OptimizationRecord | None,
) -> tuple[
    GrowthApplicationService,
    FakeGrowthRepository,
    FakeJobRepository,
]:
    growth = FakeGrowthRepository()
    jobs = FakeJobRepository()
    service = GrowthApplicationService(
        growth_repository=growth,  # type: ignore[arg-type]
        optimization_repository=FakeOptimizationRepository(record),  # type: ignore[arg-type]
        job_repository=jobs,  # type: ignore[arg-type]
        settings=Settings(),
    )
    return service, growth, jobs


@pytest.mark.asyncio
async def test_share_creation_is_idempotent_and_public_payload_has_no_private_url() -> None:
    user_id = uuid4()
    record = optimization_record(user_id)
    service, growth, jobs = service_fixture(record)

    first = await service.create_share(
        user_id=user_id,
        optimization_id=record.optimization.id,
        idempotency_key="share-request-001",
        display_score=False,
        attribution_source="WECHAT_FRIEND",
    )
    repeated = await service.create_share(
        user_id=user_id,
        optimization_id=record.optimization.id,
        idempotency_key="share-request-001",
        display_score=False,
        attribution_source="WECHAT_FRIEND",
    )

    assert first.reused is False
    assert repeated.reused is True
    assert first.share.id == repeated.share.id
    assert len(growth.shares) == 1
    assert len(jobs.jobs) == 1
    assert len(jobs.events) == 1
    assert "score" not in first.share.public_payload
    assert "private/" not in str(first.share.public_payload)


@pytest.mark.asyncio
async def test_share_requires_a_completed_critic_passed_optimization() -> None:
    user_id = uuid4()
    record = optimization_record(user_id, status=OptimizationStatus.REJECTED_BY_CRITIC)
    service, growth, jobs = service_fixture(record)

    with pytest.raises(GrowthServiceError, match="OPTIMIZATION_NOT_SHAREABLE"):
        await service.create_share(
            user_id=user_id,
            optimization_id=record.optimization.id,
            idempotency_key="share-request-001",
            display_score=False,
            attribution_source="WECHAT_FRIEND",
        )

    assert growth.shares == []
    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_vote_is_idempotent_and_can_be_changed_without_double_counting() -> None:
    user_id = uuid4()
    record = optimization_record(user_id)
    service, growth, _ = service_fixture(record)
    created = await service.create_share(
        user_id=user_id,
        optimization_id=record.optimization.id,
        idempotency_key="share-request-001",
        display_score=True,
        attribution_source="WECHAT_FRIEND",
    )
    created.share.status = ShareStatus.ACTIVE
    created.share.share_asset_id = uuid4()

    first = await service.vote(
        scene_code=created.share.scene_code,
        user_id=user_id,
        choice=VoteChoice.AFTER,
    )
    repeated = await service.vote(
        scene_code=created.share.scene_code,
        user_id=user_id,
        choice=VoteChoice.AFTER,
    )
    changed = await service.vote(
        scene_code=created.share.scene_code,
        user_id=user_id,
        choice=VoteChoice.BEFORE,
    )

    assert first.reused is False
    assert repeated.reused is True
    assert changed.reused is False
    assert changed.tally == VoteTally(before=1, after=0)
    assert len(growth.event_keys) == 2


@pytest.mark.asyncio
async def test_share_open_and_continue_attribution_are_deduplicated() -> None:
    owner_id = uuid4()
    viewer_id = uuid4()
    record = optimization_record(owner_id)
    service, growth, _ = service_fixture(record)
    created = await service.create_share(
        user_id=owner_id,
        optimization_id=record.optimization.id,
        idempotency_key="share-request-001",
        display_score=False,
        attribution_source="WECHAT_FRIEND",
    )
    created.share.status = ShareStatus.ACTIVE
    created.share.share_asset_id = uuid4()

    await service.get_share(
        scene_code=created.share.scene_code,
        viewer_user_id=owner_id,
    )
    await service.get_share(
        scene_code=created.share.scene_code,
        viewer_user_id=viewer_id,
        attribution_source="WECHAT_TIMELINE",
    )
    await service.get_share(
        scene_code=created.share.scene_code,
        viewer_user_id=viewer_id,
    )
    assert await service.record_continue(
        scene_code=created.share.scene_code,
        user_id=viewer_id,
    )
    assert not await service.record_continue(
        scene_code=created.share.scene_code,
        user_id=viewer_id,
    )

    assert {name for name, _ in growth.event_keys} == {
        "share.scene.opened",
        "growth.continue.clicked",
    }
    opened = next(event for event in growth.events if event["event_name"] == "share.scene.opened")
    assert opened["properties"] == {
        "share_id": str(created.share.id),
        "attribution_source": "WECHAT_TIMELINE",
    }


@pytest.mark.asyncio
async def test_share_invocation_is_deduplicated_per_user_and_channel() -> None:
    owner_id = uuid4()
    record = optimization_record(owner_id)
    service, growth, _ = service_fixture(record)
    created = await service.create_share(
        user_id=owner_id,
        optimization_id=record.optimization.id,
        idempotency_key="share-request-001",
        display_score=False,
        attribution_source="WECHAT_FRIEND",
    )
    created.share.status = ShareStatus.ACTIVE
    created.share.share_asset_id = uuid4()

    first = await service.record_share_invocation(
        scene_code=created.share.scene_code,
        user_id=owner_id,
        attribution_source="WECHAT_TIMELINE",
    )
    repeated = await service.record_share_invocation(
        scene_code=created.share.scene_code,
        user_id=owner_id,
        attribution_source="WECHAT_TIMELINE",
    )
    friend = await service.record_share_invocation(
        scene_code=created.share.scene_code,
        user_id=owner_id,
        attribution_source="WECHAT_FRIEND",
    )

    assert first is True
    assert repeated is False
    assert friend is True
    invoked = [event for event in growth.events if event["event_name"] == "share.wechat.invoked"]
    assert [event["properties"] for event in invoked] == [
        {
            "share_id": str(created.share.id),
            "attribution_source": "WECHAT_TIMELINE",
        },
        {
            "share_id": str(created.share.id),
            "attribution_source": "WECHAT_FRIEND",
        },
    ]


def test_vote_fingerprint_is_stable_and_scoped_to_each_share() -> None:
    settings = Settings(identity_hmac_key="test-hmac-key-with-enough-entropy")
    user_id = uuid4()
    share_a = uuid4()

    assert vote_fingerprint(settings, share_id=share_a, user_id=user_id) == (
        vote_fingerprint(settings, share_id=share_a, user_id=user_id)
    )
    assert vote_fingerprint(settings, share_id=share_a, user_id=user_id) != (
        vote_fingerprint(settings, share_id=uuid4(), user_id=user_id)
    )


@pytest.mark.asyncio
async def test_shared_view_hides_owner_job_details_from_friends() -> None:
    class FakeStorage:
        async def create_download_url(
            self,
            *,
            object_key: str,
            expires_in_seconds: int,
        ) -> str:
            return f"https://share.example/{object_key}?expires={expires_in_seconds}"

    owner_id = uuid4()
    friend_id = uuid4()
    job = GenerationJob(
        id=uuid4(),
        user_id=owner_id,
        task_type=JobTaskType.SHARE_ASSET,
        status=JobStatus.COMPLETED,
        idempotency_key="share-job",
        request_hash="a" * 64,
        progress=100,
        retry_count=0,
    )
    share = ShareRecord(
        id=uuid4(),
        user_id=owner_id,
        job_id=job.id,
        target_type="StyleOptimizationResult",
        target_id=uuid4(),
        scene_code="scene_code_1234567890",
        share_asset_id=uuid4(),
        status=ShareStatus.ACTIVE,
        public_payload={
            "change_level": 1,
            "changes": [
                {
                    "priority": 1,
                    "instruction": "把上衣前摆轻收进裤腰。",
                    "reason": "只调整穿法即可抬高视觉腰线。",
                }
            ],
            "ai_edited": True,
            "template_version": "minimal-change-card-v1.0.0",
        },
    )
    asset = UserAsset(
        id=share.share_asset_id,
        user_id=owner_id,
        kind=AssetKind.SHARE_DERIVATIVE,
        status=AssetStatus.READY,
        bucket="share",
        object_key="share-derivatives/scene/card.jpg",
    )
    view = ShareView(share=share, job=job, asset=asset)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                object_storage=FakeStorage(),
                settings=Settings(),
            )
        )
    )

    friend_response = await _response(
        view,
        request=request,  # type: ignore[arg-type]
        viewer_user_id=friend_id,
        tally=VoteTally(before=1, after=2),
        viewer_choice=None,
    )
    owner_response = await _response(
        view,
        request=request,  # type: ignore[arg-type]
        viewer_user_id=owner_id,
        tally=VoteTally(before=1, after=2),
        viewer_choice=None,
    )

    assert friend_response.job_id is None
    assert friend_response.job_status is None
    assert friend_response.error_code is None
    assert owner_response.job_id == job.id
    assert owner_response.job_status == JobStatus.COMPLETED
    assert "private/" not in str(friend_response.model_dump(mode="json"))
