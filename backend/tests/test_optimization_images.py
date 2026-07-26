import base64
from io import BytesIO

import pytest
from PIL import Image

from app.modules.optimization.images import (
    OptimizationImageError,
    comparison_data_url,
    validate_generated_image,
)


def jpeg(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_generated_image_requires_same_frame_as_source() -> None:
    metadata = validate_generated_image(
        jpeg(768, 1_152, (80, 90, 100)),
        source_width=1_000,
        source_height=1_500,
        max_bytes=10_000_000,
        max_pixels=10_000_000,
    )

    assert (metadata.width, metadata.height) == (768, 1_152)


def test_generated_image_rejects_material_aspect_ratio_change() -> None:
    with pytest.raises(OptimizationImageError, match="ASPECT_RATIO"):
        validate_generated_image(
            jpeg(1_024, 1_024, (80, 90, 100)),
            source_width=1_000,
            source_height=1_500,
            max_bytes=10_000_000,
            max_pixels=10_000_000,
        )


def test_comparison_image_places_before_and_after_side_by_side() -> None:
    data_url = comparison_data_url(
        jpeg(640, 960, (30, 60, 90)),
        jpeg(640, 960, (120, 100, 80)),
    )
    image_data = base64.b64decode(data_url.split(",", 1)[1])

    with Image.open(BytesIO(image_data)) as image:
        assert image.size == (1_296, 960)
