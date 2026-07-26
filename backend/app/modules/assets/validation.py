import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError


class InvalidImageError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    content_type: str
    format: str
    width: int
    height: int


CONTENT_TYPE_FORMATS: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def validate_upload_request(
    *,
    content_type: str,
    size_bytes: int,
    max_upload_bytes: int,
) -> str:
    normalized_content_type = content_type.lower().strip()
    if normalized_content_type not in CONTENT_TYPE_FORMATS:
        raise InvalidImageError("UNSUPPORTED_IMAGE_TYPE")
    if size_bytes <= 0:
        raise InvalidImageError("EMPTY_IMAGE")
    if size_bytes > max_upload_bytes:
        raise InvalidImageError("IMAGE_TOO_LARGE")
    return normalized_content_type


def validate_image_prefix(
    *,
    data: bytes,
    declared_content_type: str,
    max_dimension: int,
    max_pixels: int,
) -> ValidatedImage:
    expected_format = CONTENT_TYPE_FORMATS.get(declared_content_type)
    if expected_format is None:
        raise InvalidImageError("UNSUPPORTED_IMAGE_TYPE")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                detected_format = image.format
                width, height = image.size
                orientation = image.getexif().get(274)
                if orientation in {5, 6, 7, 8}:
                    width, height = height, width
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as error:
        raise InvalidImageError("INVALID_IMAGE_CONTENT") from error

    if detected_format != expected_format:
        raise InvalidImageError("IMAGE_TYPE_MISMATCH")
    if width <= 0 or height <= 0:
        raise InvalidImageError("INVALID_IMAGE_DIMENSIONS")
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise InvalidImageError("IMAGE_DIMENSIONS_TOO_LARGE")

    return ValidatedImage(
        content_type=declared_content_type,
        format=detected_format,
        width=width,
        height=height,
    )
