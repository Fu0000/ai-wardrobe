import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

import structlog
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from app.core.telemetry import record_ai_provider_attempt
from app.modules.ai.contracts import (
    AIProviderError,
    ImageEditProvider,
    ImageEditRequest,
    ImageEditResponse,
    ModelRoute,
    ProviderErrorCode,
    StructuredVisionProvider,
    StructuredVisionRequest,
    StructuredVisionResponse,
    TaskPolicy,
)

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    route: ModelRoute
    error_code: ProviderErrorCode


class AllProvidersFailedError(Exception):
    def __init__(self, attempts: tuple[ProviderAttempt, ...]) -> None:
        super().__init__("all configured AI providers failed")
        self.attempts = attempts


class InvocationObserver(Protocol):
    async def started(self, route: ModelRoute, attempt: int) -> UUID: ...

    async def succeeded(
        self,
        invocation_id: UUID,
        response: StructuredVisionResponse | ImageEditResponse,
        latency_ms: int,
    ) -> None: ...

    async def failed(
        self,
        invocation_id: UUID,
        error_code: ProviderErrorCode,
        latency_ms: int,
    ) -> None: ...


class NoopInvocationObserver:
    async def started(self, route: ModelRoute, attempt: int) -> UUID:
        return uuid4()

    async def succeeded(
        self,
        invocation_id: UUID,
        response: StructuredVisionResponse | ImageEditResponse,
        latency_ms: int,
    ) -> None:
        return None

    async def failed(
        self,
        invocation_id: UUID,
        error_code: ProviderErrorCode,
        latency_ms: int,
    ) -> None:
        return None


