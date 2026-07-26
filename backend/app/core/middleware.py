import re
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from time import perf_counter
from uuid import uuid4

import structlog
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.telemetry import current_trace_fields

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_MAX_FORWARDED_HOPS = 20
logger = structlog.get_logger(__name__)

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network


def _is_trusted_proxy(address: IPAddress, networks: tuple[IPNetwork, ...]) -> bool:
    return any(address in network for network in networks)


def resolve_forwarded_client(
    peer_host: str,
    forwarded_for_values: list[str],
    trusted_networks: tuple[IPNetwork, ...],
) -> str:
    """Resolve the client from a proxy-appended X-Forwarded-For chain.

    The immediate peer must be trusted. Scanning from the right ignores spoofed
    entries to the left of the first untrusted address.
    """

    try:
        peer = ip_address(peer_host)
    except ValueError:
        return peer_host
    if not _is_trusted_proxy(peer, trusted_networks):
        return peer_host
    if len(forwarded_for_values) != 1:
        return peer_host

    parts = [part.strip() for part in forwarded_for_values[0].split(",")]
    if not parts or len(parts) > _MAX_FORWARDED_HOPS or any(not part for part in parts):
        return peer_host
    try:
        forwarded = [ip_address(part) for part in parts]
    except ValueError:
        return peer_host

    for address in reversed([*forwarded, peer]):
        if not _is_trusted_proxy(address, trusted_networks):
            return str(address)
    return str(forwarded[0])


class TrustedProxyHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, trusted_proxy_cidrs: list[str]) -> None:
        self.app = app
        self.trusted_networks: tuple[IPNetwork, ...] = tuple(
            ip_network(cidr, strict=False) for cidr in trusted_proxy_cidrs
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("client") is None:
            await self.app(scope, receive, send)
            return

        peer_host, peer_port = scope["client"]
        try:
            peer = ip_address(peer_host)
        except ValueError:
            await self.app(scope, receive, send)
            return
        if not _is_trusted_proxy(peer, self.trusted_networks):
            await self.app(scope, receive, send)
            return

        forwarded_for = self._header_values(scope, b"x-forwarded-for")
        scope["client"] = (
            resolve_forwarded_client(
                peer_host,
                forwarded_for,
                self.trusted_networks,
            ),
            peer_port,
        )

        forwarded_proto = self._header_values(scope, b"x-forwarded-proto")
        if len(forwarded_proto) == 1:
            proto_chain = [part.strip().lower() for part in forwarded_proto[0].split(",")]
            if (
                proto_chain
                and len(proto_chain) <= _MAX_FORWARDED_HOPS
                and all(proto in {"http", "https"} for proto in proto_chain)
            ):
                scope["scheme"] = proto_chain[-1]

        await self.app(scope, receive, send)

    @staticmethod
    def _header_values(scope: Scope, name: bytes) -> list[str]:
        return [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.lower() == name
        ]


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_json_body_bytes: int) -> None:
        self.app = app
        self.max_json_body_bytes = max_json_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_json_request(scope):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_json_body_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_json_body_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_body() -> Message:
            nonlocal replayed
            if replayed or disconnected:
                return {"type": "http.disconnect"}
            replayed = True
            return {
                "type": "http.request",
                "body": bytes(body),
                "more_body": False,
            }

        await self.app(scope, replay_body, send)

    @staticmethod
    def _is_json_request(scope: Scope) -> bool:
        content_type = Headers(scope=scope).get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        return media_type == "application/json" or media_type.endswith("+json")

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = str(scope.get("state", {}).get("request_id", "unknown"))
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "REQUEST_BODY_TOO_LARGE",
                    "message": "请求数据超过允许大小。",
                    "request_id": request_id,
                    "details": None,
                }
            },
        )
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, enable_hsts: bool) -> None:
        self.app = app
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                if self.enable_hsts:
                    response_headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, request_id_header: str = "X-Request-ID") -> None:
        self.app = app
        self.request_id_header = request_id_header
        self.request_id_header_bytes = request_id_header.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        candidate = headers.get(self.request_id_header, "")
        request_id = candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        trace_fields = current_trace_fields()
        scope["state"].update(trace_fields)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope["method"],
            path=scope["path"],
            **trace_fields,
        )
        started_at = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (self.request_id_header_bytes, request_id.encode("latin-1"))
                )
                trace_id = trace_fields.get("trace_id")
                if trace_id is not None:
                    response_headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            await logger.ainfo(
                "request_completed",
                status_code=status_code,
                latency_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
