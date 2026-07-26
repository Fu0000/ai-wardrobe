from dataclasses import dataclass
from uuid import UUID, uuid4

from app.core.config import Settings
from app.modules.ai.policies import diagnosis_policy, is_canary_cohort, policy_snapshot
from app.modules.assets.models import AssetStatus
from app.modules.assets.repository import AssetRepository
from app.modules.diagnosis.models import StyleDiagnosis
from app.modules.diagnosis.prompt import DIAGNOSIS_SCHEMA_VERSION
from app.modules.diagnosis.repository import DiagnosisRecord, DiagnosisRepository
from app.modules.governance.models import QuotaType
from app.modules.governance.quota import (
    QuotaExceededError,
    QuotaRepository,
)
from app.modules.identity.models import User
from app.modules.jobs.models import JobTaskType
from app.modules.jobs.repository import JobRepository
from app.modules.jobs.service import JobApplicationService, JobServiceError


class DiagnosisServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CreatedDiagnosis:
    diagnosis: StyleDiagnosis
    reused: bool
    quota_remaining: int | None


class DiagnosisApplicationService:
    def __init__(
        self,
        *,
        diagnosis_repository: DiagnosisRepository,
        asset_repository: AssetRepository,
        job_repository: JobRepository,
        quota_repository: QuotaRepository,
        settings: Settings,
    ) -> None:
        self._diagnoses = diagnosis_repository
        self._assets = asset_repository
        self._jobs = JobApplicationService(job_repository)
        self._quota = quota_repository
        self._settings = settings

    async def create(
        self,
        *,
        user: User,
        asset_id: UUID,
        occasion: str,
        idempotency_key: str,
    ) -> CreatedDiagnosis:
        profile = user.profile
        if profile is None or not profile.has_ai_processing_consent:
            raise DiagnosisServiceError("AI_CONSENT_REQUIRED")

        asset = await self._assets.get_owned(
            asset_id=asset_id,
            user_id=user.id,
            for_update=True,
        )
        if asset is None or asset.user_id != user.id:
            raise DiagnosisServiceError("ASSET_NOT_FOUND")
        if asset.status != AssetStatus.READY:
            raise DiagnosisServiceError("ASSET_NOT_READY")

        use_canary = bool(self._settings.diagnosis_canary_model) and is_canary_cohort(
            cohort_key=str(user.id),
            percentage=self._settings.ai_canary_percentage,
        )
        policy = diagnosis_policy(self._settings, use_canary=use_canary)
        request_payload: dict[str, object] = {
            "asset_id": str(asset_id),
            "occasion": occasion,
            "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        }
        try:
            created_job = await self._jobs.create(
                user_id=user.id,
                task_type=JobTaskType.STYLE_DIAGNOSIS,
                idempotency_key=idempotency_key,
                request_payload=request_payload,
                model_policy_snapshot={
                    **policy_snapshot(policy),
                    "release_track": "canary" if use_canary else "stable",
                },
            )
        except JobServiceError as error:
            raise DiagnosisServiceError(error.code) from error

        if created_job.reused:
            existing = await self._diagnoses.get_by_job(
                job_id=created_job.job.id,
                user_id=user.id,
            )
            if existing is None:
                raise DiagnosisServiceError("DIAGNOSIS_STATE_INCOMPLETE")
            return CreatedDiagnosis(
                diagnosis=existing,
                reused=True,
                quota_remaining=None,
            )

        source_photo = await self._diagnoses.get_or_create_source_photo(
            user_id=user.id,
            asset_id=asset_id,
        )
        diagnosis = await self._diagnoses.create(
            diagnosis_id=uuid4(),
            user_id=user.id,
            source_photo_id=source_photo.id,
            job_id=created_job.job.id,
            occasion=occasion,
        )
        try:
            reservation = await self._quota.reserve(
                user_id=user.id,
                job_id=created_job.job.id,
                quota_type=QuotaType.DIAGNOSIS,
            )
        except QuotaExceededError:
            raise
        return CreatedDiagnosis(
            diagnosis=diagnosis,
            reused=False,
            quota_remaining=reservation.remaining,
        )

    async def get(
        self,
        *,
        diagnosis_id: UUID,
        user_id: UUID,
    ) -> DiagnosisRecord:
        record = await self._diagnoses.get_owned(
            diagnosis_id=diagnosis_id,
            user_id=user_id,
        )
        if record is None or record.diagnosis.user_id != user_id:
            raise DiagnosisServiceError("DIAGNOSIS_NOT_FOUND")
        return record
