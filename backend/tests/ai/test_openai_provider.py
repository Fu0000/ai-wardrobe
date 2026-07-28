from app.modules.ai.openai_provider import estimate_cost


def test_model_cost_estimate_uses_versioned_price_registry() -> None:
    assert (
        estimate_cost(
            "gpt-5.6-terra",
            input_tokens=1_000,
            output_tokens=200,
        )
        == 5_500
    )
    assert (
        estimate_cost(
            "gpt-5.6-luna",
            input_tokens=1_000,
            output_tokens=200,
        )
        == 2_200
    )


def test_unknown_model_does_not_invent_a_cost() -> None:
    assert (
        estimate_cost(
            "vendor-private-model",
            input_tokens=1_000,
            output_tokens=200,
        )
        is None
    )
