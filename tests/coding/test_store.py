# -*- coding: utf-8 -*-
"""CodingMemoryStore 测试"""

import json
import os
import sqlite3
from datetime import datetime

import pytest

from hy_memory.coding.store import _vec_node_id, _derive_coding_collection_name
from hy_memory.coding.types import CodingMemory, WorkingFact


# ================================================================
# Helpers
# ================================================================

class TestHelpers:
    def test_vec_node_id_format(self):
        assert _vec_node_id("e1", 0) == "e1:0"
        assert _vec_node_id("e1", 5) == "e1:5"

    def test_derive_collection_name(self):
        assert _derive_coding_collection_name("agent_memories") == "agent_memories_coding_keys"
        assert _derive_coding_collection_name(None) == "agent_memories_coding_keys"
        assert _derive_coding_collection_name("custom") == "custom_coding_keys"


# ================================================================
# Store e2e（用真实 chroma + mock embed）
# ================================================================

@pytest.mark.asyncio
class TestStoreCRUD:
    async def test_insert_get(self, coding_store):
        m = CodingMemory(
            experience_id="e1", user_id="u1", agent_id="a1",
            task="打包发布", search_keys=["凭证位置"], solution="...",
            boundary_envs="twine>=6", workspace_id="ws/a",
            files=["s.py"], confidence=0.9,
        )
        await coding_store.insert(m)
        got = await coding_store.get_by_id("e1", user_id="u1")
        assert got is not None
        assert got.task == "打包发布" and got.search_keys == ["凭证位置"]
        assert got.boundary_envs == "twine>=6"
        assert got.files == ["s.py"]

    async def test_get_by_id_wrong_user(self, coding_store):
        m = CodingMemory(experience_id="e1", user_id="u1", task="T", solution="S")
        await coding_store.insert(m)
        # 错的 user_id 应拿不到
        got = await coding_store.get_by_id("e1", user_id="u_wrong")
        assert got is None

    async def test_list_user_tasks(self, coding_store):
        await coding_store.insert(CodingMemory(experience_id="e1", user_id="u1",
                                               task="T1", solution="S"))
        await coding_store.insert(CodingMemory(experience_id="e2", user_id="u1",
                                               task="T2", solution="S"))
        await coding_store.insert(CodingMemory(experience_id="e3", user_id="u_other",
                                               task="OTHER", solution="S"))
        tasks = await coding_store.list_user_tasks("u1", limit=10)
        assert sorted(tasks) == ["T1", "T2"]
        assert "OTHER" not in tasks

    async def test_update_overrides(self, coding_store, mock_embed):
        m = CodingMemory(experience_id="e1", user_id="u1", task="T",
                         search_keys=["old1", "old2"], solution="old",
                         session_id="conversation-1")
        await coding_store.insert(m)

        # 旧 aspect 应可命中
        emb = (await mock_embed.embed_batch(["old1"]))[0]
        hits = await coding_store.search_by_query_embedding(emb, user_id="u1", top=5)
        assert any(h["experience_id"] == "e1" for h in hits)

        # update
        m.search_keys = ["new1"]
        m.solution = "new"
        await coding_store.update(m)

        # 新 aspect 应命中
        emb_new = (await mock_embed.embed_batch(["new1"]))[0]
        hits = await coding_store.search_by_query_embedding(emb_new, user_id="u1", top=5)
        assert any(h["experience_id"] == "e1" for h in hits)

        # SQLite 端正确
        got = await coding_store.get_by_id("e1", user_id="u1")
        assert got.solution == "new"
        assert got.search_keys == ["new1"]

    async def test_update_rejects_authority_scope_change(self, coding_store):
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u1", agent_id="scope-a",
            workspace_id="repo-a", session_id="conversation-1",
            task="T", solution="old",
        ))
        replacement = CodingMemory(
            experience_id="e1", user_id="u1", agent_id="scope-b",
            workspace_id="repo-a", session_id="conversation-1",
            task="T", solution="new",
        )

        with pytest.raises(ValueError, match="coding_memory_update_authority_mismatch"):
            await coding_store.update(replacement)

        stored = await coding_store.get_by_id("e1", user_id="u1")
        assert stored is not None
        assert stored.agent_id == "scope-a"
        assert stored.solution == "old"

    async def test_update_rejects_conversation_change_and_legacy_empty_session(
        self, coding_store
    ):
        for experience_id, old_session in (
            ("different-conversation", "conversation-1"),
            ("legacy-empty-session", None),
        ):
            await coding_store.insert(CodingMemory(
                experience_id=experience_id,
                user_id="u1",
                agent_id="scope-a",
                workspace_id="repo-a",
                session_id=old_session,
                task="T",
                solution="old",
            ))
            replacement = CodingMemory(
                experience_id=experience_id,
                user_id="u1",
                agent_id="scope-a",
                workspace_id="repo-a",
                session_id="conversation-2",
                task="T",
                solution="new",
            )

            with pytest.raises(ValueError, match="coding_memory_update_authority_mismatch"):
                await coding_store.update(replacement)

            stored = await coding_store.get_by_id(experience_id, user_id="u1")
            assert stored is not None
            assert stored.solution == "old"

    async def test_delete(self, coding_store):
        m = CodingMemory(experience_id="e1", user_id="u1", task="T",
                         search_keys=["k"], solution="S")
        await coding_store.insert(m)
        assert await coding_store.delete("e1", user_id="u1") is True
        assert await coding_store.get_by_id("e1", user_id="u1") is None
        # 删不存在的返回 False
        assert await coding_store.delete("nope", user_id="u1") is False

    async def test_delete_wrong_user(self, coding_store):
        m = CodingMemory(experience_id="e1", user_id="u1", task="T", solution="S")
        await coding_store.insert(m)
        # 错 user_id 不能删
        deleted = await coding_store.delete("e1", user_id="u_wrong")
        assert deleted is False
        # 仍然能拿到
        got = await coding_store.get_by_id("e1", user_id="u1")
        assert got is not None


