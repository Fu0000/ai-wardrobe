from io import BytesIO

import pytest
from PIL import Image

from app.modules.ai.contracts import (
    AIProviderError,
    ImageEditRequest,
    ProviderErrorCode,
    StructuredVisionRequest,
)
from app.modules.ai.local_provider import (
    LOCAL_CRITIC_MODEL,
    LOCAL_DIAGNOSIS_MODEL,
    LOCAL_IMAGE_MODEL,
    LocalImageEditProvider,
    LocalStructuredVisionProvider,
)
from app.modules.diagnosis.prompt import STYLE_DIAGNOSIS_PROMPT
from app.modules.diagnosis.schema import DiagnosisOutput
from app.modules.optimization.images import validate_generated_image
from app.modules.optimization.prompt import OPTIMIZATION_CRITIC_PROMPT
from app.modules.optimization.schema import OptimizationCriticOutput


def source_image(*, width: int = 400, height: int = 600) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(92, 108, 126)).save(
        output,
        format="PNG",
    )
    return output.getvalue()


async def test_local_structured_provider_returns_valid_diagnosis() -> None:
    provider = LocalStructuredVisionProvider()

    response = await provider.analyze(
        model=LOCAL_DIAGNOSIS_MODEL,
        request=StructuredVisionRequest(
            image_url="http://test/signed-image",
            prompt=STYLE_DIAGNOSIS_PROMPT.instructions,
            output_schema=STYLE_DIAGNOSIS_PROMPT.output_schema(),
        ),
        timeout_seconds=1,
        cost_ceiling_microunits=0,
    )
    output = DiagnosisOutput.model_validate(response.output)

    assert output.score == 82
    assert output.optimization_plan[0].action == "ADJUST_WEARING"
    assert response.provider == "local-demo"
    assert response.usage.estimated_cost_microunits == 0


async def test_local_structured_provider_returns_valid_critic_result() -> None:
    provider = LocalStructuredVisionProvider()

    response = await provider.analyze(
        model=LOCAL_CRITIC_MODEL,
        request=StructuredVisionRequest(
            image_url="data:image/jpeg;base64,test",
            prompt=OPTIMIZATION_CRITIC_PROMPT.instructions(),
            output_schema=OPTIMIZATION_CRITIC_PROMPT.output_schema(),
        ),
        timeout_seconds=1,
        cost_ceiling_microunits=0,
    )
    output = OptimizationCriticOutput.model_validate(response.output)

    assert output.overall_pass is True
    assert output.artifacts == []
    assert response.provider == "local-demo"


async def test_local_structured_provider_rejects_unknown_schema() -> None:
    provider = LocalStructuredVisionProvider()

    with pytest.raises(AIProviderError) as captured:
        await provider.analyze(
            model="unknown",
            request=StructuredVisionRequest(
                image_url="http://test/image",
                prompt="unsupported",
                output_schema={"type": "object"},
            ),
            timeout_seconds=1,
            cost_ceiling_microunits=0,
        )

    assert captured.value.code == ProviderErrorCode.INVALID_INPUT
    assert captured.value.retryable is False


async def test_local_image_provider_returns_a_safe_jpeg_with_same_aspect_ratio() -> None:
    provider = LocalImageEditProvider(max_output_bytes=2 * 1024 * 1024)
    image_bytes = source_image()

    response = await provider.edit(
        model=LOCAL_IMAGE_MODEL,
        request=ImageEditRequest(
            source_image=image_bytes,
            source_content_type="image/png",
            source_width=400,
            source_height=600,
            prompt="local flow test",
        ),
        timeout_seconds=1,
        cost_ceiling_microunits=0,
    )
    metadata = validate_generated_image(
        response.image_bytes,
        source_width=400,
        source_height=600,
        max_bytes=2 * 1024 * 1024,
        max_pixels=40_000_000,
    )

    assert metadata.content_type == "image/jpeg"
    assert metadata.width == 512
    assert metadata.height == 768
    assert response.provider == "local-demo-image"
    assert response.usage.estimated_cost_microunits == 0


@pytest.mark.parametrize(
    ("content_type", "width", "height"),
    [
        ("application/octet-stream", 400, 600),
        ("image/png", 401, 600),
    ],
)
async def test_local_image_provider_rejects_invalid_metadata(
    content_type: str,
    width: int,
    height: int,
) -> None:
    provider = LocalImageEditProvider(max_output_bytes=2 * 1024 * 1024)

    with pytest.raises(AIProviderError) as captured:
        await provider.edit(
            model=LOCAL_IMAGE_MODEL,
            request=ImageEditRequest(
                source_image=source_image(),
                source_content_type=content_type,
                source_width=width,
                source_height=height,
                prompt="local flow test",
            ),
            timeout_seconds=1,
            cost_ceiling_microunits=0,
        )

    assert captured.value.code == ProviderErrorCode.INVALID_INPUT
