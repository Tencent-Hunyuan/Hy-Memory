# -*- coding: utf-8 -*-
"""_sort_memory_nodes 在 naive 与 aware datetime 混存时不能崩溃。

memory_at 是调用方指定的发生时间，写链路不归一化时区：aware 的 ISO 字符串
（如 "2026-01-01T10:00:00+08:00"）经 parse_dt 产出 aware datetime，而
gmt_created 恒为 naive 的 datetime.now()。两类节点共存时，原排序 key 直接
比较会抛 TypeError，list_memories 整体失败。
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from hy_memory.client import HyMemoryClient


def test_sort_mixed_naive_and_aware_datetimes():
    aware = SimpleNamespace(
        memory_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        gmt_created=None,
        valid_from=None,
    )
    naive_fallback = SimpleNamespace(memory_at=None, gmt_created=None, valid_from=None)
    nodes = [naive_fallback, aware]
    out = HyMemoryClient._sort_memory_nodes(nodes, order="desc")
    assert out[0] is aware
    assert out[1] is naive_fallback


def test_sort_asc_order_still_works():
    aware = SimpleNamespace(
        memory_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        gmt_created=None,
        valid_from=None,
    )
    naive_fallback = SimpleNamespace(memory_at=None, gmt_created=None, valid_from=None)
    out = HyMemoryClient._sort_memory_nodes([aware, naive_fallback], order="asc")
    assert out[0] is naive_fallback
    assert out[1] is aware
