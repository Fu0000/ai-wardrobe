"""过期上传清理的 PostgreSQL 状态机与对象删除验证。"""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from app.core.config import Settings
from app.database.session import Database
from app.modules.assets.cleanup import CleanupOutcome, OrphanUploadCleaner
from app.modules.assets.models import AssetKind, AssetStatus, UserAsset
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import ObjectStorage, ObjectStorageUnavailableError
from app.modules.identity.models import User
from tests.integration.markers import requires_services

pytestmark = requires_services


class RecordingStorage:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []
        self.fail_keys: set[str] = set()

    async def delete_object(self, *, object_key: str) -> None:
        if object_key in self.fail_keys:
            raise ObjectStorageUnavailableError
        self.deleted_keys.append(object_key)


def _cleaner(
    *,
    settings: Settings,
    database: Database,
    storage: RecordingStorage,
) -> OrphanUploadCleaner:
    return OrphanUploadCleaner(
        settings=settings,
        database=database,
        object_storage=cast(ObjectStorage, storage),
    )


async def test_cleanup_deletes_only_expired_unfinished_uploads(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(seconds=settings.orphan_upload_ttl_seconds + 60)
    user_id = uuid4()
    purge_users.append(user_id)
    expired_id = uuid4()
    recent_id = uuid4()
    ready_id = uuid4()

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        setup.add_all(
            [
                UserAsset(
                    id=expired_id,
                    user_id=user_id,
                    kind=AssetKind.USER_UPLOAD,
                    status=AssetStatus.UPLOADING,
                    bucket="integration",
                    object_key=f"private/{user_id}/uploads/expired.jpg",
                    created_at=old,
                    updated_at=old,
                ),
                UserAsset(
                    id=recent_id,
                    user_id=user_id,
                    kind=AssetKind.USER_UPLOAD,
                    status=AssetStatus.UPLOADING,
                    bucket="integration",
                    object_key=f"private/{user_id}/uploads/recent.jpg",
                    created_at=now,
                    updated_at=now,
                ),
                UserAsset(
                    id=ready_id,
                    user_id=user_id,
                    kind=AssetKind.USER_UPLOAD,
                    status=AssetStatus.READY,
                    bucket="integration",
                    object_key=f"private/{user_id}/uploads/ready.jpg",
                    created_at=old,
                    updated_at=old,
                ),
            ]
        )
        await setup.commit()

    storage = RecordingStorage()
    outcome = await _cleaner(
        settings=settings,
        database=database,
        storage=storage,
    ).cleanup_once(now=now)

    assert outcome == CleanupOutcome(
        claimed=1,
        cleaned=1,
        retryable_failures=0,
        skipped_stale=0,
    )
    assert storage.deleted_keys == [f"private/{user_id}/uploads/expired.jpg"]

    async with database.session_factory() as verify:
        assert await verify.get(UserAsset, expired_id) is None
        assert await verify.get(UserAsset, recent_id) is not None
        assert await verify.get(UserAsset, ready_id) is not None


async def test_storage_failure_requeues_and_stale_claim_is_recoverable(
    settings: Settings,
    database: Database,
    purge_users: list[UUID],
) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(
        seconds=max(
            settings.orphan_upload_ttl_seconds,
            settings.orphan_upload_cleanup_lease_seconds,
        )
        + 60
    )
    user_id = uuid4()
    purge_users.append(user_id)
    failed_id = uuid4()
    abandoned_id = uuid4()
    failed_key = f"private/{user_id}/uploads/retry.jpg"
    abandoned_key = f"private/{user_id}/uploads/abandoned.jpg"

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        setup.add_all(
            [
                UserAsset(
                    id=failed_id,
                    user_id=user_id,
                    kind=AssetKind.USER_UPLOAD,
                    status=AssetStatus.UPLOADING,
                    bucket="integration",
                    object_key=failed_key,
                    created_at=old,
                    updated_at=old,
                ),
                UserAsset(
                    id=abandoned_id,
                    user_id=user_id,
                    kind=AssetKind.USER_UPLOAD,
                    status=AssetStatus.UPLOAD_CLEANING,
                    bucket="integration",
                    object_key=abandoned_key,
                    created_at=old,
                    updated_at=old,
                ),
            ]
        )
        await setup.commit()

    storage = RecordingStorage()
    storage.fail_keys.add(failed_key)
    first = await _cleaner(
        settings=settings,
        database=database,
        storage=storage,
    ).cleanup_once(now=now)

    assert first == CleanupOutcome(
        claimed=2,
        cleaned=1,
        retryable_failures=1,
        skipped_stale=0,
    )
    async with database.session_factory() as verify_failed:
        failed = await verify_failed.get(UserAsset, failed_id)
        assert failed is not None
        assert failed.status is AssetStatus.UPLOAD_EXPIRED
        assert await verify_failed.get(UserAsset, abandoned_id) is None

    storage.fail_keys.clear()
    second = await _cleaner(
        settings=settings,
        database=database,
        storage=storage,
    ).cleanup_once(now=now + timedelta(seconds=1))

    assert second.cleaned == 1
    async with database.session_factory() as verify_retried:
        assert await verify_retried.get(UserAsset, failed_id) is None


async def test_complete_compare_and_set_cannot_revive_a_cleanup_claim(
    database: Database,
    purge_users: list[UUID],
) -> None:
    user_id = uuid4()
    purge_users.append(user_id)
    uploading_id = uuid4()
    claimed_id = uuid4()

    async with database.session_factory() as setup:
        setup.add(User(id=user_id))
        await setup.flush()
        setup.add_all(
            [
                UserAsset(
                    id=uploading_id,
                    user_id=user_id,
                    kind=AssetKind.USER_UPLOAD,
                    status=AssetStatus.UPLOADING,
                    bucket="integration",
                    object_key=f"private/{user_id}/uploads/complete.jpg",
                ),
                UserAsset(
                    id=claimed_id,
                    user_id=user_id,
                    kind=AssetKind.USER_UPLOAD,
                    status=AssetStatus.UPLOAD_CLEANING,
                    bucket="integration",
                    object_key=f"private/{user_id}/uploads/claimed.jpg",
                ),
            ]
        )
        await setup.commit()

    async with database.session_factory() as complete:
        repository = AssetRepository(complete)
        finished = await repository.complete_if_uploading(
            asset_id=uploading_id,
            user_id=user_id,
            content_type="image/jpeg",
            size_bytes=10_000,
            width=800,
            height=1_200,
            checksum_sha256=None,
        )
        stale = await repository.complete_if_uploading(
            asset_id=claimed_id,
            user_id=user_id,
            content_type="image/jpeg",
            size_bytes=10_000,
            width=800,
            height=1_200,
            checksum_sha256=None,
        )
        await complete.commit()

    assert finished is not None
    assert finished.status is AssetStatus.READY
    assert stale is None
    async with database.session_factory() as verify:
        claimed = await verify.get(UserAsset, claimed_id)
        assert claimed is not None
        assert claimed.status is AssetStatus.UPLOAD_CLEANING
