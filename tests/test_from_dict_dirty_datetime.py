# -*- coding: utf-8 -*-
"""from_dict 对脏 datetime 字段的容错：坏值降级 None，不能让整条反序列化崩掉。

向量库 payload 由本 SDK 自己写成 int epoch，但外部写入方（导入/迁移/手工脚本）
可能塞进任意字符串。仓库其他解析点（relational_repository._parse_dt、
pipelines/writer._collect_intentions、_retrieval/strength._epoch_seconds）对坏
日期统一降级 None，from_dict 是唯一直接抛异常的例外：一条脏 payload 会让
vector_store_base._payload_to_node 整个抛 ValueError，搜索/读取全挂。
"""
from datetime import datetime

import pytest


class TestMemoryNodeDirtyDatetime:
    def test_bad_string_degrades_to_none(self):
        from hy_memory.models.memory import MemoryNode

        node = MemoryNode.from_dict({
            "node_id": "n1", "content": "hello", "gmt_created": "not-a-date",
        })
        assert node.node_id == "n1"
        assert node.content == "hello"
        # 坏值不抛异常；__post_init__ 会把 None 兜底为当前时间
        assert node.gmt_created is not None

    def test_invalid_calendar_date_degrades_to_none(self):
        from hy_memory.models.memory import MemoryNode

        node = MemoryNode.from_dict({
            "node_id": "n2", "content": "hello", "memory_at": "2026-02-30T00:00:00",
        })
        assert node.memory_at is None

    def test_valid_values_still_parse(self):
        from hy_memory.models.memory import MemoryNode

        epoch = 1764000000
        node = MemoryNode.from_dict({
            "node_id": "n3", "content": "hello",
            "memory_at": epoch,
            "gmt_created": "2026-01-02T03:04:05",
        })
        assert isinstance(node.memory_at, datetime)
        assert node.memory_at.timestamp() == epoch
        assert node.gmt_created.year == 2026

    def test_out_of_range_epoch_degrades_to_none(self):
        from hy_memory.models.memory import MemoryNode

        node = MemoryNode.from_dict({
            "node_id": "n4", "content": "hello", "gmt_created": 10**20,
        })
        assert node.gmt_created is not None  # __post_init__ 兜底，不抛 OSError


class TestDerivedNodesDirtyDatetime:
    def test_intention_node_trigger_time(self):
        from hy_memory.models.memory import IntentionNode

        node = IntentionNode.from_dict({
            "node_id": "i1", "content": "x", "trigger_time": "not-a-date",
        })
        assert node.trigger_time is None

    def test_schema_node_last_activated_at(self):
        from hy_memory.models.memory import SchemaNode

        node = SchemaNode.from_dict({
            "node_id": "s1", "content": "x", "last_activated_at": "2026-02-30",
        })
        assert node.last_activated_at is None

    def test_knowledge_gap_dates(self):
        from hy_memory.models.memory import KnowledgeGap

        node = KnowledgeGap.from_dict({
            "gap_id": "g1", "description": "x",
            "created_at": "not-a-date", "last_scanned_at": "not-a-date",
            "resolved_at": "not-a-date",
        })
        # 坏值降级 None 后由 __post_init__ 兜底成当前时间；不抛异常即是目标
        assert isinstance(node.created_at, datetime)
        assert isinstance(node.last_scanned_at, datetime)
        assert node.resolved_at is None

    def test_life_stage_start_time(self):
        from hy_memory.models.memory import LifeStage

        node = LifeStage.from_dict({
            "stage_id": "ls1", "title": "x", "start_time": "not-a-date",
        })
        assert node.start_time is None

    def test_temporal_event_time(self):
        from hy_memory.models.memory import TemporalEvent

        node = TemporalEvent.from_dict({
            "event_id": "t1", "description": "x", "event_time": "not-a-date",
        })
        assert node.event_time is None

    def test_memory_metadata_event_time(self):
        from hy_memory.models.memory import MemoryMetadata

        node = MemoryMetadata.from_dict({
            "uid": "m1", "event_time": "not-a-date",
        })
        # 坏值降级 None 后由 __post_init__ 兜底成当前时间；不抛异常即是目标
        assert isinstance(node.event_time, datetime)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
