import base64
import binascii
import math
from typing import Any, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    RateLimitError,
)

from app.modules.ai.contracts import (
    AIProviderError,
    ImageEditRequest,
    ImageEditResponse,
    ProviderErrorCode,
    ProviderUsage,
)

GPT_IMAGE_2_PRICING_MICROUNITS_PER_TOKEN = {
    "input_image": 8.0,
    "input_text": 5.0,
    "output_image": 30.0,
}
TARGET_MIN_OUTPUT_PIXELS = 1_048_576
TARGET_MAX_OUTPUT_PIXELS = 2_097_152
GPT_IMAGE_MAX_ASPECT_RATIO = 3


def normalized_image_output_size(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        raise ValueError("source image dimensions must be positive")
    long_edge, short_edge = max(width, height), min(width, height)
    if long_edge / short_edge > GPT_IMAGE_MAX_ASPECT_RATIO:
        raise ValueError("source image aspect ratio exceeds the provider limit")

    source_pixels = width * height
    target_pixels = min(
        max(source_pixels, TARGET_MIN_OUTPUT_PIXELS),
        TARGET_MAX_OUTPUT_PIXELS,
    )
    scale = math.sqrt(target_pixels / source_pixels)
    output_width = max(16, round((width * scale) / 16) * 16)
    output_height = max(16, round((height * scale) / 16) * 16)

    output_long, output_short = (
        max(output_width, output_height),
        min(
            output_width,
            output_height,
        ),
    )
    if output_long / output_short > GPT_IMAGE_MAX_ASPECT_RATIO:
        output_short += 16
        if output_width < output_height:
            output_width = output_short
        else:
            output_height = output_short
    return f"{output_width}x{output_height}"


def estimate_image_edit_cost(
    *,
    input_image_tokens: int,
    input_text_tokens: int,
    output_image_tokens: int,
) -> int:
    return round(
        input_image_tokens * GPT_IMAGE_2_PRICING_MICROUNITS_PER_TOKEN["input_image"]
        + input_text_tokens * GPT_IMAGE_2_PRICING_MICROUNITS_PER_TOKEN["input_text"]
        + output_image_tokens * GPT_IMAGE_2_PRICING_MICROUNITS_PER_TOKEN["output_image"]
    )


class OpenAIImageEditProvider:
    name = "openai-image"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        max_output_bytes: int,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
        )
        self._max_output_bytes = max_output_bytes

    async def edit(
        self,
        *,
        model: str,
        request: ImageEditRequest,
        timeout_seconds: float,
        cost_ceiling_microunits: int,
    ) -> ImageEditResponse:
        extension = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }.get(request.source_content_type)
        if extension is None:
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_INPUT,
                message="unsupported source image type",
                retryable=False,
            )
        try:
            output_size = normalized_image_output_size(
                request.source_width,
                request.source_height,
            )
        except ValueError as error:
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_INPUT,
                message="source image dimensions are not supported",
                retryable=False,
            ) from error
        try:
            response = await self._client.images.edit(
                model=model,
                image=cast(
                    Any,
                    (
                        f"source.{extension}",
                        request.source_image,
                        request.source_content_type,
                    ),
                ),
                prompt=request.prompt,
                size=output_size,
                quality="medium",
                response_format="b64_json",
                output_format="jpeg",
                output_compression=88,
                background="opaque",
                n=1,
                timeout=timeout_seconds,
            )
        except (APITimeoutError, TimeoutError) as error:
            raise AIProviderError(
                code=ProviderErrorCode.TIMEOUT,
                message="image provider timed out",
                retryable=True,
            ) from error
        except RateLimitError as error:
            raise AIProviderError(
                code=ProviderErrorCode.RATE_LIMIT,
                message="image provider rate limited the request",
                retryable=True,
            ) from error
        except APIConnectionError as error:
            raise AIProviderError(
                code=ProviderErrorCode.UNAVAILABLE,
                message="image provider is unavailable",
                retryable=True,
            ) from error
        except BadRequestError as error:
            code = (
                ProviderErrorCode.CONTENT_POLICY
                if "safety" in str(error).lower()
                else ProviderErrorCode.INVALID_INPUT
            )
            raise AIProviderError(
                code=code,
                message="image provider rejected the input",
                retryable=False,
            ) from error
        except APIStatusError as error:
            raise AIProviderError(
                code=ProviderErrorCode.UNAVAILABLE,
                message="image provider returned an error",
                retryable=error.status_code >= 500,
            ) from error

        if not response.data or not response.data[0].b64_json:
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                message="image provider returned no image",
                retryable=True,
            )
        try:
            image_bytes = base64.b64decode(
                response.data[0].b64_json,
                validate=True,
            )
        except (binascii.Error, ValueError) as error:
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                message="image provider returned invalid image data",
                retryable=True,
            ) from error
        if not image_bytes or len(image_bytes) > self._max_output_bytes:
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                message="image provider output size is invalid",
                retryable=True,
            )

        usage = response.usage
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        estimated_cost: int | None = None
        if usage is not None:
            estimated_cost = estimate_image_edit_cost(
                input_image_tokens=usage.input_tokens_details.image_tokens,
                input_text_tokens=usage.input_tokens_details.text_tokens,
                output_image_tokens=(
                    usage.output_tokens_details.image_tokens
                    if usage.output_tokens_details
                    else usage.output_tokens
                ),
            )
        if estimated_cost is not None and estimated_cost > cost_ceiling_microunits:
            raise AIProviderError(
                code=ProviderErrorCode.COST_LIMIT,
                message="image edit exceeded the cost ceiling",
                retryable=False,
            )
        return ImageEditResponse(
            image_bytes=image_bytes,
            content_type="image/jpeg",
            provider=self.name,
            model=model,
            usage=ProviderUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_microunits=estimated_cost,
            ),
            provider_request_id=None,
        )

    async def close(self) -> None:
        await self._client.close()
