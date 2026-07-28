import httpx
import pytest

from app.core.config import Settings
from app.modules.identity.wechat import WeChatLoginClient, WeChatLoginError


def settings() -> Settings:
    return Settings(
        environment="test",
        wechat_login_enabled=True,
        wechat_app_id="test-app-id",
        wechat_app_secret="test-app-secret",
        wechat_api_base_url="https://wechat.test",
    )


async def test_code_exchange_uses_backend_secret_and_returns_subject() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["appid"] == "test-app-id"
        assert request.url.params["secret"] == "test-app-secret"
        assert request.url.params["js_code"] == "one-time-code"
        return httpx.Response(
            200,
            json={
                "openid": "openid-1",
                "session_key": "session-key",
                "unionid": "union-1",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        result = await WeChatLoginClient(
            settings=settings(),
            http_client=http_client,
        ).exchange_code("one-time-code")

    assert result.open_id == "openid-1"
    assert result.union_id == "union-1"


async def test_invalid_code_is_terminal() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})
    )

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeChatLoginClient(settings=settings(), http_client=http_client)
        with pytest.raises(WeChatLoginError) as captured:
            await client.exchange_code("expired")

    assert captured.value.code == "WECHAT_CODE_INVALID"
    assert captured.value.retryable is False


async def test_network_failure_is_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = WeChatLoginClient(settings=settings(), http_client=http_client)
        with pytest.raises(WeChatLoginError) as captured:
            await client.exchange_code("code")

    assert captured.value.code == "WECHAT_UPSTREAM_UNAVAILABLE"
    assert captured.value.retryable is True
