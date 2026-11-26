# -*- coding: utf-8 -*-
"""Neo4j 投影必须保留记忆的发生时间。

`_memory_node_params` 把 `created_at` 列写成 `getattr(node, 'created_at', None) or now()`，
但 `MemoryNode` 根本没有 `created_at` 字段（模型里叫 `memory_at` / `gmt_created`），
于是这一列**恒等于投影时刻**。读侧 `_record_to_memory_node` 又把 `created_at` 映射回
`memory_at`，所以一次写入 + 读回就把 2020 年的记忆变成"刚才"，导出再导入的克隆快照
同样把时间重置成克隆时刻。
"""

from datetime import datetime

from hy_memory.data.graph_store_neo4j import Neo4jGraphStore
from hy_memory.models.memory import MemoryLayer, MemoryNode


def _node(**kwargs) -> MemoryNode:
    base = dict(
        node_id="n1",
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        content="用户 2020 年搬到了杭州",
        layer=MemoryLayer.L2_FACT,
    )
    base.update(kwargs)
    return MemoryNode(**base)


def test_projection_keeps_the_authored_memory_at():
    node = _node(memory_at=datetime(2020, 1, 1, 8, 0, 0))

    params = Neo4jGraphStore._memory_node_params(node)

    assert params["cat"] == "2020-01-01T08:00:00"


def test_projection_falls_back_to_the_write_time_when_memory_at_is_absent():
    node = _node(gmt_created=datetime(2021, 5, 5, 10, 30, 0))
    node.memory_at = None

    params = Neo4jGraphStore._memory_node_params(node)

    assert params["cat"] == "2021-05-05T10:30:00"


def test_round_trip_through_the_projection_preserves_memory_at():
    node = _node(memory_at=datetime(2020, 1, 1, 8, 0, 0))
    params = Neo4jGraphStore._memory_node_params(node)

    back = Neo4jGraphStore._record_to_memory_node({
        "node_id": params["nid"],
        "user_id": params["uid"],
        "agent_id": params["sid"],
        "content": params["content"],
        "layer": params["layer"],
        "created_at": params["cat"],
    })

    assert back.memory_at == datetime(2020, 1, 1, 8, 0, 0)
