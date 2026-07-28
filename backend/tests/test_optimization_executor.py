from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.database.session import Database
from app.modules.ai.contracts import (
    ProviderUsage,
    StructuredVisionResponse,
)
from app.modules.ai.gateway import AIGateway
from app.modules.ai.policies import optimization_critic_policy
from app.modules.jobs.invocations import DatabaseInvocationObserver
from app.modules.optimization.executor import (
    GeneratedCandidate,
    InvalidCriticResponseError,
    OptimizationExecutor,
    OptimizationGateways,
    PreparedOptimization,
    SourceImageMetadata,
)
from app.modules.optimization.images import (
    GeneratedImageMetadata,
    OptimizationImageError,
)
from app.modules.optimization.repository import OptimizationRecord
from app.modules.optimization.schema import build_change_budget_plan


def critic_payload() -> dict[str, object]:
    check = {
        "passed": True,
        "confidence": "HIGH",
        "evidence": "左右人物、背景和未修改区域保持一致。",
    }
    return {
        "identity_preserved": check,
        "pose_and_composition_preserved": check,
        "background_and_lighting_preserved": check,
        "unmentioned_garments_preserved": check,
        "requested_changes_only": check,
        "artifacts": [],
        "overall_pass": True,
        "summary": "仅执行了批准的最小修改，其他区域保持稳定。",
    }


def prepared_optimization() -> PreparedOptimization:
    plan = build_change_budget_plan(
        [
            {
                "priority": 1,
                "action": "ADJUST_WEARING",
                "instruction": "调整上衣下摆的穿着方式。",
                "reason": "这是改善整体比例所需的最少修改。",
                "preserves": "人物身份与未提及衣物",
            }
        ]
    )
    return PreparedOptimization(
        record=cast(OptimizationRecord, MagicMock()),
        source_image=b"source-image",
        plan=plan,
        execution_token="execution-token",
    )


def executor() -> OptimizationExecutor:
    return OptimizationExecutor(
        settings=Settings(environment="test"),
        database=cast(Database, MagicMock()),
    )


@pytest.mark.asyncio
async def test_critic_review_uses_the_validated_response_model() -> None:
    invalid_response = StructuredVisionResponse(
        output={},
        provider="openai",
        model="invalid-model",
        usage=ProviderUsage(),
    )
    valid_response = StructuredVisionResponse(
        output=critic_payload(),
        provider="openai",
        model="validated-model",
        usage=ProviderUsage(),
    )
    gateway_mock = MagicMock(spec=AIGateway)
    gateway_mock.structured_vision = AsyncMock(
        side_effect=[invalid_response, valid_response],
    )
    candidate = GeneratedCandidate(
        image_bytes=b"candidate-image",
        metadata=GeneratedImageMetadata(
            width=1024,
            height=1536,
            content_type="image/jpeg",
        ),
        image_model="image-model",
    )

    with patch(
        "app.modules.optimization.executor.comparison_data_url",
        return_value="data:image/jpeg;base64,test",
    ):
        review = await executor()._review_candidate(
            prepared=prepared_optimization(),
            candidate=candidate,
            gateway=cast(AIGateway, gateway_mock),
            policy=optimization_critic_policy(Settings(environment="test")),
            observer=cast(DatabaseInvocationObserver, MagicMock()),
        )

    assert review.output.overall_pass is True
    assert review.model == "validated-model"
    assert gateway_mock.structured_vision.await_count == 2


@pytest.mark.asyncio
async def test_critic_review_rejects_two_invalid_responses() -> None:
    invalid_response = StructuredVisionResponse(
        output={},
        provider="openai",
        model="invalid-model",
        usage=ProviderUsage(),
    )
    gateway_mock = MagicMock(spec=AIGateway)
    gateway_mock.structured_vision = AsyncMock(return_value=invalid_response)
    candidate = GeneratedCandidate(
        image_bytes=b"candidate-image",
        metadata=GeneratedImageMetadata(
            width=1024,
            height=1536,
            content_type="image/jpeg",
        ),
        image_model="image-model",
    )

    with (
        patch(
            "app.modules.optimization.executor.comparison_data_url",
            return_value="data:image/jpeg;base64,test",
        ),
        pytest.raises(InvalidCriticResponseError),
    ):
        await executor()._review_candidate(
            prepared=prepared_optimization(),
            candidate=candidate,
            gateway=cast(AIGateway, gateway_mock),
            policy=optimization_critic_policy(Settings(environment="test")),
            observer=cast(DatabaseInvocationObserver, MagicMock()),
        )

    assert gateway_mock.structured_vision.await_count == 2


