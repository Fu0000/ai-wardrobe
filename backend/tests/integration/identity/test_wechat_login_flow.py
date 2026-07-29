"""微信登录成功路径的 HTTP + PostgreSQL 集成测试。

微信客户端单测锁定了 code2Session 协议，认证端点测试锁定了匿名访问和失败响应，
但此前没有测试把成功响应一直走到身份落库、Token 签发、用户查询与登录事件。
这个用例只替换微信上游，其他边界使用真实 FastAPI 路由和 PostgreSQL 事务。
"""

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_http_client, get_session
from app.core.config import Settings
from app.main import create_app
from app.modules.growth.models import UserEvent
from app.modules.identity.models import UserIdentity


async def test_wechat_login_creates_and_reuses_a_protected_identity(
    session: AsyncSession,
    settings: Settings,
) -> None:
    open_id = "integration-open-id"
    app_settings = Settings(
        environment="test",
        database_url=settings.database_url,
        redis_url=settings.redis_url,
        wechat_login_enabled=True,
        wechat_app_id="integration-app-id",
        wechat_app_secret="integration-app-secret",
    )

    def wechat_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sns/jscode2session"
        assert request.url.params["appid"] == "integration-app-id"
        assert request.url.params["secret"] == "integration-app-secret"
        assert request.url.params["grant_type"] == "authorization_code"
        assert request.url.params["js_code"] in {"first-code", "second-code"}
        return httpx.Response(
            200,
            json={
                "openid": open_id,
                "session_key": "integration-session-key",
                "unionid": "integration-union-id",
            },
        )

    upstream = AsyncClient(transport=httpx.MockTransport(wechat_handler))
    app = create_app(app_settings)

    async def transactional_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = transactional_session
    app.dependency_overrides[get_http_client] = lambda: upstream

    async with (
        upstream,
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        first = await client.post(
            "/api/v1/auth/wechat/login",
            json={"code": "first-code"},
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["is_new_user"] is True
        assert first_payload["token_type"] == "Bearer"
        assert first_payload["access_token"]
        user_id = UUID(first_payload["user"]["id"])

        me = await client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {first_payload['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["id"] == str(user_id)

        second = await client.post(
            "/api/v1/auth/wechat/login",
            json={"code": "second-code"},
        )
        assert second.status_code == 200
        assert second.json()["is_new_user"] is False
        assert second.json()["user"]["id"] == str(user_id)

    identity = (
        await session.execute(
            select(UserIdentity).where(UserIdentity.user_id == user_id),
        )
    ).scalar_one()
    assert identity.provider_subject_hash != open_id
    assert open_id not in identity.provider_subject_encrypted

    events = (
        await session.execute(
            select(UserEvent)
            .where(
                UserEvent.user_id == user_id,
                UserEvent.event_name == "auth.wechat.succeeded",
            )
            .order_by(UserEvent.occurred_at),
        )
    ).scalars()
    assert [event.properties["is_new_user"] for event in events] == [True, False]
