from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app


async def test_app_errors_use_stable_safe_contract() -> None:
    app = create_app(Settings(environment="test"))
    router = APIRouter()

    @router.get("/expected-error")
    async def expected_error() -> None:
        raise AppError(
            code="ASSET_NOT_READY",
            message="图片仍在处理中。",
            status_code=409,
        )

    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/expected-error")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSET_NOT_READY"
    assert response.json()["error"]["message"] == "图片仍在处理中。"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_unknown_routes_do_not_leak_internal_details() -> None:
    app = create_app(Settings(environment="test"))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_404"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
