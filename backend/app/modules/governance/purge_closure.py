"""删除闭包的结构性守卫。

`purge_account` 的契约是「注销后该用户不留下任何可归因数据」。这个契约靠人工
记忆维持是不可靠的——`beta_feedback` 表就是在新增模块时漏接进删除闭包的。

本模块把「哪些表算用户数据」「哪些表按设计豁免」变成可被断言的声明，配套的
`tests/test_deletion_closure.py` 会在新增用户数据表未接入删除闭包时直接失败。
该守卫不依赖数据库，因此在普通 CI 中即可拦截遗漏。
"""

from app.database import models as _models  # noqa: F401  # 触发全部模型注册
from app.database.base import Base

# ---------------------------------------------------------------------------
# 豁免登记
# ---------------------------------------------------------------------------
# 只有「按设计必须在注销后留存」的表才能登记在此，每条都要写明理由。
# 新增用户数据表时，正确做法是补 purge_account，而不是往这里加名字。
# ---------------------------------------------------------------------------
PURGE_EXEMPT_TABLES: dict[str, str] = {
    "deletion_jobs": "当前注销任务需存活至执行结束，供 Worker 回写终态",
    "generation_jobs": "当前注销任务对应的 Job 需存活至执行结束，其余 Job 已删除",
}

# users 表自身不含指向 users 的外键，不会被 user_owned_tables 反射到。
# 注销后保留 DELETED 墓碑行用于重复登录识别与审计追溯，标识列随 user_identities 删除。
USER_TABLE_NAME = "users"


def user_owned_tables() -> dict[str, str]:
    """反射出所有引用 `users` 的表，返回 表名 -> 用户外键列名。

    以 SQLAlchemy 元数据为准而非硬编码清单，新增模型会被自动纳入守卫范围。
    """

    owned: dict[str, str] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if any(key.column.table.name == "users" for key in column.foreign_keys):
                owned[table.name] = column.name
                break
    return owned


def tables_requiring_purge() -> dict[str, str]:
    """必须被 `purge_account` 清空的表。"""

    return {
        name: column
        for name, column in user_owned_tables().items()
        if name not in PURGE_EXEMPT_TABLES
    }
