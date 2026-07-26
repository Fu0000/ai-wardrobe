from dataclasses import dataclass

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import (
    RateLimitDecision,
    RateLimiterUnavailableError,
    RateLimitGuard,
    rate_limit_key,
)


@dataclass
class FakeRateLimiter:
    decision: RateLimitDecision | None = None
    unavailable: bool = False

    async def acquire(
        self,
        *,
        key: str,
        capacity: int,
        window_seconds: int,
        cost: int = 1,
    ) -> RateLimitDecision:
        if self.unavailable:
            raise RateLimiterUnavailableError
        return self.decision or RateLimitDecision(
            allowed=True,
            retry_after_seconds=0,
            remaining=capacity,
        )

    async def close(self) -> None:
        return None


def test_rate_limit_keys_do_not_store_raw_subjects() -> None:
    key = rate_limit_key("user", "private-user-id")

    assert key.startswith("aiw:rate:user:")
    assert "private-user-id" not in key


@pytest.mark.asyncio
async def test_rate_limit_returns_retry_after_header() -> None:
    guard = RateLimitGuard(
        limiter=FakeRateLimiter(
            decision=RateLimitDecision(
                allowed=False,
                retry_after_seconds=7,
                remaining=0,
            )
        ),
        settings=Settings(environment="test"),
    )

    with pytest.raises(AppError) as captured:
        await guard.enforce_user("user-id")

    assert captured.value.status_code == 429
    assert captured.value.headers == {"Retry-After": "7"}


@pytest.mark.asyncio
async def test_ordinary_api_fails_open_when_redis_is_unavailable() -> None:
    guard = RateLimitGuard(
        limiter=FakeRateLimiter(unavailable=True),
        settings=Settings(environment="test"),
    )

    await guard.enforce_user("user-id")


@pytest.mark.asyncio
async def test_costly_action_fails_closed_when_redis_is_unavailable() -> None:
    guard = RateLimitGuard(
        limiter=FakeRateLimiter(unavailable=True),
        settings=Settings(environment="test"),
    )

    with pytest.raises(AppError) as captured:
        await guard.enforce_costly_action("user-id")

    assert captured.value.status_code == 503
    assert captured.value.code == "RATE_LIMITER_UNAVAILABLE"
