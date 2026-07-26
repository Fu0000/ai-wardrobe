"""集成测试的共享标记。

放在独立模块而非 conftest：从 `tests.integration.conftest` 显式导入会让
mypy 把同一文件识别为两个模块名（conftest 与 tests.integration.conftest）。
conftest 只负责 fixture，标记走这里。
"""

import os

import pytest

RUN_INTEGRATION_TESTS = os.getenv("AIW_RUN_INTEGRATION_TESTS") == "1"

requires_services = pytest.mark.skipif(
    not RUN_INTEGRATION_TESTS,
    reason="set AIW_RUN_INTEGRATION_TESTS=1 with disposable PostgreSQL and Redis",
)
