from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from app.core.config import Settings
from app.database.session import Database
from app.modules.ai.contracts import (
    ProviderErrorCode,
    StructuredVisionRequest,
)
from app.modules.ai.gateway import AIGateway, AllProvidersFailedError
from app.modules.ai.openai_provider import OpenAIStructuredVisionProvider
from app.modules.ai.policies import (
    PolicySnapshotError,
    diagnosis_job_policy,
)
from app.modules.assets.storage import (
    ObjectStorageUnavailableError,
    build_object_storage,
)
from app.modules.diagnosis.models import DiagnosisStatus
from app.modules.diagnosis.prompt import STYLE_DIAGNOSIS_PROMPT
from app.modules.diagnosis.repository import (
    DiagnosisExecutionContext,
    DiagnosisRepository,
)
from app.modules.diagnosis.schema import DiagnosisOutput, InputQuality
from app.modules.governance.quota import QuotaRepository
from app.modules.jobs.invocations import DatabaseInvocationObserver
from app.modules.jobs.models import JobStatus
from app.modules.jobs.runtime.execution import JobExecutionHarness
from app.modules.jobs.state_machine import transition_job


class RetryableDiagnosisError(Exception):
    def __init__(self, code: str, execution_token: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.execution_token = execution_token


@dataclass(frozen=True, slots=True)
class PreparedDiagnosis:
    context: DiagnosisExecutionContext
    image_url: str
    execution_token: str


class DiagnosisExecutor:
    def __init__(self, *, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database
        self._execution = JobExecutionHarness(
            lease_seconds=settings.diagnosis_execution_lease_seconds,
            stale_user_message="任务已恢复，正在继续分析。",
        )

    async def run(self, job_id: UUID) -> str:
        try:
            prepared = await self._prepare(job_id)
        except RetryableDiagnosisError:
            raise
        except Exception as error:
            raise RetryableDiagnosisError("DIAGNOSIS_PREPARATION_FAILURE") from error
        if prepared is None:
            return "SKIPPED"

        provider: OpenAIStructuredVisionProvider | None = None
        providers: tuple[OpenAIStructuredVisionProvider, ...] = ()
        if self._settings.openai_enabled:
            provider = OpenAIStructuredVisionProvider(
                api_key=self._settings.openai_api_key.get_secret_value(),
                base_url=self._settings.openai_base_url,
            )
            providers = (provider,)

        context = prepared.context
        execution_token = prepared.execution_token
        try:
            job_policy = diagnosis_job_policy(
                self._settings,
                context.job.model_policy_snapshot,
            )
        except PolicySnapshotError:
            finalized = await self.finalize_failure(
                job_id,
                code="AI_POLICY_SNAPSHOT_INVALID",
                user_message="任务配置校验失败，这次免费次数已退回。",
                expected_execution_token=execution_token,
            )
            return "FAILED_FINAL" if finalized else "SKIPPED_STALE"
        gateway = AIGateway(structured_vision_providers=providers)
        observer = DatabaseInvocationObserver(
            database=self._database,
            job_id=context.job.id,
            user_id=context.job.user_id,
            prompt_version=STYLE_DIAGNOSIS_PROMPT.prompt_version,
            schema_version=STYLE_DIAGNOSIS_PROMPT.schema_version,
        )
        request = StructuredVisionRequest(
            image_url=prepared.image_url,
            prompt=STYLE_DIAGNOSIS_PROMPT.instructions,
            output_schema=STYLE_DIAGNOSIS_PROMPT.output_schema(),
            metadata={
                "user_context": STYLE_DIAGNOSIS_PROMPT.user_context(context.diagnosis.occasion)
            },
        )

        try:
            response = await gateway.structured_vision(
                request=request,
                policy=job_policy,
                observer=observer,
            )
            output = DiagnosisOutput.model_validate(response.output)
            if output.input_quality != InputQuality.ACCEPTABLE:
                finalized = await self._finalize_input_failure(
                    job_id,
                    output,
                    execution_token=execution_token,
                )
                return "INPUT_REJECTED" if finalized else "SKIPPED_STALE"
            completed = await self._complete(
                job_id=job_id,
                output=output,
                provider=response.provider,
                model=response.model,
                execution_token=execution_token,
            )
            return "COMPLETED" if completed else "SKIPPED_STALE"
        except AllProvidersFailedError as error:
            terminal_codes = {
                ProviderErrorCode.INVALID_INPUT,
                ProviderErrorCode.CONTENT_POLICY,
            }
            if error.attempts and error.attempts[-1].error_code in terminal_codes:
                finalized = await self.finalize_failure(
                    job_id,
                    code="AI_INPUT_REJECTED",
                    user_message="这张照片暂时无法用于穿搭分析，请更换照片。",
                    expected_execution_token=execution_token,
                )
                return "FAILED_FINAL" if finalized else "SKIPPED_STALE"
            marked = await self._mark_retryable(
                job_id,
                code="AI_PROVIDER_UNAVAILABLE",
                user_message="分析服务暂时繁忙，正在自动重试。",
                execution_token=execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableDiagnosisError(
                "AI_PROVIDER_UNAVAILABLE",
                execution_token,
            ) from error
        except ValidationError as error:
            marked = await self._mark_retryable(
                job_id,
                code="AI_RESPONSE_INVALID",
                user_message="结果校验未通过，正在自动重试。",
                execution_token=execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableDiagnosisError(
                "AI_RESPONSE_INVALID",
                execution_token,
            ) from error
        except Exception as error:
            marked = await self._mark_retryable(
                job_id,
                code="DIAGNOSIS_TEMPORARY_FAILURE",
                user_message="诊断暂时未完成，正在自动重试。",
                execution_token=execution_token,
            )
            if not marked:
                return "SKIPPED_STALE"
            raise RetryableDiagnosisError(
                "DIAGNOSIS_TEMPORARY_FAILURE",
                execution_token,
            ) from error
        finally:
            if provider is not None:
                await provider.close()

    async def finalize_failure(
        self,
        job_id: UUID,
        *,
        code: str,
        user_message: str,
        expected_execution_token: str | None = None,
    ) -> bool:
        async with self._database.session_factory() as session:
            context = await DiagnosisRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if context is None:
                return False
            if not self._execution.finalize_failure(
                context.job,
                code=code,
                user_message=user_message,
                expected_execution_token=expected_execution_token,
            ):
                return False
            context.diagnosis.status = DiagnosisStatus.FAILED
            await QuotaRepository(session).release(job_id=job_id)
            await session.commit()
            return True

    async def _prepare(self, job_id: UUID) -> PreparedDiagnosis | None:
        object_storage = build_object_storage(self._settings)
        async with self._database.session_factory() as session:
            context = await DiagnosisRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if context is None:
                return None
            job = context.job
            claim = self._execution.claim(job)
            if claim is None:
                return None
            execution_token = claim.execution_token
            try:
                image_url = await object_storage.create_download_url(
                    object_key=context.asset.object_key,
                    expires_in_seconds=self._settings.cos_download_url_ttl_seconds,
                )
            except ObjectStorageUnavailableError as error:
                self._execution.mark_retryable(
                    job,
                    code="ASSET_STORAGE_UNAVAILABLE",
                    user_message="照片读取失败，正在自动重试。",
                    execution_token=execution_token,
                )
                await session.commit()
                raise RetryableDiagnosisError(
                    "ASSET_STORAGE_UNAVAILABLE",
                    execution_token,
                ) from error
            await session.commit()
            return PreparedDiagnosis(
                context=context,
                image_url=image_url,
                execution_token=execution_token,
            )

    async def _complete(
        self,
        *,
        job_id: UUID,
        output: DiagnosisOutput,
        provider: str,
        model: str,
        execution_token: str,
    ) -> bool:
        async with self._database.session_factory() as session:
            context = await DiagnosisRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if context is None or not self._execution.is_current(
                context.job,
                execution_token=execution_token,
            ):
                return False
            transition_job(context.job, JobStatus.QUALITY_CHECKING)
            diagnosis = context.diagnosis
            diagnosis.status = DiagnosisStatus.COMPLETED
            diagnosis.input_quality = output.input_quality.value
            diagnosis.input_quality_message = None
            diagnosis.score = output.score
            diagnosis.summary = output.summary
            diagnosis.strengths = [item.model_dump(mode="json") for item in output.strengths]
            diagnosis.issues = [item.model_dump(mode="json") for item in output.issues]
            diagnosis.primary_issue = (
                output.primary_issue.model_dump(mode="json") if output.primary_issue else None
            )
            diagnosis.optimization_plan = [
                item.model_dump(mode="json") for item in output.optimization_plan
            ]
            diagnosis.disclaimer = output.disclaimer
            diagnosis.model_version = f"{provider}:{model}"
            diagnosis.prompt_version = STYLE_DIAGNOSIS_PROMPT.prompt_version
            diagnosis.schema_version = STYLE_DIAGNOSIS_PROMPT.schema_version
            context.job.result_reference_type = "StyleDiagnosis"
            context.job.result_reference_id = diagnosis.id
            await QuotaRepository(session).commit(job_id=job_id)
            if not self._execution.complete(
                context.job,
                execution_token=execution_token,
            ):
                return False
            await session.commit()
            return True

    async def _finalize_input_failure(
        self,
        job_id: UUID,
        output: DiagnosisOutput,
        *,
        execution_token: str,
    ) -> bool:
        return await self.finalize_failure(
            job_id,
            code=f"INPUT_QUALITY_{output.input_quality.value}",
            user_message=output.input_quality_message or "照片不够清晰，请重新选择。",
            expected_execution_token=execution_token,
        )

    async def _mark_retryable(
        self,
        job_id: UUID,
        *,
        code: str,
        user_message: str,
        execution_token: str,
    ) -> bool:
        async with self._database.session_factory() as session:
            context = await DiagnosisRepository(session).execution_context(
                job_id=job_id,
                for_update=True,
            )
            if context is None:
                return False
            if not self._execution.mark_retryable(
                context.job,
                code=code,
                user_message=user_message,
                execution_token=execution_token,
            ):
                return False
            await session.commit()
            return True
