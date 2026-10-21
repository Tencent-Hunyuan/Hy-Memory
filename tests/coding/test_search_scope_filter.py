# -*- coding: utf-8 -*-
"""
working（coding）检索的 workspace_id / branch 作用域筛选。

规则本体在 hy_memory.coding.store._scope_where 的注释里，这里测它在两层召回上的
表现。最要紧的一组是"不传就不筛"——那是加过滤之前的行为，不能因为多了这个能力而
改变。
"""

import asyncio
import os
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.coding.store import (
    CodingMemoryStore,
    _scope_matches,
    _scope_overfetch,
    _scope_where,
)
from hy_memory.coding.types import CodingMemory, WorkingFact


# ================================================================
# Fixtures
# ================================================================

@pytest.fixture
async def scope_ctx(tmp_data_dir, mock_embed):
    """真实 CodingMemoryStore + mock embed，绑成一个能当 self 用的轻量 ctx。"""
    from hy_memory.config import MemoryConfig

    cfg = MemoryConfig.from_env()
    cfg.history.db_path = os.path.join(tmp_data_dir, "history.db")
    cfg.coding.db_path = os.path.join(tmp_data_dir, "coding_memory.db")
    cfg.vector_store.provider = "chroma"
    cfg.vector_store.path = os.path.join(tmp_data_dir, "chroma")
    cfg.vector_store.collection_name = f"test_scope_{uuid.uuid4().hex[:8]}"
    cfg.vector_store.embedding_dims = 1536

    store = CodingMemoryStore(cfg, embed_service=mock_embed)
    await store.initialize()

    ns = SimpleNamespace(
        _embed_service=mock_embed,
        _coding_store=store,
        _coding_writer=object(),
        _coding_llm_provider=None,
        _coding_init_lock=asyncio.Lock(),
    )

    async def _stub_get_coding_writer():
        return ns._coding_writer

    ns._get_coding_writer = _stub_get_coding_writer

    yield ns
    await store.close()


async def _search(
    ctx,
    query,
    *,
    workspace_id=None,
    branch=None,
    limit=10,
    agent_ids=None,
    session_ids=None,
):
    """闸门全开，只让作用域过滤起作用。"""
    return await HyMemoryClient._search_coding(
        ctx,
        target_query=query, user_ids=["u1"],
        agent_ids=agent_ids, session_ids=session_ids,
        workspace_id=workspace_id, branch=branch,
        limit=limit,
        working_fact_min_score=0.0, working_experience_min_score=0.0,
        request_id="r", t0=time.perf_counter(),
    )


async def _seed_two_workspaces(store):
    """两个 workspace 各一条经验 + 一条事实，文案对同一 query 等价。"""
    for ws, branch, suffix in (("ws/a", "main", "a"), ("ws/b", "dev", "b")):
        await store.insert(CodingMemory(
            experience_id=f"e_{suffix}", user_id="u1",
            task="打包发布到私有包索引", solution=f"S-{suffix}",
            workspace_id=ws, branch=branch,
        ))
        await store.insert_fact(WorkingFact(
            fact_id=f"f_{suffix}", user_id="u1",
            statement="打包发布到私有包索引",
            workspace_id=ws, branch=branch,
        ))


def _eids(resp):
    return sorted(e["experience_id"] for e in resp["experiences"])


def _fids(resp):
    return sorted(f["fact_id"] for f in resp["facts"])


@pytest.mark.asyncio
class TestAgentSessionScope:
    async def test_search_coding_forwards_agent_and_session_scope(self, scope_ctx):
        store = scope_ctx._coding_store
        for suffix, agent_id, session_id in (
            ("a1", "a1", "s1"),
            ("a2", "a2", "s2"),
        ):
            await store.insert(CodingMemory(
                experience_id=f"e_{suffix}", user_id="u1",
                agent_id=agent_id, session_id=session_id,
                task="打包发布到私有包索引", solution=f"S-{suffix}",
            ))
            await store.insert_fact(WorkingFact(
                fact_id=f"f_{suffix}", user_id="u1",
                agent_id=agent_id, session_id=session_id,
                statement="打包发布到私有包索引",
            ))

        resp = await _search(
            scope_ctx, "打包发布到私有包索引",
            agent_ids=["a1"], session_ids=["s1"],
        )
        assert _eids(resp) == ["e_a1"]
        assert _fids(resp) == ["f_a1"]


