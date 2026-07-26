from datetime import datetime
from typing import Annotated, Literal, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.assets.repository import AssetRepository
from app.modules.assets.service import AssetApplicationService, AssetServiceError
from app.modules.assets.storage import ObjectStorage
from app.modules.identity.api import current_user
from app.modules.identity.models import User

router = APIRouter()


class UploadTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(min_length=3, max_length=120)
    size_bytes: int = Field(gt=0)


class UploadTicketResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: UUID
    upload_url: str
    method: Literal["PUT"]
    headers: dict[str, str]
    expires_at: datetime


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(min_length=3, max_length=120)


class AssetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    content_type: str
    width: int
    height: int


class AssetAccessResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: UUID
    url: str
    expires_at: datetime


def _service(
    request: Request,
    session: AsyncSession,
) -> AssetApplicationService:
    settings: Settings = request.app.state.settings
    object_storage: ObjectStorage = request.app.state.object_storage
    return AssetApplicationService(
        repository=AssetRepository(session),
        object_storage=object_storage,
        settings=settings,
    )


def _raise_asset_error(error: AssetServiceError) -> Never:
    status_by_code = {
        "ASSET_NOT_FOUND": 404,
        "ASSET_NOT_READY": 409,
        "ASSET_UPLOAD_INCOMPLETE": 409,
        "ASSET_STORAGE_UNAVAILABLE": 503,
        "IMAGE_TOO_LARGE": 413,
        "UNSUPPORTED_IMAGE_TYPE": 415,
    }
    user_message_by_code = {
        "ASSET_UPLOAD_INCOMPLETE": "图片还没有上传完成，请稍后重试。",
        "ASSET_NOT_READY": "图片还没有准备好。",
        "ASSET_STORAGE_UNAVAILABLE": "图片服务暂时不可用，请稍后重试。",
        "IMAGE_TOO_LARGE": "图片大小不能超过 20MB。",
        "UNSUPPORTED_IMAGE_TYPE": "请选择 JPEG、PNG 或 WebP 图片。",
        "IMAGE_TYPE_MISMATCH": "图片格式与文件类型不一致。",
        "INVALID_IMAGE_CONTENT": "这不是有效的图片文件。",
        "IMAGE_DIMENSIONS_TOO_LARGE": "图片尺寸过大，请压缩后重试。",
    }
    raise AppError(
        code=error.code,
        message=user_message_by_code.get(error.code, "图片无法处理，请重新选择。"),
        status_code=status_by_code.get(error.code, 422),
    ) from error


@router.post("/assets/upload-ticket", response_model=UploadTicketResponse)
async def create_upload_ticket(
    payload: UploadTicketRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UploadTicketResponse:
    try:
        ticket = await _service(request, session).create_upload_ticket(
            user_id=user.id,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
        )
    except AssetServiceError as error:
        _raise_asset_error(error)
    return UploadTicketResponse(
        asset_id=ticket.asset_id,
        upload_url=ticket.upload_url,
        method="PUT",
        headers=ticket.headers,
        expires_at=ticket.expires_at,
    )


@router.post("/assets/{asset_id}/complete", response_model=AssetResponse)
async def complete_upload(
    asset_id: UUID,
    payload: CompleteUploadRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AssetResponse:
    try:
        asset = await _service(request, session).complete_upload(
            user_id=user.id,
            asset_id=asset_id,
            declared_content_type=payload.content_type,
        )
    except AssetServiceError as error:
        if error.commit_state:
            await session.commit()
        _raise_asset_error(error)
    return AssetResponse(
        id=asset.asset_id,
        status=asset.status,
        content_type=asset.content_type,
        width=asset.width,
        height=asset.height,
    )


@router.get(
    "/assets/{asset_id}/access-url",
    response_model=AssetAccessResponse,
)
async def create_asset_access_url(
    asset_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AssetAccessResponse:
    try:
        access = await _service(request, session).create_access_url(
            user_id=user.id,
            asset_id=asset_id,
        )
    except AssetServiceError as error:
        _raise_asset_error(error)
    return AssetAccessResponse(
        asset_id=access.asset_id,
        url=access.url,
        expires_at=access.expires_at,
    )
