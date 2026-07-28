from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

from app.core.config import Settings
from app.modules.assets.storage import (
    ObjectStorageUnavailableError,
    TencentCosObjectStorage,
)


def storage_with_client(client: MagicMock) -> TencentCosObjectStorage:
    storage = object.__new__(TencentCosObjectStorage)
    storage.bucket = "private-bucket"
    storage._client = client
    storage._upload_client = client
    return storage


@patch("app.modules.assets.storage.CosS3Client")
@patch("app.modules.assets.storage.CosConfig")
def test_cos_builds_distinct_runtime_and_upload_clients(
    config_factory: MagicMock,
    client_factory: MagicMock,
) -> None:
    runtime_config = object()
    upload_config = object()
    config_factory.side_effect = [runtime_config, upload_config]
    runtime_client = object()
    upload_client = object()
    client_factory.side_effect = [runtime_client, upload_client]

    storage = TencentCosObjectStorage(
        Settings(
            cos_enabled=True,
            cos_region="ap-guangzhou",
            cos_bucket="private-bucket",
            cos_secret_id="runtime-secret-id",
            cos_secret_key="runtime-secret-key",
            cos_security_token="runtime-token",
            cos_upload_secret_id="upload-secret-id",
            cos_upload_secret_key="upload-secret-key",
            cos_upload_security_token="upload-token",
        )
    )

    assert config_factory.call_args_list == [
        call(
            Region="ap-guangzhou",
            SecretId="runtime-secret-id",
            SecretKey="runtime-secret-key",
            Token="runtime-token",
            Scheme="https",
        ),
        call(
            Region="ap-guangzhou",
            SecretId="upload-secret-id",
            SecretKey="upload-secret-key",
            Token="upload-token",
            Scheme="https",
        ),
    ]
    assert storage._client is runtime_client
    assert storage._upload_client is upload_client


async def test_cos_signing_uses_https_methods_scope_and_exact_ttl() -> None:
    runtime_client = MagicMock()
    runtime_client.get_presigned_url.return_value = "https://cos.example/download?signed=1"
    upload_client = MagicMock()
    upload_client.get_presigned_url.return_value = "https://cos.example/upload?signed=1"
    storage = storage_with_client(runtime_client)
    storage._upload_client = upload_client
    before = datetime.now(UTC)

    upload = await storage.create_upload_authorization(
        object_key="private/user-1/upload.jpg",
        content_type="image/jpeg",
        expires_in_seconds=120,
    )
    download = await storage.create_download_url(
        object_key="private/user-1/result.jpg",
        expires_in_seconds=600,
    )

    assert upload.method == "PUT"
    assert upload.headers == {"Content-Type": "image/jpeg"}
    assert before + timedelta(seconds=119) <= upload.expires_at
    assert upload.expires_at <= datetime.now(UTC) + timedelta(seconds=121)
    assert download.startswith("https://")
    upload_client.get_presigned_url.assert_called_once_with(
        Method="PUT",
        Bucket="private-bucket",
        Key="private/user-1/upload.jpg",
        Expired=120,
        Headers={"Content-Type": "image/jpeg"},
    )
    runtime_client.get_presigned_url.assert_called_once_with(
        Method="GET",
        Bucket="private-bucket",
        Key="private/user-1/result.jpg",
        Expired=600,
    )
    assert upload_client is not runtime_client


@pytest.mark.parametrize("operation", ["upload", "download"])
async def test_cos_signing_errors_are_mapped_to_storage_unavailable(
    operation: str,
) -> None:
    runtime_client = MagicMock()
    upload_client = MagicMock()
    storage = storage_with_client(runtime_client)
    storage._upload_client = upload_client
    signing_client = upload_client if operation == "upload" else runtime_client
    signing_client.get_presigned_url.side_effect = RuntimeError(
        "sdk details with secret credentials",
    )

    with pytest.raises(ObjectStorageUnavailableError):
        if operation == "upload":
            await storage.create_upload_authorization(
                object_key="private/user-1/upload.jpg",
                content_type="image/jpeg",
                expires_in_seconds=120,
            )
        else:
            await storage.create_download_url(
                object_key="private/user-1/result.jpg",
                expires_in_seconds=600,
            )