@pytest.mark.asyncio
class TestStoreSearch:
    async def test_search_hit_task_key(self, coding_store, mock_embed):
        m = CodingMemory(experience_id="e1", user_id="u1", task="打包发布",
                         search_keys=["凭证位置", "上传地址"], solution="S",
                         workspace_id="ws/a")
        await coding_store.insert(m)
        emb = (await mock_embed.embed_batch(["打包发布"]))[0]
        hits = await coding_store.search_by_query_embedding(
            emb, user_id="u1", workspace_id="ws/a", top=10,
        )
        assert len(hits) == 1
        assert hits[0]["experience_id"] == "e1"
        assert hits[0]["matched_key_kind"] == "task"

    async def test_search_hit_aspect_key(self, coding_store, mock_embed):
        m = CodingMemory(experience_id="e1", user_id="u1", task="打包发布",
                         search_keys=["凭证位置"], solution="S")
        await coding_store.insert(m)
        emb = (await mock_embed.embed_batch(["凭证位置"]))[0]
        hits = await coding_store.search_by_query_embedding(emb, user_id="u1", top=10)
        assert hits[0]["matched_key_kind"] == "aspect"
        assert hits[0]["matched_key_text"] == "凭证位置"

    async def test_search_filters_by_workspace(self, coding_store, mock_embed):
        """workspace 传了就按作用域筛，不传则全量召回。"""
        m = CodingMemory(experience_id="e1", user_id="u1", task="T", solution="S",
                         workspace_id="ws/a")
        await coding_store.insert(m)
        emb = (await mock_embed.embed_batch(["T"]))[0]
        for ws, expect in (("ws/a", 1), ("ws/wrong", 0), (None, 1)):
            hits = await coding_store.search_by_query_embedding(
                emb, user_id="u1", workspace_id=ws, top=5,
            )
            assert len(hits) == expect, f"workspace_id={ws!r}"

    async def test_search_isolates_by_user(self, coding_store, mock_embed):
        await coding_store.insert(CodingMemory(experience_id="e1", user_id="u1",
                                               task="T", solution="S"))
        await coding_store.insert(CodingMemory(experience_id="e2", user_id="u_other",
                                               task="T", solution="S"))
        emb = (await mock_embed.embed_batch(["T"]))[0]
        hits = await coding_store.search_by_query_embedding(emb, user_id="u1", top=10)
        assert [h["experience_id"] for h in hits] == ["e1"]

    async def test_search_dedup_by_experience_id(self, coding_store, mock_embed):
        # 同一经验多个 keys 都可能命中，dedup 应保证每条经验最多一行
        m = CodingMemory(experience_id="e1", user_id="u1", task="aaa",
                         search_keys=["aaa", "aaa"], solution="S")
        await coding_store.insert(m)
        emb = (await mock_embed.embed_batch(["aaa"]))[0]
        hits = await coding_store.search_by_query_embedding(emb, user_id="u1", top=10)
        unique = {h["experience_id"] for h in hits}
        assert len(hits) == len(unique)
        assert len(hits) <= 1


