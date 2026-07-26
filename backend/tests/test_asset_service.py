from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.modules.assets import api as asset_api
from app.modules.assets.api import CompleteUploadRequest
from app.modules.assets.models import AssetKind, AssetStatus, UserAsset
from app.modules.assets.service import AssetApplicationService, AssetServiceError
from app.modules.assets.storage import (
    ObjectMetadata,
    UploadAuthorization,
)
from app.modules.identity.models import User


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (640, 960), color=(100, 120, 140)).save(
        buffer,
        format="PNG",
    )
    return buffer.getvalue()


class FakeAssetRepository:
    def __init__(self) -> None:
        self.asset: UserAsset | None = None
        self.flush_count = 0

    async def create_upload(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
        bucket: str,
        object_key: str,
    ) -> UserAsset:
        self.asset = UserAsset(
            id=asset_id,
            user_id=user_id,
            kind=AssetKind.USER_UPLOAD,
            status=AssetStatus.UPLOADING,
            bucket=bucket,
            object_key=object_key,
        )
        return self.asset

    async def get_owned(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
    ) -> UserAsset | None:
        if self.asset is not None and self.asset.id == asset_id and self.asset.user_id == user_id:
            return self.asset
        return None

    async def flush(self) -> None:
        self.flush_count += 1


class FakeObjectStorage:
    bucket = "wardrobe-test"

    def __init__(self, image_data: bytes | None = None) -> None:
        self.image_data = image_data or _png_bytes()
        self.authorization_object_key: str | None = None
        self.head_count = 0
        self.download_count = 0

    async def check_health(self) -> object:
        return {"status": "ok"}

    async def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_in_seconds: int,
    ) -> UploadAuthorization:
        self.authorization_object_key = object_key
        return UploadAuthorization(
            url=f"https://storage.example/{object_key}",
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        )

    async def head_object(self, *, object_key: str) -> ObjectMetadata:
        self.head_count += 1
        return ObjectMetadata(
            size_bytes=len(self.image_data),
            content_type="image/png",
            etag=None,
        )

    async def read_prefix(self, *, object_key: str, max_bytes: int) -> bytes:
        return self.image_data[:max_bytes]

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes:
        return self.image_data[:max_bytes]

    async def put_object(
        self,
        *,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        self.image_data = data

    async def create_download_url(
        self,
        *,
        object_key: str,
        expires_in_seconds: int,
    ) -> str:
        self.download_count += 1
        return f"https://storage.example/{object_key}?download=1"

    async def delete_object(self, *, object_key: str) -> None:
        return None


@pytest.mark.asyncio
async def test_upload_ticket_scopes_object_key_to_user() -> None:
    user_id = uuid4()
    repository = FakeAssetRepository()
    storage = FakeObjectStorage()
    service = AssetApplicationService(
        repository=repository,  # type: ignore[arg-type]
        object_storage=storage,
        settings=Settings(),
    )

    ticket = await service.create_upload_ticket(
        user_id=user_id,
        content_type="image/png",
        size_bytes=10_000,
    )

    assert storage.authorization_object_key is not None
    assert storage.authorization_object_key.startswith(f"private/{user_id}/uploads/")
    assert storage.authorization_object_key.endswith(f"{ticket.asset_id}.png")


@pytest.mark.asyncio
async def test_complete_upload_validates_and_marks_asset_ready() -> None:
    user_id = uuid4()
    repository = FakeAssetRepository()
    storage = FakeObjectStorage()
    service = AssetApplicationService(
        repository=repository,  # type: ignore[arg-type]
        object_storage=storage,
        settings=Settings(),
    )
    ticket = await service.create_upload_ticket(
        user_id=user_id,
        content_type="image/png",
        size_bytes=10_000,
    )

    completed = await service.complete_upload(
        user_id=user_id,
        asset_id=ticket.asset_id,
        declared_content_type="image/png",
    )

    assert completed.status == AssetStatus.READY
    assert (completed.width, completed.height) == (640, 960)
    assert repository.asset is not None
    assert repository.asset.status == AssetStatus.READY


@pytest.mark.asyncio
async def test_invalid_content_marks_asset_failed_and_requires_commit() -> None:
    user_id = uuid4()
    repository = FakeAssetRepository()
    storage = FakeObjectStorage(image_data=b"not an image")
    service = AssetApplicationService(
        repository=repository,  # type: ignore[arg-type]
        object_storage=storage,
        settings=Settings(),
    )
    ticket = await service.create_upload_ticket(
        user_id=user_id,
        content_type="image/png",
        size_bytes=10_000,
    )

    with pytest.raises(AssetServiceError) as captured:
        await service.complete_upload(
            user_id=user_id,
            asset_id=ticket.asset_id,
            declared_content_type="image/png",
        )

    assert captured.value.code == "INVALID_IMAGE_CONTENT"
    assert captured.value.commit_state is True
    assert repository.asset is not None
    assert repository.asset.status == AssetStatus.FAILED


@pytest.mark.asyncio
async def test_complete_upload_does_not_reveal_another_users_asset() -> None:
    owner_id = uuid4()
    repository = FakeAssetRepository()
    storage = FakeObjectStorage()
    service = AssetApplicationService(
        repository=repository,  # type: ignore[arg-type]
        object_storage=storage,
        settings=Settings(),
    )
    ticket = await service.create_upload_ticket(
        user_id=owner_id,
        content_type="image/png",
        size_bytes=10_000,
    )

    with pytest.raises(AssetServiceError, match="ASSET_NOT_FOUND"):
        await service.complete_upload(
            user_id=uuid4(),
            asset_id=ticket.asset_id,
            declared_content_type="image/png",
        )

    assert storage.head_count == 0


@pytest.mark.asyncio
async def test_access_url_requires_ready_owned_asset() -> None:
    owner_id = uuid4()
    repository = FakeAssetRepository()
    storage = FakeObjectStorage()
    service = AssetApplicationService(
        repository=repository,  # type: ignore[arg-type]
        object_storage=storage,
        settings=Settings(),
    )
    ticket = await service.create_upload_ticket(
        user_id=owner_id,
        content_type="image/png",
        size_bytes=10_000,
    )

    with pytest.raises(AssetServiceError, match="ASSET_NOT_READY"):
        await service.create_access_url(
            user_id=owner_id,
            asset_id=ticket.asset_id,
        )
    with pytest.raises(AssetServiceError, match="ASSET_NOT_FOUND"):
        await service.create_access_url(
            user_id=uuid4(),
            asset_id=ticket.asset_id,
        )

    await service.complete_upload(
        user_id=owner_id,
        asset_id=ticket.asset_id,
        declared_content_type="image/png",
    )
    access = await service.create_access_url(
        user_id=owner_id,
        asset_id=ticket.asset_id,
    )

    assert access.asset_id == ticket.asset_id
    assert access.url.endswith("?download=1")
    assert storage.download_count == 1


@pytest.mark.asyncio
async def test_api_commits_failed_validation_state_before_returning_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingService:
        async def complete_upload(
            self,
            *,
            user_id: UUID,
            asset_id: UUID,
            declared_content_type: str,
        ) -> None:
            raise AssetServiceError(
                code="INVALID_IMAGE_CONTENT",
                retryable=False,
                commit_state=True,
            )

    monkeypatch.setattr(asset_api, "_service", lambda request, session: FailingService())
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    user = MagicMock(spec=User)
    user.id = uuid4()

    with pytest.raises(AppError) as captured:
        await asset_api.complete_upload(
            asset_id=uuid4(),
            payload=CompleteUploadRequest(content_type="image/png"),
            request=MagicMock(spec=Request),
            user=user,
            session=session,
        )

    assert captured.value.code == "INVALID_IMAGE_CONTENT"
    session.commit.assert_awaited_once()
