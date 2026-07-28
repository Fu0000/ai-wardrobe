from app.main import create_app


def test_feedback_contract_is_exposed() -> None:
    schema = create_app().openapi()

    assert "/api/v1/feedback" in schema["paths"]
    assert "/api/v1/me/feedback" in schema["paths"]
    request_schema = schema["components"]["schemas"]["CreateFeedbackRequest"]
    assert request_schema["properties"]["message"]["maxLength"] == 2_000
    operation = schema["paths"]["/api/v1/me/feedback"]["get"]
    parameters = {item["name"]: item["schema"] for item in operation["parameters"]}
    assert parameters["limit"]["minimum"] == 1
    assert parameters["limit"]["maximum"] == 50
    cursor_string = next(
        item for item in parameters["cursor"]["anyOf"] if item.get("type") == "string"
    )
    assert cursor_string["maxLength"] == 256
    response_schema = schema["components"]["schemas"]["FeedbackPageResponse"]
    assert set(response_schema["required"]) == {"items", "next_cursor"}
