from io import BytesIO

import pytest
from PIL import Image

from app.modules.assets.validation import (
    InvalidImageError,
    validate_image_prefix,
    validate_upload_request,
)


def image_bytes(*, image_format: str, size: tuple[int, int] = (120, 200)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(100, 120, 140)).save(
        buffer,
        format=image_format,
    )
    return buffer.getvalue()


def oriented_jpeg_bytes() -> bytes:
    buffer = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (1_200, 800), color=(100, 120, 140)).save(
        buffer,
        format="JPEG",
        exif=exif,
    )
    return buffer.getvalue()


def test_upload_request_accepts_supported_image() -> None:
    assert (
        validate_upload_request(
            content_type=" IMAGE/JPEG ",
            size_bytes=10_000,
            max_upload_bytes=20_000,
        )
        == "image/jpeg"
    )


@pytest.mark.parametrize("content_type", ["image/gif", "application/pdf", "text/plain"])
def test_upload_request_rejects_unsupported_type(content_type: str) -> None:
    with pytest.raises(InvalidImageError, match="UNSUPPORTED_IMAGE_TYPE"):
        validate_upload_request(
            content_type=content_type,
            size_bytes=100,
            max_upload_bytes=1_000,
        )


def test_upload_request_rejects_oversized_file() -> None:
    with pytest.raises(InvalidImageError, match="IMAGE_TOO_LARGE"):
        validate_upload_request(
            content_type="image/jpeg",
            size_bytes=1_001,
            max_upload_bytes=1_000,
        )


def test_image_prefix_reads_magic_number_and_dimensions() -> None:
    result = validate_image_prefix(
        data=image_bytes(image_format="PNG", size=(640, 960)),
        declared_content_type="image/png",
        max_dimension=2_000,
        max_pixels=3_000_000,
    )

    assert result.format == "PNG"
    assert result.width == 640
    assert result.height == 960


def test_image_prefix_reports_exif_display_dimensions() -> None:
    result = validate_image_prefix(
        data=oriented_jpeg_bytes(),
        declared_content_type="image/jpeg",
        max_dimension=2_000,
        max_pixels=3_000_000,
    )

    assert (result.width, result.height) == (800, 1_200)


def test_image_prefix_rejects_declared_type_mismatch() -> None:
    with pytest.raises(InvalidImageError, match="IMAGE_TYPE_MISMATCH"):
        validate_image_prefix(
            data=image_bytes(image_format="PNG"),
            declared_content_type="image/jpeg",
            max_dimension=2_000,
            max_pixels=3_000_000,
        )


def test_image_prefix_rejects_excessive_dimensions() -> None:
    with pytest.raises(InvalidImageError, match="IMAGE_DIMENSIONS_TOO_LARGE"):
        validate_image_prefix(
            data=image_bytes(image_format="JPEG", size=(1_200, 1_200)),
            declared_content_type="image/jpeg",
            max_dimension=1_000,
            max_pixels=2_000_000,
        )


def test_image_prefix_rejects_non_image_content() -> None:
    with pytest.raises(InvalidImageError, match="INVALID_IMAGE_CONTENT"):
        validate_image_prefix(
            data=b"this is not an image",
            declared_content_type="image/jpeg",
            max_dimension=2_000,
            max_pixels=3_000_000,
        )
