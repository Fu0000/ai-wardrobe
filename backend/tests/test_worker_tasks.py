from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from celery.exceptions import Retry

from app.modules.diagnosis.executor import RetryableDiagnosisError
from app.modules.governance.deletion_executor import RetryableDeletionError
from app.modules.growth.executor import RetryableShareError
from app.modules.optimization.executor import RetryableOptimizationError
from app.worker.celery_app import celery_app
from app.worker.tasks import (
    run_deletion,
    run_share_asset,
    run_style_diagnosis,
    run_style_optimization,
)


@dataclass(frozen=True, slots=True)
class WorkerTaskCase:
    task: Any
    executor_patch: str
    retry_error: Callable[[str, str], Exception]
    max_retries: int
    first_countdown: int
    final_code: str
    final_user_message: str | None


WORKER_TASK_CASES = (
    WorkerTaskCase(
        task=run_style_diagnosis,
        executor_patch="app.worker.tasks.DiagnosisExecutor",
        retry_error=RetryableDiagnosisError,
        max_retries=2,
        first_countdown=2,
        final_code="DIAGNOSIS_RETRY_EXHAUSTED",
        final_user_message="这次诊断没有成功，免费次数已退回。",
    ),
    WorkerTaskCase(
        task=run_style_optimization,
        executor_patch="app.worker.tasks.OptimizationExecutor",
        retry_error=RetryableOptimizationError,
        max_retries=2,
        first_countdown=4,
        final_code="OPTIMIZATION_RETRY_EXHAUSTED",
        final_user_message="这次优化没有成功，免费次数已退回。",
    ),
    WorkerTaskCase(
        task=run_share_asset,
        executor_patch="app.worker.tasks.ShareAssetExecutor",
        retry_error=RetryableShareError,
        max_retries=2,
        first_countdown=2,
        final_code="SHARE_RETRY_EXHAUSTED",
        final_user_message="这次分享卡片没有生成成功，请重新创建。",
    ),
    WorkerTaskCase(
        task=run_deletion,
        executor_patch="app.worker.tasks.DeletionExecutor",
        retry_error=RetryableDeletionError,
        max_retries=3,
        first_countdown=16,
        final_code="DELETION_RETRY_EXHAUSTED",
        final_user_message=None,
    ),
)


@pytest.mark.parametrize("case", WORKER_TASK_CASES)
def test_retryable_worker_failure_is_retried_before_exhaustion(
    case: WorkerTaskCase,
) -> None:
    job_id = uuid4()
    execution_token = str(uuid4())
    retry_error = case.retry_error("TRANSIENT_FAILURE", execution_token)
    executor = MagicMock()
    executor.run = AsyncMock(side_effect=retry_error)
    executor.finalize_failure = AsyncMock(return_value=True)
    database = MagicMock()
    database.dispose = AsyncMock()

    with (
        patch("app.worker.tasks.get_settings", return_value=MagicMock()),
        patch("app.worker.tasks.Database", return_value=database),
        patch(case.executor_patch, return_value=executor),
        pytest.raises(Retry) as captured,
    ):
        case.task.apply(
            kwargs={"job_id": str(job_id)},
            retries=0,
            throw=True,
        )

    assert captured.value.exc is retry_error
    assert captured.value.when == case.first_countdown
    executor.run.assert_awaited_once_with(job_id)
    executor.finalize_failure.assert_not_awaited()
    database.dispose.assert_awaited_once()


@pytest.mark.parametrize("case", WORKER_TASK_CASES)
def test_retry_exhaustion_finalizes_with_the_same_execution_token(
    case: WorkerTaskCase,
) -> None:
    job_id = uuid4()
    execution_token = str(uuid4())
    executor = MagicMock()
    executor.run = AsyncMock(side_effect=case.retry_error("TRANSIENT_FAILURE", execution_token))
    executor.finalize_failure = AsyncMock(return_value=True)
    database = MagicMock()
    database.dispose = AsyncMock()

    with (
        patch("app.worker.tasks.get_settings", return_value=MagicMock()),
        patch("app.worker.tasks.Database", return_value=database),
        patch(case.executor_patch, return_value=executor),
    ):
        result = case.task.apply(
            kwargs={"job_id": str(job_id)},
            retries=case.max_retries,
            throw=True,
        )

    expected_arguments: dict[str, object] = {
        "code": case.final_code,
        "expected_execution_token": execution_token,
    }
    if case.final_user_message is not None:
        expected_arguments["user_message"] = case.final_user_message

    assert result.get() == "FAILED_FINAL"
    executor.run.assert_awaited_once_with(job_id)
    executor.finalize_failure.assert_awaited_once_with(
        job_id,
        **expected_arguments,
    )
    assert database.dispose.await_count == 2


def test_worker_delivery_and_queue_configuration_is_loss_resistant() -> None:
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1

    routes = celery_app.conf.task_routes
    assert routes["ai_wardrobe.run_style_diagnosis"]["queue"] == "ai_fast"
    assert routes["ai_wardrobe.run_style_optimization"]["queue"] == "image_generation"
    assert routes["ai_wardrobe.run_share_asset"]["queue"] == "media_generation"
    assert routes["ai_wardrobe.run_deletion"]["queue"] == "maintenance"
