import json
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
    ProviderErrorCode,
    ProviderUsage,
    StructuredVisionRequest,
    StructuredVisionResponse,
)

MODEL_PRICING_MICROUNITS_PER_TOKEN: dict[str, tuple[float, float]] = {
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (1.0, 6.0),
}


def estimate_cost(
    model: str,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
) -> int | None:
    pricing = MODEL_PRICING_MICROUNITS_PER_TOKEN.get(model)
    if pricing is None or input_tokens is None or output_tokens is None:
        return None
    input_price, output_price = pricing
    return round(input_tokens * input_price + output_tokens * output_price)


class OpenAIStructuredVisionProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
        )

    async def analyze(
        self,
        *,
        model: str,
        request: StructuredVisionRequest,
        timeout_seconds: float,
        cost_ceiling_microunits: int,
    ) -> StructuredVisionResponse:
        payload: dict[str, object] = {
            "model": model,
            "instructions": request.prompt,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": request.metadata.get(
                                "user_context",
                                "请分析这张穿搭照片。",
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": request.image_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "style_diagnosis",
                    "strict": True,
                    "schema": request.output_schema,
                }
            },
            "reasoning": {"effort": "low"},
            "max_output_tokens": 1_600,
            "store": False,
        }
        try:
            response = await self._client.responses.create(
                timeout=timeout_seconds,
                **cast(Any, payload),
            )
        except (APITimeoutError, TimeoutError) as error:
            raise AIProviderError(
                code=ProviderErrorCode.TIMEOUT,
                message="provider timed out",
                retryable=True,
            ) from error
        except RateLimitError as error:
            raise AIProviderError(
                code=ProviderErrorCode.RATE_LIMIT,
                message="provider rate limited the request",
                retryable=True,
            ) from error
        except APIConnectionError as error:
            raise AIProviderError(
                code=ProviderErrorCode.UNAVAILABLE,
                message="provider is unavailable",
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
                message="provider rejected the input",
                retryable=False,
            ) from error
        except APIStatusError as error:
            raise AIProviderError(
                code=ProviderErrorCode.UNAVAILABLE,
                message="provider returned an error",
                retryable=error.status_code >= 500,
            ) from error

        try:
            output = json.loads(response.output_text)
        except (json.JSONDecodeError, TypeError) as error:
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                message="provider returned invalid structured output",
                retryable=True,
            ) from error
        if not isinstance(output, dict):
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                message="provider output must be an object",
                retryable=True,
            )

        usage = response.usage
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        estimated_cost = estimate_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if estimated_cost is not None and estimated_cost > cost_ceiling_microunits:
            raise AIProviderError(
                code=ProviderErrorCode.COST_LIMIT,
                message="provider response exceeded the cost ceiling",
                retryable=False,
            )
        return StructuredVisionResponse(
            output=cast(dict[str, object], output),
            provider=self.name,
            model=model,
            usage=ProviderUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_microunits=estimated_cost,
            ),
            provider_request_id=response.id,
        )

    async def close(self) -> None:
        await self._client.close()