# ================================================================
# 规则本体（纯函数，不碰库）
# ================================================================

class TestScopeRule:
    def test_no_workspace_means_no_filter(self):
        assert _scope_where(None, None) == ("1", [])
        # 单传 branch 不生效（与 list_memories 的组合语义一致）
        assert _scope_where(None, "main") == ("1", [])
        assert _scope_where("", "main") == ("1", [])

    def test_workspace_only_is_exact(self):
        assert _scope_where("ws/a", None) == ("workspace_id = ?", ["ws/a"])

    def test_branch_stacks_and_tolerates_null(self):
        sql, params = _scope_where("ws/a", "main")
        assert sql == "workspace_id = ? AND (branch = ? OR branch IS NULL)"
        assert params == ["ws/a", "main"]

    def test_matches_mirrors_where(self):
        # 不传 → 全放行
        assert _scope_matches(None, None, None, None)
        assert _scope_matches("ws/a", "main", None, None)
        # workspace 精确匹配，NULL 不算命中
        assert _scope_matches("ws/a", None, "ws/a", None)
        assert not _scope_matches("ws/b", None, "ws/a", None)
        assert not _scope_matches(None, None, "ws/a", None)
        # branch 容 NULL，但不容"记录了别的分支"
        assert _scope_matches("ws/a", "main", "ws/a", "main")
        assert _scope_matches("ws/a", None, "ws/a", "main")
        assert not _scope_matches("ws/a", "dev", "ws/a", "main")

    def test_overfetch_scales_by_selectivity(self):
        # 作用域就是全部 → 不放大
        assert _scope_overfetch(8, 10, 10) == 8
        # 占一半 → 翻倍
        assert _scope_overfetch(8, 5, 10) == 16
        # 只占一条 → 放大到基本取全
        assert _scope_overfetch(8, 1, 21) == 168
        # 数不出选择率时退回原值，别放大到天上去
        assert _scope_overfetch(8, 0, 10) == 8

    def test_overfetch_capped_but_never_shrinks(self):
        assert _scope_overfetch(8, 1, 10 ** 6) == 2000
        # top 本来就比上限大时不能被上限压小
        assert _scope_overfetch(5000, 1, 10 ** 6) == 5000


# ================================================================
# 不传 = 不筛（回归保护）
# ================================================================

@pytest.mark.asyncio
class TestNoScopeUnchanged:
    async def test_both_absent_recalls_everything(self, scope_ctx):
        await _seed_two_workspaces(scope_ctx._coding_store)

        resp = await _search(scope_ctx, "打包发布到私有包索引")
        assert _eids(resp) == ["e_a", "e_b"]
        assert _fids(resp) == ["f_a", "f_b"]

    async def test_branch_alone_is_ignored(self, scope_ctx):
        """单传 branch 不筛。与 list_memories 一致：branch 只是 workspace 内的细分。"""
        await _seed_two_workspaces(scope_ctx._coding_store)

        resp = await _search(scope_ctx, "打包发布到私有包索引", branch="main")
        assert _eids(resp) == ["e_a", "e_b"]
        assert _fids(resp) == ["f_a", "f_b"]

    async def test_untagged_rows_still_recalled(self, scope_ctx):
        """workspace_id 为空的经验/事实，在不传作用域时照常召回。"""
        await scope_ctx._coding_store.insert(CodingMemory(
            experience_id="e_bare", user_id="u1", task="裸经验", solution="S",
        ))
        await scope_ctx._coding_store.insert_fact(WorkingFact(
            fact_id="f_bare", user_id="u1", statement="裸经验",
        ))

        resp = await _search(scope_ctx, "裸经验")
        assert _eids(resp) == ["e_bare"]
        assert _fids(resp) == ["f_bare"]


