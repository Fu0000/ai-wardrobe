from datetime import datetime
from typing import Annotated, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimitGuard
from app.core.telemetry import current_trace_fields, record_product_action
from app.modules.assets.storage import (
    ObjectStorage,
    ObjectStorageUnavailableError,
)
from app.modules.diagnosis.models import OptimizationStatus
from app.modules.diagnosis.repository import DiagnosisRepository
from app.modules.events.server import (
    ServerEventContext,
    ServerEventRecorder,
)
from app.modules.governance.quota import QuotaExceededError, QuotaRepository
from app.modules.identity.api import current_user
from app.modules.identity.models import User
from app.modules.jobs.models import JobStatus
from app.modules.jobs.repository import JobRepository
from app.modules.optimization.repository import (
    OptimizationRecord,
    OptimizationRepository,
)
from app.modules.optimization.schema import ChangeInstruction
from app.modules.optimization.service import (
    CreatedOptimization,
    OptimizationApplicationService,
    OptimizationServiceError,
)

router = APIRouter()


class CreateOptimizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_change_level: int = Field(default=3, ge=1, le=3)


class OptimizationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    diagnosis_id: UUID
    job_id: UUID
    status: OptimizationStatus
    job_status: JobStatus
    change_level: int
    changes: list[ChangeInstruction]
    quality_passed: bool
    critic_first_pass: bool | None
    before_image_url: str | None
    after_image_url: str | None
    error_code: str | None
    user_message: str | None
    created_at: datetime
    updated_at: datetime
    reused: bool = False
    quota_remaining: int | None = None


def _service(session: AsyncSession, settings: Settings) -> OptimizationApplicationService:
    return OptimizationApplicationService(
        optimization_repository=OptimizationRepository(session),
        diagnosis_repository=DiagnosisRepository(session),
        job_repository=JobRepository(session),
        quota_repository=QuotaRepository(session),
        settings=settings,
    )


def _raise_service_error(error: OptimizationServiceError) -> Never:
    status_by_code = {
        "DIAGNOSIS_NOT_FOUND": 404,
        "DIAGNOSIS_NOT_READY": 409,
        "DIAGNOSIS_SOURCE_DELETED": 409,
        "OPTIMIZATION_NOT_FOUND": 404,
        "CHANGE_BUDGET_EXCEEDS_LIMIT": 409,
        "IDEMPOTENCY_KEY_REUSED": 409,
        "INVALID_IDEMPOTENCY_KEY": 400,
    }
    message_by_code = {
        "DIAGNOSIS_NOT_FOUND": "诊断不存在。",
        "DIAGNOSIS_NOT_READY": "请等待文字诊断完成后再生成优化图。",
        "DIAGNOSIS_SOURCE_DELETED": "原始照片正在删除，无法继续生成优化图。",
        "OPTIMIZATION_NOT_FOUND": "优化结果不存在。",
        "CHANGE_BUDGET_EXCEEDS_LIMIT": "这份建议需要更高的修改额度。",
        "OPTIMIZATION_PLAN_INVALID": "当前诊断没有可安全执行的修改计划。",
        "IDEMPOTENCY_KEY_REUSED": "该请求标识已用于其他优化，请重新提交。",
        "INVALID_IDEMPOTENCY_KEY": "请求标识无效。",
    }
    raise AppError(
        code=error.code,
        message=message_by_code.get(
            error.code,
            "优化任务暂时无法创建，请稍后重试。",
        ),
        status_code=status_by_code.get(error.code, 409),
    ) from error


