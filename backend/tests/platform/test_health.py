from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.readiness import ReadinessReport
from app.main import create_app


class FakeReadinessProbe:
    def __init__(self, dependencies: dict[str, str]) -> None:
        self._dependencies = dependencies

    async def check(self) -> ReadinessReport:
        return ReadinessReport(dependencies=self._dependencies)  # type: ignore[arg-type]

    async def close(self) -> None:
        return None


async def test_liveness_returns_versioned_service_metadata() -> None:
    app = create_app(Settings(environment="test"))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json()["status"] == "ok"
    assert response.json()["environment"] == "test"
    assert response.json()["version"] == "0.1.0"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Strict-Transport-Security" not in response.headers


async def test_readiness_reports_only_sanitized_dependency_states() -> None:
    app = create_app(Settings(environment="test"))
    app.state.readiness_probe = FakeReadinessProbe(
        {
            "database": "ok",
            "object_storage": "disabled",
            "redis": "ok",
        }
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["dependencies"] == {
        "database": "ok",
        "object_storage": "disabled",
        "redis": "ok",
    }


async def test_readiness_fails_closed_when_a_required_dependency_is_down() -> None:
    app = create_app(Settings(environment="test"))
    app.state.readiness_probe = FakeReadinessProbe(
        {
            "database": "ok",
            "object_storage": "disabled",
            "redis": "failed",
        }
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "3"
    assert response.json()["error"]["code"] == "SERVICE_NOT_READY"
    assert response.json()["error"]["details"] == [
        {"dependency": "database", "status": "ok"},
        {"dependency": "object_storage", "status": "disabled"},
        {"dependency": "redis", "status": "failed"},
    ]


async def test_valid_request_id_is_propagated() -> None:
    app = create_app(Settings(environment="test"))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/meta",
            headers={"X-Request-ID": "test-request-1234"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-1234"


async def test_short_or_invalid_request_id_is_replaced() -> None:
    app = create_app(Settings(environment="test"))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/health/live",
            headers={"X-Request-ID": "bad"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "bad"
    assert len(response.headers["X-Request-ID"]) == 32
