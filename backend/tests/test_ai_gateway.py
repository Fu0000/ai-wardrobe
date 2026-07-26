import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.modules.ai.contracts import (
    AIProviderError,
    ImageEditResponse,
    ModelRoute,
    ProviderErrorCode,
    ProviderUsage,
    StructuredVisionRequest,
    StructuredVisionResponse,
    TaskPolicy,
)
from app.modules.ai.gateway import AIGateway, AllProvidersFailedError


@dataclass
class StubVisionProvider:
    name: str
    response: StructuredVisionResponse | None = None
    error: AIProviderError | None = None
    delay_seconds: float = 0
    call_count: int = 0

    async def analyze(
        self,
        *,
        model: str,
        request: StructuredVisionRequest,
        timeout_seconds: float,
        cost_ceiling_microunits: int,
    ) -> StructuredVisionResponse:
        self.call_count += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error:
            raise self.error
        if self.response is None:
            raise AssertionError("stub provider has no response")
        return self.response


@dataclass
class RecordingObserver:
    started_routes: list[tuple[str, int]]
    succeeded_ids: list[UUID]
    failed_codes: list[ProviderErrorCode]

    async def started(self, route: ModelRoute, attempt: int) -> UUID:
        self.started_routes.append((route.provider, attempt))
        return uuid4()

    async def succeeded(
        self,
        invocation_id: UUID,
        result: StructuredVisionResponse | ImageEditResponse,
        latency_ms: int,
    ) -> None:
        self.succeeded_ids.append(invocation_id)

    async def failed(
        self,
        invocation_id: UUID,
        error_code: ProviderErrorCode,
        latency_ms: int,
    ) -> None:
        self.failed_codes.append(error_code)


def policy(*provider_names: str, timeout_seconds: float = 1) -> TaskPolicy:
    return TaskPolicy(
        routes=tuple(ModelRoute(name, f"{name}-model") for name in provider_names),
        timeout_seconds=timeout_seconds,
        cost_ceiling_microunits=100_000,
        quality_threshold=0.8,
    )


def request() -> StructuredVisionRequest:
    return StructuredVisionRequest(
        image_url="https://private.example/signed",
        prompt="Analyze untrusted visual content.",
        output_schema={"type": "object"},
    )


def response(provider: str) -> StructuredVisionResponse:
    return StructuredVisionResponse(
        output={"score": 82},
        provider=provider,
        model=f"{provider}-model",
        usage=ProviderUsage(estimated_cost_microunits=3_000),
    )


async def test_primary_provider_returns_without_calling_fallback() -> None:
    primary = StubVisionProvider(name="primary", response=response("primary"))
    fallback = StubVisionProvider(name="fallback", response=response("fallback"))
    gateway = AIGateway(structured_vision_providers=(primary, fallback))

    result = await gateway.structured_vision(
        request=request(),
        policy=policy("primary", "fallback"),
    )

    assert result.provider == "primary"
    assert primary.call_count == 1
    assert fallback.call_count == 0


async def test_retryable_error_routes_to_fallback() -> None:
    primary = StubVisionProvider(
        name="primary",
        error=AIProviderError(
            code=ProviderErrorCode.RATE_LIMIT,
            message="rate limited",
            retryable=True,
        ),
    )
    fallback = StubVisionProvider(name="fallback", response=response("fallback"))
    gateway = AIGateway(structured_vision_providers=(primary, fallback))
    observer = RecordingObserver([], [], [])

    result = await gateway.structured_vision(
        request=request(),
        policy=policy("primary", "fallback"),
        observer=observer,
    )

    assert result.provider == "fallback"
    assert fallback.call_count == 1
    assert observer.started_routes == [("primary", 1), ("fallback", 2)]
    assert observer.failed_codes == [ProviderErrorCode.RATE_LIMIT]
    assert len(observer.succeeded_ids) == 1


async def test_terminal_input_error_does_not_waste_fallback_call() -> None:
    primary = StubVisionProvider(
        name="primary",
        error=AIProviderError(
            code=ProviderErrorCode.INVALID_INPUT,
            message="invalid image",
            retryable=False,
        ),
    )
    fallback = StubVisionProvider(name="fallback", response=response("fallback"))
    gateway = AIGateway(structured_vision_providers=(primary, fallback))

    with pytest.raises(AllProvidersFailedError) as captured:
        await gateway.structured_vision(
            request=request(),
            policy=policy("primary", "fallback"),
        )

    assert captured.value.attempts[0].error_code == ProviderErrorCode.INVALID_INPUT
    assert fallback.call_count == 0


async def test_timeout_routes_to_fallback() -> None:
    primary = StubVisionProvider(
        name="primary",
        response=response("primary"),
        delay_seconds=0.05,
    )
    fallback = StubVisionProvider(name="fallback", response=response("fallback"))
    gateway = AIGateway(structured_vision_providers=(primary, fallback))

    result = await gateway.structured_vision(
        request=request(),
        policy=policy("primary", "fallback", timeout_seconds=0.01),
    )

    assert result.provider == "fallback"


def test_task_policy_rejects_invalid_safety_limits() -> None:
    with pytest.raises(ValueError, match="at least one"):
        TaskPolicy(
            routes=(),
            timeout_seconds=10,
            cost_ceiling_microunits=10,
            quality_threshold=0.8,
        )
