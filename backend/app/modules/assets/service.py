from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.modules.assets.models import AssetStatus, UserAsset
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import (
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageUnavailableError,
)
from app.modules.assets.validation import (
    CONTENT_TYPE_EXTENSIONS,
    InvalidImageError,
    validate_image_prefix,
    validate_upload_request,
)


class AssetServiceError(Exception):
    def __init__(
        self,
        *,
        code: str,
        retryable: bool,
        commit_state: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.commit_state = commit_state


@dataclass(frozen=True, slots=True)
class UploadTicket:
    asset_id: UUID
    upload_url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CompletedAsset:
    asset_id: UUID
    status: AssetStatus
    width: int
    height: int
    content_type: str


@dataclass(frozen=True, slots=True)
class AssetAccess:
    asset_id: UUID
    url: str
    expires_at: datetime


class AssetApplicationService:
    def __init__(
        self,
        *,
        repository: AssetRepository,
        object_storage: ObjectStorage,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._settings = settings

    async def create_upload_ticket(
        self,
        *,
        user_id: UUID,
        content_type: str,
        size_bytes: int,
    ) -> UploadTicket:
        try:
            normalized_type = validate_upload_request(
                content_type=content_type,
                size_bytes=size_bytes,
                max_upload_bytes=self._settings.max_upload_bytes,
            )
        except InvalidImageError as error:
            raise AssetServiceError(code=error.code, retryable=False) from error

        asset_id = uuid4()
        extension = CONTENT_TYPE_EXTENSIONS[normalized_type]
        object_key = f"private/{user_id}/uploads/{asset_id}.{extension}"

        await self._repository.create_upload(
            asset_id=asset_id,
            user_id=user_id,
            bucket=self._object_storage.bucket,
            object_key=object_key,
        )
        try:
            authorization = await self._object_storage.create_upload_authorization(
                object_key=object_key,
                content_type=normalized_type,
                expires_in_seconds=self._settings.cos_upload_ticket_ttl_seconds,
            )
        except ObjectStorageUnavailableError as error:
            raise AssetServiceError(
                code="ASSET_STORAGE_UNAVAILABLE",
                retryable=True,
            ) from error

        return UploadTicket(
            asset_id=asset_id,
            upload_url=authorization.url,
            method=authorization.method,
            headers=authorization.headers,
            expires_at=authorization.expires_at,
        )

    async def complete_upload(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
        declared_content_type: str,
    ) -> CompletedAsset:
        asset = await self._repository.get_owned(
            asset_id=asset_id,
            user_id=user_id,
        )
        if asset is None or asset.user_id != user_id:
            raise AssetServiceError(code="ASSET_NOT_FOUND", retryable=False)
        if asset.status == AssetStatus.READY:
            return self._completed(asset)
        if asset.status != AssetStatus.UPLOADING:
            raise AssetServiceError(code="ASSET_NOT_COMPLETABLE", retryable=False)

        try:
            metadata = await self._object_storage.head_object(object_key=asset.object_key)
            normalized_type = validate_upload_request(
                content_type=declared_content_type,
                size_bytes=metadata.size_bytes,
                max_upload_bytes=self._settings.max_upload_bytes,
            )
            if metadata.content_type.lower().split(";", maxsplit=1)[0] != normalized_type:
                raise InvalidImageError("IMAGE_TYPE_MISMATCH")
            prefix = await self._object_storage.read_prefix(
                object_key=asset.object_key,
                max_bytes=self._settings.image_header_read_bytes,
            )
            validated = validate_image_prefix(
                data=prefix,
                declared_content_type=normalized_type,
                max_dimension=self._settings.max_image_dimension,
                max_pixels=self._settings.max_image_pixels,
            )
        except ObjectNotFoundError as error:
            raise AssetServiceError(code="ASSET_UPLOAD_INCOMPLETE", retryable=True) from error
        except ObjectStorageUnavailableError as error:
            raise AssetServiceError(
                code="ASSET_STORAGE_UNAVAILABLE",
                retryable=True,
            ) from error
        except InvalidImageError as error:
            marked_failed = await self._repository.fail_if_uploading(
                asset_id=asset.id,
                user_id=user_id,
            )
            raise AssetServiceError(
                code=error.code,
                retryable=False,
                commit_state=marked_failed,
            ) from error

        completed = await self._repository.complete_if_uploading(
            asset_id=asset.id,
            user_id=user_id,
            content_type=validated.content_type,
            size_bytes=metadata.size_bytes,
            width=validated.width,
            height=validated.height,
            checksum_sha256=(metadata.etag if metadata.etag and len(metadata.etag) == 64 else None),
        )
        if completed is None:
            raise AssetServiceError(code="ASSET_NOT_COMPLETABLE", retryable=False)
        return self._completed(completed)

    async def create_access_url(
        self,
        *,
        user_id: UUID,
        asset_id: UUID,
    ) -> AssetAccess:
        asset = await self._repository.get_owned(
            asset_id=asset_id,
            user_id=user_id,
        )
        if asset is None or asset.user_id != user_id:
            raise AssetServiceError(code="ASSET_NOT_FOUND", retryable=False)
        if asset.status != AssetStatus.READY:
            raise AssetServiceError(code="ASSET_NOT_READY", retryable=False)

        try:
            url = await self._object_storage.create_download_url(
                object_key=asset.object_key,
                expires_in_seconds=self._settings.cos_download_url_ttl_seconds,
            )
        except ObjectStorageUnavailableError as error:
            raise AssetServiceError(
                code="ASSET_STORAGE_UNAVAILABLE",
                retryable=True,
            ) from error

        return AssetAccess(
            asset_id=asset.id,
            url=url,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self._settings.cos_download_url_ttl_seconds),
        )

    @staticmethod
    def _completed(asset: UserAsset) -> CompletedAsset:
        if asset.width is None or asset.height is None or asset.content_type is None:
            raise AssetServiceError(code="ASSET_METADATA_INCOMPLETE", retryable=True)
        return CompletedAsset(
            asset_id=asset.id,
            status=asset.status,
            width=asset.width,
            height=asset.height,
            content_type=asset.content_type,
        )
