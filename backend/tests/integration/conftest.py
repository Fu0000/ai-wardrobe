"""集成测试的共享装置。

每个集成测试原本各自构造 Settings、引擎、连接与事务，并手写清理逻辑。
重复的样板不只是冗余——`test_stale_job_reaper_db` 用手写 `_cleanup` 删数据，
一旦断言中途失败就会留下脏数据污染后续用例。

这里统一提供「连接级事务 + 结束回滚」的 session，任何写入都不会落盘，
因此不需要也不应该再写清理代码。
"""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import Table, delete
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.core.config import Settings
from app.database import models as database_models  # noqa: F401  # 注册全部模型
from app.database.base import Base
from app.database.session import Database
from app.modules.governance.purge_closure import user_owned_tables

# 整个 integration 包共用同一道开关，各测试文件不必再各自声明。
pytestmark = pytest.mark.integration


def _user_scoped_cleanup_order() -> list[Table]:
    """按外键依赖倒序排出用户数据表，最后删 users 本身。

    复用 purge_closure 的反射而非另写一份清单，新增用户表会自动纳入清理。
    """

    metadata = Base.metadata
    owned = user_owned_tables()
    ordered = [table for table in metadata.sorted_tables if table.name in owned]
    ordered.reverse()  # sorted_tables 是建表序，删除要反过来
    ordered.append(metadata.tables["users"])
    return ordered


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest_asyncio.fixture
async def connection(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """一条处于未提交事务中的连接，退出时无条件回滚。"""

    engine = create_async_engine(settings.database_url)
    conn = await engine.connect()
    transaction = await conn.begin()
    try:
        yield conn
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """绑定在回滚事务上的会话。写入对本用例可见，但永不落盘。"""

    async with AsyncSession(bind=connection, expire_on_commit=False) as active:
        yield active


@pytest_asyncio.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    """真实的 Database 实例，供需要自行开事务的被测代码使用。

    注意：它不共享 `session` 的回滚事务——被测代码会真正提交。用它的测试
    应配合 `purge_users` 登记需要清理的用户。
    """

    instance = Database(settings)
    try:
        yield instance
    finally:
        await instance.dispose()


@pytest_asyncio.fixture
async def purge_users(database: Database) -> AsyncIterator[list[UUID]]:
    """登记需要在用例结束后清理的用户 ID。

    被测代码真正提交时无法靠回滚兜底，只能显式删除。放在 fixture 的
    teardown 而非用例的 finally 里，断言中途失败也一定会执行。
    """

    user_ids: list[UUID] = []
    try:
        yield user_ids
    finally:
        # 不能在 finally 里 return——那会吞掉用例本身的异常（B012）。
        if user_ids:
            async with database.session_factory() as cleanup:
                for table in _user_scoped_cleanup_order():
                    column = table.c["user_id"] if "user_id" in table.c else table.c["id"]
                    await cleanup.execute(delete(table).where(column.in_(user_ids)))
                await cleanup.commit()
