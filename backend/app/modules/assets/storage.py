import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

from qcloud_cos import CosConfig, CosS3Client

from app.core.config import Settings


class ObjectStorageUnavailableError(Exception):
    pass


class ObjectNotFoundError(Exception):
    pass


class InvalidLocalStorageTokenError(Exception):
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


class LocalObjectStorage:
    bucket = "local-development"

    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.local_storage_root).expanduser().resolve()
        self._base_url = settings.local_storage_base_url.rstrip("/")
        self._signing_key = settings.secret_key.get_secret_value().encode("utf-8")

    async def check_health(self) -> object:
        def check() -> bool:
            self._root.mkdir(parents=True, exist_ok=True)
            descriptor, raw_path = tempfile.mkstemp(prefix=".health-", dir=self._root)
            os.close(descriptor)
            Path(raw_path).unlink(missing_ok=True)
            return True

        try:
            return await asyncio.to_thread(check)
        except OSError as error:
            raise ObjectStorageUnavailableError from error

    async def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_in_seconds: int,
    ) -> UploadAuthorization:
        self._object_path(object_key)
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
        token = self._encode_token(
            operation="upload",
            object_key=object_key,
            content_type=content_type,
            expires_at=expires_at,
        )
        return UploadAuthorization(
            url=f"{self._base_url}/api/v1/local-storage/uploads/{quote(token, safe='')}",
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=expires_at,
        )

    async def head_object(self, *, object_key: str) -> ObjectMetadata:
        path = self._object_path(object_key)

        def head() -> ObjectMetadata:
            try:
                stat = path.stat()
            except FileNotFoundError as error:
                raise ObjectNotFoundError from error
            if not path.is_file():
                raise ObjectNotFoundError
            return ObjectMetadata(
                size_bytes=stat.st_size,
                content_type=self._content_type(path),
                etag=self._sha256(path),
            )

        try:
            return await asyncio.to_thread(head)
        except ObjectNotFoundError:
            raise
        except OSError as error:
            raise ObjectStorageUnavailableError from error

    async def read_prefix(self, *, object_key: str, max_bytes: int) -> bytes:
        path = self._object_path(object_key)

        def read() -> bytes:
            try:
                with path.open("rb") as stream:
                    return stream.read(max_bytes)
            except FileNotFoundError as error:
                raise ObjectNotFoundError from error

        try:
            return await asyncio.to_thread(read)
        except ObjectNotFoundError:
            raise
        except OSError as error:
            raise ObjectStorageUnavailableError from error

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes:
        path = self._object_path(object_key)

        def read() -> bytes:
            try:
                if path.stat().st_size > max_bytes:
                    raise ObjectStorageUnavailableError("object exceeds safe read limit")
                return path.read_bytes()
            except FileNotFoundError as error:
                raise ObjectNotFoundError from error

        try:
            return await asyncio.to_thread(read)
        except (ObjectNotFoundError, ObjectStorageUnavailableError):
            raise
        except OSError as error:
            raise ObjectStorageUnavailableError from error

    async def put_object(
        self,
        *,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        path = self._object_path(object_key)
        expected_content_type = self._content_type(path)
        if expected_content_type != content_type:
            raise ObjectStorageUnavailableError("object content type does not match its key")
        try:
            await asyncio.to_thread(self._atomic_write, path, data)
        except OSError as error:
            raise ObjectStorageUnavailableError from error

    async def create_download_url(
        self,
        *,
        object_key: str,
        expires_in_seconds: int,
    ) -> str:
        self._object_path(object_key)
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
        token = self._encode_token(
            operation="download",
            object_key=object_key,
            content_type=None,
            expires_at=expires_at,
        )
        return f"{self._base_url}/api/v1/local-storage/objects/{quote(token, safe='')}"

    async def delete_object(self, *, object_key: str) -> None:
        path = self._object_path(object_key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError as error:
            raise ObjectStorageUnavailableError from error

    async def write_signed_upload(
        self,
        *,
        token: str,
        data: bytes,
        content_type: str,
    ) -> None:
        payload = self._decode_token(token, expected_operation="upload")
        expected_content_type = payload.get("content_type")
        if expected_content_type != content_type:
            raise InvalidLocalStorageTokenError
        object_key = payload.get("object_key")
        if not isinstance(object_key, str):
            raise InvalidLocalStorageTokenError
        await self.put_object(
            object_key=object_key,
            data=data,
            content_type=content_type,
        )

    async def read_signed_download(self, *, token: str, max_bytes: int) -> tuple[bytes, str]:
        payload = self._decode_token(token, expected_operation="download")
        object_key = payload.get("object_key")
        if not isinstance(object_key, str):
            raise InvalidLocalStorageTokenError
        path = self._object_path(object_key)
        data = await self.read_object(object_key=object_key, max_bytes=max_bytes)
        return data, self._content_type(path)

    def _object_path(self, object_key: str) -> Path:
        pure_path = PurePosixPath(object_key)
        raw_parts = object_key.split("/")
        if (
            not object_key
            or pure_path.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise ObjectStorageUnavailableError("invalid local object key")
        candidate = self._root.joinpath(*pure_path.parts).resolve()
        if not candidate.is_relative_to(self._root):
            raise ObjectStorageUnavailableError("invalid local object key")
        return candidate

    def _encode_token(
        self,
        *,
        operation: str,
        object_key: str,
        content_type: str | None,
        expires_at: datetime,
    ) -> str:
        payload = {
            "operation": operation,
            "object_key": object_key,
            "content_type": content_type,
            "expires_at": int(expires_at.timestamp()),
        }
        body = self._urlsafe_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = self._urlsafe_encode(
            hmac.new(self._signing_key, body.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{body}.{signature}"

    def _decode_token(self, token: str, *, expected_operation: str) -> dict[str, object]:
        try:
            body, signature = token.split(".", maxsplit=1)
            expected_signature = self._urlsafe_encode(
                hmac.new(self._signing_key, body.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected_signature):
                raise InvalidLocalStorageTokenError
            payload = json.loads(self._urlsafe_decode(body))
        except (
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
            InvalidLocalStorageTokenError,
        ) as error:
            raise InvalidLocalStorageTokenError from error
        if not isinstance(payload, dict) or payload.get("operation") != expected_operation:
            raise InvalidLocalStorageTokenError
        expires_at = payload.get("expires_at")
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= int(datetime.now(UTC).timestamp())
        ):
            raise InvalidLocalStorageTokenError
        return payload

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _content_type(path: Path) -> str:
        content_type, _ = mimetypes.guess_type(path.name)
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ObjectStorageUnavailableError("unsupported local object content type")
        return content_type

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _urlsafe_encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _urlsafe_decode(value: str) -> str:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding).decode("utf-8")


class TencentCosObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.cos_bucket
        runtime_config = CosConfig(
            Region=settings.cos_region,
            SecretId=settings.cos_secret_id.get_secret_value(),
            SecretKey=settings.cos_secret_key.get_secret_value(),
            Token=settings.cos_security_token.get_secret_value() or None,
            Scheme="https",
        )
        upload_config = CosConfig(
            Region=settings.cos_region,
            SecretId=settings.cos_upload_secret_id.get_secret_value(),
            SecretKey=settings.cos_upload_secret_key.get_secret_value(),
            Token=settings.cos_upload_security_token.get_secret_value() or None,
            Scheme="https",
        )
        self._client = CosS3Client(runtime_config)
        self._upload_client = CosS3Client(upload_config)

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
                value: str = self._upload_client.get_presigned_url(
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
    if settings.cos_enabled:
        return TencentCosObjectStorage(settings)
    if settings.local_storage_enabled:
        return LocalObjectStorage(settings)
    return DisabledObjectStorage()
