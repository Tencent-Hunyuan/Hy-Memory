"""kuzu 缺失时的报错必须给出可执行的替代路径。

pyproject 用 marker 把 kuzu 限制在 Python < 3.14（上游还没有该版本的 wheel），
所以「kuzu 没装」在 3.14 上是预期状态，不是用户漏装了一个普通依赖。原来的
提示只有 `pip install kuzu`，照做会在 3.14 上再次撞上 sdist 编译失败。
"""

import asyncio
import sys

import pytest

from hy_memory.config import MemoryConfig


def _cfg(path: str) -> MemoryConfig:
    cfg = MemoryConfig()
    cfg.graph_store.provider = "kuzu"
    cfg.graph_store.db_path = path
    return cfg


def test_missing_kuzu_points_at_a_working_alternative(tmp_path, monkeypatch):
    # sys.modules 里放 None 会让 `import kuzu` 直接抛 ImportError，与 3.14 上
    # 它被 marker 排除后的状态一致。
    monkeypatch.setitem(sys.modules, "kuzu", None)

    from hy_memory.data.graph_store_kuzu import KuzuGraphStore

    store = KuzuGraphStore(_cfg(str(tmp_path / "g")))
    with pytest.raises(ImportError) as excinfo:
        asyncio.run(store.initialize())

    message = str(excinfo.value)
    assert "pip install kuzu" in message
    # 3.14 上这条必须指向不需要 kuzu 的后端，否则用户会照着再失败一次。
    assert "hy-memory[graph]" in message
    assert "3.14" in message
