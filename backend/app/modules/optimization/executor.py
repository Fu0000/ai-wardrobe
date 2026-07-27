import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.core.config import Settings
from app.database.session import Database
from app.modules.ai.contracts import (
    ImageEditRequest,
    ProviderErrorCode,
    StructuredVisionRequest,
)
from app.modules.ai.gateway import AIGateway, AllProvidersFailedError
from app.modules.ai.openai_image_provider import OpenAIImageEditProvider
from app.modules.ai.openai_provider import OpenAIStructuredVisionProvider
from app.modules.ai.policies import (
    PolicySnapshotError,
    optimization_job_policies,
)
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import (
    ObjectNotFoundError,
    ObjectStorageUnavailableError,
    build_object_storage,
)
from app.modules.diagnosis.models import OptimizationStatus
from app.modules.governance.quota import QuotaRepository
from app.modules.jobs.execution import JobExecutionHarness
from app.modules.jobs.invocations import DatabaseInvocationObserver
from app.modules.jobs.models import JobStatus
from app.modules.jobs.state_machine import transition_job
from app.modules.optimization.images import (
    OptimizationImageError,
    comparison_data_url,
    validate_generated_image,
)
from app.modules.optimization.prompt import (
    OPTIMIZATION_CRITIC_PROMPT,
    OPTIMIZATION_IMAGE_PROMPT_VERSION,
    OPTIMIZATION_PLAN_SCHEMA_VERSION,
    image_edit_prompt,
)
from app.modules.optimization.repository import (
    OptimizationRecord,
    OptimizationRepository,
)
from app.modules.optimization.schema import (
    ChangeBudgetPlan,
    ChangeInstruction,
    OptimizationCriticOutput,
    PreserveInvariant,
)


