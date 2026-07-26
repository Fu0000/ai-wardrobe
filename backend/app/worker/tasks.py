import asyncio
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.telemetry import WorkerSpan
from app.database.session import Database
from app.modules.diagnosis.executor import DiagnosisExecutor, RetryableDiagnosisError
from app.modules.events.dispatcher import CeleryEventPublisher, OutboxDispatcher
from app.modules.governance.deletion_executor import (
    DeletionExecutor,
    RetryableDeletionError,
)
from app.modules.growth.executor import RetryableShareError, ShareAssetExecutor
from app.modules.jobs.reaper import StaleJobReaper
from app.modules.optimization.executor import (
    OptimizationExecutor,
    RetryableOptimizationError,
)
from app.worker.celery_app import celery_app


@celery_app.task(name="ai_wardrobe.reap_stale_jobs")  # type: ignore[untyped-decorator]
def reap_stale_jobs() -> int:
    """回收 Worker 失联后卡死的任务，释放其占用的配额。"""

    async def reap() -> int:
        with WorkerSpan(
            name="celery reap_stale_jobs",
            headers={},
            job_id=None,
        ) as operation:
            settings = get_settings()
            database = Database(settings)
            try:
                outcome = await StaleJobReaper(
                    settings=settings,
                    database=database,
                ).reap_once()
                operation.set_outcome("reaped")
                return outcome.reaped
            finally:
                await database.dispose()

    return asyncio.run(reap())


