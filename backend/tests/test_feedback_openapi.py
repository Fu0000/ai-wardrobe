from app.main import create_app


def test_feedback_contract_is_exposed() -> None:
    schema = create_app().openapi()

    assert "/api/v1/feedback" in schema["paths"]
    assert "/api/v1/me/feedback" in schema["paths"]
    request_schema = schema["components"]["schemas"]["CreateFeedbackRequest"]
    assert request_schema["properties"]["message"]["maxLength"] == 2_000
