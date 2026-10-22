# -*- coding: utf-8 -*-
"""
search 侧的 layers 服务端筛选（R3：CTX 需按类型定向检索）。

两层保障，缺一不可：

1. 定向召回（reader 侧）：把 layers 下推到召回阶段，避免无关层级挤占 limit
   名额导致目标层级召回不足。client 层的过滤只能筛掉多余的，补不回少召的。
2. 兜底过滤（client 侧）：各 reader 对 request.layers 的支持程度不一，profile /
   graph 路普遍按固定层级召回、不吃 request.layers。契约是「只返回命中层级」，
   故返回前再筛一次。

本文件锁死第 2 层，以及入参归一化与向后兼容。
"""

import asyncio
import inspect

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.models.memory import MemoryLayer


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestSearchSignature:
    def test_async_search_exposes_layers(self):
        params = inspect.signature(HyMemoryClient.async_search).parameters
        assert "layers" in params
        assert params["layers"].default is None, "不传时必须与改动前行为一致"

    def test_sync_search_exposes_layers(self):
        params = inspect.signature(HyMemoryClient.search).parameters
        assert "layers" in params
        assert params["layers"].default is None

    def test_search_uses_schema_parameter_names(self):
        for method in (HyMemoryClient.search, HyMemoryClient.async_search):
            params = inspect.signature(method).parameters
            assert "schema_limit" in params
            assert "schema_min_score" in params
            assert "profile_limit" not in params
            assert "profile_min_score" not in params


class TestReadRequestWiring:
    """layers 必须真的下推到 ReadRequest，否则 reader 无从定向召回。"""

    def test_read_request_accepts_layers_as_str_list(self):
        from hy_memory.pipelines.base import ReadRequest

        req = ReadRequest(query="q", layers=["l2_fact"])
        assert req.layers == ["l2_fact"], (
            "ReadRequest.layers 是 List[str]；client 侧须把枚举转回字符串"
        )

    def test_normalize_layers_output_is_enum(self):
        """client 内部统一成枚举，写入 ReadRequest 前再取 .value。"""
        got = HyMemoryClient._normalize_layers(["l2_fact"])
        assert got == [MemoryLayer.L2_FACT]
        assert [l.value for l in got] == ["l2_fact"]

    def test_legacy_alias_normalized_before_recall(self):
        """旧编号要在入口就归一，否则 reader 按原样比对会漏召回。"""
        got = HyMemoryClient._normalize_layers(["l5_schema"])
        assert [l.value for l in got] == ["l6_schema"]

    def test_invalid_layer_raises(self):
        with pytest.raises(ValueError):
            HyMemoryClient._normalize_layers(["l3_identity"])


class TestDedupKindPairing:
    """dedup 的 _memory_kind 必须与它的 public item 来自同一条 reader 结果。"""

    def test_memory_kind_follows_source_after_layer_filter(self):
        memories = [
            {"memory_id": "raw", "layer": "l1_raw", "content": "raw"},
            {
                "memory_id": "canon",
                "layer": "l2_fact",
                "content": "canon",
                "_memory_kind": "canonical_state",
            },
        ]
        all_mems, dedup_mems = HyMemoryClient._public_search_views(
            memories,
            wanted_layers={"l2_fact"},
            lite_mode=False,
        )
        assert [m["memory_id"] for m in all_mems] == ["canon"]
        assert dedup_mems[0]["_memory_kind"] == "canonical_state"


class TestHybridDirectedRecall:
    """共享 reader 主流程必须把 request.layers 下推到召回阶段。"""

    def test_normal_channels_use_request_layers(self):
        import hy_memory.pipelines.reader_base as m

        src = inspect.getsource(m)
        assert "_normal_layers" in src, "normal 路必须按 request.layers 定向召回"
        # 语义、BM25 和按日期补召回都必须遵守同一层级过滤。
        assert src.count("layers=_normal_layers") == 3

    def test_hidden_l0_context_keeps_fixed_layer(self):
        """L0 remains a fixed hidden context query, not a public Search result."""
        import hy_memory.pipelines.reader_base as m

        src = inspect.getsource(m)
        assert "layers=_VDB_HIDDEN_L0_LAYERS" in src

    def test_recall_task_order_unchanged(self):
        """三路结果按固定下标 unpack，不得因筛选跳过某路而错位。"""
        import hy_memory.pipelines.reader_base as m

        src = inspect.getsource(m)
        assert "results[0]" in src and "results[1]" in src and "results[2]" in src