# ================================================================
# 旧 schema 迁移
# ================================================================

_LEGACY_DDL = """
CREATE TABLE coding_memory_meta (
    memory_id        TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    agent_id         TEXT NOT NULL DEFAULT 'default_agent',
    task             TEXT NOT NULL,
    search_keys      TEXT NOT NULL DEFAULT '[]',
    solution         TEXT NOT NULL,
    boundary_envs    TEXT NOT NULL DEFAULT '',
    boundary_scope   TEXT NOT NULL,
    workspace_id     TEXT,
    branch           TEXT,
    session_id       TEXT,
    files            TEXT NOT NULL DEFAULT '[]',
    confidence       REAL NOT NULL DEFAULT 0.7,
    source           TEXT NOT NULL DEFAULT 'auto_extract',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


@pytest.mark.asyncio
class TestLegacySchemaMigration:
    async def test_migrates_and_keeps_rows(self, coding_config, mock_embed):
        from hy_memory.coding.store import CodingMemoryStore

        db_path = coding_config.coding.db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(_LEGACY_DDL)
        conn.execute("CREATE INDEX idx_coding_user ON coding_memory_meta(user_id)")
        conn.execute(
            "INSERT INTO coding_memory_meta VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("m_old", "u1", "a1", "老 task", json.dumps(["k1"]), "老 solution",
             "twine>=6", "project", "ws/a", "main", "s1", json.dumps(["f.py"]),
             0.9, "auto_extract", "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
        )
        conn.commit()
        conn.close()

        store = CodingMemoryStore(coding_config, embed_service=mock_embed)
        await store.initialize()
        try:
            got = await store.get_by_id("m_old", user_id="u1")
            assert got is not None
            # id 沿用旧值，因此向量库 node_id 不受影响
            assert got.experience_id == "m_old"
            assert got.task == "老 task"
            assert got.search_keys == ["k1"]
            assert got.boundary_envs == "twine>=6"
            assert got.files == ["f.py"]
            # 迁移后仍可正常写入
            await store.insert(CodingMemory(experience_id="e_new", user_id="u1",
                                            task="新 task", solution="S"))
            assert sorted(await store.list_user_tasks("u1")) == ["新 task", "老 task"]
        finally:
            await store.close()

    async def test_migration_is_idempotent(self, coding_config, mock_embed):
        from hy_memory.coding.store import CodingMemoryStore

        for _ in range(2):
            store = CodingMemoryStore(coding_config, embed_service=mock_embed)
            await store.initialize()
            await store.close()

        conn = sqlite3.connect(coding_config.coding.db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(coding_memory_meta)")]
        conn.close()
        assert "experience_id" in cols
        assert "memory_id" not in cols
        assert "boundary_scope" not in cols

    async def test_legacy_rows_get_empty_source_fact_ids(self, coding_config, mock_embed):
        """老库没有 source_fact_ids 列，迁移后应该是空链而不是炸掉。"""
        from hy_memory.coding.store import CodingMemoryStore

        db_path = coding_config.coding.db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(_LEGACY_DDL)
        conn.execute(
            "INSERT INTO coding_memory_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("m_old", "u1", "a1", "老 task", "[]", "老 solution",
             "", "project", "ws/a", "main", "s1", "[]",
             0.9, "auto_extract", "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
        )
        conn.commit()
        conn.close()

        store = CodingMemoryStore(coding_config, embed_service=mock_embed)
        await store.initialize()
        try:
            got = await store.get_by_id("m_old", user_id="u1")
            assert got.source_fact_ids == []
            await store.set_experience_source_facts("m_old", ["f1"])
            got = await store.get_by_id("m_old", user_id="u1")
            assert got.source_fact_ids == ["f1"]
        finally:
            await store.close()

    async def test_adds_source_fact_ids_to_renamed_schema(self, coding_config, mock_embed):
        """已经改过名、但建于加 source_fact_ids 之前的库，走 ADD COLUMN 补列。"""
        from hy_memory.coding.store import CodingMemoryStore

        db_path = coding_config.coding.db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(_PRE_FACT_DDL)
        conn.execute(
            "INSERT INTO coding_memory_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("e1", "u1", "a1", "task", "[]", "solution", "", "ws/a", "main", "s1",
             "[]", 0.9, "auto_extract", "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
        )
        conn.commit()
        conn.close()

        store = CodingMemoryStore(coding_config, embed_service=mock_embed)
        await store.initialize()
        try:
            got = await store.get_by_id("e1", user_id="u1")
            assert got.source_fact_ids == []
        finally:
            await store.close()

    async def test_adds_chain_summary_to_pre_chain_schema(self, coding_config, mock_embed):
        """建于加演变总结之前的库，走 ADD COLUMN 补列，老事实总结为空。"""
        from hy_memory.coding.store import CodingMemoryStore
        from hy_memory.coding.types import WorkingFact

        db_path = coding_config.coding.db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(_PRE_CHAIN_FACT_DDL)
        conn.execute(
            "INSERT INTO working_fact VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("f_old", "u1", "a1", "s1", "ws/a", "老结论", "[]", None, "[]",
             "active", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        store = CodingMemoryStore(coding_config, embed_service=mock_embed)
        await store.initialize()
        try:
            got = await store.get_fact("f_old", user_id="u1")
            assert got is not None
            assert got.fact_chain_summary == ""
            # 补列之后照常能写总结
            await store.insert_fact(WorkingFact(
                fact_id="f_new", user_id="u1", statement="新结论",
                fact_chain_summary="从老结论改成了新结论。",
            ))
            back = await store.get_fact("f_new", user_id="u1")
            assert back.fact_chain_summary == "从老结论改成了新结论。"
        finally:
            await store.close()

    async def test_writes_ignore_abandoned_fact_chain_column(
        self, coding_config, mock_embed
    ):
        """短命的 fact_chain 列已废弃，但老库里还留着，而且是 NOT NULL。

        现在的 INSERT 不再提这一列，靠它的 DEFAULT 兜住；兜不住的话所有写入都会
        直接报 NOT NULL constraint failed。
        """
        from hy_memory.coding.store import CodingMemoryStore
        from hy_memory.coding.types import WorkingFact

        db_path = coding_config.coding.db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(_ABANDONED_CHAIN_FACT_DDL)
        conn.commit()
        conn.close()

        store = CodingMemoryStore(coding_config, embed_service=mock_embed)
        await store.initialize()
        try:
            await store.insert_fact(WorkingFact(
                fact_id="f1", user_id="u1", statement="新结论",
                fact_chain_summary="一句总结。",
            ))
            got = await store.get_fact("f1", user_id="u1")
            assert got.statement == "新结论"
            assert got.fact_chain_summary == "一句总结。"
            # upsert 走的是另一条 SQL 分支，同样不该被那一列绊住
            await store.supersede_fact("f1", "f2")
            assert (await store.get_fact("f1", user_id="u1")).status == "superseded"
        finally:
            await store.close()


# 加演变总结之前的事实表
_PRE_CHAIN_FACT_DDL = """
CREATE TABLE IF NOT EXISTS working_fact (
    fact_id                TEXT PRIMARY KEY,
    user_id                TEXT NOT NULL,
    agent_id               TEXT NOT NULL DEFAULT 'default_agent',
    session_id             TEXT,
    workspace_id           TEXT,
    statement              TEXT NOT NULL,
    files                  TEXT NOT NULL DEFAULT '[]',
    fact_link              TEXT,
    derived_experience_ids TEXT NOT NULL DEFAULT '[]',
    status                 TEXT NOT NULL DEFAULT 'active',
    created_at             TEXT NOT NULL
);
"""


# 短暂存在过 fact_chain 列的那一版事实表（现已废弃，老库里还留着）
_ABANDONED_CHAIN_FACT_DDL = """
CREATE TABLE IF NOT EXISTS working_fact (
    fact_id                TEXT PRIMARY KEY,
    user_id                TEXT NOT NULL,
    agent_id               TEXT NOT NULL DEFAULT 'default_agent',
    session_id             TEXT,
    workspace_id           TEXT,
    statement              TEXT NOT NULL,
    files                  TEXT NOT NULL DEFAULT '[]',
    fact_link              TEXT,
    derived_experience_ids TEXT NOT NULL DEFAULT '[]',
    status                 TEXT NOT NULL DEFAULT 'active',
    fact_chain             TEXT NOT NULL DEFAULT '[]',
    fact_chain_summary     TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL
);
"""


# ================================================================
# 事实层存储
# ================================================================

_PRE_FACT_DDL = """
CREATE TABLE IF NOT EXISTS coding_memory_meta (
    experience_id    TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    agent_id         TEXT NOT NULL DEFAULT 'default_agent',
    task             TEXT NOT NULL,
    search_keys      TEXT NOT NULL DEFAULT '[]',
    solution         TEXT NOT NULL,
    boundary_envs    TEXT NOT NULL DEFAULT '',
    workspace_id     TEXT,
    branch           TEXT,
    session_id       TEXT,
    files            TEXT NOT NULL DEFAULT '[]',
    confidence       REAL NOT NULL DEFAULT 0.7,
    source           TEXT NOT NULL DEFAULT 'auto_extract',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


class TestFactCollectionName:
    def test_derive(self):
        from hy_memory.coding.store import _derive_fact_collection_name
        assert _derive_fact_collection_name("agent_memories") == "agent_memories_working_facts"
        assert _derive_fact_collection_name(None) == "agent_memories_working_facts"

    def test_separate_from_experience_collection(self):
        """两层各自独立 collection，不能撞名。"""
        from hy_memory.coding.store import (
            _derive_coding_collection_name, _derive_fact_collection_name,
        )
        assert _derive_coding_collection_name("x") != _derive_fact_collection_name("x")


@pytest.mark.asyncio
class TestFactStore:
    async def test_insert_and_get(self, coding_store):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", agent_id="a1", session_id="s1",
            workspace_id="ws/a", statement="事实正文", files=["a.py"],
            derived_experience_ids=["e1"],
        ))
        got = await coding_store.get_fact("f1", user_id="u1")
        assert got.statement == "事实正文"
        assert got.files == ["a.py"]
        assert got.derived_experience_ids == ["e1"]
        assert got.status == "active"
        assert got.fact_link is None
        # created_at 自动补
        assert got.created_at is not None

    async def test_get_fact_user_isolation(self, coding_store):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", statement="S",
        ))
        assert await coding_store.get_fact("f1", user_id="u_other") is None

    async def test_get_facts_preserves_order(self, coding_store):
        for i in range(3):
            await coding_store.insert_fact(WorkingFact(
                fact_id=f"f{i}", user_id="u1", statement=f"事实 {i}",
            ))
        got = await coding_store.get_facts(["f2", "f0", "f_missing"], user_id="u1")
        assert [f.fact_id for f in got] == ["f2", "f0"]

    async def test_search_facts_scores_and_isolation(self, coding_store):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", statement="打包发布到私有包索引",
        ))
        await coding_store.insert_fact(WorkingFact(
            fact_id="f2", user_id="u_other", statement="打包发布到私有包索引",
        ))
        emb = await coding_store.embed_service.embed("打包发布到私有包索引")

        hits = await coding_store.search_facts(emb, user_id="u1", top=5)
        assert [f.fact_id for f, _ in hits] == ["f1"]
        assert hits[0][1] > 0.9

    async def test_search_facts_filters_agent_and_session_lists(self, coding_store):
        for fact_id, agent_id, session_id in (
            ("f_a1", "a1", "s1"),
            ("f_a2", "a2", "s2"),
        ):
            await coding_store.insert_fact(WorkingFact(
                fact_id=fact_id, user_id="u1", agent_id=agent_id,
                session_id=session_id, statement="同一个事实",
            ))
        emb = await coding_store.embed_service.embed("同一个事实")

        hits = await coding_store.search_facts(
            emb, user_id="u1", agent_ids=["a1"], top=5,
        )
        assert [f.fact_id for f, _ in hits] == ["f_a1"]

        hits = await coding_store.search_facts(
            emb, user_id="u1", session_ids=["s2"], top=5,
        )
        assert [f.fact_id for f, _ in hits] == ["f_a2"]

        hits = await coding_store.search_facts(
            emb, user_id="u1", agent_ids=["a1", "a2"], top=5,
        )
        assert {f.fact_id for f, _ in hits} == {"f_a1", "f_a2"}

    async def test_search_experiences_filters_agent_and_session_lists(self, coding_store):
        for experience_id, agent_id, session_id in (
            ("e_a1", "a1", "s1"),
            ("e_a2", "a2", "s2"),
        ):
            await coding_store.insert(CodingMemory(
                experience_id=experience_id, user_id="u1",
                agent_id=agent_id, session_id=session_id,
                task="同一个任务", search_keys=["同一个任务"], solution="S",
            ))
        emb = await coding_store.embed_service.embed("同一个任务")

        hits = await coding_store.search_by_query_embedding(
            emb, user_id="u1", agent_ids=["a1"], top=5,
        )
        assert {h["experience_id"] for h in hits} == {"e_a1"}

        hits = await coding_store.search_by_query_embedding(
            emb, user_id="u1", session_ids=["s2"], top=5,
        )
        assert {h["experience_id"] for h in hits} == {"e_a2"}

        hits = await coding_store.search_by_query_embedding(
            emb, user_id="u1", agent_ids=["a1", "a2"], top=5,
        )
        assert {h["experience_id"] for h in hits} == {"e_a1", "e_a2"}

    async def test_supersede_soft_deletes(self, coding_store):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", statement="旧结论",
        ))
        assert await coding_store.supersede_fact("f_old", "f_new") is True

        got = await coding_store.get_fact("f_old", user_id="u1")
        # 行还在，链路可回溯
        assert got.status == "superseded"
        assert got.fact_link == "f_new"
        # 但已经不占召回名额
        emb = await coding_store.embed_service.embed("旧结论")
        assert await coding_store.search_facts(emb, user_id="u1") == []

    async def test_search_respects_top(self, coding_store):
        for i in range(6):
            await coding_store.insert_fact(WorkingFact(
                fact_id=f"f{i}", user_id="u1", statement=f"事实 {i}",
            ))
        emb = await coding_store.embed_service.embed("事实 0")
        assert len(await coding_store.search_facts(emb, user_id="u1", top=3)) == 3

    async def test_orphan_index_rows_dont_starve_recall(self, coding_store):
        """向量行还在、SQLite 行没了的孤儿不能吃掉候选名额。

        两边不是一个事务里写的，中间挂掉就会留下这种残留。不过采样的话前 top 条
        全是孤儿时召回会直接空掉。
        """
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_real", user_id="u1", statement="真实事实",
        ))
        # 造 5 条孤儿：向量库有，SQLite 删掉
        for i in range(5):
            await coding_store.insert_fact(WorkingFact(
                fact_id=f"f_orphan{i}", user_id="u1", statement="真实事实",
            ))
        with coding_store._lock:
            coding_store._conn.execute(
                "DELETE FROM working_fact WHERE fact_id LIKE 'f_orphan%'"
            )

        emb = await coding_store.embed_service.embed("真实事实")
        hits = await coding_store.search_facts(emb, user_id="u1", top=5)
        assert [f.fact_id for f, _ in hits] == ["f_real"]

    async def test_supersede_twice_is_noop(self, coding_store):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", statement="旧结论",
        ))
        assert await coding_store.supersede_fact("f_old", "f_a") is True
        assert await coding_store.supersede_fact("f_old", "f_b") is False
        got = await coding_store.get_fact("f_old", user_id="u1")
        assert got.fact_link == "f_a"

    async def test_supersede_missing_fact(self, coding_store):
        assert await coding_store.supersede_fact("nope", "f_new") is False


