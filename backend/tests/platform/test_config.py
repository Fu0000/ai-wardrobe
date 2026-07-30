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
        "cos_upload_secret_id": "cos-upload-secret-id",
        "cos_upload_secret_key": "cos-upload-secret-key",
        "database_url": (
            "postgresql+psycopg://aiw:secret@10.32.16.10:5432/ai_wardrobe"
            "?sslmode=verify-full&sslrootcert=system"
        ),
        "redis_url": (
            "rediss://:secret@10.32.16.20:6379/0"
            "?ssl_cert_reqs=required"
            "&ssl_ca_certs=/var/run/secrets/ai-wardrobe/redis-ca.pem"
        ),
        "openai_enabled": True,
        "openai_api_key": "openai-key",
        "rate_limit_enabled": True,
        "otel_enabled": True,
        "otel_exporter_otlp_endpoint": "http://otel-collector:4318",
        "trusted_proxy_cidrs": ["10.42.7.0/24"],
        "cors_origins": ["https://staging.example.com"],
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


def test_deployed_environment_requires_separate_cos_upload_credentials() -> None:
    with pytest.raises(ValidationError, match="runtime/upload credentials"):
        production_settings(cos_upload_secret_key="")


def test_deployed_environment_rejects_reused_cos_identity() -> None:
    with pytest.raises(ValidationError, match="identities must be different"):
        production_settings(cos_upload_secret_id="cos-secret-id")


def test_local_cos_configuration_also_requires_separate_identities() -> None:
    with pytest.raises(ValidationError, match="identities must be different"):
        Settings(
            cos_enabled=True,
            cos_bucket="private-bucket",
            cos_secret_id="shared-secret-id",
            cos_secret_key="runtime-secret-key",
            cos_upload_secret_id="shared-secret-id",
            cos_upload_secret_key="upload-secret-key",
        )


def test_local_object_storage_cannot_run_with_cos() -> None:
    with pytest.raises(ValidationError, match="cannot be enabled together"):
        Settings(
            cos_enabled=True,
            cos_bucket="private-bucket",
            cos_secret_id="runtime-secret-id",
            cos_secret_key="runtime-secret-key",
            cos_upload_secret_id="upload-secret-id",
            cos_upload_secret_key="upload-secret-key",
            local_storage_enabled=True,
        )


def test_local_object_storage_is_restricted_to_non_deployed_environments() -> None:
    with pytest.raises(ValidationError, match="restricted to local and test"):
        production_settings(
            cos_enabled=False,
            local_storage_enabled=True,
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://localhost:8000",
        "http://192.168.1.2:8000",
        "http://localhost:8000/prefix",
        "http://localhost:8000?token=unsafe",
    ],
)
def test_local_object_storage_requires_a_loopback_http_origin(base_url: str) -> None:
    with pytest.raises(ValidationError, match="loopback HTTP origin"):
        Settings(
            environment="test",
            local_storage_enabled=True,
            local_storage_base_url=base_url,
        )


@pytest.mark.parametrize("root", ["/", str(Path.home())])
def test_local_object_storage_rejects_broad_roots(root: str) -> None:
    with pytest.raises(ValidationError, match="dedicated directory"):
        Settings(
            environment="test",
            local_storage_enabled=True,
            local_storage_root=root,
        )


def test_local_ai_cannot_run_with_openai() -> None:
    with pytest.raises(ValidationError, match="cannot be enabled together"):
        Settings(
            openai_enabled=True,
            local_ai_enabled=True,
        )


def test_local_ai_is_restricted_to_non_deployed_environments() -> None:
    with pytest.raises(ValidationError, match="restricted to local and test"):
        production_settings(
            openai_enabled=False,
            local_ai_enabled=True,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "database_url",
            "postgresql+psycopg://aiw:secret@10.32.16.10:5432/ai_wardrobe",
            "sslmode=verify-full",
        ),
        (
            "database_url",
            ("postgresql+psycopg://aiw:secret@10.32.16.10:5432/ai_wardrobe?sslmode=require"),
            "sslmode=verify-full",
        ),
        (
            "redis_url",
            "redis://:secret@10.32.16.20:6379/0",
            "must use rediss",
        ),
        (
            "redis_url",
            "rediss://:secret@10.32.16.20:6379/0?ssl_cert_reqs=none",
            "ssl_cert_reqs=required",
        ),
        (
            "redis_url",
            "rediss://:secret@10.32.16.20:6379/0?ssl_cert_reqs=required",
            "ssl_ca_certs",
        ),
    ],
)
def test_deployed_data_endpoints_require_verified_tls(
    field: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        production_settings(**{field: value})


def test_kubernetes_mounts_data_ca_on_every_database_consumer() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    manifests = [
        repository_root / "infra/k8s/base/api.yaml",
        repository_root / "infra/k8s/base/workers.yaml",
        repository_root / "infra/k8s/migration-job.yaml",
    ]

    rendered = "\n".join(path.read_text(encoding="utf-8") for path in manifests)
    assert rendered.count("secretName: ai-wardrobe-data-ca") == 7
    assert rendered.count("mountPath: /var/run/secrets/ai-wardrobe") == 7
    assert rendered.count("readOnly: true") >= 7
