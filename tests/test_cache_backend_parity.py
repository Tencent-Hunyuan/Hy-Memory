"""所有 cache 后端必须实现同一组**有状态**方法。

为什么需要这条守卫：`CacheBase` 给有状态方法留了 no-op 默认实现（`return None` / `return False`），
好处是新后端不必全量实现就能跑，坏处是**漏实现会静默降级、不报任何错**。

实测代价：dev10 的 episode 划分只在 `cache_sqlite` 实现了，而生产用的是 `cache_mysql`，
于是 `get_episode_state` 永远返回 None → 每次写入都开新 episode。线上 8798 次划分决策
**全是 `new`、`idle_seconds` 全为 null**，功能等于完全没生效，却一条错误日志都没有。

审计类方法（memory_operations / pipeline_logs / metrics）不在此列 —— 它们丢了只影响观测。
这里只管**会改变功能行为**的状态方法。
"""

import inspect

import pytest

from hy_memory.data.cache_base import CacheBase
from hy_memory.data.cache_mysql import MysqlCache
from hy_memory.data.cache_sqlite import SqliteCache


# 会改变功能行为的有状态方法：漏实现 = 功能静默失效
STATEFUL_METHODS = [
    "get_episode_state",
    "upsert_episode_state",
    "claim_write_outcome",
    "finish_write_outcome",
    "get_write_outcome",
]

BACKENDS = [
    pytest.param(SqliteCache, id="sqlite"),
    pytest.param(MysqlCache, id="mysql"),
]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("method", STATEFUL_METHODS)
def test_backend_overrides_stateful_method(backend, method):
    """后端必须**自己实现**，不能继承基类的 no-op。"""
    assert hasattr(backend, method), f"{backend.__name__} 缺少 {method}"
    own = getattr(backend, method)
    base = getattr(CacheBase, method)
    assert own is not base, (
        f"{backend.__name__}.{method} 直接继承了 CacheBase 的 no-op 默认实现 —— "
        f"该后端上此功能会静默失效（不报错、行为退化）"
    )


@pytest.mark.parametrize("method", STATEFUL_METHODS)
def test_signatures_match_base(method):
    """签名要和基类一致，否则调用方按基类签名传参会 TypeError（且常被 except 吞掉）。"""
    base_sig = inspect.signature(getattr(CacheBase, method))
    for backend in (SqliteCache, MysqlCache):
        sig = inspect.signature(getattr(backend, method))
        assert list(sig.parameters) == list(base_sig.parameters), (
            f"{backend.__name__}.{method} 签名与 CacheBase 不一致：\n"
            f"  base: {base_sig}\n  impl: {sig}"
        )


@pytest.mark.parametrize("backend", BACKENDS)
def test_episode_state_table_declared(backend):
    """建表 DDL 里必须有 episode_state —— 方法实现了但表没建同样会静默失效。"""
    import hy_memory.data.cache_mysql as m
    import hy_memory.data.cache_sqlite as s

    mod = s if backend is SqliteCache else m
    src = inspect.getsource(mod)
    assert "CREATE TABLE IF NOT EXISTS episode_state" in src, (
        f"{mod.__name__} 未声明 episode_state 建表语句"
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_write_outcomes_table_declared(backend):
    import hy_memory.data.cache_mysql as m
    import hy_memory.data.cache_sqlite as s

    mod = s if backend is SqliteCache else m
    src = inspect.getsource(mod)
    assert "CREATE TABLE IF NOT EXISTS write_outcomes" in src


class TestRollingSummaryFullyRemoved:
    """rolling summary 已整体移除，残留引用会让应用在启动路径报错。"""

    @pytest.mark.parametrize("method", [
        "get_summary_buffer", "upsert_summary_buffer", "clear_summary_buffer",
    ])
    def test_summary_buffer_methods_gone(self, method):
        for cls in (CacheBase, SqliteCache, MysqlCache):
            assert not hasattr(cls, method), f"{cls.__name__} 仍有 {method}"

    def test_summary_buffer_table_gone(self):
        import hy_memory.data.cache_mysql as m
        import hy_memory.data.cache_sqlite as s

        for mod in (s, m):
            assert "summary_buffer" not in inspect.getsource(mod), (
                f"{mod.__name__} 仍有 summary_buffer 建表/查询"
            )

    def test_modules_gone(self):
        for name in ("hy_memory.agent.rolling_summary", "hy_memory.agent.summarizer"):
            with pytest.raises(ImportError):
                __import__(name)

    def test_enable_summary_switch_gone(self):
        from hy_memory.config import LLMConfig
        from hy_memory.pipelines.base import WriteRequest

        assert not hasattr(LLMConfig(), "enable_summary")
        assert not hasattr(LLMConfig(), "summary_buffer_window")
        assert not hasattr(WriteRequest(), "enable_summary")

    def test_public_api_param_gone(self):
        from hy_memory.client import HyMemoryClient

        for fn in ("add", "async_add"):
            if not hasattr(HyMemoryClient, fn):
                continue
            params = inspect.signature(getattr(HyMemoryClient, fn)).parameters
            assert "enable_summary" not in params, f"client.{fn} 仍有 enable_summary"

    def test_mem_agent_summary_path_gone(self):
        from hy_memory.agent.mem_agent import MemAgent, ProcessMode

        assert not hasattr(ProcessMode, "SUMMARY_ONLY")
        params = inspect.signature(MemAgent.process_add).parameters
        assert "enable_summary" not in params
        src = inspect.getsource(MemAgent)
        assert "self.summarizer" not in src
