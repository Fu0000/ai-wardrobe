import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from qcloud_cos import CosConfig, CosS3Client

from app.core.config import Settings


class ObjectStorageUnavailableError(Exception):
    pass


class ObjectNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class UploadAuthorization:
    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    size_bytes: int
    content_type: str
    etag: str | None


class ObjectStorage(Protocol):
    @property
    def bucket(self) -> str: ...

    async def check_health(self) -> object: ...

    async def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_in_seconds: int,
    ) -> UploadAuthorization: ...

    async def head_object(self, *, object_key: str) -> ObjectMetadata: ...

    async def read_prefix(self, *, object_key: str, max_bytes: int) -> bytes: ...

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes: ...

    async def put_object(
        self,
        *,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None: ...

    async def create_download_url(
        self,
        *,
        object_key: str,
        expires_in_seconds: int,
    ) -> str: ...

    async def delete_object(self, *, object_key: str) -> None: ...


class DisabledObjectStorage:
    bucket = "disabled"

    async def check_health(self) -> object:
        raise ObjectStorageUnavailableError

    async def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_in_seconds: int,
    ) -> UploadAuthorization:
        raise ObjectStorageUnavailableError

    async def head_object(self, *, object_key: str) -> ObjectMetadata:
        raise ObjectStorageUnavailableError

    async def read_prefix(self, *, object_key: str, max_bytes: int) -> bytes:
        raise ObjectStorageUnavailableError

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes:
        raise ObjectStorageUnavailableError

    async def put_object(
        self,
        *,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        raise ObjectStorageUnavailableError

    async def create_download_url(
        self,
        *,
        object_key: str,
        expires_in_seconds: int,
    ) -> str:
        raise ObjectStorageUnavailableError

    async def delete_object(self, *, object_key: str) -> None:
        raise ObjectStorageUnavailableError


class TencentCosObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.cos_bucket
        config = CosConfig(
            Region=settings.cos_region,
            SecretId=settings.cos_secret_id.get_secret_value(),
            SecretKey=settings.cos_secret_key.get_secret_value(),
            Scheme="https",
        )
        self._client = CosS3Client(config)

    async def check_health(self) -> object:
        def head_bucket() -> object:
            try:
                return self._client.head_bucket(Bucket=self.bucket)
            except Exception as error:
                raise ObjectStorageUnavailableError from error

        return await asyncio.to_thread(head_bucket)

    async def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_in_seconds: int,
    ) -> UploadAuthorization:
        def sign() -> str:
            try:
                value: str = self._client.get_presigned_url(
                    Method="PUT",
                    Bucket=self.bucket,
                    Key=object_key,
                    Expired=expires_in_seconds,
                    Headers={"Content-Type": content_type},
                )
                return value
            except Exception as error:
                raise ObjectStorageUnavailableError from error

        url = await asyncio.to_thread(sign)
        return UploadAuthorization(
            url=url,
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        )

    async def head_object(self, *, object_key: str) -> ObjectMetadata:
        def head() -> ObjectMetadata:
            try:
                result = self._client.head_object(
                    Bucket=self.bucket,
                    Key=object_key,
                )
            except Exception as error:
                status_code = getattr(error, "get_status_code", lambda: None)()
                if status_code == 404:
                    raise ObjectNotFoundError from error
                raise ObjectStorageUnavailableError from error

            return ObjectMetadata(
                size_bytes=int(result["Content-Length"]),
                content_type=str(result.get("Content-Type", "")),
                etag=str(result["ETag"]).strip('"') if result.get("ETag") else None,
            )

        return await asyncio.to_thread(head)

    async def read_prefix(self, *, object_key: str, max_bytes: int) -> bytes:
        def read() -> bytes:
            try:
                result = self._client.get_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Range=f"bytes=0-{max_bytes - 1}",
                )
                raw_stream = result["Body"].get_raw_stream()
                value: bytes = raw_stream.read(max_bytes)
                return value
            except Exception as error:
                status_code = getattr(error, "get_status_code", lambda: None)()
                if status_code == 404:
                    raise ObjectNotFoundError from error
                raise ObjectStorageUnavailableError from error

        return await asyncio.to_thread(read)

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes:
        def read() -> bytes:
            try:
                result = self._client.get_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Range=f"bytes=0-{max_bytes}",
                )
                raw_stream = result["Body"].get_raw_stream()
                value: bytes = raw_stream.read(max_bytes + 1)
            except Exception as error:
                status_code = getattr(error, "get_status_code", lambda: None)()
                if status_code == 404:
                    raise ObjectNotFoundError from error
                raise ObjectStorageUnavailableError from error
            if len(value) > max_bytes:
                raise ObjectStorageUnavailableError("object exceeds safe read limit")
            return value

        return await asyncio.to_thread(read)

    async def put_object(
        self,
        *,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        def put() -> None:
            try:
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Body=data,
                    ContentType=content_type,
                )
            except Exception as error:
                raise ObjectStorageUnavailableError from error

        await asyncio.to_thread(put)

    async def create_download_url(
        self,
        *,
        object_key: str,
        expires_in_seconds: int,
    ) -> str:
        def sign() -> str:
            try:
                value: str = self._client.get_presigned_url(
                    Method="GET",
                    Bucket=self.bucket,
                    Key=object_key,
                    Expired=expires_in_seconds,
                )
                return value
            except Exception as error:
                raise ObjectStorageUnavailableError from error

        return await asyncio.to_thread(sign)

    async def delete_object(self, *, object_key: str) -> None:
        def delete() -> None:
            try:
                self._client.delete_object(Bucket=self.bucket, Key=object_key)
            except Exception as error:
                raise ObjectStorageUnavailableError from error

        await asyncio.to_thread(delete)


def build_object_storage(settings: Settings) -> ObjectStorage:
    if not settings.cos_enabled:
        return DisabledObjectStorage()
    return TencentCosObjectStorage(settings)