# ================================================================
# 只传 workspace_id
# ================================================================

@pytest.mark.asyncio
class TestWorkspaceOnly:
    async def test_filters_both_layers(self, scope_ctx):
        await _seed_two_workspaces(scope_ctx._coding_store)

        resp = await _search(scope_ctx, "打包发布到私有包索引", workspace_id="ws/a")
        assert _eids(resp) == ["e_a"]
        assert _fids(resp) == ["f_a"]
        assert resp["experiences"][0]["workspace_id"] == "ws/a"

    async def test_ignores_branch_of_in_scope_rows(self, scope_ctx):
        """只传 workspace_id 时 branch 不参与，同 workspace 下各分支都召回。"""
        for i, br in enumerate(("main", "dev", None)):
            await scope_ctx._coding_store.insert(CodingMemory(
                experience_id=f"e{i}", user_id="u1", task="同一个任务", solution="S",
                workspace_id="ws/a", branch=br,
            ))

        resp = await _search(scope_ctx, "同一个任务", workspace_id="ws/a")
        assert _eids(resp) == ["e0", "e1", "e2"]

    async def test_untagged_rows_excluded(self, scope_ctx):
        """workspace_id 为空 = 不属于任何 repo，按 repo 筛时排除。"""
        await scope_ctx._coding_store.insert(CodingMemory(
            experience_id="e_bare", user_id="u1", task="裸经验", solution="S",
        ))
        await scope_ctx._coding_store.insert(CodingMemory(
            experience_id="e_a", user_id="u1", task="裸经验", solution="S",
            workspace_id="ws/a",
        ))

        resp = await _search(scope_ctx, "裸经验", workspace_id="ws/a")
        assert _eids(resp) == ["e_a"]


# ================================================================
# workspace_id + branch
# ================================================================

@pytest.mark.asyncio
class TestWorkspaceAndBranch:
    async def test_branch_narrows_within_workspace(self, scope_ctx):
        for i, (ws, br) in enumerate(
            (("ws/a", "main"), ("ws/a", "dev"), ("ws/b", "main"))
        ):
            await scope_ctx._coding_store.insert(CodingMemory(
                experience_id=f"e{i}", user_id="u1", task="同一个任务", solution="S",
                workspace_id=ws, branch=br,
            ))
            await scope_ctx._coding_store.insert_fact(WorkingFact(
                fact_id=f"f{i}", user_id="u1", statement="同一个任务",
                workspace_id=ws, branch=br,
            ))

        resp = await _search(
            scope_ctx, "同一个任务", workspace_id="ws/a", branch="main",
        )
        assert _eids(resp) == ["e0"]
        assert _fids(resp) == ["f0"]


# ================================================================
# 传了但没有匹配数据
# ================================================================

@pytest.mark.asyncio
class TestNoMatchInScope:
    async def test_unknown_workspace_returns_empty(self, scope_ctx):
        await _seed_two_workspaces(scope_ctx._coding_store)

        resp = await _search(scope_ctx, "打包发布到私有包索引", workspace_id="ws/nope")
        assert resp["experiences"] == []
        assert resp["facts"] == []
        assert resp["scene"] == "productivity"

    async def test_unknown_branch_returns_empty(self, scope_ctx):
        await _seed_two_workspaces(scope_ctx._coding_store)

        resp = await _search(
            scope_ctx, "打包发布到私有包索引", workspace_id="ws/a", branch="release",
        )
        assert resp["experiences"] == []
        assert resp["facts"] == []

    async def test_empty_scope_skips_vdb_entirely(self, scope_ctx):
        """作用域内一条都没有时不该再去查向量库——SQLite 已经给出确定答案。"""
        await _seed_two_workspaces(scope_ctx._coding_store)
        store = scope_ctx._coding_store

        calls = []
        for vec in (store._vec, store._fact_vec):
            original = vec.search

            async def _counting(*a, _orig=original, **kw):
                calls.append(1)
                return await _orig(*a, **kw)

            vec.search = _counting

        resp = await _search(scope_ctx, "打包发布到私有包索引", workspace_id="ws/nope")
        assert resp["experiences"] == [] and resp["facts"] == []
        assert calls == []


