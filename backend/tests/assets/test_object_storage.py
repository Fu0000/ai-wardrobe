from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.modules.assets.storage import (
    ObjectStorageUnavailableError,
    TencentCosObjectStorage,
)


def storage_with_client(client: MagicMock) -> TencentCosObjectStorage:
    storage = object.__new__(TencentCosObjectStorage)
    storage.bucket = "private-bucket"
    storage._client = client
    return storage


async def test_cos_signing_uses_https_methods_scope_and_exact_ttl() -> None:
    client = MagicMock()
    client.get_presigned_url.side_effect = [
        "https://cos.example/upload?signed=1",
        "https://cos.example/download?signed=1",
    ]
    storage = storage_with_client(client)
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
    assert client.get_presigned_url.call_args_list == [
        (
            (),
            {
                "Method": "PUT",
                "Bucket": "private-bucket",
                "Key": "private/user-1/upload.jpg",
                "Expired": 120,
                "Headers": {"Content-Type": "image/jpeg"},
            },
        ),
        (
            (),
            {
                "Method": "GET",
                "Bucket": "private-bucket",
                "Key": "private/user-1/result.jpg",
                "Expired": 600,
            },
        ),
    ]


@pytest.mark.parametrize("operation", ["upload", "download"])
async def test_cos_signing_errors_are_mapped_to_storage_unavailable(
    operation: str,
) -> None:
    client = MagicMock()
    client.get_presigned_url.side_effect = RuntimeError(
        "sdk details with secret credentials",
    )
    storage = storage_with_client(client)

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
