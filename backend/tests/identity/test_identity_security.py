from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.modules.identity.security import (
    AccessTokenService,
    InvalidAccessTokenError,
    SubjectProtector,
)


def test_subject_protection_is_stable_and_reversible() -> None:
    protector = SubjectProtector(Settings(environment="test"))

    first_digest = protector.digest("wechat-open-id")
    second_digest = protector.digest("wechat-open-id")
    ciphertext = protector.encrypt("wechat-open-id")

    assert first_digest == second_digest
    assert first_digest != "wechat-open-id"
    assert "wechat-open-id" not in ciphertext
    assert protector.decrypt(ciphertext) == "wechat-open-id"


def test_access_token_round_trip() -> None:
    service = AccessTokenService(Settings(environment="test"))
    user_id = uuid4()

    token = service.issue(user_id)

    assert service.verify(token.value) == user_id
    assert token.expires_in_seconds == 7_200


def test_expired_access_token_is_rejected() -> None:
    service = AccessTokenService(Settings(environment="test", access_token_ttl_seconds=1))
    expired_at = datetime.now(UTC) - timedelta(seconds=10)
    token = service.issue(uuid4(), now=expired_at)

    with pytest.raises(InvalidAccessTokenError):
        service.verify(token.value)


def test_token_signed_with_another_key_is_rejected() -> None:
    first = AccessTokenService(Settings(environment="test", access_token_key="a" * 32))
    second = AccessTokenService(Settings(environment="test", access_token_key="b" * 32))

    with pytest.raises(InvalidAccessTokenError):
        second.verify(first.issue(uuid4()).value)
