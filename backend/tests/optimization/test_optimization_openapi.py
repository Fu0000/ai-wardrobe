from app.core.config import Settings
from app.main import create_app


def test_optimization_routes_publish_idempotent_openapi_contract() -> None:
    schema = create_app(Settings()).openapi()
    create_operation = schema["paths"]["/api/v1/style-diagnoses/{diagnosis_id}/optimizations"][
        "post"
    ]

    assert create_operation["responses"]["202"]
    assert any(
        parameter["in"] == "header"
        and parameter["name"] == "Idempotency-Key"
        and parameter["required"] is True
        for parameter in create_operation["parameters"]
    )
    assert "/api/v1/style-optimizations/{optimization_id}" in schema["paths"]
