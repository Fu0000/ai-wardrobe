from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.models import AssetKind, AssetStatus, UserAsset


class AssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_upload(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
        bucket: str,
        object_key: str,
    ) -> UserAsset:
        asset = UserAsset(
            id=asset_id,
            user_id=user_id,
            kind=AssetKind.USER_UPLOAD,
            status=AssetStatus.UPLOADING,
            bucket=bucket,
            object_key=object_key,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def get_owned(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> UserAsset | None:
        statement = select(UserAsset).where(
            UserAsset.id == asset_id,
            UserAsset.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def create_generated(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
        bucket: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        width: int,
        height: int,
        checksum_sha256: str,
        kind: AssetKind = AssetKind.OPTIMIZATION_RESULT,
    ) -> UserAsset:
        asset = UserAsset(
            id=asset_id,
            user_id=user_id,
            kind=kind,
            status=AssetStatus.READY,
            bucket=bucket,
            object_key=object_key,
            content_type=content_type,
            size_bytes=size_bytes,
            width=width,
            height=height,
            checksum_sha256=checksum_sha256,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def complete_if_uploading(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
        content_type: str,
        size_bytes: int,
        width: int,
        height: int,
        checksum_sha256: str | None,
    ) -> UserAsset | None:
        """仅在清理器尚未认领上传时原子完成。

        校验图片需要访问对象存储，期间清理器可能把过期行转入
        ``UPLOAD_CLEANING``。条件更新是双方的提交点，避免最后写入者把已清理对象
        重新标记为 READY。
        """

        statement = (
            update(UserAsset)
            .where(
                UserAsset.id == asset_id,
                UserAsset.user_id == user_id,
                UserAsset.status == AssetStatus.UPLOADING,
            )
            .values(
                status=AssetStatus.READY,
                content_type=content_type,
                size_bytes=size_bytes,
                width=width,
                height=height,
                checksum_sha256=checksum_sha256,
            )
            .returning(UserAsset)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def fail_if_uploading(
        self,
        *,
        asset_id: UUID,
        user_id: UUID,
    ) -> bool:
        statement = (
            update(UserAsset)
            .where(
                UserAsset.id == asset_id,
                UserAsset.user_id == user_id,
                UserAsset.status == AssetStatus.UPLOADING,
            )
            .values(status=AssetStatus.FAILED)
            .returning(UserAsset.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def flush(self) -> None:
        await self._session.flush()
