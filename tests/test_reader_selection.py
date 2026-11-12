import asyncio

from hy_memory.config import MemoryConfig
from hy_memory.pipelines.reader_base import BaseReadPipeline
from hy_memory.pipelines.reader_hybrid import HybridReadPipeline
from hy_memory.pipelines.reader_legacy import LegacyReadPipeline


def test_legacy_hybrid_and_native_are_registered(monkeypatch):
    from hy_memory.pipelines._retrieval import config

    assert config.ALL_READERS == ("legacy", "hybrid", "native")
    assert config.resolve_reader_name("native") == "native"
    assert config.resolve_reader_name("legacy") == "legacy"
    assert config.resolve_reader_name("hybrid") == "hybrid"
    assert config.resolve_reader_name("hybrid_v2") == "legacy"

    monkeypatch.setenv("HY_MEMORY_READER", "mem0")
    assert config.resolve_reader_name() == "legacy"


def test_readers_share_the_exact_same_processing_pipeline():
    assert issubclass(LegacyReadPipeline, BaseReadPipeline)
    assert issubclass(HybridReadPipeline, BaseReadPipeline)
    assert LegacyReadPipeline.read is BaseReadPipeline.read
    assert HybridReadPipeline.read is BaseReadPipeline.read
    assert LegacyReadPipeline._apply_graph_rerank is BaseReadPipeline._apply_graph_rerank
    assert HybridReadPipeline._apply_graph_rerank is BaseReadPipeline._apply_graph_rerank


def test_reader_difference_is_keyword_recall_only():
    assert LegacyReadPipeline.VERSION == "legacy"
    assert HybridReadPipeline.VERSION == "hybrid"
    assert LegacyReadPipeline.ENABLE_KEYWORD_RECALL is False
    assert HybridReadPipeline.ENABLE_KEYWORD_RECALL is True
    runtime_metadata = {
        "__module__", "__doc__", "__abstractmethods__", "_abc_impl",
    }
    assert set(LegacyReadPipeline.__dict__) - runtime_metadata == {
        "VERSION", "ENABLE_KEYWORD_RECALL",
    }
    assert set(HybridReadPipeline.__dict__) - runtime_metadata == {
        "VERSION", "ENABLE_KEYWORD_RECALL",
    }


class _KeywordStore:
    def __init__(self):
        self.calls = []

    async def keyword_search(self, **kwargs):
        self.calls.append(kwargs)
        return [{"node_id": "kw"}]


def test_keyword_recall_strategy_switch():
    store = _KeywordStore()
    legacy = LegacyReadPipeline(MemoryConfig())
    hybrid = HybridReadPipeline(MemoryConfig())

    assert asyncio.run(legacy._keyword_search(store, query="q")) == []
    assert store.calls == []
    assert asyncio.run(hybrid._keyword_search(store, query="q")) == [{"node_id": "kw"}]
    assert store.calls == [{"query": "q"}]


def test_local_rerank_defaults_off_and_is_shared():
    config = MemoryConfig()
    assert config.recall.local_rerank_enabled is False
    assert config.recall.local_rerank_model == "nreimers/mmarco-mMiniLMv2-L6-H384-v1"
    assert LegacyReadPipeline._read_once is BaseReadPipeline._read_once
    assert HybridReadPipeline._read_once is BaseReadPipeline._read_once