class RetryableOptimizationError(Exception):
    def __init__(self, code: str, execution_token: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.execution_token = execution_token


@dataclass(frozen=True, slots=True)
class PreparedOptimization:
    record: OptimizationRecord
    source_image: bytes
    plan: ChangeBudgetPlan
    execution_token: str


class OptimizationExecutor:
    def __init__(self, *, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database
        self._execution = JobExecutionHarness(
            lease_seconds=settings.optimization_execution_lease_seconds,
            stale_user_message="任务已恢复，正在继续生成。",
        )

    async def run(self, job_id: UUID) -> str:
        try:
            prepared = await self._prepare(job_id)
        except RetryableOptimizationError:
            raise
        except Exception as error:
            raise RetryableOptimizationError("OPTIMIZATION_PREPARATION_FAILURE") from error
        if prepared is None:
            return "SKIPPED"

        image_provider: OpenAIImageEditProvider | None = None
        critic_provider: OpenAIStructuredVisionProvider | None = None
        image_providers: tuple[OpenAIImageEditProvider, ...] = ()
        critic_providers: tuple[OpenAIStructuredVisionProvider, ...] = ()
        if self._settings.openai_enabled:
            api_key = self._settings.openai_api_key.get_secret_value()
            image_provider = OpenAIImageEditProvider(
                api_key=api_key,
                base_url=self._settings.openai_base_url,
                max_output_bytes=self._settings.max_upload_bytes,
            )
            critic_provider = OpenAIStructuredVisionProvider(
                api_key=api_key,
                base_url=self._settings.openai_base_url,
            )
            image_providers = (image_provider,)
            critic_providers = (critic_provider,)

        image_gateway = AIGateway(image_edit_providers=image_providers)
        critic_gateway = AIGateway(
            structured_vision_providers=critic_providers,
        )
        record = prepared.record
        source_asset = record.source_asset
        try:
            image_policy, critic_policy, max_generation_attempts = optimization_job_policies(
                self._settings,
                record.job.model_policy_snapshot,
            )
        except PolicySnapshotError:
            finalized = await self.finalize_failure(
                job_id,
                code="AI_POLICY_SNAPSHOT_INVALID",
                user_message="任务配置校验失败，这次免费次数已退回。",
                expected_execution_token=prepared.execution_token,
            )
            return "FAILED_FINAL" if finalized else "SKIPPED_STALE"
        if (
            source_asset.content_type is None
            or source_asset.width is None
            or source_asset.height is None
        ):
            await self.finalize_failure(
                job_id,
                code="SOURCE_ASSET_METADATA_INCOMPLETE",
                user_message="原照片信息不完整，请重新上传后再试。",
                expected_execution_token=prepared.execution_token,
            )
            return "FAILED_FINAL"

        image_observer = DatabaseInvocationObserver(
            database=self._database,
            job_id=record.job.id,
            user_id=record.job.user_id,
            prompt_version=OPTIMIZATION_IMAGE_PROMPT_VERSION,
            schema_version=OPTIMIZATION_PLAN_SCHEMA_VERSION,
        )
        critic_observer = DatabaseInvocationObserver(
            database=self._database,
            job_id=record.job.id,
            user_id=record.job.user_id,
            prompt_version=OPTIMIZATION_CRITIC_PROMPT.prompt_version,
            schema_version=OPTIMIZATION_CRITIC_PROMPT.schema_version,
        )
        reports: list[dict[str, object]] = []

        try:
            for generation_attempt in range(
                1,
                max_generation_attempts + 1,
            ):
                edit_response = await image_gateway.image_edit(
                    request=ImageEditRequest(
                        source_image=prepared.source_image,
                        source_content_type=source_asset.content_type,
                        source_width=source_asset.width,
                        source_height=source_asset.height,
                        prompt=image_edit_prompt(
                            prepared.plan,
                            record.diagnosis.occasion,
                        ),
                    ),
                    policy=image_policy,
                    observer=image_observer,
                )
                try:
                    metadata = validate_generated_image(
                        edit_response.image_bytes,
                        source_width=source_asset.width,
                        source_height=source_asset.height,
                        max_bytes=self._settings.max_upload_bytes,
                        max_pixels=self._settings.max_image_pixels,
                    )
                except OptimizationImageError as error:
                    reports.append(
                        {
                            "generation_attempt": generation_attempt,
                            "overall_pass": False,
                            "failure_code": error.code,
                        }
                    )
                    continue

                comparison_url = comparison_data_url(
                    prepared.source_image,
                    edit_response.image_bytes,
                )
                critic_output: OptimizationCriticOutput | None = None
                for _critic_attempt in range(2):
                    critic_response = await critic_gateway.structured_vision(
                        request=StructuredVisionRequest(
                            image_url=comparison_url,
                            prompt=OPTIMIZATION_CRITIC_PROMPT.instructions(),
                            output_schema=OPTIMIZATION_CRITIC_PROMPT.output_schema(),
                            metadata={
                                "user_context": OPTIMIZATION_CRITIC_PROMPT.user_context(
                                    prepared.plan
                                )
                            },
                        ),
                        policy=critic_policy,
                        observer=critic_observer,
                    )
                    try:
                        critic_output = OptimizationCriticOutput.model_validate(
                            critic_response.output
                        )
                        break
                    except ValidationError:
                        continue
                if critic_output is None:
                    raise RetryableOptimizationError(
                        "OPTIMIZATION_CRITIC_RESPONSE_INVALID",
                        prepared.execution_token,
                    )

                report = critic_output.model_dump(mode="json")
                report["generation_attempt"] = generation_attempt
                reports.append(report)
                if not critic_output.overall_pass:
                    continue
                if not await self._is_current(
                    job_id,
                    prepared.execution_token,
                ):
                    return "SKIPPED_STALE"

                result_asset_id = uuid4()
                object_key = (
                    f"private/{record.job.user_id}/optimizations/"
                    f"{record.optimization.id}/{prepared.execution_token}.jpg"
                )
                storage = build_object_storage(self._settings)
                await storage.put_object(
                    object_key=object_key,
                    data=edit_response.image_bytes,
                    content_type=metadata.content_type,
                )
                completed = await self._complete(
                    job_id=job_id,
                    execution_token=prepared.execution_token,
                    result_asset_id=result_asset_id,
                    object_key=object_key,
                    image_bytes=edit_response.image_bytes,
                    width=metadata.width,
                    height=metadata.height,
                    image_model=edit_response.model,
                    critic_model=critic_response.model,
                    reports=reports,
                    accepted_attempt=generation_attempt,
                )
                return "COMPLETED" if completed else "SKIPPED_STALE"

            finalized = await self.finalize_failure(
                job_id,
                code="OPTIMIZATION_REJECTED_BY_CRITIC",
                user_message="优化图未通过一致性检查，这次免费次数已退回。",
                expected_execution_token=prepared.execution_token,
                rejected_by_critic=True,
                quality_report={
                    "attempts": reports,
                    "first_pass": False,
                    "accepted_attempt": None,
                },
            )
            return "REJECTED_BY_CRITIC" if finalized else "SKIPPED_STALE"
        except AllProvidersFailedError as error:
            terminal_codes = {
                ProviderErrorCode.INVALID_INPUT,
                ProviderErrorCode.CONTENT_POLICY,
                ProviderErrorCode.COST_LIMIT,
            }
            if error.attempts and error.attempts[-1].error_code in terminal_codes:
                finalized = await self.finalize_failure(
                    job_id,
                    code="OPTIMIZATION_PROVIDER_REJECTED",
                    user_message="这张照片暂时无法生成安全的优化图，免费次数已退回。",
                    expected_execution_token=prepared.execution_token,
                )
                return "FAILED_FINAL" if finalized else "SKIPPED_STALE"
            marked = await self._mark_retryable(
                job_id,
                code="OPTIMIZATION_PROVIDER_UNAVAILABLE",
                user_message="图片服务暂时繁忙，正在自动重试。",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableOptimizationError(
                "OPTIMIZATION_PROVIDER_UNAVAILABLE",
                prepared.execution_token,
            ) from error
        except RetryableOptimizationError:
            marked = await self._mark_retryable(
                job_id,
                code="OPTIMIZATION_CRITIC_RESPONSE_INVALID",
                user_message="质量检查暂时未完成，正在自动重试。",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise
        except (ObjectStorageUnavailableError, OptimizationImageError) as error:
            marked = await self._mark_retryable(
                job_id,
                code="OPTIMIZATION_STORAGE_UNAVAILABLE",
                user_message="优化图暂时无法保存，正在自动重试。",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableOptimizationError(
                "OPTIMIZATION_STORAGE_UNAVAILABLE",
                prepared.execution_token,
            ) from error
        except Exception as error:
            marked = await self._mark_retryable(
                job_id,
                code="OPTIMIZATION_TEMPORARY_FAILURE",
                user_message="优化暂时未完成，正在自动重试。",
                execution_token=prepared.execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableOptimizationError(
                "OPTIMIZATION_TEMPORARY_FAILURE",
                prepared.execution_token,
            ) from error
        finally:
            if image_provider is not None:
                await image_provider.close()
            if critic_provider is not None:
                await critic_provider.close()

    async def finalize_failure(
        self,
        job_id: UUID,
        *,
        code: str,
        user_message: str,
        expected_execution_token: str | None = None,
        rejected_by_critic: bool = False,
        quality_report: dict[str, object] | None = None,
    ) -> bool:
        async with self._database.session_factory() as session:
            record = await OptimizationRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if record is None:
                return False
            job = record.job
            if not self._execution.finalize_failure(
                job,
                code=code,
                user_message=user_message,
                expected_execution_token=expected_execution_token,
            ):
                return False
            record.optimization.status = (
                OptimizationStatus.REJECTED_BY_CRITIC
                if rejected_by_critic
                else OptimizationStatus.FAILED
            )
            if quality_report is not None:
                record.optimization.quality_report = quality_report
            await QuotaRepository(session).release(job_id=job_id)
            await session.commit()
            return True

    async def _prepare(self, job_id: UUID) -> PreparedOptimization | None:
        async with self._database.session_factory() as session:
            record = await OptimizationRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if record is None:
                return None
            job = record.job
            claim = self._execution.claim(job)
            if claim is None:
                return None
            execution_token = claim.execution_token
            try:
                plan = self._plan(record)
            except (ValidationError, ValueError):
                self._execution.finalize_failure(
                    job,
                    code="OPTIMIZATION_PLAN_INVALID",
                    user_message="这份诊断没有可安全执行的优化计划。",
                    expected_execution_token=execution_token,
                )
                record.optimization.status = OptimizationStatus.FAILED
                await QuotaRepository(session).release(job_id=job_id)
                await session.commit()
                return None
            await session.commit()

        storage = build_object_storage(self._settings)
        try:
            source_image = await storage.read_object(
                object_key=record.source_asset.object_key,
                max_bytes=self._settings.max_upload_bytes,
            )
        except ObjectStorageUnavailableError as error:
            await self._mark_retryable(
                job_id,
                code="ASSET_STORAGE_UNAVAILABLE",
                user_message="原照片读取失败，正在自动重试。",
                execution_token=execution_token,
            )
            raise RetryableOptimizationError(
                "ASSET_STORAGE_UNAVAILABLE",
                execution_token,
            ) from error
        except ObjectNotFoundError:
            await self.finalize_failure(
                job_id,
                code="SOURCE_ASSET_NOT_FOUND",
                user_message="原照片已不存在，请重新上传后再试。",
                expected_execution_token=execution_token,
            )
            return None
        return PreparedOptimization(
            record=record,
            source_image=source_image,
            plan=plan,
            execution_token=execution_token,
        )

    @staticmethod
    def _plan(record: OptimizationRecord) -> ChangeBudgetPlan:
        changes = [
            ChangeInstruction.model_validate(item) for item in record.optimization.change_summary
        ]
        replacement_count = sum(change.action.value == "REPLACE_ONE_ITEM" for change in changes)
        return ChangeBudgetPlan(
            level=record.optimization.change_level,
            replacement_count=replacement_count,
            changes=changes,
            preserve_invariants=list(PreserveInvariant),
        )

    async def _is_current(self, job_id: UUID, execution_token: str) -> bool:
        async with self._database.session_factory() as session:
            record = await OptimizationRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            return bool(
                record
                and self._execution.is_current(
                    record.job,
                    execution_token=execution_token,
                )
            )

    async def _complete(
        self,
        *,
        job_id: UUID,
        execution_token: str,
        result_asset_id: UUID,
        object_key: str,
        image_bytes: bytes,
        width: int,
        height: int,
        image_model: str,
        critic_model: str,
        reports: list[dict[str, object]],
        accepted_attempt: int,
    ) -> bool:
        async with self._database.session_factory() as session:
            record = await OptimizationRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if record is None or not self._execution.is_current(
                record.job,
                execution_token=execution_token,
            ):
                return False
            transition_job(record.job, JobStatus.QUALITY_CHECKING)
            await AssetRepository(session).create_generated(
                asset_id=result_asset_id,
                user_id=record.job.user_id,
                bucket=self._settings.cos_bucket,
                object_key=object_key,
                content_type="image/jpeg",
                size_bytes=len(image_bytes),
                width=width,
                height=height,
                checksum_sha256=hashlib.sha256(image_bytes).hexdigest(),
            )
            optimization = record.optimization
            optimization.result_asset_id = result_asset_id
            optimization.status = OptimizationStatus.COMPLETED
            optimization.quality_report = {
                "attempts": reports,
                "first_pass": accepted_attempt == 1,
                "accepted_attempt": accepted_attempt,
            }
            optimization.model_version = f"image:{image_model};critic:{critic_model}"
            optimization.prompt_version = (
                f"{OPTIMIZATION_IMAGE_PROMPT_VERSION};{OPTIMIZATION_CRITIC_PROMPT.prompt_version}"
            )
            optimization.schema_version = (
                f"{OPTIMIZATION_PLAN_SCHEMA_VERSION};{OPTIMIZATION_CRITIC_PROMPT.schema_version}"
            )
            record.job.result_reference_type = "StyleOptimizationResult"
            record.job.result_reference_id = optimization.id
            await QuotaRepository(session).commit(job_id=job_id)
            if not self._execution.complete(
                record.job,
                execution_token=execution_token,
            ):
                return False
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
            record = await OptimizationRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if record is None:
                return False
            if not self._execution.mark_retryable(
                record.job,
                code=code,
                user_message=user_message,
                execution_token=execution_token,
            ):
                return False
            await session.commit()
            return True
