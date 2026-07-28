from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "debug": False,
        "expose_api_docs": False,
        "secret_key": "s" * 32,
        "access_token_key": "a" * 32,
        "identity_hmac_key": "h" * 32,
        "identity_encryption_key": Fernet.generate_key().decode(),
        "wechat_login_enabled": True,
        "wechat_app_id": "wx-test-app",
        "wechat_app_secret": "wechat-secret",
        "cos_enabled": True,
        "cos_bucket": "private-bucket",
        "cos_secret_id": "cos-secret-id",
        "cos_secret_key": "cos-secret-key",
        "openai_enabled": True,
        "openai_api_key": "openai-key",
        "rate_limit_enabled": True,
        "otel_enabled": True,
        "otel_exporter_otlp_endpoint": "http://otel-collector:4318",
        "trusted_proxy_cidrs": ["10.42.7.0/24"],
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_staging_applies_deployed_environment_safety_checks() -> None:
    with pytest.raises(ValidationError, match="rate limiting must be enabled"):
        production_settings(
            environment="staging",
            rate_limit_enabled=False,
        )


def test_staging_manifest_uses_staging_runtime_environment() -> None:
    manifest = (Path(__file__).resolve().parents[3] / "infra/k8s/base/configmap.yaml").read_text(
        encoding="utf-8"
    )

    assert "  AIW_ENVIRONMENT: staging\n" in manifest
    assert "  AIW_ENVIRONMENT: production\n" not in manifest


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValidationError, match="debug must be disabled"):
        Settings(
            environment="production",
            debug=True,
            expose_api_docs=False,
            secret_key="x" * 32,
        )


def test_production_rejects_public_api_docs() -> None:
    with pytest.raises(ValidationError, match="documentation must be disabled"):
        Settings(
            environment="production",
            debug=False,
            expose_api_docs=True,
            secret_key="x" * 32,
        )


def test_production_requires_strong_secret() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            environment="production",
            debug=False,
            expose_api_docs=False,
            secret_key="too-short",
        )


def test_execution_lease_must_cover_primary_and_fallback_timeouts() -> None:
    with pytest.raises(ValidationError, match="primary and fallback"):
        Settings(
            diagnosis_timeout_seconds=20,
            diagnosis_execution_lease_seconds=49,
        )


def test_optimization_lease_must_cover_generation_and_critic_retries() -> None:
    with pytest.raises(ValidationError, match="generation and critic"):
        Settings(optimization_execution_lease_seconds=229)


def test_orphan_upload_ttl_must_outlive_upload_ticket() -> None:
    with pytest.raises(ValueError, match="orphan upload TTL"):
        Settings(
            cos_upload_ticket_ttl_seconds=900,
            orphan_upload_ttl_seconds=900,
        )


def test_canary_percentage_requires_a_candidate_model() -> None:
    with pytest.raises(ValidationError, match="canary model is required"):
        Settings(ai_canary_percentage=10)


def test_canary_percentage_is_bounded() -> None:
    with pytest.raises(ValidationError, match="between 0 and 100"):
        Settings(
            ai_canary_percentage=101,
            diagnosis_canary_model="candidate",
        )


def test_trusted_proxy_cidr_must_be_valid() -> None:
    with pytest.raises(ValidationError, match="invalid trusted proxy CIDR"):
        Settings(trusted_proxy_cidrs=["not-a-network"])


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "::/0"])
def test_trusted_proxy_cidr_cannot_trust_every_address(cidr: str) -> None:
    with pytest.raises(ValidationError, match="entire address space"):
        Settings(trusted_proxy_cidrs=[cidr])


def test_identity_encryption_key_must_be_a_fernet_key() -> None:
    with pytest.raises(ValidationError, match="valid Fernet key"):
        Settings(identity_encryption_key="not-a-fernet-key")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("secret_key", "local-development-only-change-me"),
        ("access_token_key", "local-access-token-key-change-me-000000"),
        ("identity_hmac_key", "local-identity-hmac-key-change-me"),
        (
            "identity_encryption_key",
            "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        ),
    ],
)
def test_production_rejects_known_local_cryptographic_keys(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="local development defaults"):
        production_settings(**{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("wechat_api_base_url", "http://wechat.invalid", "WeChat API must use HTTPS"),
        ("openai_base_url", "http://provider.invalid", "AI provider API must use HTTPS"),
    ],
)
def test_production_provider_endpoints_require_https(
    field: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        production_settings(**{field: value})
