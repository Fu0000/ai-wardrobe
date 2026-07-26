import pytest

from app.modules.ai.openai_image_provider import (
    estimate_image_edit_cost,
    normalized_image_output_size,
)


def test_gpt_image_cost_uses_separate_image_and_text_rates() -> None:
    assert (
        estimate_image_edit_cost(
            input_image_tokens=1_000,
            input_text_tokens=200,
            output_image_tokens=3_000,
        )
        == 99_000
    )


@pytest.mark.parametrize(
    ("source_size", "expected"),
    [
        ((1_080, 1_440), "1088x1440"),
        ((400, 600), "832x1248"),
        ((4_000, 3_000), "1680x1248"),
    ],
)
def test_gpt_image_output_size_meets_provider_constraints(
    source_size: tuple[int, int],
    expected: str,
) -> None:
    output = normalized_image_output_size(*source_size)
    width, height = (int(value) for value in output.split("x"))

    assert output == expected
    assert width % 16 == 0
    assert height % 16 == 0
    assert 655_360 <= width * height <= 8_294_400
    assert max(width, height) / min(width, height) <= 3


def test_gpt_image_output_size_rejects_panorama() -> None:
    with pytest.raises(ValueError, match="aspect ratio"):
        normalized_image_output_size(400, 1_601)
