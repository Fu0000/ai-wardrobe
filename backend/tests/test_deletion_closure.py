"""删除闭包守卫：新增用户数据表若未接入 purge_account，本测试失败。

`beta_feedback` 曾因新增模块时漏接删除闭包而在注销后留存用户自由文本。补一次
数据只解决当次，因此这里用静态分析把「删除闭包是否完整」变成每次 CI 都会跑的
断言——不依赖数据库，Docker 不可用时同样生效。
"""

import ast
from pathlib import Path

from app.modules.governance.purge_closure import (
    PURGE_EXEMPT_TABLES,
    tables_requiring_purge,
    user_owned_tables,
)

_REPOSITORY_PATH = (
    Path(__file__).resolve().parents[1] / "app/modules/governance/deletion_repository.py"
)


def _models_deleted_in_purge_account() -> set[str]:
    """静态解析 purge_account，取出所有 delete(Model) 中的模型名。

    用 AST 而非 grep，避免注释或字符串里的同名词造成假阳性。
    """

    tree = ast.parse(_REPOSITORY_PATH.read_text(encoding="utf-8"))
    purge = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "purge_account"
    )

    deleted: set[str] = set()
    for node in ast.walk(purge):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "delete" and node.args:
            target = node.args[0]
            if isinstance(target, ast.Name):
                deleted.add(target.id)
    return deleted


def _model_name_by_table() -> dict[str, str]:
    from app.database.base import Base

    return {
        mapper.local_table.name: mapper.class_.__name__
        for mapper in Base.registry.mappers
        if mapper.local_table is not None
    }


def test_every_user_owned_table_is_deleted_or_exempt() -> None:
    """核心守卫：每张用户数据表要么被 purge_account 删除，要么已登记豁免。"""

    deleted_models = _models_deleted_in_purge_account()
    model_by_table = _model_name_by_table()

    missing: list[str] = []
    for table_name in sorted(tables_requiring_purge()):
        model_name = model_by_table.get(table_name)
        if model_name is None or model_name not in deleted_models:
            missing.append(f"{table_name}({model_name})")

    assert not missing, (
        f"这些用户数据表未接入 purge_account：{missing}。"
        "请在 purge_account 中补 delete(...)，而不是加入 PURGE_EXEMPT_TABLES。"
    )


def test_beta_feedback_is_covered() -> None:
    """回归锁定：本轮修复的具体缺口不得再次出现。"""

    owned = user_owned_tables()
    assert owned.get("beta_feedback") == "user_id"
    assert "BetaFeedback" in _models_deleted_in_purge_account()


def test_exemptions_are_still_user_owned_tables() -> None:
    """豁免登记不得与模型脱节。"""

    owned = user_owned_tables()
    stale = sorted(set(PURGE_EXEMPT_TABLES) - set(owned))
    assert not stale, f"豁免登记中的表已不再引用 users，请清理：{stale}"


def test_reflection_discovers_known_user_tables() -> None:
    """自检：反射机制若失效，守卫会静默变成空转，这里先失败。"""

    owned = user_owned_tables()
    for table_name in (
        "user_identities",
        "user_profiles",
        "user_assets",
        "source_photos",
        "style_diagnoses",
        "style_optimization_results",
        "share_records",
        "usage_counters",
        "beta_feedback",
    ):
        assert table_name in owned, f"反射未发现 {table_name}，删除闭包守卫已失效"