@celery_app.task(name="ai_wardrobe.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_outbox() -> int:
    async def dispatch() -> int:
        with WorkerSpan(
            name="celery dispatch_outbox",
            headers={},
            job_id=None,
        ) as operation:
            settings = get_settings()
            database = Database(settings)
            try:
                dispatcher = OutboxDispatcher(
                    settings=settings,
                    database=database,
                    publisher=CeleryEventPublisher(celery_app),
                )
                published = await dispatcher.dispatch_once()
                operation.set_outcome("published")
                return published
            finally:
                await database.dispose()

    return asyncio.run(dispatch())


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="ai_wardrobe.run_style_diagnosis",
    max_retries=2,
    acks_late=True,
)
def run_style_diagnosis(task: Any, *, job_id: str) -> str:
    async def execute() -> str:
        with WorkerSpan(
            name="celery run_style_diagnosis",
            headers=getattr(task.request, "headers", {}),
            job_id=job_id,
        ) as operation:
            settings = get_settings()
            database = Database(settings)
            try:
                outcome = await DiagnosisExecutor(
                    settings=settings,
                    database=database,
                ).run(UUID(job_id))
                operation.set_outcome(outcome)
                return outcome
            finally:
                await database.dispose()

    try:
        return asyncio.run(execute())
    except RetryableDiagnosisError as error:
        execution_token = error.execution_token
        max_retries = int(task.max_retries or 0)
        if task.request.retries >= max_retries:

            async def finalize() -> None:
                settings = get_settings()
                database = Database(settings)
                try:
                    await DiagnosisExecutor(
                        settings=settings,
                        database=database,
                    ).finalize_failure(
                        UUID(job_id),
                        code="DIAGNOSIS_RETRY_EXHAUSTED",
                        user_message="这次诊断没有成功，免费次数已退回。",
                        expected_execution_token=execution_token,
                    )
                finally:
                    await database.dispose()

            asyncio.run(finalize())
            return "FAILED_FINAL"
        raise task.retry(
            exc=error,
            countdown=min(30, 2 ** (task.request.retries + 1)),
        ) from error


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="ai_wardrobe.run_style_optimization",
    max_retries=2,
    acks_late=True,
)
def run_style_optimization(task: Any, *, job_id: str) -> str:
    async def execute() -> str:
        with WorkerSpan(
            name="celery run_style_optimization",
            headers=getattr(task.request, "headers", {}),
            job_id=job_id,
        ) as operation:
            settings = get_settings()
            database = Database(settings)
            try:
                outcome = await OptimizationExecutor(
                    settings=settings,
                    database=database,
                ).run(UUID(job_id))
                operation.set_outcome(outcome)
                return outcome
            finally:
                await database.dispose()

    try:
        return asyncio.run(execute())
    except RetryableOptimizationError as error:
        execution_token = error.execution_token
        max_retries = int(task.max_retries or 0)
        if task.request.retries >= max_retries:

            async def finalize() -> None:
                settings = get_settings()
                database = Database(settings)
                try:
                    await OptimizationExecutor(
                        settings=settings,
                        database=database,
                    ).finalize_failure(
                        UUID(job_id),
                        code="OPTIMIZATION_RETRY_EXHAUSTED",
                        user_message="这次优化没有成功，免费次数已退回。",
                        expected_execution_token=execution_token,
                    )
                finally:
                    await database.dispose()

            asyncio.run(finalize())
            return "FAILED_FINAL"
        raise task.retry(
            exc=error,
            countdown=min(60, 2 ** (task.request.retries + 2)),
        ) from error


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="ai_wardrobe.run_share_asset",
    max_retries=2,
    acks_late=True,
)
def run_share_asset(task: Any, *, job_id: str) -> str:
    async def execute() -> str:
        with WorkerSpan(
            name="celery run_share_asset",
            headers=getattr(task.request, "headers", {}),
            job_id=job_id,
        ) as operation:
            settings = get_settings()
            database = Database(settings)
            try:
                outcome = await ShareAssetExecutor(
                    settings=settings,
                    database=database,
                ).run(UUID(job_id))
                operation.set_outcome(outcome)
                return outcome
            finally:
                await database.dispose()

    try:
        return asyncio.run(execute())
    except RetryableShareError as error:
        execution_token = error.execution_token
        max_retries = int(task.max_retries or 0)
        if task.request.retries >= max_retries:

            async def finalize() -> None:
                settings = get_settings()
                database = Database(settings)
                try:
                    await ShareAssetExecutor(
                        settings=settings,
                        database=database,
                    ).finalize_failure(
                        UUID(job_id),
                        code="SHARE_RETRY_EXHAUSTED",
                        user_message="这次分享卡片没有生成成功，请重新创建。",
                        expected_execution_token=execution_token,
                    )
                finally:
                    await database.dispose()

            asyncio.run(finalize())
            return "FAILED_FINAL"
        raise task.retry(
            exc=error,
            countdown=min(30, 2 ** (task.request.retries + 1)),
        ) from error


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="ai_wardrobe.run_deletion",
    max_retries=3,
    acks_late=True,
)
def run_deletion(task: Any, *, job_id: str) -> str:
    async def execute() -> str:
        with WorkerSpan(
            name="celery run_deletion",
            headers=getattr(task.request, "headers", {}),
            job_id=job_id,
        ) as operation:
            settings = get_settings()
            database = Database(settings)
            try:
                outcome = await DeletionExecutor(
                    settings=settings,
                    database=database,
                ).run(UUID(job_id))
                operation.set_outcome(outcome)
                return outcome
            finally:
                await database.dispose()

    try:
        return asyncio.run(execute())
    except RetryableDeletionError as error:
        execution_token = error.execution_token
        max_retries = int(task.max_retries or 0)
        if task.request.retries >= max_retries:

            async def finalize() -> None:
                settings = get_settings()
                database = Database(settings)
                try:
                    await DeletionExecutor(
                        settings=settings,
                        database=database,
                    ).finalize_failure(
                        UUID(job_id),
                        code="DELETION_RETRY_EXHAUSTED",
                        expected_execution_token=execution_token,
                    )
                finally:
                    await database.dispose()

            asyncio.run(finalize())
            return "FAILED_FINAL"
        raise task.retry(
            exc=error,
            countdown=min(300, 2 ** (task.request.retries + 4)),
        ) from error
