from datetime import datetime
from enum import StrEnum
from typing import Annotated, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimitGuard
from app.core.telemetry import record_product_action
from app.modules.assets.repository import AssetRepository
from app.modules.diagnosis.models import DiagnosisStatus, StyleDiagnosis
from app.modules.diagnosis.repository import DiagnosisRecord, DiagnosisRepository
from app.modules.diagnosis.schema import (
    DiagnosisOutput,
    DiagnosisPoint,
    InputQuality,
    OptimizationStep,
    PrimaryIssue,
)
from app.modules.diagnosis.service import (
    CreatedDiagnosis,
    DiagnosisApplicationService,
    DiagnosisServiceError,
)
from app.modules.governance.quota import QuotaExceededError, QuotaRepository
from app.modules.identity.api import current_user
from app.modules.identity.models import User
from app.modules.jobs.models import JobStatus
from app.modules.jobs.repository import JobRepository

router = APIRouter()


class Occasion(StrEnum):
    DAILY = "DAILY"
    SCHOOL = "SCHOOL"
    WORK = "WORK"
    INTERVIEW = "INTERVIEW"
    DATE = "DATE"
    SOCIAL = "SOCIAL"
    FORMAL = "FORMAL"
    TRAVEL = "TRAVEL"
    OTHER = "OTHER"


class CreateDiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    occasion: Occasion


class DiagnosisResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    job_id: UUID
    occasion: str
    status: DiagnosisStatus
    job_status: JobStatus
    result: DiagnosisOutput | None
    error_code: str | None
    user_message: str | None
    created_at: datetime
    updated_at: datetime
    reused: bool = False
    quota_remaining: int | None = None


def _service(session: AsyncSession, settings: Settings) -> DiagnosisApplicationService:
    return DiagnosisApplicationService(
        diagnosis_repository=DiagnosisRepository(session),
        asset_repository=AssetRepository(session),
        job_repository=JobRepository(session),
        quota_repository=QuotaRepository(session),
        settings=settings,
    )


def _raise_service_error(error: DiagnosisServiceError) -> Never:
    status_by_code = {
        "AI_CONSENT_REQUIRED": 403,
        "ASSET_NOT_FOUND": 404,
        "ASSET_NOT_READY": 409,
        "DIAGNOSIS_NOT_FOUND": 404,
        "IDEMPOTENCY_KEY_REUSED": 409,
        "INVALID_IDEMPOTENCY_KEY": 400,
    }
    message_by_code = {
        "AI_CONSENT_REQUIRED": "请先在个人设置中允许 AI 分析穿搭照片。",
        "ASSET_NOT_FOUND": "照片不存在。",
        "ASSET_NOT_READY": "照片还没有准备好。",
        "DIAGNOSIS_NOT_FOUND": "诊断不存在。",
        "IDEMPOTENCY_KEY_REUSED": "该请求标识已用于其他诊断，请重新提交。",
        "INVALID_IDEMPOTENCY_KEY": "请求标识无效。",
    }
    raise AppError(
        code=error.code,
        message=message_by_code.get(error.code, "诊断暂时无法创建，请稍后重试。"),
        status_code=status_by_code.get(error.code, 409),
    ) from error


def _result(diagnosis: StyleDiagnosis) -> DiagnosisOutput | None:
    if diagnosis.status != DiagnosisStatus.COMPLETED:
        return None
    if (
        diagnosis.input_quality is None
        or diagnosis.score is None
        or diagnosis.summary is None
        or diagnosis.strengths is None
        or diagnosis.issues is None
        or diagnosis.primary_issue is None
        or diagnosis.optimization_plan is None
        or diagnosis.disclaimer is None
    ):
        raise AppError(
            code="DIAGNOSIS_RESULT_INCOMPLETE",
            message="诊断结果正在恢复，请稍后刷新。",
            status_code=503,
        )
    return DiagnosisOutput(
        input_quality=InputQuality(diagnosis.input_quality),
        input_quality_message=diagnosis.input_quality_message,
        score=diagnosis.score,
        summary=diagnosis.summary,
        strengths=[DiagnosisPoint.model_validate(item) for item in diagnosis.strengths],
        issues=[DiagnosisPoint.model_validate(item) for item in diagnosis.issues],
        primary_issue=PrimaryIssue.model_validate(diagnosis.primary_issue),
        optimization_plan=[
            OptimizationStep.model_validate(item) for item in diagnosis.optimization_plan
        ],
        disclaimer=diagnosis.disclaimer,
    )


def _response(
    record: DiagnosisRecord,
    *,
    reused: bool = False,
    quota_remaining: int | None = None,
) -> DiagnosisResponse:
    diagnosis = record.diagnosis
    job = record.job
    return DiagnosisResponse(
        id=diagnosis.id,
        job_id=job.id,
        occasion=diagnosis.occasion,
        status=diagnosis.status,
        job_status=job.status,
        result=_result(diagnosis),
        error_code=job.error_code,
        user_message=job.user_message,
        created_at=diagnosis.created_at,
        updated_at=diagnosis.updated_at,
        reused=reused,
        quota_remaining=quota_remaining,
    )


@router.post(
    "/style-diagnoses",
    response_model=DiagnosisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_diagnosis(
    payload: CreateDiagnosisRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> DiagnosisResponse:
    rate_guard: RateLimitGuard = request.app.state.rate_limit_guard
    await rate_guard.enforce_costly_action(str(user.id))
    settings: Settings = request.app.state.settings
    service = _service(session, settings)
    try:
        created: CreatedDiagnosis = await service.create(
            user=user,
            asset_id=payload.asset_id,
            occasion=payload.occasion.value,
            idempotency_key=idempotency_key,
        )
        record = await service.get(
            diagnosis_id=created.diagnosis.id,
            user_id=user.id,
        )
    except QuotaExceededError as error:
        raise AppError(
            code="QUOTA_EXCEEDED",
            message="今日免费诊断次数已用完，请明天再试。",
            status_code=429,
            details=[{"period": error.period.value, "limit": error.limit}],
        ) from error
    except DiagnosisServiceError as error:
        _raise_service_error(error)
    response = _response(
        record,
        reused=created.reused,
        quota_remaining=created.quota_remaining,
    )
    record_product_action(
        action="diagnosis_requested",
        outcome="reused" if created.reused else "created",
    )
    return response


@router.get("/style-diagnoses/{diagnosis_id}", response_model=DiagnosisResponse)
async def get_diagnosis(
    diagnosis_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DiagnosisResponse:
    settings: Settings = request.app.state.settings
    try:
        record = await _service(session, settings).get(
            diagnosis_id=diagnosis_id,
            user_id=user.id,
        )
    except DiagnosisServiceError as error:
        _raise_service_error(error)
    return _response(record)
