from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from httpx import AsyncClient
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.dependencies import get_http_client, get_session
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimitGuard
from app.core.telemetry import current_trace_fields
from app.modules.events.server import (
    ServerEventContext,
    ServerEventRecorder,
)
from app.modules.identity.models import User, UserProfile, UserStatus
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.security import (
    AccessTokenService,
    InvalidAccessTokenError,
    SubjectProtector,
)
from app.modules.identity.service import IdentityApplicationService
from app.modules.identity.wechat import WeChatLoginClient, WeChatLoginError

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


class WeChatLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=4, max_length=256)


class UserSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    display_name: str | None


class LoginResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "Bearer"  # noqa: S105
    expires_in: int
    is_new_user: bool
    user: UserSummary


class MeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    display_name: str | None
    consent_version: str | None
    has_ai_processing_consent: bool


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=40)
    consent_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    has_ai_processing_consent: bool | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("display_name cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_consent_update(self) -> "UpdateProfileRequest":
        if not self.model_fields_set:
            raise ValueError("at least one profile field is required")
        consent_requested = "has_ai_processing_consent" in self.model_fields_set
        version_requested = "consent_version" in self.model_fields_set
        if version_requested and not consent_requested:
            raise ValueError("consent_version requires an explicit consent decision")
        if self.has_ai_processing_consent and not self.consent_version:
            raise ValueError("consent_version is required when granting consent")
        return self


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def authenticated_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            code="AUTHENTICATION_REQUIRED",
            message="请先登录。",
            status_code=401,
        )

    try:
        user_id = AccessTokenService(_settings(request)).verify(credentials.credentials)
    except InvalidAccessTokenError as error:
        raise AppError(
            code="INVALID_ACCESS_TOKEN",
            message="登录状态已失效，请重新登录。",
            status_code=401,
        ) from error

    result = await session.execute(
        select(User).where(User.id == user_id).options(joinedload(User.profile))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError(
            code="USER_NOT_ACTIVE",
            message="当前账号不可用。",
            status_code=401,
        )
    rate_limit_guard: RateLimitGuard = request.app.state.rate_limit_guard
    await rate_limit_guard.enforce_user(str(user.id))
    return user


async def current_user(
    user: Annotated[User, Depends(authenticated_user)],
) -> User:
    if user.status != UserStatus.ACTIVE:
        raise AppError(
            code="USER_NOT_ACTIVE",
            message="当前账号不可用。",
            status_code=401,
        )
    return user


@router.post("/auth/wechat/login", response_model=LoginResponse)
async def login_with_wechat(
    payload: WeChatLoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    http_client: Annotated[AsyncClient, Depends(get_http_client)],
) -> LoginResponse:
    settings = _settings(request)
    service = IdentityApplicationService(
        repository=IdentityRepository(session),
        wechat_client=WeChatLoginClient(
            settings=settings,
            http_client=http_client,
        ),
        subject_protector=SubjectProtector(settings),
        token_service=AccessTokenService(settings),
    )

    try:
        result = await service.login_with_wechat_code(payload.code)
    except WeChatLoginError as error:
        if error.code == "WECHAT_CODE_INVALID":
            raise AppError(
                code="WECHAT_CODE_INVALID",
                message="登录凭证已失效，请重新登录。",
                status_code=401,
            ) from error
        raise AppError(
            code="WECHAT_LOGIN_UNAVAILABLE",
            message="微信登录暂时不可用，请稍后重试。",
            status_code=503,
        ) from error

    await ServerEventRecorder(
        session,
        settings,
        ServerEventContext(
            request_id=str(getattr(request.state, "request_id", "unavailable")),
            trace_id=current_trace_fields().get("trace_id", "unavailable"),
        ),
    ).record(
        subject_user_id=result.user_id,
        event_name="auth.wechat.succeeded",
        entity_type="User",
        entity_id=result.user_id,
        dedupe_key=f"login:{getattr(request.state, 'request_id', uuid4().hex)}",
        properties={"is_new_user": result.is_new_user},
    )
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in_seconds,
        is_new_user=result.is_new_user,
        user=UserSummary(
            id=result.user_id,
            display_name=result.display_name,
        ),
    )


def _me_response(user: User) -> MeResponse:
    profile = user.profile
    return MeResponse(
        id=user.id,
        display_name=profile.display_name if profile else None,
        consent_version=profile.consent_version if profile else None,
        has_ai_processing_consent=(profile.has_ai_processing_consent if profile else False),
    )


@router.get("/me", response_model=MeResponse)
async def get_me(
    user: Annotated[User, Depends(current_user)],
) -> MeResponse:
    return _me_response(user)


@router.patch("/me/profile", response_model=MeResponse)
@router.post("/me/profile", response_model=MeResponse, include_in_schema=False)
async def update_profile(
    payload: UpdateProfileRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    profile = user.profile
    if profile is None:
        profile = UserProfile(user=user)
        session.add(profile)

    previous_consent = profile.has_ai_processing_consent
    previous_consent_version = profile.consent_version
    if "display_name" in payload.model_fields_set:
        profile.display_name = payload.display_name
    if "has_ai_processing_consent" in payload.model_fields_set:
        profile.has_ai_processing_consent = bool(payload.has_ai_processing_consent)
        profile.consent_version = (
            payload.consent_version if profile.has_ai_processing_consent else None
        )
        consent_changed = previous_consent != profile.has_ai_processing_consent or (
            profile.has_ai_processing_consent
            and previous_consent_version != profile.consent_version
        )
        if consent_changed:
            granted = profile.has_ai_processing_consent
            await ServerEventRecorder(
                session,
                _settings(request),
                ServerEventContext(
                    request_id=str(getattr(request.state, "request_id", "unavailable")),
                    trace_id=current_trace_fields().get("trace_id", "unavailable"),
                ),
            ).record(
                subject_user_id=user.id,
                event_name="consent.ai.accepted" if granted else "consent.ai.revoked",
                entity_type="User",
                entity_id=user.id,
                dedupe_key=f"consent:{uuid4().hex}",
                properties={
                    "consent_version": (
                        profile.consent_version if granted else previous_consent_version
                    )
                },
            )

    await session.flush()
    return _me_response(user)
