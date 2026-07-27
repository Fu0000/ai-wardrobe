"""清理未完成上传留下的数据库行与对象存储残留。

上传票据是短期授权，但用户可能在拿到 PUT URL 后退出、断网或永远不调用
``complete``。仅让签名过期不会删除已经上传的对象，因此需要一个可重入的清理器。

清理采用两阶段认领：

1. 在短事务中把过期 ``UPLOADING`` 原子转为 ``UPLOAD_CLEANING``；
2. 事务外删除对象，再以 ``updated_at`` 作为 fencing version 删除数据库行。

Worker 崩溃后，超过清理租约的 ``UPLOAD_CLEANING`` 会被重新认领。旧 Worker 即使迟到，
也无法凭旧 version 删除新认领的行；对象删除本身保持幂等。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import Settings
from app.core.telemetry import record_orphan_upload_cleanup
from app.database.session import Database
from app.modules.assets.models import AssetKind, AssetStatus, UserAsset
from app.modules.assets.storage import (
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageUnavailableError,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    asset_id: UUID
    object_key: str
    claim_version: datetime


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    claimed: int
    cleaned: int
    retryable_failures: int
    skipped_stale: int


class OrphanUploadCleaner:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        object_storage: ObjectStorage,
    ) -> None:
        self._settings = settings
        self._database = database
        self._object_storage = object_storage

    async def cleanup_once(self, *, now: datetime | None = None) -> CleanupOutcome:
        moment = now or datetime.now(UTC)
        candidates = await self._claim_candidates(now=moment)
        cleaned = 0
        retryable_failures = 0
        skipped_stale = 0

        for candidate in candidates:
            try:
                await self._object_storage.delete_object(
                    object_key=candidate.object_key,
                )
            except ObjectNotFoundError:
                # 对象从未上传或已被前一次尝试删除，均可继续收敛 DB 行。
                pass
            except ObjectStorageUnavailableError:
                retryable_failures += 1
                await self._requeue(candidate, now=moment)
                await logger.awarning(
                    "orphan_upload_cleanup_retryable",
                    asset_id=str(candidate.asset_id),
                )
                continue

            if await self._finalize(candidate):
                cleaned += 1
                await logger.ainfo(
                    "orphan_upload_cleaned",
                    asset_id=str(candidate.asset_id),
                )
            else:
                skipped_stale += 1

        outcome = CleanupOutcome(
            claimed=len(candidates),
            cleaned=cleaned,
            retryable_failures=retryable_failures,
            skipped_stale=skipped_stale,
        )
        record_orphan_upload_cleanup(outcome="cleaned", count=outcome.cleaned)
        record_orphan_upload_cleanup(
            outcome="retryable_failure",
            count=outcome.retryable_failures,
        )
        record_orphan_upload_cleanup(
            outcome="skipped_stale",
            count=outcome.skipped_stale,
        )
        return outcome

    async def _claim_candidates(self, *, now: datetime) -> list[CleanupCandidate]:
        upload_cutoff = now - timedelta(seconds=self._settings.orphan_upload_ttl_seconds)
        lease_cutoff = now - timedelta(seconds=self._settings.orphan_upload_cleanup_lease_seconds)

        async with self._database.session_factory() as session:
            statement = (
                select(UserAsset)
                .where(
                    UserAsset.kind == AssetKind.USER_UPLOAD,
                    or_(
                        and_(
                            UserAsset.status == AssetStatus.UPLOADING,
                            UserAsset.created_at < upload_cutoff,
                        ),
                        UserAsset.status == AssetStatus.UPLOAD_EXPIRED,
                        and_(
                            UserAsset.status == AssetStatus.UPLOAD_CLEANING,
                            UserAsset.updated_at < lease_cutoff,
                        ),
                    ),
                )
                .order_by(UserAsset.created_at)
                .limit(self._settings.orphan_upload_cleanup_batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(statement)
            assets = list(result.scalars())
            candidates: list[CleanupCandidate] = []
            for asset in assets:
                asset.status = AssetStatus.UPLOAD_CLEANING
                asset.updated_at = now
                candidates.append(
                    CleanupCandidate(
                        asset_id=asset.id,
                        object_key=asset.object_key,
                        claim_version=now,
                    )
                )
            await session.commit()
            return candidates

    async def _finalize(self, candidate: CleanupCandidate) -> bool:
        async with self._database.session_factory() as session:
            statement = delete(UserAsset).where(self._owns_claim(candidate)).returning(UserAsset.id)
            result = await session.execute(statement)
            deleted_id = result.scalar_one_or_none()
            await session.commit()
            return deleted_id is not None

    async def _requeue(
        self,
        candidate: CleanupCandidate,
        *,
        now: datetime,
    ) -> None:
        async with self._database.session_factory() as session:
            statement = (
                update(UserAsset)
                .where(self._owns_claim(candidate))
                .values(
                    status=AssetStatus.UPLOAD_EXPIRED,
                    updated_at=now,
                )
            )
            await session.execute(statement)
            await session.commit()

    @staticmethod
    def _owns_claim(candidate: CleanupCandidate) -> ColumnElement[bool]:
        return and_(
            UserAsset.id == candidate.asset_id,
            UserAsset.status == AssetStatus.UPLOAD_CLEANING,
            UserAsset.updated_at == candidate.claim_version,
        )