class AIGateway:
    def __init__(
        self,
        *,
        structured_vision_providers: tuple[StructuredVisionProvider, ...] = (),
        image_edit_providers: tuple[ImageEditProvider, ...] = (),
    ) -> None:
        self._structured_vision = self._index_providers(structured_vision_providers)
        self._image_edit = self._index_providers(image_edit_providers)

    @staticmethod
    def _index_providers[T](providers: tuple[T, ...]) -> dict[str, T]:
        indexed: dict[str, T] = {}
        for provider in providers:
            name = getattr(provider, "name", None)
            if not isinstance(name, str) or not name:
                raise ValueError("AI providers must expose a non-empty name")
            if name in indexed:
                raise ValueError(f"duplicate AI provider name: {name}")
            indexed[name] = provider
        return indexed

    async def structured_vision(
        self,
        *,
        request: StructuredVisionRequest,
        policy: TaskPolicy,
        observer: InvocationObserver | None = None,
    ) -> StructuredVisionResponse:
        attempts: list[ProviderAttempt] = []
        invocation_observer = observer or NoopInvocationObserver()

        for attempt, route in enumerate(policy.routes, start=1):
            provider = self._structured_vision.get(route.provider)
            if provider is None:
                record_ai_provider_attempt(
                    operation="structured_vision",
                    provider=route.provider,
                    model=route.model,
                    outcome=ProviderErrorCode.UNAVAILABLE.value,
                    duration_ms=0,
                )
                attempts.append(
                    ProviderAttempt(
                        route=route,
                        error_code=ProviderErrorCode.UNAVAILABLE,
                    )
                )
                continue

            invocation_id = await invocation_observer.started(route, attempt)
            started_at = perf_counter()
            try:
                with tracer.start_as_current_span(
                    "gen_ai structured_vision",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "gen_ai.operation.name": "structured_vision",
                        "gen_ai.provider.name": route.provider,
                        "gen_ai.request.model": route.model,
                        "aiw.provider.attempt": attempt,
                    },
                ) as span:
                    async with asyncio.timeout(policy.timeout_seconds):
                        response = await provider.analyze(
                            model=route.model,
                            request=request,
                            timeout_seconds=policy.timeout_seconds,
                            cost_ceiling_microunits=policy.cost_ceiling_microunits,
                        )
                    if response.usage.input_tokens is not None:
                        span.set_attribute(
                            "gen_ai.usage.input_tokens",
                            response.usage.input_tokens,
                        )
                    if response.usage.output_tokens is not None:
                        span.set_attribute(
                            "gen_ai.usage.output_tokens",
                            response.usage.output_tokens,
                        )
                latency_ms = round((perf_counter() - started_at) * 1_000)
                await invocation_observer.succeeded(
                    invocation_id,
                    response,
                    latency_ms,
                )
                record_ai_provider_attempt(
                    operation="structured_vision",
                    provider=route.provider,
                    model=route.model,
                    outcome="success",
                    duration_ms=latency_ms,
                )
                return response
            except TimeoutError:
                latency_ms = round((perf_counter() - started_at) * 1_000)
                await invocation_observer.failed(
                    invocation_id,
                    ProviderErrorCode.TIMEOUT,
                    latency_ms,
                )
                record_ai_provider_attempt(
                    operation="structured_vision",
                    provider=route.provider,
                    model=route.model,
                    outcome=ProviderErrorCode.TIMEOUT.value,
                    duration_ms=latency_ms,
                )
                attempts.append(
                    ProviderAttempt(
                        route=route,
                        error_code=ProviderErrorCode.TIMEOUT,
                    )
                )
                await logger.awarning(
                    "ai_provider_timeout",
                    provider=route.provider,
                    model=route.model,
                )
            except AIProviderError as error:
                latency_ms = round((perf_counter() - started_at) * 1_000)
                await invocation_observer.failed(
                    invocation_id,
                    error.code,
                    latency_ms,
                )
                record_ai_provider_attempt(
                    operation="structured_vision",
                    provider=route.provider,
                    model=route.model,
                    outcome=error.code.value,
                    duration_ms=latency_ms,
                )
                attempts.append(ProviderAttempt(route=route, error_code=error.code))
                await logger.awarning(
                    "ai_provider_error",
                    provider=route.provider,
                    model=route.model,
                    error_code=error.code,
                    retryable=error.retryable,
                )
                if not error.retryable:
                    break

        raise AllProvidersFailedError(tuple(attempts))

    async def image_edit(
        self,
        *,
        request: ImageEditRequest,
        policy: TaskPolicy,
        observer: InvocationObserver | None = None,
    ) -> ImageEditResponse:
        attempts: list[ProviderAttempt] = []
        invocation_observer = observer or NoopInvocationObserver()

        for attempt, route in enumerate(policy.routes, start=1):
            provider = self._image_edit.get(route.provider)
            if provider is None:
                record_ai_provider_attempt(
                    operation="image_edit",
                    provider=route.provider,
                    model=route.model,
                    outcome=ProviderErrorCode.UNAVAILABLE.value,
                    duration_ms=0,
                )
                attempts.append(
                    ProviderAttempt(
                        route=route,
                        error_code=ProviderErrorCode.UNAVAILABLE,
                    )
                )
                continue

            invocation_id = await invocation_observer.started(route, attempt)
            started_at = perf_counter()
            try:
                with tracer.start_as_current_span(
                    "gen_ai image_edit",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "gen_ai.operation.name": "image_edit",
                        "gen_ai.provider.name": route.provider,
                        "gen_ai.request.model": route.model,
                        "aiw.provider.attempt": attempt,
                    },
                ):
                    async with asyncio.timeout(policy.timeout_seconds):
                        response = await provider.edit(
                            model=route.model,
                            request=request,
                            timeout_seconds=policy.timeout_seconds,
                            cost_ceiling_microunits=policy.cost_ceiling_microunits,
                        )
                latency_ms = round((perf_counter() - started_at) * 1_000)
                await invocation_observer.succeeded(
                    invocation_id,
                    response,
                    latency_ms,
                )
                record_ai_provider_attempt(
                    operation="image_edit",
                    provider=route.provider,
                    model=route.model,
                    outcome="success",
                    duration_ms=latency_ms,
                )
                return response
            except TimeoutError:
                latency_ms = round((perf_counter() - started_at) * 1_000)
                await invocation_observer.failed(
                    invocation_id,
                    ProviderErrorCode.TIMEOUT,
                    latency_ms,
                )
                record_ai_provider_attempt(
                    operation="image_edit",
                    provider=route.provider,
                    model=route.model,
                    outcome=ProviderErrorCode.TIMEOUT.value,
                    duration_ms=latency_ms,
                )
                attempts.append(
                    ProviderAttempt(
                        route=route,
                        error_code=ProviderErrorCode.TIMEOUT,
                    )
                )
            except AIProviderError as error:
                latency_ms = round((perf_counter() - started_at) * 1_000)
                await invocation_observer.failed(
                    invocation_id,
                    error.code,
                    latency_ms,
                )
                record_ai_provider_attempt(
                    operation="image_edit",
                    provider=route.provider,
                    model=route.model,
                    outcome=error.code.value,
                    duration_ms=latency_ms,
                )
                attempts.append(ProviderAttempt(route=route, error_code=error.code))
                if not error.retryable:
                    break

        raise AllProvidersFailedError(tuple(attempts))