@pytest.mark.asyncio
async def test_invalid_generated_images_are_reported_before_rejection() -> None:
    optimization_executor = executor()
    prepared = prepared_optimization()
    job_id = uuid4()
    reports: list[dict[str, object]] = []
    generate_mock = AsyncMock(
        side_effect=[
            OptimizationImageError("GENERATED_IMAGE_INVALID"),
            OptimizationImageError("GENERATED_IMAGE_ASPECT_RATIO_CHANGED"),
        ]
    )
    reject_mock = AsyncMock(return_value="REJECTED_BY_CRITIC")

    with (
        patch.object(
            optimization_executor,
            "_generate_candidate",
            new=generate_mock,
        ),
        patch.object(
            optimization_executor,
            "_reject_candidates",
            new=reject_mock,
        ),
    ):
        outcome = await optimization_executor._run_generation_attempts(
            job_id=job_id,
            prepared=prepared,
            gateways=cast(OptimizationGateways, MagicMock()),
            source_metadata=cast(SourceImageMetadata, MagicMock()),
            image_policy=optimization_critic_policy(Settings(environment="test")),
            critic_policy=optimization_critic_policy(Settings(environment="test")),
            max_generation_attempts=2,
            image_observer=cast(DatabaseInvocationObserver, MagicMock()),
            critic_observer=cast(DatabaseInvocationObserver, MagicMock()),
            reports=reports,
        )

    assert outcome == "REJECTED_BY_CRITIC"
    assert reports == [
        {
            "generation_attempt": 1,
            "overall_pass": False,
            "failure_code": "GENERATED_IMAGE_INVALID",
        },
        {
            "generation_attempt": 2,
            "overall_pass": False,
            "failure_code": "GENERATED_IMAGE_ASPECT_RATIO_CHANGED",
        },
    ]
    reject_mock.assert_awaited_once_with(
        job_id=job_id,
        prepared=prepared,
        reports=reports,
    )


@pytest.mark.asyncio
async def test_programming_error_is_finalized_and_reraised() -> None:
    optimization_executor = executor()
    job_id = uuid4()
    prepared = prepared_optimization()
    gateways_mock = MagicMock()
    gateways_mock.close = AsyncMock()
    prepare_mock = AsyncMock(return_value=prepared)
    run_prepared_mock = AsyncMock(side_effect=TypeError("programming defect"))
    finalize_mock = AsyncMock(return_value=True)
    mark_retryable_mock = AsyncMock(return_value=True)

    with (
        patch.object(optimization_executor, "_prepare", new=prepare_mock),
        patch.object(
            optimization_executor,
            "_gateways",
            return_value=cast(OptimizationGateways, gateways_mock),
        ),
        patch.object(
            optimization_executor,
            "_run_prepared",
            new=run_prepared_mock,
        ),
        patch.object(
            optimization_executor,
            "finalize_failure",
            new=finalize_mock,
        ),
        patch.object(
            optimization_executor,
            "_mark_retryable",
            new=mark_retryable_mock,
        ),
        pytest.raises(TypeError, match="programming defect"),
    ):
        await optimization_executor.run(job_id)

    finalize_mock.assert_awaited_once_with(
        job_id,
        code="OPTIMIZATION_INTERNAL_ERROR",
        user_message="优化任务遇到内部错误，这次免费次数已退回。",
        expected_execution_token=prepared.execution_token,
    )
    mark_retryable_mock.assert_not_awaited()
    gateways_mock.close.assert_awaited_once_with()
