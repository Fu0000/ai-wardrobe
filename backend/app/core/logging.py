import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

from app.core.config import Settings

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "openid",
        "unionid",
        "provider_subject",
        "provider_subject_encrypted",
        "union_subject",
        "union_subject_encrypted",
        "object_key",
        "url",
        "image",
        "image_data",
        "image_url",
        "upload_url",
        "download_url",
        "signed_url",
        "card_url",
        "before_image_url",
        "after_image_url",
    }
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"\.[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])"
)
_QUERY_URL_PATTERN = re.compile(r"(?i)https?://[^\s\"']+\?[^\s\"']+")
_IMAGE_DATA_PATTERN = re.compile(r"(?i)data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=_-]+")
_SENSITIVE_THIRD_PARTY_LOGGERS = ("httpx", "httpcore")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_url")
    )


def _sanitize_text(value: str) -> str:
    sanitized = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", value)
    sanitized = _JWT_PATTERN.sub(_REDACTED, sanitized)
    sanitized = _QUERY_URL_PATTERN.sub(_REDACTED, sanitized)
    return _IMAGE_DATA_PATTERN.sub("[REDACTED_IMAGE_DATA]", sanitized)


def _redact_value(key: str, value: object) -> object:
    if _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, Mapping):
        return {
            str(nested_key): _redact_value(str(nested_key), nested_value)
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value("", item) for item in value]
    return value


def redact_sensitive_fields(
    logger: object,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> Mapping[str, Any]:
    del logger, method_name
    return {key: _redact_value(key, value) for key, value in event_dict.items()}


def configure_logging(settings: Settings) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive_fields,
    ]

    renderer: structlog.typing.Processor
    if settings.environment in {"local", "test"}:
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
        force=True,
    )
    # HTTPX logs complete request URLs at INFO. Provider credentials can be
    # carried in query parameters (for example WeChat code2Session), so these
    # libraries must never inherit the application's INFO/DEBUG log level.
    for logger_name in _SENSITIVE_THIRD_PARTY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.log_level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
