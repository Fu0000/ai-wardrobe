import base64
import binascii
from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]

_LOCAL_SECRET_KEY = "local-development-only-change-me"  # noqa: S105
_LOCAL_ACCESS_TOKEN_KEY = "local-access-token-key-change-me-000000"  # noqa: S105
_LOCAL_IDENTITY_HMAC_KEY = "local-identity-hmac-key-change-me"
_LOCAL_IDENTITY_ENCRYPTION_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_prefix="AIW_",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI Wardrobe API"
    environment: Environment = "local"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    secret_key: SecretStr = SecretStr(_LOCAL_SECRET_KEY)
    access_token_key: SecretStr = SecretStr(_LOCAL_ACCESS_TOKEN_KEY)
    access_token_ttl_seconds: int = Field(default=7_200, ge=1, le=86_400)
    access_token_issuer: str = "ai-wardrobe"  # noqa: S105
    access_token_audience: str = "ai-wardrobe-miniapp"  # noqa: S105
    identity_hmac_key: SecretStr = SecretStr(_LOCAL_IDENTITY_HMAC_KEY)
    identity_encryption_key: SecretStr = SecretStr(_LOCAL_IDENTITY_ENCRYPTION_KEY)

    wechat_login_enabled: bool = False
    wechat_app_id: str = ""
    wechat_app_secret: SecretStr = SecretStr("")
    wechat_api_base_url: str = "https://api.weixin.qq.com"

    openai_enabled: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    local_ai_enabled: bool = False
    diagnosis_primary_model: str = "gpt-5.6-terra"
    diagnosis_fallback_model: str = "gpt-5.6-luna"
    diagnosis_timeout_seconds: float = 20.0
    diagnosis_cost_ceiling_microunits: int = 100_000
    diagnosis_execution_lease_seconds: int = 120
    optimization_image_model: str = "gpt-image-2-2026-04-21"
    optimization_critic_primary_model: str = "gpt-5.6-terra"
    optimization_critic_fallback_model: str = "gpt-5.6-luna"
    optimization_image_timeout_seconds: float = 60.0
    optimization_critic_timeout_seconds: float = 20.0
    optimization_image_cost_ceiling_microunits: int = 400_000
    optimization_critic_cost_ceiling_microunits: int = 100_000
    optimization_execution_lease_seconds: int = 300
    optimization_max_generation_attempts: int = 2
    ai_canary_percentage: int = 0
    diagnosis_canary_model: str = ""
    optimization_image_canary_model: str = ""
    optimization_critic_canary_model: str = ""
    share_execution_lease_seconds: int = 60
    share_ttl_days: int = 30
    deletion_execution_lease_seconds: int = 300

    cos_enabled: bool = False
    cos_region: str = "ap-shanghai"
    cos_bucket: str = ""
    cos_secret_id: SecretStr = SecretStr("")
    cos_secret_key: SecretStr = SecretStr("")
    cos_security_token: SecretStr = SecretStr("")
    cos_upload_secret_id: SecretStr = SecretStr("")
    cos_upload_secret_key: SecretStr = SecretStr("")
    cos_upload_security_token: SecretStr = SecretStr("")
    cos_upload_ticket_ttl_seconds: int = Field(default=300, ge=60, le=900)
    cos_download_url_ttl_seconds: int = Field(default=900, ge=60, le=3_600)
    local_storage_enabled: bool = False
    local_storage_root: str = "../.local/object-storage"
    local_storage_base_url: str = "http://localhost:8000"
    max_upload_bytes: int = 20 * 1024 * 1024
    max_image_dimension: int = 12_000
    max_image_pixels: int = 40_000_000
    image_header_read_bytes: int = 2 * 1024 * 1024

    api_v1_prefix: str = "/api/v1"
    expose_api_docs: bool = True
    request_id_header: str = "X-Request-ID"
    cors_origins: list[str] = Field(default_factory=list)
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    max_json_body_bytes: int = 1024 * 1024
    readiness_timeout_seconds: float = 2.0
    otel_enabled: bool = False
    otel_service_name: str = "ai-wardrobe-api"
    otel_exporter_otlp_endpoint: str = ""
    otel_sample_ratio: float = 0.2
    otel_export_timeout_seconds: float = 5.0
    otel_metrics_export_interval_seconds: int = 30

    database_url: str = "postgresql+psycopg://ai_wardrobe:ai_wardrobe@localhost:5432/ai_wardrobe"
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_enabled: bool = False
    rate_limit_ip_per_minute: int = 300
    rate_limit_user_per_minute: int = 120
    rate_limit_costly_per_minute: int = 10
    outbox_worker_id: str = "outbox-local"
    outbox_batch_size: int = 50
    outbox_lock_timeout_seconds: int = 300
    outbox_dispatch_interval_seconds: float = 5.0
    # 重试上限。退避封顶 900 秒，16 次约覆盖 2.3 小时——足以扛过一次 Broker
    # 停机与人工介入；超过后转入 DEAD_LETTER，不再无限重试。
    outbox_max_attempts: int = 16
    # 过期任务回收：租约到期后再宽限一段时间才判定失联，避免与正常的租约续期竞争。
    # 默认值需严格长于最长的执行租约（优化任务 300 秒），留出一倍余量。
    stale_job_grace_seconds: int = 600
    stale_job_batch_size: int = 50
    stale_job_reap_interval_seconds: float = 60.0
    # 上传票据过期后保留一段完成宽限，再由清理器删除 DB 行与对象存储残留。
    orphan_upload_ttl_seconds: int = Field(default=3_600, ge=300, le=86_400)
    orphan_upload_cleanup_lease_seconds: int = Field(default=900, ge=60, le=3_600)
    orphan_upload_cleanup_batch_size: int = Field(default=50, ge=1, le=500)
    orphan_upload_cleanup_interval_seconds: float = Field(default=300, ge=10, le=3_600)

    @model_validator(mode="after")
    def validate_deployed_environment_safety(self) -> "Settings":
        if self.diagnosis_timeout_seconds <= 0:
            raise ValueError("diagnosis timeout must be positive")
        if self.diagnosis_cost_ceiling_microunits <= 0:
            raise ValueError("diagnosis cost ceiling must be positive")
        minimum_lease = (self.diagnosis_timeout_seconds * 2) + 10
        if self.diagnosis_execution_lease_seconds < minimum_lease:
            raise ValueError("diagnosis execution lease must cover primary and fallback timeouts")
        if (
            self.optimization_image_timeout_seconds <= 0
            or self.optimization_critic_timeout_seconds <= 0
        ):
            raise ValueError("optimization timeouts must be positive")
        if (
            self.optimization_image_cost_ceiling_microunits <= 0
            or self.optimization_critic_cost_ceiling_microunits <= 0
        ):
            raise ValueError("optimization cost ceilings must be positive")
        if not 1 <= self.optimization_max_generation_attempts <= 3:
            raise ValueError("optimization generation attempts must be between 1 and 3")
        if not 0 <= self.ai_canary_percentage <= 100:
            raise ValueError("AI canary percentage must be between 0 and 100")
        if self.ai_canary_percentage > 0 and not any(
            (
                self.diagnosis_canary_model,
                self.optimization_image_canary_model,
                self.optimization_critic_canary_model,
            )
        ):
            raise ValueError("an AI canary model is required when canary traffic is enabled")
        minimum_optimization_lease = (
            self.optimization_max_generation_attempts
            * (
                self.optimization_image_timeout_seconds
                + (self.optimization_critic_timeout_seconds * 2)
            )
            + 30
        )
        if self.optimization_execution_lease_seconds < minimum_optimization_lease:
            raise ValueError(
                "optimization execution lease must cover generation and critic attempts"
            )
        # 宽限期必须长于最长的执行租约，否则回收器会误杀仍在正常执行的任务。
        longest_lease = max(
            self.diagnosis_execution_lease_seconds,
            self.optimization_execution_lease_seconds,
            self.share_execution_lease_seconds,
            self.deletion_execution_lease_seconds,
        )
        if self.stale_job_grace_seconds < longest_lease:
            raise ValueError("stale job grace period must exceed the longest execution lease")
        if self.stale_job_batch_size <= 0:
            raise ValueError("stale job batch size must be positive")
        if self.stale_job_reap_interval_seconds <= 0:
            raise ValueError("stale job reap interval must be positive")
        if self.orphan_upload_ttl_seconds <= self.cos_upload_ticket_ttl_seconds:
            raise ValueError("orphan upload TTL must exceed upload ticket TTL")
        if self.outbox_max_attempts <= 0:
            raise ValueError("outbox max attempts must be positive")
        if self.share_execution_lease_seconds < 30:
            raise ValueError("share execution lease must be at least 30 seconds")
        if not 1 <= self.share_ttl_days <= 90:
            raise ValueError("share TTL must be between 1 and 90 days")
        if self.deletion_execution_lease_seconds < 60:
            raise ValueError("deletion execution lease must be at least 60 seconds")
        if not 64 * 1024 <= self.max_json_body_bytes <= 10 * 1024 * 1024:
            raise ValueError("JSON body limit must be between 64 KiB and 10 MiB")
        if not 0.1 <= self.readiness_timeout_seconds <= 10:
            raise ValueError("readiness timeout must be between 0.1 and 10 seconds")
        if not 0 <= self.otel_sample_ratio <= 1:
            raise ValueError("OpenTelemetry sample ratio must be between 0 and 1")
        if not 0.1 <= self.otel_export_timeout_seconds <= 30:
            raise ValueError("OpenTelemetry export timeout must be between 0.1 and 30 seconds")
        if not 5 <= self.otel_metrics_export_interval_seconds <= 300:
            raise ValueError("OpenTelemetry metrics interval must be between 5 and 300 seconds")
        encryption_key = self.identity_encryption_key.get_secret_value()
        try:
            decoded_encryption_key = base64.b64decode(
                encryption_key,
                altchars=b"-_",
                validate=True,
            )
        except (binascii.Error, ValueError) as error:
            raise ValueError("identity encryption key must be a valid Fernet key") from error
        if len(decoded_encryption_key) != 32:
            raise ValueError("identity encryption key must be a valid Fernet key")
        for cidr in self.trusted_proxy_cidrs:
            try:
                network = ip_network(cidr, strict=False)
            except ValueError as error:
                raise ValueError(f"invalid trusted proxy CIDR: {cidr}") from error
            if network.prefixlen == 0:
                raise ValueError("trusted proxy CIDRs must not trust the entire address space")
        if self.cos_enabled:
            if (
                not self.cos_bucket
                or not self.cos_secret_id.get_secret_value()
                or not self.cos_secret_key.get_secret_value()
                or not self.cos_upload_secret_id.get_secret_value()
                or not self.cos_upload_secret_key.get_secret_value()
            ):
                raise ValueError(
                    "separate COS runtime/upload credentials and bucket are required "
                    "when COS is enabled"
                )
            if (
                self.cos_secret_id.get_secret_value()
                == self.cos_upload_secret_id.get_secret_value()
            ):
                raise ValueError("COS runtime and upload identities must be different")
        if self.cos_enabled and self.local_storage_enabled:
            raise ValueError("COS and local object storage cannot be enabled together")
        if self.local_storage_enabled:
            if self.environment not in {"local", "test"}:
                raise ValueError(
                    "local object storage is restricted to local and test environments"
                )
            local_storage_url = urlsplit(self.local_storage_base_url)
            if (
                local_storage_url.scheme != "http"
                or local_storage_url.hostname not in {"localhost", "127.0.0.1", "::1", "test"}
                or local_storage_url.path not in {"", "/"}
                or local_storage_url.query
                or local_storage_url.fragment
            ):
                raise ValueError("local object storage URL must use a loopback HTTP origin")
            storage_root = Path(self.local_storage_root).expanduser().resolve()
            if storage_root == Path(storage_root.anchor) or storage_root == Path.home().resolve():
                raise ValueError("local object storage root must be a dedicated directory")
        if self.openai_enabled and self.local_ai_enabled:
            raise ValueError("OpenAI and local AI cannot be enabled together")
        if self.local_ai_enabled and self.environment not in {"local", "test"}:
            raise ValueError("local AI is restricted to local and test environments")
        if self.environment not in {"staging", "production"}:
            return self

        if self.access_token_ttl_seconds < 300:
            raise ValueError("deployed access token TTL must be at least 300 seconds")
        if self.debug:
            raise ValueError("debug must be disabled in deployed environments")
        if self.expose_api_docs:
            raise ValueError("API documentation must be disabled in deployed environments")
        if len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("deployed secret key must contain at least 32 characters")
        if len(self.access_token_key.get_secret_value()) < 32:
            raise ValueError("deployed access token key must contain at least 32 characters")
        if len(self.identity_hmac_key.get_secret_value()) < 32:
            raise ValueError("deployed identity HMAC key must contain at least 32 characters")
        local_defaults = (
            (self.secret_key.get_secret_value(), _LOCAL_SECRET_KEY),
            (self.access_token_key.get_secret_value(), _LOCAL_ACCESS_TOKEN_KEY),
            (self.identity_hmac_key.get_secret_value(), _LOCAL_IDENTITY_HMAC_KEY),
            (encryption_key, _LOCAL_IDENTITY_ENCRYPTION_KEY),
        )
        if any(value == known_default for value, known_default in local_defaults):
            raise ValueError("deployed cryptographic keys must not use local development defaults")
        if not self.wechat_login_enabled:
            raise ValueError("WeChat login must be enabled in deployed environments")
        if not self.wechat_app_id or not self.wechat_app_secret.get_secret_value():
            raise ValueError("WeChat credentials are required in deployed environments")
        if not self.wechat_api_base_url.startswith("https://"):
            raise ValueError("deployed WeChat API must use HTTPS")
        if not self.cos_enabled:
            raise ValueError("COS must be enabled in deployed environments")
        if not self.openai_enabled or not self.openai_api_key.get_secret_value():
            raise ValueError("an AI provider must be configured in deployed environments")
        if not self.openai_base_url.startswith("https://"):
            raise ValueError("deployed AI provider API must use HTTPS")
        if not self.rate_limit_enabled:
            raise ValueError("rate limiting must be enabled in deployed environments")
        database_url = urlsplit(self.database_url)
        database_query = parse_qs(database_url.query)
        if (
            database_url.scheme != "postgresql+psycopg"
            or database_query.get("sslmode") != ["verify-full"]
            or not database_query.get("sslrootcert")
        ):
            raise ValueError(
                "deployed PostgreSQL must use psycopg with sslmode=verify-full and an sslrootcert"
            )
        redis_url = urlsplit(self.redis_url)
        redis_query = parse_qs(redis_url.query)
        if (
            redis_url.scheme != "rediss"
            or redis_query.get("ssl_cert_reqs") != ["required"]
            or not redis_query.get("ssl_ca_certs")
        ):
            raise ValueError(
                "deployed Redis must use rediss with ssl_cert_reqs=required and ssl_ca_certs"
            )
        if not self.trusted_proxy_cidrs:
            raise ValueError("trusted proxy CIDRs are required in deployed environments")
        if not self.otel_enabled or not self.otel_exporter_otlp_endpoint.startswith(
            ("http://", "https://")
        ):
            raise ValueError("deployed OpenTelemetry requires an OTLP HTTP endpoint")
        if any(origin == "*" or not origin.startswith("https://") for origin in self.cors_origins):
            raise ValueError("deployed CORS origins must use explicit HTTPS origins")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
