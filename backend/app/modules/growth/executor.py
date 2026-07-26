import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.database.session import Database
from app.modules.assets.models import AssetKind
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import (
    ObjectNotFoundError,
    ObjectStorageUnavailableError,
    build_object_storage,
)
from app.modules.growth.images import ShareImage, ShareImageError, render_share_card
from app.modules.growth.models import ShareStatus
from app.modules.growth.repository import (
    GrowthRepository,
    ShareExecutionContext,
)
from app.modules.jobs.models import JobStatus
from app.modules.jobs.state_machine import transition_job


class RetryableShareError(Exception):
    def __init__(self, code: str, execution_token: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.execution_token = execution_token


@dataclass(frozen=True, slots=True)
class PreparedShare:
    context: ShareExecutionContext
    before_image: bytes
    after_image: bytes
    execution_token: str


class ShareAssetExecutor:
    def __init__(self, *, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database

    async def run(self, job_id: UUID) -> str:
        try:
            prepared = await self._prepare(job_id)
        except RetryableShareError:
            raise
        except Exception as error:
            raise RetryableShareError("SHARE_PREPARATION_FAILURE") from error
        if prepared is None:
            return "SKIPPED"

        try:
            rendered = await asyncio.to_thread(
                render_share_card,
                prepared.before_image,
                prepared.after_image,
                change_level=prepared.context.optimization.change_level,
                score=self._public_score(prepared.context.share.public_payload),
            )
            if len(rendered.data) > self._settings.max_upload_bytes:
                raise ShareImageError("share card exceeds safe size")
            if not await self._is_current(job_id, prepared.execution_token):
                return "SKIPPED_STALE"

            object_key = (
                f"share-derivatives/{prepared.context.share.scene_code}/"
                f"{prepared.execution_token}.jpg"
            )
            storage = build_object_storage(self._settings)
            await storage.put_object(
                object_key=object_key,
                data=rendered.data,
                content_type=rendered.content_type,
            )
            completed = await self._complete(
                job_id=job_id,
                execution_token=prepared.execution_token,
                object_key=object_key,
                rendered=rendered,
            )
            return "COMPLETED" if completed else "SKIPPED_STALE"
        except ShareImageError:
            finalized = await self.finalize_failure(
                job_id,
                code="SHARE_IMAGE_INVALID",
                user_message="分享卡片未能安全生成，请重新创建。",
                expected_execution_token=prepared.execution_token,
            )
            return "FAILED_FINAL" if finalized else "SKIPPED_STALE"
        except ObjectStorageUnavailableError as error:
            marked = await self._mark_retryable(
                job_id,
                code="SHARE_STORAGE_UNAVAILABLE",
                user_message="分享卡片暂时无法保存，正在自动重试。",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableShareError(
                "SHARE_STORAGE_UNAVAILABLE",
                prepared.execution_token,
            ) from error
        except Exception as error:
            marked = await self._mark_retryable(
                job_id,
                code="SHARE_TEMPORARY_FAILURE",
                user_message="分享卡片暂时未完成，正在自动重试。",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableShareError(
                "SHARE_TEMPORARY_FAILURE",
                prepared.execution_token,
            ) from error

    @staticmethod
    def _public_score(payload: dict[str, object]) -> int | None:
        value = payload.get("score")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    async def finalize_failure(
        self,
        job_id: UUID,
        *,
        code: str,
        user_message: str,
        expected_execution_token: str | None = None,
    ) -> bool:
        async with self._database.session_factory() as session:
            context = await GrowthRepository(session).job_context(
                job_id=job_id,
                for_update=True,
            )
            if context is None:
                return False
            job = context.job
            if job.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED_FINAL,
                JobStatus.CANCELLED,
            }:
                return False
            now = datetime.now(UTC)
            if (
                expected_execution_token is not None
                and job.execution_token != expected_execution_token
            ):
                return False
            if (
                expected_execution_token is None
                and job.status in {JobStatus.PROCESSING, JobStatus.QUALITY_CHECKING}
                and job.execution_lease_expires_at is not None
                and job.execution_lease_expires_at > now
            ):
                return False
            if job.status == JobStatus.PENDING:
                transition_job(job, JobStatus.QUEUED)
            transition_job(
                job,
                JobStatus.FAILED_FINAL,
                error_code=code,
                user_message=user_message,
            )
            context.share.status = ShareStatus.FAILED
            await session.commit()
            return True

    async def _prepare(self, job_id: UUID) -> PreparedShare | None:
        async with self._database.session_factory() as session:
            repository = GrowthRepository(session)
            job_context = await repository.job_context(
                job_id=job_id,
                for_update=True,
            )
            if job_context is None:
                return None
            job = job_context.job
            now = datetime.now(UTC)
            if job.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED_FINAL,
                JobStatus.CANCELLED,
                JobStatus.TIMED_OUT,
            }:
                return None
            if job.status in {JobStatus.PROCESSING, JobStatus.QUALITY_CHECKING}:
                if (
                    job.execution_lease_expires_at is not None
                    and job.execution_lease_expires_at > now
                ):
                    return None
                transition_job(
                    job,
                    JobStatus.FAILED_RETRYABLE,
                    error_code="STALE_EXECUTION_RECOVERED",
                    user_message="分享任务已恢复，正在继续生成。",
                )
            if job.status in {JobStatus.PENDING, JobStatus.FAILED_RETRYABLE}:
                transition_job(job, JobStatus.QUEUED)
            transition_job(job, JobStatus.PROCESSING)
            execution_token = str(uuid4())
            job.execution_token = execution_token
            job.execution_lease_expires_at = now + timedelta(
                seconds=self._settings.share_execution_lease_seconds
            )
            context = await repository.execution_context(job_id=job_id)
            if context is None:
                transition_job(
                    job,
                    JobStatus.FAILED_FINAL,
                    error_code="SHARE_SOURCE_NOT_FOUND",
                    user_message="用于分享的图片已不存在，请重新生成优化结果。",
                )
                job_context.share.status = ShareStatus.FAILED
                await session.commit()
                return None
            await session.commit()

        storage = build_object_storage(self._settings)
        try:
            before_image, after_image = await asyncio.gather(
                storage.read_object(
                    object_key=context.before_asset.object_key,
                    max_bytes=self._settings.max_upload_bytes,
                ),
                storage.read_object(
                    object_key=context.after_asset.object_key,
                    max_bytes=self._settings.max_upload_bytes,
                ),
            )
        except ObjectNotFoundError:
            await self.finalize_failure(
                job_id,
                code="SHARE_SOURCE_NOT_FOUND",
                user_message="用于分享的图片已不存在，请重新生成优化结果。",
                expected_execution_token=execution_token,
            )
            return None
        except ObjectStorageUnavailableError as error:
            await self._mark_retryable(
                job_id,
                code="SHARE_SOURCE_UNAVAILABLE",
                user_message="图片读取失败，正在自动重试。",
                execution_token=execution_token,
            )
            raise RetryableShareError(
                "SHARE_SOURCE_UNAVAILABLE",
                execution_token,
            ) from error
        return PreparedShare(
            context=context,
            before_image=before_image,
            after_image=after_image,
            execution_token=execution_token,
        )

    async def _is_current(self, job_id: UUID, execution_token: str) -> bool:
        async with self._database.session_factory() as session:
            context = await GrowthRepository(session).job_context(
                job_id=job_id,
                for_update=True,
            )
            return bool(
                context
                and context.job.status == JobStatus.PROCESSING
                and context.job.execution_token == execution_token
            )

    async def _complete(
        self,
        *,
        job_id: UUID,
        execution_token: str,
        object_key: str,
        rendered: ShareImage,
    ) -> bool:
        async with self._database.session_factory() as session:
            repository = GrowthRepository(session)
            context = await repository.execution_context(
                job_id=job_id,
                for_update=True,
            )
            if context is None:
                job_context = await repository.job_context(
                    job_id=job_id,
                    for_update=True,
                )
                if (
                    job_context is not None
                    and job_context.job.status == JobStatus.PROCESSING
                    and job_context.job.execution_token == execution_token
                ):
                    transition_job(
                        job_context.job,
                        JobStatus.FAILED_FINAL,
                        error_code="SHARE_SOURCE_NOT_FOUND",
                        user_message="用于分享的图片已不存在，请重新生成优化结果。",
                    )
                    job_context.share.status = ShareStatus.FAILED
                    await session.commit()
                return False
            if (
                context.job.status != JobStatus.PROCESSING
                or context.job.execution_token != execution_token
            ):
                return False
            transition_job(context.job, JobStatus.QUALITY_CHECKING)
            asset_id = uuid4()
            await AssetRepository(session).create_generated(
                asset_id=asset_id,
                user_id=context.share.user_id,
                bucket=self._settings.cos_bucket,
                object_key=object_key,
                content_type=rendered.content_type,
                size_bytes=len(rendered.data),
                width=rendered.width,
                height=rendered.height,
                checksum_sha256=hashlib.sha256(rendered.data).hexdigest(),
                kind=AssetKind.SHARE_DERIVATIVE,
            )
            context.share.share_asset_id = asset_id
            context.share.status = ShareStatus.ACTIVE
            context.share.expires_at = datetime.now(UTC) + timedelta(
                days=self._settings.share_ttl_days
            )
            latency_ms = (
                round((datetime.now(UTC) - context.job.started_at).total_seconds() * 1_000)
                if context.job.started_at is not None
                else None
            )
            await GrowthRepository(session).record_event(
                event_id=uuid4(),
                user_id=context.share.user_id,
                event_name="share.asset.created",
                entity_type="ShareRecord",
                entity_id=context.share.id,
                dedupe_key=str(context.share.id),
                properties={
                    "latency_ms": latency_ms,
                    "template_version": context.share.public_payload.get(
                        "template_version",
                        "UNKNOWN",
                    ),
                },
            )
            context.job.result_reference_type = "ShareRecord"
            context.job.result_reference_id = context.share.id
            transition_job(context.job, JobStatus.COMPLETED)
            await session.commit()
            return True

    async def _mark_retryable(
        self,
        job_id: UUID,
        *,
        code: str,
        user_message: str,
        execution_token: str,
    ) -> bool:
        async with self._database.session_factory() as session:
            context = await GrowthRepository(session).job_context(
                job_id=job_id,
                for_update=True,
            )
            if context is None or context.job.execution_token != execution_token:
                return False
            if context.job.status in {
                JobStatus.PROCESSING,
                JobStatus.QUALITY_CHECKING,
            }:
                transition_job(
                    context.job,
                    JobStatus.FAILED_RETRYABLE,
                    error_code=code,
                    user_message=user_message,
                )
                await session.commit()
                return True
            return False
