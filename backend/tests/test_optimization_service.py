from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.modules.assets.models import AssetKind, AssetStatus, UserAsset
from app.modules.diagnosis.models import (
    DiagnosisStatus,
    StyleDiagnosis,
    StyleOptimizationResult,
)
from app.modules.diagnosis.repository import DiagnosisRecord
from app.modules.events.models import OutboxEvent
from app.modules.governance.models import QuotaReservationStatus
from app.modules.governance.quota import QuotaReservationResult
from app.modules.jobs.models import GenerationJob, JobStatus, JobTaskType
from app.modules.optimization.repository import OptimizationRecord
from app.modules.optimization.service import (
    OptimizationApplicationService,
    OptimizationServiceError,
)


class FakeDiagnosisRepository:
    def __init__(
        self,
        record: DiagnosisRecord | None,
        *,
        source_status: AssetStatus = AssetStatus.READY,
    ) -> None:
        self.record = record
        self.source_asset = (
            UserAsset(
                id=uuid4(),
                user_id=record.diagnosis.user_id,
                kind=AssetKind.USER_UPLOAD,
                status=source_status,
                bucket="test",
                object_key=f"private/{record.diagnosis.user_id}/source.jpg",
            )
            if record is not None
            else None
        )

    async def get_owned(
        self,
        *,
        diagnosis_id: UUID,
        user_id: UUID,
    ) -> DiagnosisRecord | None:
        if (
            self.record
            and self.record.diagnosis.id == diagnosis_id
            and self.record.diagnosis.user_id == user_id
        ):
            return self.record
        return None

    async def get_owned_source_asset(
        self,
        *,
        diagnosis_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> UserAsset | None:
        if (
            self.record is not None
            and self.record.diagnosis.id == diagnosis_id
            and self.record.diagnosis.user_id == user_id
        ):
            return self.source_asset
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


class FakeOptimizationRepository:
    def __init__(self, jobs: FakeJobRepository, diagnosis: StyleDiagnosis) -> None:
        self._jobs = jobs
        self._diagnosis = diagnosis
        self.optimizations: list[StyleOptimizationResult] = []

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
        self.optimizations.append(optimization)
        return optimization

    async def get_by_job(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
    ) -> StyleOptimizationResult | None:
        return next(
            (
                item
                for item in self.optimizations
                if item.job_id == job_id and item.user_id == user_id
            ),
            None,
        )

    async def get_owned(
        self,
        *,
        optimization_id: UUID,
        user_id: UUID,
    ) -> OptimizationRecord | None:
        optimization = next(
            (
                item
                for item in self.optimizations
                if item.id == optimization_id and item.user_id == user_id
            ),
            None,
        )
        if optimization is None:
            return None
        source_asset = UserAsset(
            id=uuid4(),
            user_id=user_id,
            kind=AssetKind.USER_UPLOAD,
            status=AssetStatus.READY,
            bucket="test",
            object_key=f"private/{user_id}/source.jpg",
        )
        return OptimizationRecord(
            optimization=optimization,
            job=next(job for job in self._jobs.jobs if job.id == optimization.job_id),
            diagnosis=self._diagnosis,
            source_asset=source_asset,
            result_asset=None,
        )


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
            remaining=1,
        )


def diagnosis_record(
    user_id: UUID,
    *,
    status: DiagnosisStatus = DiagnosisStatus.COMPLETED,
    actions: list[str] | None = None,
) -> DiagnosisRecord:
    diagnosis_id = uuid4()
    job = GenerationJob(
        id=uuid4(),
        user_id=user_id,
        task_type=JobTaskType.STYLE_DIAGNOSIS,
        status=JobStatus.COMPLETED,
        idempotency_key="diagnosis-request",
        request_hash="a" * 64,
        progress=100,
        retry_count=0,
    )
    plan_actions = actions or ["ADJUST_WEARING"]
    diagnosis = StyleDiagnosis(
        id=diagnosis_id,
        user_id=user_id,
        source_photo_id=uuid4(),
        job_id=job.id,
        occasion="WORK",
        status=status,
        optimization_plan=[
            {
                "priority": index + 1,
                "action": action,
                "instruction": f"执行第 {index + 1} 个最小调整。",
                "reason": "只做达到明显改善所需的最少修改。",
                "preserves": "身份与未提及衣物",
            }
            for index, action in enumerate(plan_actions)
        ],
    )
    return DiagnosisRecord(diagnosis=diagnosis, job=job)


