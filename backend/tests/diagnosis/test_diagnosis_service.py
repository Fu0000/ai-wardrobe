from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.modules.assets.models import (
    AssetKind,
    AssetStatus,
    PhotoPurpose,
    SourcePhoto,
    UserAsset,
)
from app.modules.diagnosis.models import DiagnosisStatus, StyleDiagnosis
from app.modules.diagnosis.repository import DiagnosisRecord
from app.modules.diagnosis.service import (
    DiagnosisApplicationService,
    DiagnosisServiceError,
)
from app.modules.events.models import OutboxEvent
from app.modules.governance.models import QuotaReservationStatus
from app.modules.governance.quota import QuotaReservationResult
from app.modules.identity.models import User
from app.modules.jobs.models import GenerationJob, JobTaskType


class FakeAssetRepository:
    def __init__(self, asset: UserAsset | None) -> None:
        self.asset = asset
        self.lookup_count = 0

    async def get_owned(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> UserAsset | None:
        self.lookup_count += 1
        if self.asset is not None and self.asset.id == asset_id and self.asset.user_id == user_id:
            return self.asset
        return None


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

    async def get_owned(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> GenerationJob | None:
        return next(
            (job for job in self.jobs if job.id == job_id and job.user_id == user_id),
            None,
        )


class FakeDiagnosisRepository:
    def __init__(self, jobs: FakeJobRepository) -> None:
        self._jobs = jobs
        self.photos: list[SourcePhoto] = []
        self.diagnoses: list[StyleDiagnosis] = []

    async def get_or_create_source_photo(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
    ) -> SourcePhoto:
        existing = next(
            (photo for photo in self.photos if photo.asset_id == asset_id),
            None,
        )
        if existing:
            return existing
        photo = SourcePhoto(
            id=uuid4(),
            user_id=user_id,
            asset_id=asset_id,
            purpose=PhotoPurpose.OUTFIT_DIAGNOSIS,
        )
        self.photos.append(photo)
        return photo

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
        self.diagnoses.append(diagnosis)
        return diagnosis

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> StyleDiagnosis | None:
        return next(
            (
                diagnosis
                for diagnosis in self.diagnoses
                if diagnosis.job_id == job_id and diagnosis.user_id == user_id
            ),
            None,
        )

    async def get_owned(
        self,
        *,
        diagnosis_id: UUID,
        user_id: UUID,
    ) -> DiagnosisRecord | None:
        diagnosis = next(
            (
                item
                for item in self.diagnoses
                if item.id == diagnosis_id and item.user_id == user_id
            ),
            None,
        )
        if diagnosis is None:
            return None
        job = next(job for job in self._jobs.jobs if job.id == diagnosis.job_id)
        return DiagnosisRecord(diagnosis=diagnosis, job=job)


class FakeQuotaRepository:
    def __init__(self) -> None:
        self.reserve_count = 0

    async def reserve(
        self,
        *,
        user_id: UUID,
        job_id: UUID,
        quota_type: object,
    ) -> QuotaReservationResult:
        self.reserve_count += 1
        return QuotaReservationResult(
            reservation_id=uuid4(),
            status=QuotaReservationStatus.RESERVED,
            remaining=4,
        )


def user_with_consent(has_consent: bool = True) -> User:
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.profile = MagicMock(has_ai_processing_consent=has_consent)
    return user


def ready_asset(user_id: UUID) -> UserAsset:
    return UserAsset(
        id=uuid4(),
        user_id=user_id,
        kind=AssetKind.USER_UPLOAD,
        status=AssetStatus.READY,
        bucket="wardrobe-test",
        object_key=f"private/{user_id}/photo.png",
    )


def service_fixture(
    *,
    user: User,
    asset: UserAsset | None,
) -> tuple[
    DiagnosisApplicationService,
    FakeJobRepository,
    FakeDiagnosisRepository,
    FakeQuotaRepository,
]:
    jobs = FakeJobRepository()
    diagnoses = FakeDiagnosisRepository(jobs)
    quota = FakeQuotaRepository()
    service = DiagnosisApplicationService(
        diagnosis_repository=diagnoses,  # type: ignore[arg-type]
        asset_repository=FakeAssetRepository(asset),  # type: ignore[arg-type]
        job_repository=jobs,  # type: ignore[arg-type]
        quota_repository=quota,  # type: ignore[arg-type]
        settings=Settings(),
    )
    return service, jobs, diagnoses, quota


@pytest.mark.asyncio
async def test_diagnosis_requires_explicit_ai_consent() -> None:
    user = user_with_consent(False)
    service, jobs, _, quota = service_fixture(
        user=user,
        asset=ready_asset(user.id),
    )

    with pytest.raises(DiagnosisServiceError, match="AI_CONSENT_REQUIRED"):
        await service.create(
            user=user,
            asset_id=uuid4(),
            occasion="DAILY",
            idempotency_key="diagnosis-request-1",
        )

    assert jobs.jobs == []
    assert quota.reserve_count == 0


@pytest.mark.asyncio
async def test_diagnosis_hides_another_users_asset() -> None:
    user = user_with_consent()
    other_asset = ready_asset(uuid4())
    service, jobs, _, quota = service_fixture(user=user, asset=other_asset)

    with pytest.raises(DiagnosisServiceError, match="ASSET_NOT_FOUND"):
        await service.create(
            user=user,
            asset_id=other_asset.id,
            occasion="WORK",
            idempotency_key="diagnosis-request-1",
        )

    assert jobs.jobs == []
    assert quota.reserve_count == 0


@pytest.mark.asyncio
async def test_diagnosis_requires_completed_asset() -> None:
    user = user_with_consent()
    asset = ready_asset(user.id)
    asset.status = AssetStatus.UPLOADING
    service, jobs, _, quota = service_fixture(user=user, asset=asset)

    with pytest.raises(DiagnosisServiceError, match="ASSET_NOT_READY"):
        await service.create(
            user=user,
            asset_id=asset.id,
            occasion="DATE",
            idempotency_key="diagnosis-request-1",
        )

    assert jobs.jobs == []
    assert quota.reserve_count == 0


@pytest.mark.asyncio
async def test_diagnosis_is_idempotent_and_reserves_quota_once() -> None:
    user = user_with_consent()
    asset = ready_asset(user.id)
    service, jobs, diagnoses, quota = service_fixture(user=user, asset=asset)

    first = await service.create(
        user=user,
        asset_id=asset.id,
        occasion="INTERVIEW",
        idempotency_key="diagnosis-request-1",
    )
    repeated = await service.create(
        user=user,
        asset_id=asset.id,
        occasion="INTERVIEW",
        idempotency_key="diagnosis-request-1",
    )

    assert first.reused is False
    assert repeated.reused is True
    assert repeated.diagnosis.id == first.diagnosis.id
    assert first.quota_remaining == 4
    assert len(jobs.jobs) == 1
    assert len(jobs.events) == 1
    assert len(diagnoses.photos) == 1
    assert len(diagnoses.diagnoses) == 1
    assert quota.reserve_count == 1


@pytest.mark.asyncio
async def test_diagnosis_lookup_is_user_scoped() -> None:
    user = user_with_consent()
    asset = ready_asset(user.id)
    service, _, _, _ = service_fixture(user=user, asset=asset)
    created = await service.create(
        user=user,
        asset_id=asset.id,
        occasion="TRAVEL",
        idempotency_key="diagnosis-request-1",
    )

    with pytest.raises(DiagnosisServiceError, match="DIAGNOSIS_NOT_FOUND"):
        await service.get(
            diagnosis_id=created.diagnosis.id,
            user_id=uuid4(),
        )
