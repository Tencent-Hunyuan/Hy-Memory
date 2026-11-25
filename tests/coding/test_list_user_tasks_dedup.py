# -*- coding: utf-8 -*-
"""list_user_tasks 返回去重后的 task 列表。

docstring 约定是「该 user 已有的 task 列表（最近修改的优先），供 extractor
prompt 引用」。同一 task 落在多条 experience 上是常态（同一任务反复改），不去重
时重复行会把 LIMIT 名额吃光，extractor 的 prompt 里只能看到同一个任务名。
"""
import os

from hy_memory.coding.repository_sqlite import SqliteMemoryRepository
from hy_memory.coding.types import CodingMemory


def _memory(key: str, task: str, updated_at) -> CodingMemory:
    return CodingMemory(
        experience_id=key, user_id="u1", agent_id="a1", task=task,
        solution="方案", search_keys=[], confidence=0.9,
    )


def test_list_user_tasks_dedupes(tmp_path):
    repo = SqliteMemoryRepository(
        str(tmp_path / "memory.db"), str(tmp_path / "trace.db"),
    )
    repo.initialize()
    try:
        from datetime import datetime

        repo.upsert_experience(_memory(
            "e1", "调优缓存", datetime(2026, 1, 1),
        ))
        repo.upsert_experience(_memory(
            "e2", "重构登录", datetime(2026, 1, 2),
        ))
        repo.upsert_experience(_memory(
            "e3", "重构登录", datetime(2026, 1, 3),  # 同 task 最近更新，占 DESC 头部
        ))
        tasks = repo.list_user_tasks("u1", 2)
        assert tasks == ["重构登录", "调优缓存"], tasks
    finally:
        repo.close()