# ================================================================
# 老数据：branch 是 NULL
# ================================================================

@pytest.mark.asyncio
class TestLegacyNullBranch:
    async def test_null_branch_survives_branch_filter(self, scope_ctx):
        """写入时没给 branch 的行，在按 branch 筛时不能被误杀。

        事实表的 branch 列是后补的，老库里整表都是 NULL；写入方不传 branch 的新行
        同样是 NULL。把 NULL 当成"记录了别的分支"筛掉，等于让这些数据永久失联。
        """
        await scope_ctx._coding_store.insert(CodingMemory(
            experience_id="e_old", user_id="u1", task="老经验", solution="S",
            workspace_id="ws/a", branch=None,
        ))
        await scope_ctx._coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", statement="老经验",
            workspace_id="ws/a", branch=None,
        ))

        resp = await _search(
            scope_ctx, "老经验", workspace_id="ws/a", branch="main",
        )
        assert _eids(resp) == ["e_old"]
        assert _fids(resp) == ["f_old"]

    async def test_other_branch_still_excluded(self, scope_ctx):
        """容 NULL 不等于放弃筛选：明确写了别的分支的行照样排除。"""
        await scope_ctx._coding_store.insert(CodingMemory(
            experience_id="e_dev", user_id="u1", task="老经验", solution="S",
            workspace_id="ws/a", branch="dev",
        ))
        await scope_ctx._coding_store.insert(CodingMemory(
            experience_id="e_null", user_id="u1", task="老经验", solution="S",
            workspace_id="ws/a", branch=None,
        ))

        resp = await _search(
            scope_ctx, "老经验", workspace_id="ws/a", branch="main",
        )
        assert _eids(resp) == ["e_null"]


# 建于事实表加 branch 列之前的库
_PRE_BRANCH_FACT_DDL = """
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
    fact_chain_summary     TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL
);
"""


