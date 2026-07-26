from app.core.logging import redact_sensitive_fields


def test_log_processor_redacts_nested_credentials_and_private_asset_references() -> None:
    signed_url = (
        "https://cos.example/private/user-1/source.jpg?q-signature=secret-signature&q-ak=secret-id"
    )
    event = redact_sensitive_fields(
        object(),
        "info",
        {
            "event": "provider request failed",
            "authorization": "Bearer top-secret-token",
            "request": {
                "headers": {
                    "Authorization": "Bearer nested-secret-token",
                    "traceparent": "00-abc-def-01",
                },
                "image_url": signed_url,
                "object_key": "private/user-1/source.jpg",
            },
        },
    )

    rendered = repr(event)
    assert "top-secret-token" not in rendered
    assert "nested-secret-token" not in rendered
    assert "secret-signature" not in rendered
    assert "private/user-1/source.jpg" not in rendered
    assert event["authorization"] == "[REDACTED]"


def test_log_processor_sanitizes_secrets_embedded_in_exception_text() -> None:
    event = redact_sensitive_fields(
        object(),
        "error",
        {
            "exception": (
                "GET https://api.example/resource?access_token=secret-token failed; "
                "Authorization: Bearer another-secret; "
                "payload=data:image/png;base64,cHJpdmF0ZS1pbWFnZQ==; "
                "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEifQ."
                "QWERTYUIOPASDFGHJKLZXCVBNM"
            )
        },
    )

    rendered = str(event["exception"])
    assert "secret-token" not in rendered
    assert "another-secret" not in rendered
    assert "cHJpdmF0ZS1pbWFnZQ" not in rendered
    assert "eyJhbGciOiJIUzI1NiJ9" not in rendered
    assert "[REDACTED]" in rendered
