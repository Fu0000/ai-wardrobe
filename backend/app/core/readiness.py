import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import structlog
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings
from app.database.session import Database
from app.modules.assets.storage import ObjectStorage

DependencyState = Literal["ok", "failed", "disabled"]
logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    dependencies: dict[str, DependencyState]

    @property
    def ready(self) -> bool:
        return all(state != "failed" for state in self.dependencies.values())


class ReadinessProbe(Protocol):
    async def check(self) -> ReadinessReport: ...

    async def close(self) -> None: ...


class UninitializedReadinessProbe:
    async def check(self) -> ReadinessReport:
        return ReadinessReport(dependencies={"application": "failed"})

    async def close(self) -> None:
        return None


class ReadinessChecker:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        redis_client: Redis,
        object_storage: ObjectStorage,
    ) -> None:
        self._settings = settings
        self._database = database
        self._redis = redis_client
        self._object_storage = object_storage

    async def check(self) -> ReadinessReport:
        checks: dict[str, Callable[[], Awaitable[object]]] = {
            "database": self._check_database,
            "redis": self._check_redis,
        }
        dependencies: dict[str, DependencyState] = {}
        if self._settings.cos_enabled:
            checks["object_storage"] = self._check_object_storage
        else:
            dependencies["object_storage"] = "disabled"

        results = await asyncio.gather(
            *(self._run_check(name, check) for name, check in checks.items())
        )
        dependencies.update(results)
        return ReadinessReport(dependencies=dict(sorted(dependencies.items())))

    async def _run_check(
        self,
        name: str,
        check: Callable[[], Awaitable[object]],
    ) -> tuple[str, DependencyState]:
        try:
            await asyncio.wait_for(
                check(),
                timeout=self._settings.readiness_timeout_seconds,
            )
        except Exception:
            await logger.awarning("readiness_dependency_failed", dependency=name)
            return name, "failed"
        return name, "ok"

    async def _check_database(self) -> object:
        async with self._database.engine.connect() as connection:
            return await connection.execute(text("SELECT 1"))

    async def _check_redis(self) -> object:
        return await cast(Awaitable[object], self._redis.ping())

    async def _check_object_storage(self) -> object:
        return await self._object_storage.check_health()

    async def close(self) -> None:
        await self._redis.aclose()


def build_readiness_probe(
    *,
    settings: Settings,
    database: Database,
    object_storage: ObjectStorage,
) -> ReadinessProbe:
    redis_client = Redis.from_url(
        settings.redis_url,
        # 同 rate_limit：encoding 必须是合法编码名，None 会让带字符串参数的
        # 命令抛 TypeError。
        encoding="utf-8",
        decode_responses=False,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
        health_check_interval=30,
    )
    return ReadinessChecker(
        settings=settings,
        database=database,
        redis_client=redis_client,
        object_storage=object_storage,
    )