def service_fixture(
    record: DiagnosisRecord,
    *,
    source_status: AssetStatus = AssetStatus.READY,
) -> tuple[
    OptimizationApplicationService,
    FakeJobRepository,
    FakeOptimizationRepository,
    FakeQuotaRepository,
]:
    jobs = FakeJobRepository()
    optimizations = FakeOptimizationRepository(jobs, record.diagnosis)
    quota = FakeQuotaRepository()
    service = OptimizationApplicationService(
        optimization_repository=optimizations,  # type: ignore[arg-type]
        diagnosis_repository=FakeDiagnosisRepository(  # type: ignore[arg-type]
            record,
            source_status=source_status,
        ),
        job_repository=jobs,  # type: ignore[arg-type]
        quota_repository=quota,  # type: ignore[arg-type]
        settings=Settings(),
    )
    return service, jobs, optimizations, quota


@pytest.mark.asyncio
async def test_optimization_cannot_start_after_source_photo_deletion_begins() -> None:
    user_id = uuid4()
    record = diagnosis_record(user_id)
    service, jobs, _, quota = service_fixture(
        record,
        source_status=AssetStatus.DELETION_PENDING,
    )

    with pytest.raises(OptimizationServiceError, match="DIAGNOSIS_SOURCE_DELETED"):
        await service.create(
            user_id=user_id,
            diagnosis_id=record.diagnosis.id,
            idempotency_key="optimization-after-delete",
            max_change_level=3,
        )

    assert jobs.jobs == []
    assert quota.reserve_count == 0


@pytest.mark.asyncio
async def test_optimization_is_idempotent_and_reserves_once() -> None:
    user_id = uuid4()
    record = diagnosis_record(user_id)
    service, jobs, optimizations, quota = service_fixture(record)

    first = await service.create(
        user_id=user_id,
        diagnosis_id=record.diagnosis.id,
        idempotency_key="optimization-request-1",
        max_change_level=3,
    )
    repeated = await service.create(
        user_id=user_id,
        diagnosis_id=record.diagnosis.id,
        idempotency_key="optimization-request-1",
        max_change_level=3,
    )

    assert first.reused is False
    assert repeated.reused is True
    assert repeated.optimization.id == first.optimization.id
    assert len(jobs.jobs) == 1
    assert len(jobs.events) == 1
    assert len(optimizations.optimizations) == 1
    assert quota.reserve_count == 1


@pytest.mark.asyncio
async def test_optimization_hides_another_users_diagnosis() -> None:
    owner_id = uuid4()
    record = diagnosis_record(owner_id)
    service, jobs, _, quota = service_fixture(record)

    with pytest.raises(OptimizationServiceError, match="DIAGNOSIS_NOT_FOUND"):
        await service.create(
            user_id=uuid4(),
            diagnosis_id=record.diagnosis.id,
            idempotency_key="optimization-request-1",
            max_change_level=3,
        )

    assert jobs.jobs == []
    assert quota.reserve_count == 0


@pytest.mark.asyncio
async def test_optimization_requires_completed_diagnosis() -> None:
    user_id = uuid4()
    record = diagnosis_record(user_id, status=DiagnosisStatus.PENDING)
    service, jobs, _, _ = service_fixture(record)

    with pytest.raises(OptimizationServiceError, match="DIAGNOSIS_NOT_READY"):
        await service.create(
            user_id=user_id,
            diagnosis_id=record.diagnosis.id,
            idempotency_key="optimization-request-1",
            max_change_level=3,
        )

    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_user_change_budget_cap_is_enforced_before_job_creation() -> None:
    user_id = uuid4()
    record = diagnosis_record(user_id, actions=["REPLACE_ONE_ITEM", "REPLACE_ONE_ITEM"])
    service, jobs, _, quota = service_fixture(record)

    with pytest.raises(
        OptimizationServiceError,
        match="CHANGE_BUDGET_EXCEEDS_LIMIT",
    ):
        await service.create(
            user_id=user_id,
            diagnosis_id=record.diagnosis.id,
            idempotency_key="optimization-request-1",
            max_change_level=2,
        )

    assert jobs.jobs == []
    assert quota.reserve_count == 0
