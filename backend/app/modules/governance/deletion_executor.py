from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import Settings
from app.database.session import Database
from app.modules.assets.storage import (
    ObjectStorageUnavailableError,
    build_object_storage,
)
from app.modules.governance.deletion_repository import (
    AssetDeletionPlan,
    DeletionContext,
    DeletionRepository,
)
from app.modules.governance.models import DeletionStatus, DeletionType
from app.modules.jobs.execution import JobExecutionHarness
from app.modules.jobs.models import JobStatus
from app.modules.jobs.state_machine import transition_job


class RetryableDeletionError(Exception):
    def __init__(self, code: str, execution_token: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.execution_token = execution_token


@dataclass(frozen=True, slots=True)
class PreparedDeletion:
    object_keys: list[str]
    execution_token: str


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    completed: bool
    stale: bool
    additional_object_keys: tuple[str, ...] = ()


class DeletionExecutor:
    def __init__(self, *, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database
        self._execution = JobExecutionHarness(
            lease_seconds=settings.deletion_execution_lease_seconds,
            stale_user_message="删除任务已恢复，正在继续处理。",
        )

    async def run(self, job_id: UUID) -> str:
        try:
            prepared = await self._prepare(job_id)
        except RetryableDeletionError:
            raise
        except Exception as error:
            raise RetryableDeletionError("DELETION_PREPARATION_FAILURE") from error
        if prepared is None:
            return "SKIPPED"

        try:
            storage = build_object_storage(self._settings)
            deleted_keys: set[str] = set()
            pending_keys = set(prepared.object_keys)
            for _ in range(5):
                for object_key in sorted(pending_keys):
                    await storage.delete_object(object_key=object_key)
                    deleted_keys.add(object_key)
                decision = await self._complete_if_stable(
                    job_id=job_id,
                    execution_token=prepared.execution_token,
                    deleted_keys=deleted_keys,
                )
                if decision.stale:
                    return "SKIPPED_STALE"
                if decision.completed:
                    return "COMPLETED"
                pending_keys = set(decision.additional_object_keys)
            raise RuntimeError("deletion graph did not stabilize")
        except ObjectStorageUnavailableError as error:
            marked = await self._mark_retryable(
                job_id,
                code="DELETION_STORAGE_UNAVAILABLE",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableDeletionError(
                "DELETION_STORAGE_UNAVAILABLE",
                prepared.execution_token,
            ) from error
        except Exception as error:
            marked = await self._mark_retryable(
                job_id,
                code="DELETION_TEMPORARY_FAILURE",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableDeletionError(
                "DELETION_TEMPORARY_FAILURE",
                prepared.execution_token,
            ) from error

    async def finalize_failure(
        self,
        job_id: UUID,
        *,
        code: str,
        expected_execution_token: str | None = None,
    ) -> bool:
        async with self._database.session_factory() as session:
            context = await DeletionRepository(session).context(
                job_id=job_id,
                for_update=True,
            )
            if context is None:
                return False
            if not self._execution.finalize_failure(
                context.job,
                code=code,
                user_message="删除暂时未完成，请手动重试。",
                expected_execution_token=expected_execution_token,
            ):
                return False
            context.deletion.status = DeletionStatus.FAILED_FINAL
            context.deletion.last_error = code
            context.deletion.next_retry_at = None
            await session.commit()
            return True

    async def _prepare(self, job_id: UUID) -> PreparedDeletion | None:
        async with self._database.session_factory() as session:
            repository = DeletionRepository(session)
            context = await repository.context(job_id=job_id, for_update=True)
            if context is None:
                return None
            if context.deletion.status == DeletionStatus.COMPLETED:
                return None
            claim = self._execution.claim(context.job)
            if claim is None:
                return None
            execution_token = claim.execution_token
            if context.deletion.deletion_type not in {
                DeletionType.ACCOUNT,
                DeletionType.ASSET,
            }:
                self._fail_unsupported(
                    context,
                    expected_execution_token=execution_token,
                )
                await session.commit()
                return None
            if claim.recovered_stale_execution:
                context.deletion.status = DeletionStatus.FAILED_RETRYABLE
            context.deletion.status = DeletionStatus.PROCESSING
            context.deletion.attempt_count += 1
            context.deletion.next_retry_at = None
            if context.deletion.deletion_type == DeletionType.ACCOUNT:
                object_keys = await repository.account_object_keys(user_id=context.user.id)
            elif context.deletion.target_id is not None:
                plan = await repository.asset_deletion_plan(
                    user_id=context.user.id,
                    asset_id=context.deletion.target_id,
                )
                object_keys = list(plan.object_keys) if plan is not None else []
            else:
                self._fail_invalid_target(
                    context,
                    expected_execution_token=execution_token,
                )
                await session.commit()
                return None
            await session.commit()
            return PreparedDeletion(
                object_keys=object_keys,
                execution_token=execution_token,
            )

    @staticmethod
    def _fail_unsupported(
        context: DeletionContext,
        *,
        expected_execution_token: str,
    ) -> None:
        JobExecutionHarness.finalize_failure(
            context.job,
            code="DELETION_TYPE_UNSUPPORTED",
            user_message="这项删除请求暂时不受支持。",
            expected_execution_token=expected_execution_token,
        )
        context.deletion.status = DeletionStatus.FAILED_FINAL
        context.deletion.last_error = "DELETION_TYPE_UNSUPPORTED"

    @staticmethod
    def _fail_invalid_target(
        context: DeletionContext,
        *,
        expected_execution_token: str,
    ) -> None:
        JobExecutionHarness.finalize_failure(
            context.job,
            code="DELETION_TARGET_INVALID",
            user_message="删除目标无效，请重新发起。",
            expected_execution_token=expected_execution_token,
        )
        context.deletion.status = DeletionStatus.FAILED_FINAL
        context.deletion.last_error = "DELETION_TARGET_INVALID"

    async def _complete_if_stable(
        self,
        *,
        job_id: UUID,
        execution_token: str,
        deleted_keys: set[str],
    ) -> CompletionDecision:
        async with self._database.session_factory() as session:
            repository = DeletionRepository(session)
            context = await repository.context(job_id=job_id, for_update=True)
            if context is None or not self._execution.is_current(
                context.job,
                execution_token=execution_token,
            ):
                return CompletionDecision(completed=False, stale=True)

            asset_plan: AssetDeletionPlan | None = None
            if context.deletion.deletion_type == DeletionType.ACCOUNT:
                current_keys = await repository.account_object_keys(user_id=context.user.id)
            elif (
                context.deletion.deletion_type == DeletionType.ASSET
                and context.deletion.target_id is not None
            ):
                asset_plan = await repository.asset_deletion_plan(
                    user_id=context.user.id,
                    asset_id=context.deletion.target_id,
                )
                current_keys = list(asset_plan.object_keys) if asset_plan is not None else []
            else:
                self._fail_unsupported(
                    context,
                    expected_execution_token=execution_token,
                )
                await session.commit()
                return CompletionDecision(completed=False, stale=True)

            additional_keys = tuple(sorted(set(current_keys) - deleted_keys))
            if additional_keys:
                return CompletionDecision(
                    completed=False,
                    stale=False,
                    additional_object_keys=additional_keys,
                )

            transition_job(context.job, JobStatus.QUALITY_CHECKING)
            context.deletion.completed_steps = ["COS_OBJECTS_DELETED"]
            if context.deletion.deletion_type == DeletionType.ACCOUNT:
                await repository.purge_account(
                    user_id=context.user.id,
                    keep_deletion_id=context.deletion.id,
                    keep_generation_job_id=context.job.id,
                )
                context.deletion.completed_steps = [
                    "COS_OBJECTS_DELETED",
                    "BUSINESS_DATA_PURGED",
                    "IDENTITY_REMOVED",
                ]
            else:
                if asset_plan is not None:
                    await repository.purge_asset_plan(asset_plan)
                context.deletion.completed_steps = [
                    "COS_OBJECTS_DELETED",
                    "BUSINESS_DATA_PURGED",
                    "OBJECT_ACCESS_REVOKED",
                ]
            context.deletion.status = DeletionStatus.COMPLETED
            context.deletion.last_error = None
            context.deletion.next_retry_at = None
            context.job.result_reference_type = "DeletionJob"
            context.job.result_reference_id = context.deletion.id
            if not self._execution.complete(
                context.job,
                execution_token=execution_token,
            ):
                return CompletionDecision(completed=False, stale=True)
            await session.commit()
            return CompletionDecision(completed=True, stale=False)

    async def _mark_retryable(
        self,
        job_id: UUID,
        *,
        code: str,
        execution_token: str,
    ) -> bool:
        async with self._database.session_factory() as session:
            context = await DeletionRepository(session).context(
                job_id=job_id,
                for_update=True,
            )
            if context is None:
                return False
            if not self._execution.mark_retryable(
                context.job,
                code=code,
                user_message="删除暂时未完成，系统正在自动重试。",
                execution_token=execution_token,
            ):
                return False
            context.deletion.status = DeletionStatus.FAILED_RETRYABLE
            context.deletion.last_error = code
            context.deletion.next_retry_at = datetime.now(UTC) + timedelta(minutes=1)
            await session.commit()
            return True
