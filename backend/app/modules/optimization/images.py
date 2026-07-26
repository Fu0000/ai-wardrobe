import base64
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


class OptimizationImageError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GeneratedImageMetadata:
    width: int
    height: int
    content_type: str


def validate_generated_image(
    data: bytes,
    *,
    source_width: int,
    source_height: int,
    max_bytes: int,
    max_pixels: int,
) -> GeneratedImageMetadata:
    if not data or len(data) > max_bytes:
        raise OptimizationImageError("GENERATED_IMAGE_SIZE_INVALID")
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError) as error:
        raise OptimizationImageError("GENERATED_IMAGE_INVALID") from error
    if image_format != "JPEG":
        raise OptimizationImageError("GENERATED_IMAGE_TYPE_INVALID")
    if width < 512 or height < 512 or width * height > max_pixels:
        raise OptimizationImageError("GENERATED_IMAGE_DIMENSIONS_INVALID")
    source_ratio = source_width / source_height
    result_ratio = width / height
    if abs(source_ratio - result_ratio) / source_ratio > 0.03:
        raise OptimizationImageError("GENERATED_IMAGE_ASPECT_RATIO_CHANGED")
    return GeneratedImageMetadata(
        width=width,
        height=height,
        content_type="image/jpeg",
    )


def comparison_data_url(before: bytes, after: bytes) -> str:
    try:
        with Image.open(BytesIO(before)) as before_image:
            before_rgb = ImageOps.exif_transpose(before_image).convert("RGB")
        with Image.open(BytesIO(after)) as after_image:
            after_rgb = ImageOps.exif_transpose(after_image).convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise OptimizationImageError("COMPARISON_IMAGE_INVALID") from error

    panel_size = (640, 960)
    before_panel = ImageOps.pad(before_rgb, panel_size, color=(239, 235, 226))
    after_panel = ImageOps.pad(after_rgb, panel_size, color=(239, 235, 226))
    canvas = Image.new("RGB", (1_296, 960), color=(30, 30, 28))
    canvas.paste(before_panel, (0, 0))
    canvas.paste(after_panel, (656, 0))
    buffer = BytesIO()
    canvas.save(buffer, format="JPEG", quality=82, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
