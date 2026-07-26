from dataclasses import dataclass

from httpx import AsyncClient, HTTPError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings


class WeChatLoginError(Exception):
    def __init__(self, *, code: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class WeChatSession:
    open_id: str
    session_key: str
    union_id: str | None


class _Code2SessionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openid: str | None = None
    session_key: str | None = None
    unionid: str | None = None
    errcode: int | None = None
    errmsg: str | None = Field(default=None, max_length=240)


class WeChatLoginClient:
    def __init__(self, *, settings: Settings, http_client: AsyncClient) -> None:
        self._http_client = http_client
        self._base_url = settings.wechat_api_base_url.rstrip("/")
        self._app_id = settings.wechat_app_id
        self._app_secret = settings.wechat_app_secret.get_secret_value()
        self._enabled = settings.wechat_login_enabled

    async def exchange_code(self, code: str) -> WeChatSession:
        if not self._enabled:
            raise WeChatLoginError(code="WECHAT_LOGIN_DISABLED", retryable=False)

        try:
            response = await self._http_client.get(
                f"{self._base_url}/sns/jscode2session",
                params={
                    "appid": self._app_id,
                    "secret": self._app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            payload = _Code2SessionPayload.model_validate(response.json())
        except (HTTPError, ValidationError, ValueError) as error:
            raise WeChatLoginError(
                code="WECHAT_UPSTREAM_UNAVAILABLE",
                retryable=True,
            ) from error

        if payload.errcode is not None and payload.errcode != 0:
            retryable = payload.errcode not in {40029, 40163}
            raise WeChatLoginError(
                code="WECHAT_CODE_INVALID" if not retryable else "WECHAT_UPSTREAM_ERROR",
                retryable=retryable,
            )

        if not payload.openid or not payload.session_key:
            raise WeChatLoginError(
                code="WECHAT_INVALID_RESPONSE",
                retryable=True,
            )

        return WeChatSession(
            open_id=payload.openid,
            session_key=payload.session_key,
            union_id=payload.unionid,
        )
