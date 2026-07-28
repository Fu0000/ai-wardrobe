from datetime import datetime
from typing import Annotated, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.core.errors import AppError
from app.core.telemetry import current_trace_fields
from app.modules.events.server import (
    ServerEventContext,
    ServerEventRecorder,
)
from app.modules.governance.deletion_repository import DeletionRepository
from app.modules.governance.deletion_service import (
    CreatedDeletion,
    DeletionApplicationService,
    DeletionServiceError,
)
from app.modules.governance.models import DeletionJob, DeletionStatus
from app.modules.identity.api import authenticated_user, current_user
from app.modules.identity.models import User
from app.modules.jobs.repository import JobRepository

router = APIRouter()


class DeletionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    job_id: UUID | None
    status: DeletionStatus
    completed_steps: list[str]
    user_message: str
    can_retry: bool
    requested_at: datetime
    updated_at: datetime
    reused: bool = False


def _service(session: AsyncSession) -> DeletionApplicationService:
    return DeletionApplicationService(
        deletion_repository=DeletionRepository(session),
        job_repository=JobRepository(session),
    )


def _raise_service_error(error: DeletionServiceError) -> Never:
    status_by_code = {
        "DELETION_NOT_FOUND": 404,
        "ASSET_NOT_FOUND": 404,
        "ASSET_DELETION_NOT_FOUND": 404,
        "ACCOUNT_ALREADY_DELETED": 409,
        "IDEMPOTENCY_KEY_REUSED": 409,
        "INVALID_IDEMPOTENCY_KEY": 400,
    }
    message_by_code = {
        "DELETION_NOT_FOUND": "还没有账户删除请求。",
        "ASSET_NOT_FOUND": "照片不存在或已删除。",
        "ASSET_DELETION_NOT_FOUND": "还没有这张照片的删除请求。",
        "ACCOUNT_ALREADY_DELETED": "账户数据已删除。",
        "IDEMPOTENCY_KEY_REUSED": "该请求标识已用于其他删除请求。",
        "INVALID_IDEMPOTENCY_KEY": "请求标识无效。",
    }
    raise AppError(
        code=error.code,
        message=message_by_code.get(error.code, "删除服务暂时不可用，请稍后重试。"),
        status_code=status_by_code.get(error.code, 409),
    ) from error


def _response(
    deletion: DeletionJob,
    *,
    reused: bool = False,
) -> DeletionResponse:
    if deletion.deletion_type.value == "ACCOUNT":
        message_by_status = {
            DeletionStatus.PENDING: "删除请求已提交，正在等待处理。",
            DeletionStatus.PROCESSING: "正在删除照片、AI 结果和账户资料。",
            DeletionStatus.COMPLETED: "账户数据已删除。",
            DeletionStatus.FAILED_RETRYABLE: "删除暂时未完成，系统正在自动重试。",
            DeletionStatus.FAILED_FINAL: "删除暂时未完成，请手动重试。",
        }
    else:
        message_by_status = {
            DeletionStatus.PENDING: "照片删除请求已提交，正在等待处理。",
            DeletionStatus.PROCESSING: "正在删除照片和相关 AI 派生图片。",
            DeletionStatus.COMPLETED: "照片和相关 AI 派生图片已删除。",
            DeletionStatus.FAILED_RETRYABLE: "照片暂未删除，系统正在自动重试。",
            DeletionStatus.FAILED_FINAL: "照片暂未删除，请手动重试。",
        }
    return DeletionResponse(
        id=deletion.id,
        job_id=deletion.job_id,
        status=deletion.status,
        completed_steps=deletion.completed_steps,
        user_message=message_by_status[deletion.status],
        can_retry=deletion.status == DeletionStatus.FAILED_FINAL,
        requested_at=deletion.created_at,
        updated_at=deletion.updated_at,
        reused=reused,
    )


@router.post(
    "/me/deletion-request",
    response_model=DeletionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_account_deletion(
    request: Request,
    user: Annotated[User, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> DeletionResponse:
    try:
        created: CreatedDeletion = await _service(session).request_account_deletion(
            user=user,
            idempotency_key=idempotency_key,
        )
    except DeletionServiceError as error:
        _raise_service_error(error)
    settings: Settings = request.app.state.settings
    await ServerEventRecorder(
        session,
        settings,
        ServerEventContext(
            request_id=str(getattr(request.state, "request_id", "unavailable")),
            trace_id=current_trace_fields().get("trace_id", "unavailable"),
        ),
    ).record(
        subject_user_id=user.id,
        event_name="privacy.deletion.requested",
        entity_type="DeletionJob",
        entity_id=created.deletion.id,
        dedupe_key=str(created.deletion.id),
        properties={"deletion_type": created.deletion.deletion_type.value},
    )
    return _response(created.deletion, reused=created.reused)


@router.get("/me/deletion-status", response_model=DeletionResponse)
async def get_account_deletion_status(
    user: Annotated[User, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeletionResponse:
    try:
        deletion = await _service(session).account_status(user_id=user.id)
    except DeletionServiceError as error:
        _raise_service_error(error)
    return _response(deletion)


@router.delete(
    "/me/photos/{asset_id}",
    response_model=DeletionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_photo_deletion(
    asset_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> DeletionResponse:
    try:
        created = await _service(session).request_asset_deletion(
            user_id=user.id,
            asset_id=asset_id,
            idempotency_key=idempotency_key,
        )
    except DeletionServiceError as error:
        _raise_service_error(error)
    settings: Settings = request.app.state.settings
    await ServerEventRecorder(
        session,
        settings,
        ServerEventContext(
            request_id=str(getattr(request.state, "request_id", "unavailable")),
            trace_id=current_trace_fields().get("trace_id", "unavailable"),
        ),
    ).record(
        subject_user_id=user.id,
        event_name="privacy.deletion.requested",
        entity_type="DeletionJob",
        entity_id=created.deletion.id,
        dedupe_key=str(created.deletion.id),
        properties={"deletion_type": created.deletion.deletion_type.value},
    )
    return _response(created.deletion, reused=created.reused)


@router.get(
    "/me/photos/{asset_id}/deletion-status",
    response_model=DeletionResponse,
)
async def get_photo_deletion_status(
    asset_id: UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeletionResponse:
    try:
        deletion = await _service(session).asset_status(
            user_id=user.id,
            asset_id=asset_id,
        )
    except DeletionServiceError as error:
        _raise_service_error(error)
    return _response(deletion)
