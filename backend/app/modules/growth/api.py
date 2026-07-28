from datetime import datetime
from typing import Annotated, Literal, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimitGuard
from app.core.telemetry import current_trace_fields, record_product_action
from app.modules.assets.models import AssetKind, AssetStatus
from app.modules.assets.storage import ObjectStorageUnavailableError
from app.modules.events.server import ServerEventContext
from app.modules.growth.models import ShareStatus, VoteChoice
from app.modules.growth.repository import GrowthRepository, ShareView, VoteTally
from app.modules.growth.service import (
    CreatedShare,
    GrowthApplicationService,
    GrowthServiceError,
    ShareDetails,
)
from app.modules.identity.api import current_user
from app.modules.identity.models import User
from app.modules.jobs.models import JobStatus
from app.modules.jobs.repository import JobRepository
from app.modules.optimization.repository import OptimizationRepository

router = APIRouter()

AttributionSource = Literal[
    "WECHAT_FRIEND",
    "WECHAT_TIMELINE",
    "PREVIEW",
]
WechatAttributionSource = Literal[
    "WECHAT_FRIEND",
    "WECHAT_TIMELINE",
]
SceneCodePath = Annotated[
    str,
    Path(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
]


class CreateShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    optimization_id: UUID
    display_score: bool = False
    attribution_source: AttributionSource = "WECHAT_FRIEND"


class PublicChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=1, le=3)
    instruction: str = Field(min_length=4, max_length=140)
    reason: str = Field(min_length=6, max_length=180)


class PublicSharePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_level: int = Field(ge=1, le=3)
    changes: list[PublicChange] = Field(min_length=1, max_length=3)
    score: int | None = Field(default=None, ge=0, le=100)
    ai_edited: bool
    template_version: str


class VoteCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    before: int
    after: int


class ShareResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    scene_code: str
    job_id: UUID | None
    status: ShareStatus
    job_status: JobStatus | None
    card_url: str | None
    change_level: int
    changes: list[PublicChange]
    score: int | None
    ai_edited: bool
    votes: VoteCounts
    viewer_choice: VoteChoice | None
    expires_at: datetime | None
    error_code: str | None
    user_message: str | None
    reused: bool = False


class CreateVoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_code: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    choice: VoteChoice


class VoteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_code: str
    choice: VoteChoice
    votes: VoteCounts
    reused: bool


class ContinueResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    attributed: bool


class RecordShareInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attribution_source: WechatAttributionSource


class ShareInvocationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    recorded: bool


def _event_context(request: Request) -> ServerEventContext:
    return ServerEventContext(
        request_id=str(getattr(request.state, "request_id", "unavailable")),
        trace_id=current_trace_fields().get("trace_id", "unavailable"),
        app_channel="api",
    )


def _service(
    session: AsyncSession,
    settings: Settings,
    event_context: ServerEventContext,
) -> GrowthApplicationService:
    return GrowthApplicationService(
        growth_repository=GrowthRepository(
            session,
            settings=settings,
            event_context=event_context,
        ),
        optimization_repository=OptimizationRepository(session),
        job_repository=JobRepository(session),
        settings=settings,
    )


def _raise_service_error(error: GrowthServiceError) -> Never:
    status_by_code = {
        "OPTIMIZATION_NOT_SHAREABLE": 409,
        "SHARE_NOT_FOUND": 404,
        "SHARE_EXPIRED": 410,
        "SHARE_NOT_ACTIVE": 409,
        "IDEMPOTENCY_KEY_REUSED": 409,
        "INVALID_IDEMPOTENCY_KEY": 400,
    }
    message_by_code = {
        "OPTIMIZATION_NOT_SHAREABLE": "只有通过一致性检查的优化结果可以分享。",
        "SHARE_NOT_FOUND": "这份分享不存在。",
        "SHARE_EXPIRED": "这份分享已经失效。",
        "SHARE_NOT_ACTIVE": "分享卡片仍在生成，请稍后再试。",
        "IDEMPOTENCY_KEY_REUSED": "该请求标识已用于其他分享，请重新提交。",
        "INVALID_IDEMPOTENCY_KEY": "请求标识无效。",
    }
    raise AppError(
        code=error.code,
        message=message_by_code.get(error.code, "分享服务暂时不可用，请稍后重试。"),
        status_code=status_by_code.get(error.code, 409),
    ) from error


