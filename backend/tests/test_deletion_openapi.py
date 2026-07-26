from app.core.config import Settings
from app.main import create_app


def test_deletion_routes_are_idempotent_and_queryable() -> None:
    schema = create_app(Settings()).openapi()
    create_operation = schema["paths"]["/api/v1/me/deletion-request"]["post"]

    assert create_operation["responses"]["202"]
    assert any(
        parameter["in"] == "header"
        and parameter["name"] == "Idempotency-Key"
        and parameter["required"] is True
        for parameter in create_operation["parameters"]
    )
    assert "/api/v1/me/deletion-status" in schema["paths"]
    photo_operation = schema["paths"]["/api/v1/me/photos/{asset_id}"]["delete"]
    assert photo_operation["responses"]["202"]
    assert any(
        parameter["in"] == "header"
        and parameter["name"] == "Idempotency-Key"
        and parameter["required"] is True
        for parameter in photo_operation["parameters"]
    )
    assert "/api/v1/me/photos/{asset_id}/deletion-status" in schema["paths"]
