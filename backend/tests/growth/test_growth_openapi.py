from app.core.config import Settings
from app.main import create_app


def test_growth_routes_publish_share_and_vote_contracts() -> None:
    schema = create_app(Settings()).openapi()
    create_operation = schema["paths"]["/api/v1/shares"]["post"]

    assert create_operation["responses"]["202"]
    assert any(
        parameter["in"] == "header"
        and parameter["name"] == "Idempotency-Key"
        and parameter["required"] is True
        for parameter in create_operation["parameters"]
    )
    assert "/api/v1/shares/{scene_code}" in schema["paths"]
    assert "/api/v1/votes" in schema["paths"]
    assert "/api/v1/votes/{scene_code}/result" in schema["paths"]
    assert "/api/v1/shares/{scene_code}/continue" in schema["paths"]
    assert "/api/v1/shares/{scene_code}/invocations" in schema["paths"]
    share_parameters = schema["paths"]["/api/v1/shares/{scene_code}"]["get"]["parameters"]
    source = next(
        parameter for parameter in share_parameters if parameter["name"] == "attribution_source"
    )
    source_enum = next(item for item in source["schema"]["anyOf"] if item.get("type") == "string")
    assert source_enum["enum"] == ["WECHAT_FRIEND", "WECHAT_TIMELINE"]
    for path, method in (
        ("/api/v1/shares/{scene_code}", "get"),
        ("/api/v1/votes/{scene_code}/result", "get"),
        ("/api/v1/shares/{scene_code}/continue", "post"),
        ("/api/v1/shares/{scene_code}/invocations", "post"),
    ):
        operation = schema["paths"][path][method]
        scene = next(
            parameter for parameter in operation["parameters"] if parameter["name"] == "scene_code"
        )
        assert scene["schema"]["minLength"] == 16
        assert scene["schema"]["maxLength"] == 64
        assert scene["schema"]["pattern"] == "^[A-Za-z0-9_-]+$"