async def _response(
    view: ShareView,
    *,
    request: Request,
    viewer_user_id: UUID,
    tally: VoteTally,
    viewer_choice: VoteChoice | None,
    reused: bool = False,
) -> ShareResponse:
    try:
        payload = PublicSharePayload.model_validate(view.share.public_payload)
    except ValidationError as error:
        raise AppError(
            code="SHARE_PAYLOAD_INVALID",
            message="分享卡片暂时无法读取，请稍后重试。",
            status_code=503,
        ) from error

    card_url: str | None = None
    asset = view.asset
    is_owner = viewer_user_id == view.share.user_id
    if view.share.status == ShareStatus.ACTIVE:
        if (
            asset is None
            or asset.kind != AssetKind.SHARE_DERIVATIVE
            or asset.status != AssetStatus.READY
        ):
            raise AppError(
                code="SHARE_ASSET_UNAVAILABLE",
                message="分享卡片暂时无法读取，请稍后重试。",
                status_code=503,
            )
        try:
            card_url = await request.app.state.object_storage.create_download_url(
                object_key=asset.object_key,
                expires_in_seconds=request.app.state.settings.cos_download_url_ttl_seconds,
            )
        except ObjectStorageUnavailableError as error:
            raise AppError(
                code="SHARE_ASSET_UNAVAILABLE",
                message="分享卡片暂时无法读取，请稍后重试。",
                status_code=503,
            ) from error
    return ShareResponse(
        id=view.share.id,
        scene_code=view.share.scene_code,
        job_id=view.share.job_id if is_owner else None,
        status=view.share.status,
        job_status=view.job.status if view.job and is_owner else None,
        card_url=card_url,
        change_level=payload.change_level,
        changes=payload.changes,
        score=payload.score,
        ai_edited=payload.ai_edited,
        votes=VoteCounts(before=tally.before, after=tally.after),
        viewer_choice=viewer_choice,
        expires_at=view.share.expires_at,
        error_code=view.job.error_code if view.job and is_owner else None,
        user_message=view.job.user_message if view.job and is_owner else None,
        reused=reused,
    )


@router.post(
    "/shares",
    response_model=ShareResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_share(
    payload: CreateShareRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> ShareResponse:
    guard: RateLimitGuard = request.app.state.rate_limit_guard
    await guard.enforce_costly_action(str(user.id))
    settings: Settings = request.app.state.settings
    service = _service(session, settings, _event_context(request))
    try:
        created: CreatedShare = await service.create_share(
            user_id=user.id,
            optimization_id=payload.optimization_id,
            idempotency_key=idempotency_key,
            display_score=payload.display_score,
            attribution_source=payload.attribution_source,
        )
        details = await service.get_share(
            scene_code=created.share.scene_code,
            viewer_user_id=user.id,
        )
    except GrowthServiceError as error:
        _raise_service_error(error)
    response = await _response(
        details.view,
        request=request,
        viewer_user_id=user.id,
        tally=details.tally,
        viewer_choice=details.viewer_choice,
        reused=created.reused,
    )
    record_product_action(
        action="share_requested",
        outcome="reused" if created.reused else "created",
    )
    return response


@router.get("/shares/{scene_code}", response_model=ShareResponse)
async def get_share(
    scene_code: SceneCodePath,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    attribution_source: Annotated[WechatAttributionSource | None, Query()] = None,
) -> ShareResponse:
    try:
        details: ShareDetails = await _service(
            session,
            request.app.state.settings,
            _event_context(request),
        ).get_share(
            scene_code=scene_code,
            viewer_user_id=user.id,
            attribution_source=attribution_source,
        )
    except GrowthServiceError as error:
        _raise_service_error(error)
    return await _response(
        details.view,
        request=request,
        viewer_user_id=user.id,
        tally=details.tally,
        viewer_choice=details.viewer_choice,
    )


@router.post("/votes", response_model=VoteResponse)
async def create_vote(
    payload: CreateVoteRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VoteResponse:
    try:
        result = await _service(
            session,
            request.app.state.settings,
            _event_context(request),
        ).vote(
            scene_code=payload.scene_code,
            user_id=user.id,
            choice=payload.choice,
        )
    except GrowthServiceError as error:
        _raise_service_error(error)
    response = VoteResponse(
        scene_code=result.share.scene_code,
        choice=result.choice,
        votes=VoteCounts(
            before=result.tally.before,
            after=result.tally.after,
        ),
        reused=result.reused,
    )
    record_product_action(
        action="vote_recorded",
        outcome="reused" if result.reused else "created",
    )
    return response


@router.get("/votes/{scene_code}/result", response_model=ShareResponse)
async def get_vote_result(
    scene_code: SceneCodePath,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShareResponse:
    return await get_share(scene_code, request, user, session, None)


@router.post(
    "/shares/{scene_code}/invocations",
    response_model=ShareInvocationResponse,
)
async def record_share_invocation(
    scene_code: SceneCodePath,
    payload: RecordShareInvocationRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShareInvocationResponse:
    try:
        recorded = await _service(
            session,
            request.app.state.settings,
            _event_context(request),
        ).record_share_invocation(
            scene_code=scene_code,
            user_id=user.id,
            attribution_source=payload.attribution_source,
        )
    except GrowthServiceError as error:
        _raise_service_error(error)
    record_product_action(
        action="share_invocation_recorded",
        outcome="created" if recorded else "reused",
    )
    return ShareInvocationResponse(recorded=recorded)


@router.post(
    "/shares/{scene_code}/continue",
    response_model=ContinueResponse,
)
async def record_continue(
    scene_code: SceneCodePath,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContinueResponse:
    try:
        attributed = await _service(
            session,
            request.app.state.settings,
            _event_context(request),
        ).record_continue(
            scene_code=scene_code,
            user_id=user.id,
        )
    except GrowthServiceError as error:
        _raise_service_error(error)
    record_product_action(
        action="continue_recorded",
        outcome="attributed" if attributed else "unattributed",
    )
    return ContinueResponse(attributed=attributed)
