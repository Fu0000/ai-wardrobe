import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


class InvalidAccessTokenError(Exception):
    pass


class SubjectDecryptionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AccessToken:
    value: str
    expires_in_seconds: int


class SubjectProtector:
    def __init__(self, settings: Settings) -> None:
        self._hmac_key = settings.identity_hmac_key.get_secret_value().encode()
        self._cipher = Fernet(settings.identity_encryption_key.get_secret_value().encode())

    def digest(self, subject: str) -> str:
        return hmac.new(
            self._hmac_key,
            subject.encode(),
            hashlib.sha256,
        ).hexdigest()

    def encrypt(self, subject: str) -> str:
        return self._cipher.encrypt(subject.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._cipher.decrypt(ciphertext.encode()).decode()
        except InvalidToken as error:
            raise SubjectDecryptionError from error


class AccessTokenService:
    _algorithm = "HS256"

    def __init__(self, settings: Settings) -> None:
        self._key = settings.access_token_key.get_secret_value()
        self._issuer = settings.access_token_issuer
        self._audience = settings.access_token_audience
        self._ttl = settings.access_token_ttl_seconds

    def issue(self, user_id: UUID, *, now: datetime | None = None) -> AccessToken:
        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self._ttl)
        claims: dict[str, Any] = {
            "sub": str(user_id),
            "iss": self._issuer,
            "aud": self._audience,
            "iat": issued_at,
            "exp": expires_at,
            "jti": uuid4().hex,
            "typ": "access",
        }
        value = jwt.encode(claims, self._key, algorithm=self._algorithm)
        return AccessToken(value=value, expires_in_seconds=self._ttl)

    def verify(self, token: str) -> UUID:
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["sub", "iss", "aud", "iat", "exp", "jti", "typ"],
                },
            )
            if claims["typ"] != "access":
                raise InvalidAccessTokenError
            return UUID(str(claims["sub"]))
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise InvalidAccessTokenError from error