@pytest.mark.asyncio
class TestPreBranchFactSchema:
    async def test_add_column_and_old_rows_stay_reachable(
        self, coding_config, mock_embed
    ):
        """老事实表走 ADD COLUMN 补 branch，补出来的 NULL 行仍然筛得到。"""
        db_path = coding_config.coding.db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(_PRE_BRANCH_FACT_DDL)
        conn.execute(
            "INSERT INTO working_fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f_old", "u1", "a1", "s1", "ws/a", "老结论", "[]", None, "[]",
             "active", "", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        store = CodingMemoryStore(coding_config, embed_service=mock_embed)
        await store.initialize()
        try:
            cols = [
                r["name"] for r in
                store._conn.execute("PRAGMA table_info(working_fact)").fetchall()
            ]
            assert "branch" in cols

            got = await store.get_fact("f_old", user_id="u1")
            assert got is not None and got.branch is None

            # 老库里向量是有的（写入时两边一起落），上面裸 SQL 只造了 SQLite 那半
            # 边。search_facts 从向量库起手，不补这一步召回必空，测不到作用域规则。
            await store._upsert_fact_vec(got)

            emb = await mock_embed.embed("老结论")
            hits = await store.search_facts(
                emb, user_id="u1", workspace_id="ws/a", branch="main",
            )
            assert [f.fact_id for f, _ in hits] == ["f_old"]

            # 补列之后照常能写 branch
            await store.insert_fact(WorkingFact(
                fact_id="f_new", user_id="u1", statement="新结论",
                workspace_id="ws/a", branch="main",
            ))
            back = await store.get_fact("f_new", user_id="u1")
            assert back.branch == "main"
        finally:
            await store.close()


# ================================================================
# 事实层被筛空 → 双层退化成单层
# ================================================================

@pytest.mark.asyncio
class TestFactLinkDegradation:
    async def _seed_linked(self, store):
        """一条 task 跟 query 完全不像的经验，只能靠事实顺链够到。"""
        await store.insert(CodingMemory(
            experience_id="e1", user_id="u1", task="毫不相干的任务名 QQQQ",
            solution="S", workspace_id="ws/a", branch="main",
        ))
        await store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", statement="new_file_search 位于 src/search.py",
            workspace_id="ws/a", branch="main",
            derived_experience_ids=["e1"],
        ))

    async def test_in_scope_keeps_fact_link_path(self, scope_ctx):
        await self._seed_linked(scope_ctx._coding_store)

        resp = await _search(
            scope_ctx, "new_file_search 位于 src/search.py",
            workspace_id="ws/a", branch="main",
        )
        assert _fids(resp) == ["f1"]
        assert _eids(resp) == ["e1"]
        assert resp["experiences"][0]["via"] == "fact_link"

    async def test_out_of_scope_collapses_to_single_layer(self, scope_ctx, caplog):
        """事实被作用域筛掉，顺它带出的经验一起消失，退化时要有 warning。

        经验本身也在 ws/a，所以这里两层一起空；退化本身在结果里看不出来，
        只能靠日志。
        """
        await self._seed_linked(scope_ctx._coding_store)

        with caplog.at_level("WARNING", logger="hy_memory.client"):
            resp = await _search(
                scope_ctx, "new_file_search 位于 src/search.py", workspace_id="ws/b",
            )
        assert resp["facts"] == []
        assert resp["experiences"] == []
        assert any("fact_link path disabled" in r.message for r in caplog.records)

    async def test_experience_survives_when_only_fact_is_out_of_scope(self, scope_ctx):
        """经验在作用域内、担保它的事实不在，经验退回直查那一路。"""
        store = scope_ctx._coding_store
        await store.insert(CodingMemory(
            experience_id="e1", user_id="u1", task="共同的查询词", solution="S",
            workspace_id="ws/a",
        ))
        await store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", statement="共同的查询词",
            workspace_id="ws/b", derived_experience_ids=["e1"],
        ))

        resp = await _search(scope_ctx, "共同的查询词", workspace_id="ws/a")
        assert resp["facts"] == []
        assert _eids(resp) == ["e1"]
        assert resp["experiences"][0]["via"] == "direct"


# ================================================================
# 过滤不吃召回
# ================================================================

@pytest.mark.asyncio
class TestScopeDoesNotEatRecall:
    async def test_in_scope_hit_found_behind_many_out_of_scope_ones(self, scope_ctx):
        """作用域内唯一那条排在 20 条更相似的域外经验之后，仍要召回到。

        向量检索是先取 top-N 再返回：照 top-N 原样过滤，这一条永远进不了名额，
        过滤就等于把召回清零。按选择率放大取数才能捞到它。
        """
        store = scope_ctx._coding_store
        query = "打包发布到私有包索引"
        for i in range(20):
            await store.insert(CodingMemory(
                experience_id=f"e_other{i}", user_id="u1",
                task=query, solution="S", workspace_id="ws/other",
            ))
        # 域内那条文案不同，纯按分数排在 20 条精确命中之后
        await store.insert(CodingMemory(
            experience_id="e_mine", user_id="u1",
            task="发布流程里凭证放哪", solution="S", workspace_id="ws/mine",
        ))

        resp = await _search(scope_ctx, query, workspace_id="ws/mine", limit=1)
        assert _eids(resp) == ["e_mine"]


# ================================================================
# list 与 search 共用同一套规则
# ================================================================

@pytest.mark.asyncio
class TestListSharesTheSameRule:
    async def test_list_branch_tolerates_null(self, coding_store):
        for i, br in enumerate(("main", "dev", None)):
            await coding_store.insert(CodingMemory(
                experience_id=f"e{i}", user_id="u1", task=f"task {i}", solution="S",
                workspace_id="ws/a", branch=br,
            ))

        got = await coding_store.list_by_workspace("u1", "ws/a", branch="main")
        assert sorted(m.experience_id for m in got) == ["e0", "e2"]

        got = await coding_store.list_by_workspace("u1", "ws/a")
        assert sorted(m.experience_id for m in got) == ["e0", "e1", "e2"]
