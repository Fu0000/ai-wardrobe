from io import BytesIO

import pytest
from PIL import Image

from app.modules.growth.images import ShareImageError, render_share_card


def jpeg_bytes(
    *,
    size: tuple[int, int],
    color: tuple[int, int, int],
    orientation: int | None = None,
) -> bytes:
    buffer = BytesIO()
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    Image.new("RGB", size, color=color).save(
        buffer,
        format="JPEG",
        exif=exif,
    )
    return buffer.getvalue()


def test_share_card_is_a_metadata_free_standalone_derivative() -> None:
    rendered = render_share_card(
        jpeg_bytes(size=(800, 1_200), color=(80, 100, 120), orientation=6),
        jpeg_bytes(size=(800, 1_200), color=(120, 100, 80)),
        change_level=2,
        score=82,
    )

    with Image.open(BytesIO(rendered.data)) as card:
        assert card.format == "JPEG"
        assert card.size == (1_200, 1_500)
        assert not card.getexif()
    assert rendered.content_type == "image/jpeg"
    assert b"private/" not in rendered.data


def test_share_card_rejects_invalid_image_or_change_level() -> None:
    image = jpeg_bytes(size=(800, 1_200), color=(80, 100, 120))
    with pytest.raises(ShareImageError, match="invalid source"):
        render_share_card(b"not-an-image", image, change_level=1)
    with pytest.raises(ShareImageError, match="change level"):
        render_share_card(image, image, change_level=4)
    with pytest.raises(ShareImageError, match="score"):
        render_share_card(image, image, change_level=1, score=101)
