from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class AITask(StrEnum):
    STYLE_DIAGNOSIS = "STYLE_DIAGNOSIS"
    STYLE_OPTIMIZATION = "STYLE_OPTIMIZATION"


class ProviderErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    CONTENT_POLICY = "CONTENT_POLICY"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    COST_LIMIT = "COST_LIMIT"


@dataclass(frozen=True, slots=True)
class ModelRoute:
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    routes: tuple[ModelRoute, ...]
    timeout_seconds: float
    cost_ceiling_microunits: int
    quality_threshold: float

    def __post_init__(self) -> None:
        if not self.routes:
            raise ValueError("at least one model route is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.cost_ceiling_microunits < 0:
            raise ValueError("cost ceiling cannot be negative")
        if not 0 <= self.quality_threshold <= 1:
            raise ValueError("quality threshold must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class StructuredVisionRequest:
    image_url: str
    prompt: str
    output_schema: dict[str, object]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImageEditRequest:
    source_image: bytes
    source_content_type: str
    source_width: int
    source_height: int
    prompt: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_microunits: int | None = None


@dataclass(frozen=True, slots=True)
class StructuredVisionResponse:
    output: dict[str, object]
    provider: str
    model: str
    usage: ProviderUsage
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ImageEditResponse:
    image_bytes: bytes
    content_type: str
    provider: str
    model: str
    usage: ProviderUsage
    provider_request_id: str | None = None


class AIProviderError(Exception):
    def __init__(
        self,
        *,
        code: ProviderErrorCode,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class StructuredVisionProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def analyze(
        self,
        *,
        model: str,
        request: StructuredVisionRequest,
        timeout_seconds: float,
        cost_ceiling_microunits: int,
    ) -> StructuredVisionResponse: ...


class ImageEditProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def edit(
        self,
        *,
        model: str,
        request: ImageEditRequest,
        timeout_seconds: float,
        cost_ceiling_microunits: int,
    ) -> ImageEditResponse: ...
