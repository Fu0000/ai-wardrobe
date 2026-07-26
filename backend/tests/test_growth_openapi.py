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
