from dataclasses import dataclass
from uuid import UUID, uuid4

from app.core.config import Settings
from app.modules.ai.policies import (
    is_canary_cohort,
    optimization_critic_policy,
    optimization_image_policy,
    policy_snapshot,
)
from app.modules.assets.models import AssetStatus
from app.modules.diagnosis.models import DiagnosisStatus, StyleOptimizationResult
from app.modules.diagnosis.repository import DiagnosisRepository
from app.modules.governance.models import QuotaType
from app.modules.governance.quota import QuotaExceededError, QuotaRepository
from app.modules.jobs.models import JobTaskType
from app.modules.jobs.repository import JobRepository
from app.modules.jobs.service import JobApplicationService, JobServiceError
from app.modules.optimization.prompt import OPTIMIZATION_PLAN_SCHEMA_VERSION
from app.modules.optimization.repository import (
    OptimizationRecord,
    OptimizationRepository,
)
from app.modules.optimization.schema import build_change_budget_plan


class OptimizationServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CreatedOptimization:
    optimization: StyleOptimizationResult
    reused: bool
    quota_remaining: int | None


class OptimizationApplicationService:
    def __init__(
        self,
        *,
        optimization_repository: OptimizationRepository,
        diagnosis_repository: DiagnosisRepository,
        job_repository: JobRepository,
        quota_repository: QuotaRepository,
        settings: Settings,
    ) -> None:
        self._optimizations = optimization_repository
        self._diagnoses = diagnosis_repository
        self._jobs = JobApplicationService(job_repository)
        self._quota = quota_repository
        self._settings = settings

    async def create(
        self,
        *,
        user_id: UUID,
        diagnosis_id: UUID,
        idempotency_key: str,
        max_change_level: int,
    ) -> CreatedOptimization:
        diagnosis_record = await self._diagnoses.get_owned(
            diagnosis_id=diagnosis_id,
            user_id=user_id,
        )
        if diagnosis_record is None:
            raise OptimizationServiceError("DIAGNOSIS_NOT_FOUND")
        diagnosis = diagnosis_record.diagnosis
        if diagnosis.status != DiagnosisStatus.COMPLETED or diagnosis.optimization_plan is None:
            raise OptimizationServiceError("DIAGNOSIS_NOT_READY")
        source_asset = await self._diagnoses.get_owned_source_asset(
            diagnosis_id=diagnosis_id,
            user_id=user_id,
            for_update=True,
        )
        if source_asset is None or source_asset.status != AssetStatus.READY:
            raise OptimizationServiceError("DIAGNOSIS_SOURCE_DELETED")
        try:
            plan = build_change_budget_plan(diagnosis.optimization_plan)
        except (ValueError, TypeError) as error:
            raise OptimizationServiceError("OPTIMIZATION_PLAN_INVALID") from error
        if plan.level > max_change_level:
            raise OptimizationServiceError("CHANGE_BUDGET_EXCEEDS_LIMIT")

        use_canary = bool(
            self._settings.optimization_image_canary_model
            or self._settings.optimization_critic_canary_model
        ) and is_canary_cohort(
            cohort_key=str(user_id),
            percentage=self._settings.ai_canary_percentage,
        )
        image_policy = optimization_image_policy(
            self._settings,
            use_canary=use_canary,
        )
        critic_policy = optimization_critic_policy(
            self._settings,
            use_canary=use_canary,
        )
        request_payload: dict[str, object] = {
            "diagnosis_id": str(diagnosis_id),
            "max_change_level": max_change_level,
            "change_plan": plan.model_dump(mode="json"),
            "schema_version": OPTIMIZATION_PLAN_SCHEMA_VERSION,
        }
        try:
            created_job = await self._jobs.create(
                user_id=user_id,
                task_type=JobTaskType.STYLE_OPTIMIZATION,
                idempotency_key=idempotency_key,
                request_payload=request_payload,
                model_policy_snapshot={
                    "image_edit": policy_snapshot(image_policy),
                    "critic": policy_snapshot(critic_policy),
                    "max_generation_attempts": (
                        self._settings.optimization_max_generation_attempts
                    ),
                    "release_track": "canary" if use_canary else "stable",
                },
            )
        except JobServiceError as error:
            raise OptimizationServiceError(error.code) from error

        if created_job.reused:
            existing = await self._optimizations.get_by_job(
                job_id=created_job.job.id,
                user_id=user_id,
            )
            if existing is None:
                raise OptimizationServiceError("OPTIMIZATION_STATE_INCOMPLETE")
            return CreatedOptimization(
                optimization=existing,
                reused=True,
                quota_remaining=None,
            )

        optimization = await self._optimizations.create(
            optimization_id=uuid4(),
            user_id=user_id,
            diagnosis_id=diagnosis_id,
            job_id=created_job.job.id,
            change_level=plan.level,
            change_summary=[change.model_dump(mode="json") for change in plan.changes],
        )
        try:
            reservation = await self._quota.reserve(
                user_id=user_id,
                job_id=created_job.job.id,
                quota_type=QuotaType.OPTIMIZATION,
            )
        except QuotaExceededError:
            raise
        return CreatedOptimization(
            optimization=optimization,
            reused=False,
            quota_remaining=reservation.remaining,
        )

    async def get(
        self,
        *,
        optimization_id: UUID,
        user_id: UUID,
    ) -> OptimizationRecord:
        record = await self._optimizations.get_owned(
            optimization_id=optimization_id,
            user_id=user_id,
        )
        if record is None or record.optimization.user_id != user_id:
            raise OptimizationServiceError("OPTIMIZATION_NOT_FOUND")
        return record
