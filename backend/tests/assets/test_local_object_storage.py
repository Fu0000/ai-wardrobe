from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app
from app.modules.assets.storage import (
    InvalidLocalStorageTokenError,
    LocalObjectStorage,
    ObjectNotFoundError,
    ObjectStorageUnavailableError,
    build_object_storage,
)


def local_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "local_storage_enabled": True,
        "local_storage_root": str(tmp_path / "objects"),
        "local_storage_base_url": "http://test",
        "secret_key": "test-local-storage-signing-key",
    }
    values.update(overrides)
    return Settings.model_validate(values)


async def test_local_storage_round_trip_and_delete(tmp_path: Path) -> None:
    storage = LocalObjectStorage(local_settings(tmp_path))
    object_key = "private/user-1/uploads/photo.jpg"
    image_data = b"local-image-data"

    authorization = await storage.create_upload_authorization(
        object_key=object_key,
        content_type="image/jpeg",
        expires_in_seconds=60,
    )
    upload_token = urlsplit(authorization.url).path.rsplit("/", maxsplit=1)[-1]
    await storage.write_signed_upload(
        token=upload_token,
        data=image_data,
        content_type="image/jpeg",
    )

    metadata = await storage.head_object(object_key=object_key)
    assert metadata.size_bytes == len(image_data)
    assert metadata.content_type == "image/jpeg"
    assert len(metadata.etag or "") == 64
    assert await storage.read_prefix(object_key=object_key, max_bytes=5) == b"local"
    assert await storage.read_object(object_key=object_key, max_bytes=100) == image_data

    download_url = await storage.create_download_url(
        object_key=object_key,
        expires_in_seconds=60,
    )
    download_token = urlsplit(download_url).path.rsplit("/", maxsplit=1)[-1]
    downloaded, content_type = await storage.read_signed_download(
        token=download_token,
        max_bytes=100,
    )
    assert downloaded == image_data
    assert content_type == "image/jpeg"

    await storage.delete_object(object_key=object_key)
    with pytest.raises(ObjectNotFoundError):
        await storage.head_object(object_key=object_key)


async def test_local_storage_rejects_tampered_expired_and_wrong_type_tokens(
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(local_settings(tmp_path))
    authorization = await storage.create_upload_authorization(
        object_key="private/user-1/uploads/photo.jpg",
        content_type="image/jpeg",
        expires_in_seconds=60,
    )
    token = urlsplit(authorization.url).path.rsplit("/", maxsplit=1)[-1]

    with pytest.raises(InvalidLocalStorageTokenError):
        await storage.write_signed_upload(
            token=f"{token}tampered",
            data=b"image",
            content_type="image/jpeg",
        )
    with pytest.raises(InvalidLocalStorageTokenError):
        await storage.write_signed_upload(
            token=token,
            data=b"image",
            content_type="image/png",
        )

    expired_token = storage._encode_token(
        operation="upload",
        object_key="private/user-1/uploads/photo.jpg",
        content_type="image/jpeg",
        expires_at=datetime.now(UTC),
    )
    with pytest.raises(InvalidLocalStorageTokenError):
        await storage.write_signed_upload(
            token=expired_token,
            data=b"image",
            content_type="image/jpeg",
        )


@pytest.mark.parametrize(
    "object_key",
    [
        "",
        "/absolute/photo.jpg",
        "../outside.jpg",
        "private/../../outside.jpg",
        "./photo.jpg",
    ],
)
async def test_local_storage_rejects_unsafe_object_keys(
    tmp_path: Path,
    object_key: str,
) -> None:
    storage = LocalObjectStorage(local_settings(tmp_path))

    with pytest.raises(ObjectStorageUnavailableError):
        await storage.put_object(
            object_key=object_key,
            data=b"image",
            content_type="image/jpeg",
        )


async def test_local_storage_signed_http_endpoints(tmp_path: Path) -> None:
    settings = local_settings(tmp_path)
    storage = LocalObjectStorage(settings)
    app = create_app(settings)
    app.state.object_storage = storage
    authorization = await storage.create_upload_authorization(
        object_key="private/user-1/uploads/photo.png",
        content_type="image/png",
        expires_in_seconds=60,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        upload_response = await client.put(
            authorization.url,
            content=b"png-image-data",
            headers=authorization.headers,
        )
        assert upload_response.status_code == 204

        download_url = await storage.create_download_url(
            object_key="private/user-1/uploads/photo.png",
            expires_in_seconds=60,
        )
        download_response = await client.get(download_url)
        assert download_response.status_code == 200
        assert download_response.content == b"png-image-data"
        assert download_response.headers["content-type"] == "image/png"
        assert download_response.headers["cache-control"] == "private, no-store"


async def test_local_storage_http_endpoint_hides_invalid_tokens(tmp_path: Path) -> None:
    settings = local_settings(tmp_path)
    app = create_app(settings)
    app.state.object_storage = LocalObjectStorage(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.put(
            "/api/v1/local-storage/uploads/not-a-token",
            content=b"image",
            headers={"Content-Type": "image/jpeg"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LOCAL_STORAGE_UPLOAD_NOT_FOUND"


def test_object_storage_builder_selects_local_storage(tmp_path: Path) -> None:
    storage = build_object_storage(local_settings(tmp_path))

    assert isinstance(storage, LocalObjectStorage)
    assert storage.bucket == "local-development"
