import json

from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.middleware import TrustedProxyHeadersMiddleware
from app.main import create_app


async def test_json_request_body_limit_rejects_before_routing() -> None:
    app = create_app(
        Settings(
            environment="test",
            max_json_body_bytes=64 * 1024,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/does-not-exist",
            content=b'{"data":"' + (b"x" * (64 * 1024)) + b'"}',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_non_json_body_is_not_buffered_or_subject_to_json_limit() -> None:
    app = create_app(
        Settings(
            environment="test",
            max_json_body_bytes=64 * 1024,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/does-not-exist",
            content=b"x" * (65 * 1024),
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_404"


async def _run_proxy_middleware(
    *,
    peer: str,
    headers: list[tuple[bytes, bytes]],
    trusted_proxy_cidrs: list[str],
) -> dict[str, str]:
    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {
                "client": scope["client"][0],
                "scheme": scope["scheme"],
            }
        )
        await response(scope, receive, send)

    middleware = TrustedProxyHeadersMiddleware(
        downstream,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": (peer, 54321),
        "server": ("api", 8000),
    }
    await middleware(scope, receive, send)
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    result: dict[str, str] = json.loads(body)
    return result


async def test_untrusted_peer_cannot_spoof_forwarding_headers() -> None:
    result = await _run_proxy_middleware(
        peer="203.0.113.10",
        headers=[
            (b"x-forwarded-for", b"198.51.100.9"),
            (b"x-forwarded-proto", b"https"),
        ],
        trusted_proxy_cidrs=["10.42.7.0/24"],
    )

    assert result == {"client": "203.0.113.10", "scheme": "http"}


async def test_trusted_proxy_uses_rightmost_untrusted_hop() -> None:
    result = await _run_proxy_middleware(
        peer="10.42.7.4",
        headers=[
            (b"x-forwarded-for", b"192.0.2.99, 198.51.100.21, 10.42.7.3"),
            (b"x-forwarded-proto", b"http, https"),
        ],
        trusted_proxy_cidrs=["10.42.7.0/24"],
    )

    assert result == {"client": "198.51.100.21", "scheme": "https"}


async def test_malformed_or_duplicate_forwarded_for_is_ignored() -> None:
    malformed = await _run_proxy_middleware(
        peer="10.42.7.4",
        headers=[(b"x-forwarded-for", b"198.51.100.21, invalid")],
        trusted_proxy_cidrs=["10.42.7.0/24"],
    )
    duplicated = await _run_proxy_middleware(
        peer="10.42.7.4",
        headers=[
            (b"x-forwarded-for", b"198.51.100.21"),
            (b"x-forwarded-for", b"192.0.2.99"),
        ],
        trusted_proxy_cidrs=["10.42.7.0/24"],
    )

    assert malformed["client"] == "10.42.7.4"
    assert duplicated["client"] == "10.42.7.4"
