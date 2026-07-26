import hashlib
import math
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol, cast

import structlog
from fastapi import Request
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.errors import AppError

logger = structlog.get_logger(__name__)

RATE_LIMIT_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now = redis.call('TIME')
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
local values = redis.call('HMGET', key, 'tokens', 'updated_at_ms')
local tokens = tonumber(values[1])
local updated_at_ms = tonumber(values[2])

if tokens == nil or updated_at_ms == nil then
  tokens = capacity
  updated_at_ms = now_ms
else
  local elapsed = math.max(0, now_ms - updated_at_ms)
  tokens = math.min(capacity, tokens + (elapsed * capacity / window_ms))
end

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after_ms = math.ceil((cost - tokens) * window_ms / capacity)
end

redis.call('HSET', key, 'tokens', tokens, 'updated_at_ms', now_ms)
redis.call('PEXPIRE', key, window_ms * 2)
return {allowed, retry_after_ms, math.floor(tokens)}
"""


class RateLimiterUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int
    remaining: int


class RateLimiter(Protocol):
    async def acquire(
        self,
        *,
        key: str,
        capacity: int,
        window_seconds: int,
        cost: int = 1,
    ) -> RateLimitDecision: ...

    async def close(self) -> None: ...


class DisabledRateLimiter:
    async def acquire(
        self,
        *,
        key: str,
        capacity: int,
        window_seconds: int,
        cost: int = 1,
    ) -> RateLimitDecision:
        return RateLimitDecision(
            allowed=True,
            retry_after_seconds=0,
            remaining=capacity,
        )

    async def close(self) -> None:
        return None


class RedisTokenBucketRateLimiter:
    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def acquire(
        self,
        *,
        key: str,
        capacity: int,
        window_seconds: int,
        cost: int = 1,
    ) -> RateLimitDecision:
        if capacity <= 0 or window_seconds <= 0 or cost <= 0:
            raise ValueError("rate limit parameters must be positive")
        try:
            raw = await cast(
                Awaitable[object],
                self._redis.eval(
                    RATE_LIMIT_LUA,
                    1,
                    key,
                    str(capacity),
                    str(window_seconds * 1_000),
                    str(cost),
                ),
            )
        except Exception as error:
            raise RateLimiterUnavailableError from error
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise RateLimiterUnavailableError
        values = cast(list[int | bytes], raw)
        allowed = int(values[0])
        retry_after_ms = int(values[1])
        remaining = int(values[2])
        return RateLimitDecision(
            allowed=allowed == 1,
            retry_after_seconds=max(1, math.ceil(retry_after_ms / 1_000)) if not allowed else 0,
            remaining=remaining,
        )

    async def close(self) -> None:
        await self._redis.aclose()


def rate_limit_key(layer: str, subject: str) -> str:
    digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
    return f"aiw:rate:{layer}:{digest}"


class RateLimitGuard:
    def __init__(self, *, limiter: RateLimiter, settings: Settings) -> None:
        self._limiter = limiter
        self._settings = settings

    async def enforce_ip(self, request: Request) -> None:
        subject = request.client.host if request.client else "unknown"
        await self._enforce(
            layer="ip",
            subject=subject,
            capacity=self._settings.rate_limit_ip_per_minute,
            window_seconds=60,
            fail_closed=False,
        )

    async def enforce_user(self, user_id: str) -> None:
        await self._enforce(
            layer="user",
            subject=user_id,
            capacity=self._settings.rate_limit_user_per_minute,
            window_seconds=60,
            fail_closed=False,
        )

    async def enforce_costly_action(self, user_id: str) -> None:
        await self._enforce(
            layer="costly",
            subject=user_id,
            capacity=self._settings.rate_limit_costly_per_minute,
            window_seconds=60,
            fail_closed=True,
        )

    async def _enforce(
        self,
        *,
        layer: str,
        subject: str,
        capacity: int,
        window_seconds: int,
        fail_closed: bool,
    ) -> None:
        try:
            decision = await self._limiter.acquire(
                key=rate_limit_key(layer, subject),
                capacity=capacity,
                window_seconds=window_seconds,
            )
        except RateLimiterUnavailableError as error:
            await logger.aerror("rate_limiter_unavailable", layer=layer)
            if fail_closed:
                raise AppError(
                    code="RATE_LIMITER_UNAVAILABLE",
                    message="服务暂时繁忙，请稍后重试。",
                    status_code=503,
                ) from error
            return

        if not decision.allowed:
            raise AppError(
                code="RATE_LIMIT_EXCEEDED",
                message="操作过于频繁，请稍后重试。",
                status_code=429,
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )

    async def close(self) -> None:
        await self._limiter.close()


def build_rate_limit_guard(settings: Settings) -> RateLimitGuard:
    if not settings.rate_limit_enabled:
        return RateLimitGuard(limiter=DisabledRateLimiter(), settings=settings)
    client = Redis.from_url(
        settings.redis_url,
        encoding=None,
        decode_responses=False,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        health_check_interval=30,
    )
    return RateLimitGuard(
        limiter=RedisTokenBucketRateLimiter(client),
        settings=settings,
    )


async def enforce_ip_rate_limit(request: Request) -> None:
    guard: RateLimitGuard = request.app.state.rate_limit_guard
    await guard.enforce_ip(request)
