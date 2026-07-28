"""受保护端点的认证拒绝行为。

此前无任何端点级的 401 断言：`test_identity_security` 只覆盖 Token 的
签发与校验，没有测试「端点拿到坏 Token 会怎样」。更关键的是
`authenticated_user` 与 `current_user` 的差异——前者允许 DELETION_PENDING
用户通过以便轮询删除进度，后者不允许——完全没有测试锁定，任何人「顺手
统一一下」都不会有测试失败。
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_http_client, get_session
from app.core.config import Settings
from app.main import create_app
from app.modules.identity.security import AccessTokenService

# 受保护端点抽样：覆盖 current_user 与 authenticated_user 两类依赖。
PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/me"),
    ("PATCH", "/api/v1/me/profile"),
    ("POST", "/api/v1/client-events"),
    ("GET", "/api/v1/me/deletion-status"),
    ("GET", f"/api/v1/jobs/{uuid4()}"),
    ("GET", f"/api/v1/style-diagnoses/{uuid4()}"),
]


def _settings() -> Settings:
    return Settings(environment="test")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """带假会话的应用。

    认证拒绝发生在触达数据库之前，用假会话即可——真实连接由集成测试覆盖。
    没有它 lifespan 不初始化 app.state.database，测试会以 AttributeError
    失败而非我们要断言的 401。
    """

    app = create_app(_settings())

    async def fake_session() -> AsyncIterator[AsyncSession]:
        session = MagicMock(spec=AsyncSession)
        # 签名合法但用户不存在：execute 返回空结果集。
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result)
        yield session

    app.dependency_overrides[get_session] = fake_session
    # 登录端点的依赖里有 http_client，FastAPI 在解析依赖时就会取它，
    # 早于请求体校验。这里给个占位，测试不会真的发出外部请求。
    app.dependency_overrides[get_http_client] = lambda: MagicMock()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as active:
        yield active


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
async def test_missing_credentials_are_rejected(
    client: AsyncClient,
    method: str,
    path: str,
) -> None:
    response = await client.request(method, path, json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
async def test_malformed_tokens_are_rejected(
    client: AsyncClient,
    method: str,
    path: str,
) -> None:
    response = await client.request(
        method,
        path,
        json={},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"


async def test_non_bearer_scheme_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )

    assert response.status_code == 401


async def test_token_signed_with_another_key_is_rejected(client: AsyncClient) -> None:
    """密钥轮换后旧 Token 必须失效，而不是被当作合法凭据。"""

    foreign = AccessTokenService(
        Settings(
            environment="test",
            access_token_key="another-service-access-token-key-000000",
        )
    )
    token = foreign.issue(uuid4()).value

    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"


async def test_rejection_does_not_leak_internal_reason(client: AsyncClient) -> None:
    """401 文案不得暴露密钥、签名或用户是否存在。"""

    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.bogus.signature"},
    )

    body = response.json()["error"]
    assert response.status_code == 401
    for leaked in ("key", "signature", "secret", "jwt", "hs256", "密钥", "签名"):
        assert leaked not in body["message"].lower()
    # 不区分「Token 无效」与「用户不存在」，避免成为账号枚举信道。
    assert body["code"] == "INVALID_ACCESS_TOKEN"


async def test_unknown_user_is_indistinguishable_from_bad_token(
    client: AsyncClient,
) -> None:
    """签名合法但用户不存在时，不得返回与坏 Token 不同的可区分信号。"""

    token = AccessTokenService(_settings()).issue(uuid4()).value

    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "USER_NOT_ACTIVE"


async def test_login_endpoint_stays_anonymous(client: AsyncClient) -> None:
    """登录端点不得要求凭据，否则用户永远无法首次登录。

    用格式非法的请求体触发 422：能走到校验说明认证依赖没有拦在前面，
    同时避免真的去调用微信接口。
    """

    response = await client.post("/api/v1/auth/wechat/login", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] != "AUTHENTICATION_REQUIRED"


async def test_health_endpoints_stay_public(client: AsyncClient) -> None:
    """探针端点必须免认证，否则负载均衡无法摘流。"""

    assert (await client.get("/health/live")).status_code == 200
