import hashlib
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.telemetry import current_trace_fields
from app.database.session import Database
from app.modules.ai.contracts import (
    ImageEditRequest,
    ProviderErrorCode,
    StructuredVisionRequest,
    TaskPolicy,
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
from app.modules.events.server import (
    ServerEventContext,
    ServerEventRecorder,
    elapsed_milliseconds,
)
from app.modules.governance.quota import QuotaRepository
from app.modules.jobs.invocations import DatabaseInvocationObserver
from app.modules.jobs.models import JobStatus
from app.modules.jobs.runtime.execution import JobExecutionHarness
from app.modules.jobs.state_machine import transition_job
from app.modules.optimization.images import (
    GeneratedImageMetadata,
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


class InvalidCriticResponseError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PreparedOptimization:
    record: OptimizationRecord
    source_image: bytes
    plan: ChangeBudgetPlan
    execution_token: str


@dataclass(frozen=True, slots=True)
class GeneratedCandidate:
    image_bytes: bytes
    metadata: GeneratedImageMetadata
    image_model: str


@dataclass(frozen=True, slots=True)
class SourceImageMetadata:
    content_type: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CriticReview:
    output: OptimizationCriticOutput
    model: str


@dataclass(frozen=True, slots=True)
class OptimizationGateways:
    image: AIGateway
    critic: AIGateway
    image_provider: OpenAIImageEditProvider | None
    critic_provider: OpenAIStructuredVisionProvider | None

    async def close(self) -> None:
        if self.image_provider is not None:
            await self.image_provider.close()
        if self.critic_provider is not None:
            await self.critic_provider.close()


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
        except SQLAlchemyError as error:
            raise RetryableOptimizationError("OPTIMIZATION_PREPARATION_FAILURE") from error
        if prepared is None:
            return "SKIPPED"

        gateways = self._gateways()
        try:
            return await self._run_prepared(
                job_id=job_id,
                prepared=prepared,
                gateways=gateways,
            )
        except AllProvidersFailedError as error:
            return await self._handle_provider_failure(job_id, prepared, error)
        except InvalidCriticResponseError as error:
            return await self._retry_or_skip(
                job_id,
                prepared,
                code="OPTIMIZATION_CRITIC_RESPONSE_INVALID",
                user_message="质量检查暂时未完成，正在自动重试。",
                cause=error,
            )
        except ObjectStorageUnavailableError as error:
            return await self._retry_or_skip(
                job_id,
                prepared,
                code="OPTIMIZATION_STORAGE_UNAVAILABLE",
                user_message="优化图暂时无法保存，正在自动重试。",
                cause=error,
            )
        except OptimizationImageError as error:
            return await self._retry_or_skip(
                job_id,
                prepared,
                code="OPTIMIZATION_IMAGE_PROCESSING_FAILED",
                user_message="优化图校验暂时未完成，正在自动重试。",
                cause=error,
            )
        except SQLAlchemyError as error:
            return await self._retry_or_skip(
                job_id,
                prepared,
                code="OPTIMIZATION_DATABASE_UNAVAILABLE",
                user_message="任务状态暂时无法保存，正在自动重试。",
                cause=error,
            )
        except Exception:
            with suppress(SQLAlchemyError):
                await self.finalize_failure(
                    job_id,
                    code="OPTIMIZATION_INTERNAL_ERROR",
                    user_message="优化任务遇到内部错误，这次免费次数已退回。",
                    expected_execution_token=prepared.execution_token,
                )
            raise
        finally:
            await gateways.close()

    def _gateways(self) -> OptimizationGateways:
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
        return OptimizationGateways(
            image=AIGateway(image_edit_providers=image_providers),
            critic=AIGateway(structured_vision_providers=critic_providers),
            image_provider=image_provider,
            critic_provider=critic_provider,
        )

    async def _run_prepared(
        self,
        *,
        job_id: UUID,
        prepared: PreparedOptimization,
        gateways: OptimizationGateways,
    ) -> str:
        record = prepared.record
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
        source_metadata = self._source_metadata(record)
        if source_metadata is None:
            finalized = await self.finalize_failure(
                job_id,
                code="SOURCE_ASSET_METADATA_INCOMPLETE",
                user_message="原照片信息不完整，请重新上传后再试。",
                expected_execution_token=prepared.execution_token,
            )
            return "FAILED_FINAL" if finalized else "SKIPPED_STALE"

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
        return await self._run_generation_attempts(
            job_id=job_id,
            prepared=prepared,
            gateways=gateways,
            source_metadata=source_metadata,
            image_policy=image_policy,
            critic_policy=critic_policy,
            max_generation_attempts=max_generation_attempts,
            image_observer=image_observer,
            critic_observer=critic_observer,
            reports=reports,
        )

    @staticmethod
    def _source_metadata(record: OptimizationRecord) -> SourceImageMetadata | None:
        asset = record.source_asset
        if asset.content_type is None or asset.width is None or asset.height is None:
            return None
        return SourceImageMetadata(
            content_type=asset.content_type,
            width=asset.width,
            height=asset.height,
        )

    async def _run_generation_attempts(
        self,
        *,
        job_id: UUID,
        prepared: PreparedOptimization,
        gateways: OptimizationGateways,
        source_metadata: SourceImageMetadata,
        image_policy: TaskPolicy,
        critic_policy: TaskPolicy,
        max_generation_attempts: int,
        image_observer: DatabaseInvocationObserver,
        critic_observer: DatabaseInvocationObserver,
        reports: list[dict[str, object]],
    ) -> str:
        for generation_attempt in range(1, max_generation_attempts + 1):
            try:
                candidate = await self._generate_candidate(
                    prepared=prepared,
                    gateway=gateways.image,
                    source_metadata=source_metadata,
                    policy=image_policy,
                    observer=image_observer,
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

            review = await self._review_candidate(
                prepared=prepared,
                candidate=candidate,
                gateway=gateways.critic,
                policy=critic_policy,
                observer=critic_observer,
            )
            report = review.output.model_dump(mode="json")
            report["generation_attempt"] = generation_attempt
            reports.append(report)
            if not review.output.overall_pass:
                continue
            return await self._persist_candidate(
                job_id=job_id,
                prepared=prepared,
                candidate=candidate,
                review=review,
                reports=reports,
                accepted_attempt=generation_attempt,
            )

        return await self._reject_candidates(
            job_id=job_id,
            prepared=prepared,
            reports=reports,
        )

    async def _generate_candidate(
        self,
        *,
        prepared: PreparedOptimization,
        gateway: AIGateway,
        source_metadata: SourceImageMetadata,
        policy: TaskPolicy,
        observer: DatabaseInvocationObserver,
    ) -> GeneratedCandidate:
        response = await gateway.image_edit(
            request=ImageEditRequest(
                source_image=prepared.source_image,
                source_content_type=source_metadata.content_type,
                source_width=source_metadata.width,
                source_height=source_metadata.height,
                prompt=image_edit_prompt(
                    prepared.plan,
                    prepared.record.diagnosis.occasion,
                ),
            ),
            policy=policy,
            observer=observer,
        )
        metadata = validate_generated_image(
            response.image_bytes,
            source_width=source_metadata.width,
            source_height=source_metadata.height,
            max_bytes=self._settings.max_upload_bytes,
            max_pixels=self._settings.max_image_pixels,
        )
        return GeneratedCandidate(
            image_bytes=response.image_bytes,
            metadata=metadata,
            image_model=response.model,
        )

    async def _review_candidate(
        self,
        *,
        prepared: PreparedOptimization,
        candidate: GeneratedCandidate,
        gateway: AIGateway,
        policy: TaskPolicy,
        observer: DatabaseInvocationObserver,
    ) -> CriticReview:
        comparison_url = comparison_data_url(
            prepared.source_image,
            candidate.image_bytes,
        )
        for _critic_attempt in range(2):
            response = await gateway.structured_vision(
                request=StructuredVisionRequest(
                    image_url=comparison_url,
                    prompt=OPTIMIZATION_CRITIC_PROMPT.instructions(),
                    output_schema=OPTIMIZATION_CRITIC_PROMPT.output_schema(),
                    metadata={
                        "user_context": OPTIMIZATION_CRITIC_PROMPT.user_context(prepared.plan)
                    },
                ),
                policy=policy,
                observer=observer,
            )
            try:
                output = OptimizationCriticOutput.model_validate(response.output)
            except ValidationError:
                continue
            return CriticReview(output=output, model=response.model)
        raise InvalidCriticResponseError

    async def _persist_candidate(
        self,
        *,
        job_id: UUID,
        prepared: PreparedOptimization,
        candidate: GeneratedCandidate,
        review: CriticReview,
        reports: list[dict[str, object]],
        accepted_attempt: int,
    ) -> str:
        if not await self._is_current(job_id, prepared.execution_token):
            return "SKIPPED_STALE"

        record = prepared.record
        result_asset_id = uuid4()
        object_key = (
            f"private/{record.job.user_id}/optimizations/"
            f"{record.optimization.id}/{prepared.execution_token}.jpg"
        )
        storage = build_object_storage(self._settings)
        await storage.put_object(
            object_key=object_key,
            data=candidate.image_bytes,
            content_type=candidate.metadata.content_type,
        )
        completed = await self._complete(
            job_id=job_id,
            execution_token=prepared.execution_token,
            result_asset_id=result_asset_id,
            object_key=object_key,
            image_bytes=candidate.image_bytes,
            width=candidate.metadata.width,
            height=candidate.metadata.height,
            image_model=candidate.image_model,
            critic_model=review.model,
            reports=reports,
            accepted_attempt=accepted_attempt,
        )
        return "COMPLETED" if completed else "SKIPPED_STALE"

    async def _reject_candidates(
        self,
        *,
        job_id: UUID,
        prepared: PreparedOptimization,
        reports: list[dict[str, object]],
    ) -> str:
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

    async def _handle_provider_failure(
        self,
        job_id: UUID,
        prepared: PreparedOptimization,
        error: AllProvidersFailedError,
    ) -> str:
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
        return await self._retry_or_skip(
            job_id,
            prepared,
            code="OPTIMIZATION_PROVIDER_UNAVAILABLE",
            user_message="图片服务暂时繁忙，正在自动重试。",
            cause=error,
        )

    async def _retry_or_skip(
        self,
        job_id: UUID,
        prepared: PreparedOptimization,
        *,
        code: str,
        user_message: str,
        cause: Exception,
    ) -> str:
        marked = await self._mark_retryable(
            job_id,
            code=code,
            user_message=user_message,
            execution_token=prepared.execution_token,
        )
        if not marked:
            return "SKIPPED_STALE"
        raise RetryableOptimizationError(
            code,
            prepared.execution_token,
        ) from cause

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
            await ServerEventRecorder(
                session,
                self._settings,
                ServerEventContext(
                    request_id=f"job:{job_id}",
                    trace_id=current_trace_fields().get("trace_id", "unavailable"),
                    app_channel="worker",
                ),
            ).record(
                subject_user_id=record.job.user_id,
                event_name="optimization.result.completed",
                entity_type="GenerationJob",
                entity_id=record.job.id,
                dedupe_key=str(record.job.id),
                properties={
                    "job_id": str(record.job.id),
                    "critic_attempts": accepted_attempt,
                    "latency_ms": elapsed_milliseconds(
                        record.job.started_at or record.job.created_at,
                        record.job.completed_at,
                    ),
                },
            )
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
