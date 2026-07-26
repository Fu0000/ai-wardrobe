from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


class ShareImageError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ShareImage:
    data: bytes
    width: int
    height: int
    content_type: str = "image/jpeg"


def render_share_card(
    before: bytes,
    after: bytes,
    *,
    change_level: int,
    score: int | None = None,
) -> ShareImage:
    if change_level not in {1, 2, 3}:
        raise ShareImageError("invalid change level")
    if score is not None and not 0 <= score <= 100:
        raise ShareImageError("invalid score")
    try:
        with Image.open(BytesIO(before)) as before_source:
            before_rgb = ImageOps.exif_transpose(before_source).convert("RGB")
        with Image.open(BytesIO(after)) as after_source:
            after_rgb = ImageOps.exif_transpose(after_source).convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise ShareImageError("invalid source image") from error

    width, height = 1_200, 1_500
    paper = (244, 240, 231)
    ink = (31, 31, 29)
    sage = (91, 112, 91)
    vermilion = (205, 72, 48)
    card = Image.new("RGB", (width, height), paper)
    panel_size = (568, 1_100)
    before_panel = ImageOps.pad(before_rgb, panel_size, color=paper)
    after_panel = ImageOps.pad(after_rgb, panel_size, color=paper)
    card.paste(before_panel, (24, 76))
    card.paste(after_panel, (608, 76))

    draw = ImageDraw.Draw(card)
    label_font = ImageFont.load_default(size=28)
    title_font = ImageFont.load_default(size=42)
    body_font = ImageFont.load_default(size=26)
    draw.rounded_rectangle((42, 96, 184, 146), radius=25, fill=ink)
    draw.text((68, 106), "BEFORE", font=label_font, fill=(255, 255, 255))
    draw.rounded_rectangle((980, 96, 1158, 146), radius=25, fill=vermilion)
    draw.text((1_016, 106), "AFTER", font=label_font, fill=(255, 255, 255))
    draw.line((600, 76, 600, 1_176), fill=(255, 255, 255), width=6)

    draw.text((48, 1_230), "MINIMAL CHANGE", font=label_font, fill=sage)
    draw.text(
        (48, 1_278),
        f"Change Budget / Level {change_level}",
        font=title_font,
        fill=ink,
    )
    if score is not None:
        draw.text(
            (48, 1_328),
            f"STYLE SCORE / {score}",
            font=body_font,
            fill=vermilion,
        )
    draw.rounded_rectangle((916, 1_230, 1_152, 1_306), radius=38, fill=vermilion)
    draw.text((956, 1_249), "AI EDITED", font=body_font, fill=(255, 255, 255))
    draw.text(
        (48, 1_400),
        "Same person. Only approved changes. Vote in the mini program.",
        font=body_font,
        fill=(81, 80, 75),
    )

    output = BytesIO()
    card.save(
        output,
        format="JPEG",
        quality=86,
        optimize=True,
        progressive=True,
    )
    return ShareImage(data=output.getvalue(), width=width, height=height)