@pytest.mark.asyncio
class TestFactAncestors:
    """历史不另存，顺 fact_link 反查还原。"""

    async def _chain(self, store, n, *, user_id="u1"):
        """建 n 代：f0 被 f1 顶掉，f1 被 f2 顶掉……返回 id 列表，最新在末尾。"""
        ids = []
        for i in range(n):
            fid = f"f{i}"
            await store.insert_fact(WorkingFact(
                fact_id=fid, user_id=user_id, statement=f"第 {i} 代",
                created_at=datetime(2026, 1, i + 1),
            ))
            if ids:
                await store.supersede_fact(ids[-1], fid)
            ids.append(fid)
        return ids

    async def test_walks_back_in_time_order(self, coding_store):
        ids = await self._chain(coding_store, 4)
        got = await coding_store.get_fact_ancestors(ids[-1], user_id="u1")
        assert [f.fact_id for f in got] == ["f0", "f1", "f2"], "时间正序，不含自己"
        assert [f.statement for f in got] == ["第 0 代", "第 1 代", "第 2 代"]

    async def test_no_ancestors_for_fresh_fact(self, coding_store):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_solo", user_id="u1", statement="全新结论",
        ))
        assert await coding_store.get_fact_ancestors("f_solo", user_id="u1") == []

    async def test_respects_max_depth(self, coding_store):
        """长命事实不该把整条链都拖进提示词。"""
        ids = await self._chain(coding_store, 6)
        got = await coding_store.get_fact_ancestors(
            ids[-1], user_id="u1", max_depth=2
        )
        assert [f.fact_id for f in got] == ["f3", "f4"], "截断留最近的几代"

    async def test_scoped_by_user(self, coding_store):
        """别人的事实不该出现在我的血统里。"""
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_theirs", user_id="u2", statement="别人的旧结论",
        ))
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_mine", user_id="u1", statement="我的结论",
        ))
        await coding_store.supersede_fact("f_theirs", "f_mine")
        assert await coding_store.get_fact_ancestors("f_mine", user_id="u1") == []

    async def test_cycle_does_not_hang(self, coding_store):
        """fact_link 成环理论上不该发生，真发生了也不能死循环。"""
        for fid in ("f_a", "f_b"):
            await coding_store.insert_fact(WorkingFact(
                fact_id=fid, user_id="u1", statement=fid,
            ))
        await coding_store.supersede_fact("f_a", "f_b")
        await coding_store.supersede_fact("f_b", "f_a")

        got = await coding_store.get_fact_ancestors("f_a", user_id="u1")
        assert [f.fact_id for f in got] == ["f_b"], "撞回起点就停"

    async def test_set_derived_experiences(self, coding_store):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", statement="S",
        ))
        await coding_store.set_fact_derived_experiences("f1", ["e1", "e2"])
        got = await coding_store.get_fact("f1", user_id="u1")
        assert got.derived_experience_ids == ["e1", "e2"]

    async def test_experience_and_fact_dont_cross_contaminate(self, coding_store):
        """两层各自独立 collection：搜事实不该搜出经验，反之亦然。"""
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u1", task="同样的文本", solution="S",
        ))
        await coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", statement="同样的文本",
        ))
        emb = await coding_store.embed_service.embed("同样的文本")

        fact_hits = await coding_store.search_facts(emb, user_id="u1", top=10)
        assert [f.fact_id for f, _ in fact_hits] == ["f1"]

        exp_hits = await coding_store.search_by_query_embedding(emb, user_id="u1", top=10)
        assert [h["experience_id"] for h in exp_hits] == ["e1"]