async def _response(
    record: OptimizationRecord,
    *,
    storage: ObjectStorage,
    settings: Settings,
    reused: bool = False,
    quota_remaining: int | None = None,
) -> OptimizationResponse:
    try:
        before_url = await storage.create_download_url(
            object_key=record.source_asset.object_key,
            expires_in_seconds=settings.cos_download_url_ttl_seconds,
        )
    except ObjectStorageUnavailableError as error:
        raise AppError(
            code="ASSET_STORAGE_UNAVAILABLE",
            message="图片暂时无法读取，请稍后刷新。",
            status_code=503,
        ) from error
    after_url: str | None = None
    if (
        record.optimization.status == OptimizationStatus.COMPLETED
        and record.result_asset is not None
    ):
        try:
            after_url = await storage.create_download_url(
                object_key=record.result_asset.object_key,
                expires_in_seconds=settings.cos_download_url_ttl_seconds,
            )
        except ObjectStorageUnavailableError as error:
            raise AppError(
                code="ASSET_STORAGE_UNAVAILABLE",
                message="图片暂时无法读取，请稍后刷新。",
                status_code=503,
            ) from error
    return OptimizationResponse(
        id=record.optimization.id,
        diagnosis_id=record.optimization.diagnosis_id,
        job_id=record.job.id,
        status=record.optimization.status,
        job_status=record.job.status,
        change_level=record.optimization.change_level,
        changes=[
            ChangeInstruction.model_validate(item) for item in record.optimization.change_summary
        ],
        quality_passed=record.optimization.status == OptimizationStatus.COMPLETED,
        critic_first_pass=(
            bool(record.optimization.quality_report.get("first_pass"))
            if record.optimization.quality_report is not None
            else None
        ),
        before_image_url=before_url,
        after_image_url=after_url,
        error_code=record.job.error_code,
        user_message=record.job.user_message,
        created_at=record.optimization.created_at,
        updated_at=record.optimization.updated_at,
        reused=reused,
        quota_remaining=quota_remaining,
    )


@router.post(
    "/style-diagnoses/{diagnosis_id}/optimizations",
    response_model=OptimizationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_optimization(
    diagnosis_id: UUID,
    payload: CreateOptimizationRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> OptimizationResponse:
    rate_guard: RateLimitGuard = request.app.state.rate_limit_guard
    await rate_guard.enforce_costly_action(str(user.id))
    settings: Settings = request.app.state.settings
    service = _service(session, settings)
    try:
        created: CreatedOptimization = await service.create(
            user_id=user.id,
            diagnosis_id=diagnosis_id,
            idempotency_key=idempotency_key,
            max_change_level=payload.max_change_level,
        )
        record = await service.get(
            optimization_id=created.optimization.id,
            user_id=user.id,
        )
    except QuotaExceededError as error:
        raise AppError(
            code="QUOTA_EXCEEDED",
            message="今日免费优化次数已用完，请明天再试。",
            status_code=429,
            details=[{"period": error.period.value, "limit": error.limit}],
        ) from error
    except OptimizationServiceError as error:
        _raise_service_error(error)
    await ServerEventRecorder(
        session,
        settings,
        ServerEventContext(
            request_id=str(getattr(request.state, "request_id", "unavailable")),
            trace_id=current_trace_fields().get("trace_id", "unavailable"),
        ),
    ).record(
        subject_user_id=user.id,
        event_name="optimization.job.created",
        entity_type="GenerationJob",
        entity_id=record.job.id,
        dedupe_key=str(record.job.id),
        properties={
            "job_id": str(record.job.id),
            "change_level": record.optimization.change_level,
        },
    )
    response = await _response(
        record,
        storage=request.app.state.object_storage,
        settings=settings,
        reused=created.reused,
        quota_remaining=created.quota_remaining,
    )
    record_product_action(
        action="optimization_requested",
        outcome="reused" if created.reused else "created",
    )
    return response


@router.get(
    "/style-optimizations/{optimization_id}",
    response_model=OptimizationResponse,
)
async def get_optimization(
    optimization_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OptimizationResponse:
    settings: Settings = request.app.state.settings
    try:
        record = await _service(session, settings).get(
            optimization_id=optimization_id,
            user_id=user.id,
        )
    except OptimizationServiceError as error:
        _raise_service_error(error)
    return await _response(
        record,
        storage=request.app.state.object_storage,
        settings=settings,
    )
