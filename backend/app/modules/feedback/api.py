from datetime import datetime
from typing import Annotated, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.errors import AppError
from app.core.rate_limit import RateLimitGuard
from app.core.telemetry import record_product_action
from app.modules.feedback.models import BetaFeedback, FeedbackCategory, FeedbackStatus
from app.modules.feedback.repository import FeedbackRepository
from app.modules.feedback.service import (
    FeedbackApplicationService,
    FeedbackInput,
    FeedbackServiceError,
)
from app.modules.identity.api import current_user
from app.modules.identity.models import User

router = APIRouter()


class CreateFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FeedbackCategory
    rating: int | None = Field(default=None, ge=1, le=5)
    message: str = Field(min_length=10, max_length=2_000)
    related_job_id: UUID | None = None
    page: str | None = Field(default=None, max_length=120)
    app_version: str | None = Field(default=None, max_length=40)
    platform: str | None = Field(default=None, max_length=40)
    system_version: str | None = Field(default=None, max_length=80)
    wechat_version: str | None = Field(default=None, max_length=40)
    network_type: str | None = Field(default=None, max_length=24)
    trace_id: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
    )

    @field_validator(
        "page",
        "app_version",
        "platform",
        "system_version",
        "wechat_version",
        "network_type",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 10:
            raise ValueError("feedback message must contain at least 10 characters")
        return normalized


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    category: FeedbackCategory
    status: FeedbackStatus
    rating: int | None
    message: str
    related_job_id: UUID | None
    created_at: datetime
    reused: bool = False


class FeedbackPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[FeedbackResponse]
    next_cursor: str | None


def _service(session: AsyncSession) -> FeedbackApplicationService:
    return FeedbackApplicationService(FeedbackRepository(session))


def _raise_service_error(error: FeedbackServiceError) -> Never:
    status_by_code = {
        "INVALID_IDEMPOTENCY_KEY": 400,
        "INVALID_FEEDBACK_CURSOR": 400,
        "IDEMPOTENCY_KEY_REUSED": 409,
        "RELATED_JOB_NOT_FOUND": 404,
        "FEEDBACK_CREATE_CONFLICT": 409,
    }
    message_by_code = {
        "INVALID_IDEMPOTENCY_KEY": "反馈请求标识无效。",
        "INVALID_FEEDBACK_CURSOR": "反馈列表游标无效，请重新加载。",
        "IDEMPOTENCY_KEY_REUSED": "该请求标识已用于其他反馈，请重新提交。",
        "RELATED_JOB_NOT_FOUND": "关联任务不存在。",
        "FEEDBACK_CREATE_CONFLICT": "反馈正在提交，请稍后重试。",
    }
    raise AppError(
        code=error.code,
        message=message_by_code.get(error.code, "反馈暂时无法提交，请稍后重试。"),
        status_code=status_by_code.get(error.code, 409),
    ) from error


def _response(
    feedback: BetaFeedback,
    *,
    reused: bool = False,
) -> FeedbackResponse:
    return FeedbackResponse(
        id=feedback.id,
        category=feedback.category,
        status=feedback.status,
        rating=feedback.rating,
        message=feedback.message,
        related_job_id=feedback.related_job_id,
        created_at=feedback.created_at,
        reused=reused,
    )


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_feedback(
    payload: CreateFeedbackRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> FeedbackResponse:
    guard: RateLimitGuard = request.app.state.rate_limit_guard
    await guard.enforce_costly_action(str(user.id))
    try:
        created = await _service(session).create(
            user_id=user.id,
            idempotency_key=idempotency_key,
            feedback_input=FeedbackInput(
                category=payload.category,
                rating=payload.rating,
                message=payload.message,
                related_job_id=payload.related_job_id,
                page=payload.page,
                app_version=payload.app_version,
                platform=payload.platform,
                system_version=payload.system_version,
                wechat_version=payload.wechat_version,
                network_type=payload.network_type,
                trace_id=payload.trace_id,
            ),
        )
    except FeedbackServiceError as error:
        _raise_service_error(error)
    response = _response(created.feedback, reused=created.reused)
    record_product_action(
        action="feedback_submitted",
        outcome="reused" if created.reused else "created",
    )
    return response


@router.get("/me/feedback", response_model=FeedbackPageResponse)
async def list_feedback(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> FeedbackPageResponse:
    try:
        page = await _service(session).list_owned(
            user_id=user.id,
            limit=limit,
            cursor=cursor,
        )
    except FeedbackServiceError as error:
        _raise_service_error(error)
    return FeedbackPageResponse(
        items=[_response(item) for item in page.items],
        next_cursor=page.next_cursor,
    )
