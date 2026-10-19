"""Episode（内部会话）测试。

Episode 是 SDK 内部按写入时间间隔切出的"一次讨论"（可跨多次 write）。它有两个用途：

1. 读侧：用 `episode_id` **框出同一次讨论**的候选做加权 —— 覆盖"公园/骑车/吃饭同时写入，
   但 query 只命中吃饭"这类向量检索找不到的关联。
2. 写侧：episode 关闭时总结一次，替代 rolling summary 的"每 N 轮重写 + 把旧摘要喂回 LLM"。
"""

import asyncio
import tempfile
from datetime import datetime, timedelta

import pytest

from hy_memory.config import CacheConfig, MemoryConfig
from hy_memory.data.cache_sqlite import SqliteCache
from hy_memory.models.memory import MemoryLayer, MemoryNode


# ======================================================================
# Episode 状态存储
# ======================================================================

class TestEpisodeState:
    """cache 里的 episode 状态：只存 (episode_id, 上次写入时间, 已积累条数)。"""

    @pytest.mark.asyncio
    async def test_roundtrip_and_absent(self):
        cfg = MemoryConfig()
        cfg.cache = CacheConfig(
            backend="sqlite", db_path=tempfile.mkdtemp() + "/c.db"
        )
        cache = SqliteCache(cfg)
        await cache.initialize()
        try:
            assert await cache.get_episode_state("u::a::s") is None

            await cache.upsert_episode_state(
                "u::a::s", user_id="u", agent_id="a", session_id="s",
                episode_id="ep1", last_write_ts=100.0, memory_count=3,
            )
            got = await cache.get_episode_state("u::a::s")
            assert got["episode_id"] == "ep1"
            assert got["last_write_ts"] == 100.0
            assert got["memory_count"] == 3

            # 同 key 再写是更新而不是插新行
            await cache.upsert_episode_state(
                "u::a::s", user_id="u", agent_id="a", session_id="s",
                episode_id="ep1", last_write_ts=200.0, memory_count=7,
            )
            got = await cache.get_episode_state("u::a::s")
            assert got["memory_count"] == 7 and got["last_write_ts"] == 200.0
        finally:
            await cache.close()


# ======================================================================
# Episode 划分规则（写侧，无 LLM）
# ======================================================================

class _FakeCache:
    """只实现 episode 状态两个方法的假 cache。"""

    def __init__(self):
        self.store = {}

    async def get_episode_state(self, key):
        return self.store.get(key)

    async def upsert_episode_state(self, key, **kw):
        self.store[key] = {
            "episode_id": kw["episode_id"],
            "last_write_ts": kw["last_write_ts"],
            "memory_count": kw["memory_count"],
        }
        return True


def _writer(cache, *, gap=1800.0, cap=50):
    """造一个只够跑 _resolve_episode_id / _schedule_episode_summary 的 writer。"""
    from hy_memory.pipelines.writer import MemoryWriter

    w = MemoryWriter.__new__(MemoryWriter)
    cfg = MemoryConfig()
    cfg.recall.episode_gap_seconds = gap
    cfg.recall.episode_max_memories = cap
    w.config = cfg
    w._cache = cache
    return w


def _req(ts, *, user="u", session="s"):
    from hy_memory.pipelines.base import WriteRequest

    return WriteRequest(
        user_id=user, agent_id="a", session_id=session,
        messages=[], memory_at=ts,
    )


class TestEpisodePartition:
    """间隔内算同一次讨论，超了开新的 —— 纯确定性，不用 LLM。"""

    @pytest.mark.asyncio
    async def test_writes_within_gap_share_episode(self):
        """一件事跨几次 write：间隔短 → 同一个 episode_id。"""
        w = _writer(_FakeCache(), gap=1800.0)
        t0 = datetime(2026, 9, 13, 10, 0, 0)

        ep1 = await w._resolve_episode_id(_req(t0), 3)
        ep2 = await w._resolve_episode_id(_req(t0 + timedelta(minutes=5)), 2)
        ep3 = await w._resolve_episode_id(_req(t0 + timedelta(minutes=20)), 2)

        assert ep1 and ep1 == ep2 == ep3, "间隔内的连续写入应归到同一 episode"

    @pytest.mark.asyncio
    async def test_gap_exceeded_starts_new_episode(self):
        w = _writer(_FakeCache(), gap=1800.0)
        t0 = datetime(2026, 9, 13, 10, 0, 0)

        ep1 = await w._resolve_episode_id(_req(t0), 3)
        ep2 = await w._resolve_episode_id(_req(t0 + timedelta(hours=3)), 3)

        assert ep1 != ep2, "超过间隔应开新 episode"

    @pytest.mark.asyncio
    async def test_cap_forces_rollover(self):
        """一直开着的会话不能变成一个巨型 episode。"""
        w = _writer(_FakeCache(), gap=1800.0, cap=5)
        t0 = datetime(2026, 9, 13, 10, 0, 0)

        ep1 = await w._resolve_episode_id(_req(t0), 4)
        ep2 = await w._resolve_episode_id(_req(t0 + timedelta(minutes=1)), 4)  # →8 超 cap
        ep3 = await w._resolve_episode_id(_req(t0 + timedelta(minutes=2)), 1)

        assert ep1 == ep2, "第二次写入时累计仍未超 cap"
        assert ep3 != ep2, "累计超过 cap 后应强制滚动"

    @pytest.mark.asyncio
    async def test_different_users_and_sessions_isolated(self):
        w = _writer(_FakeCache())
        t0 = datetime(2026, 9, 13, 10, 0, 0)

        a = await w._resolve_episode_id(_req(t0, user="u1"), 1)
        b = await w._resolve_episode_id(_req(t0, user="u2"), 1)
        c = await w._resolve_episode_id(_req(t0, user="u1", session="other"), 1)

        assert a != b and a != c, "不同 user / session 不共享 episode"

    @pytest.mark.asyncio
    async def test_no_cache_degrades_gracefully(self):
        """cache 不可用时返回空串，写入照常，episode 功能自然降级。"""
        w = _writer(None)
        assert await w._resolve_episode_id(_req(datetime.now()), 3) == ""


class TestEpisodeCloseTriggersSummary:
    """episode 关闭（滚动）是总结它的时机 —— 只在这一刻触发，且只触发一次。"""

    @pytest.mark.asyncio
    async def test_summary_scheduled_only_on_rollover(self):
        w = _writer(_FakeCache(), gap=1800.0)
        scheduled = []
        w._schedule_episode_summary = lambda req, ep: scheduled.append(ep)
        t0 = datetime(2026, 9, 13, 10, 0, 0)

        ep1 = await w._resolve_episode_id(_req(t0), 3)
        await w._resolve_episode_id(_req(t0 + timedelta(minutes=5)), 3)
        assert scheduled == [], "同一 episode 内继续写入不应触发总结"

        await w._resolve_episode_id(_req(t0 + timedelta(hours=3)), 3)
        assert scheduled == [ep1], "只在关闭时对刚关闭的那个 episode 触发一次"

    @pytest.mark.asyncio
    async def test_summary_uses_the_writer_vector_store(self, monkeypatch):
        """后台摘要必须拿到 writer 已初始化的 vector store。

        旧实现访问了不存在的 ``self.vector_store``，AttributeError 被
        except 吞掉，L3 episode 摘要从不生成。
        """
        from types import SimpleNamespace

        import hy_memory.agent.episode_summary as episode_summary

        captured = {}

        async def fake_summarize(**kwargs):
            captured.update(kwargs)
            return "episode-node"

        monkeypatch.setattr(
            episode_summary, "summarize_closed_episode", fake_summarize,
        )

        w = _writer(_FakeCache())
        w.config.recall.episode_summary_enabled = True
        w._mem_agent = SimpleNamespace(llm_provider=object())
        w._embed_service = object()
        vector_store = object()
        w._external_vector_store = vector_store
        w._vector_store = vector_store
        w._vector_store_initialized = True

        w._schedule_episode_summary(_req(datetime(2026, 9, 13)), "ep-1")
        await asyncio.sleep(0.05)

        assert captured["vector_store"] is vector_store


# ======================================================================
# Episode 摘要（写侧，省 token）
# ======================================================================

class _FakeLLM:
    def __init__(self, text="用户这次讨论了周末去公园的安排。"):
        self.text = text
        self.calls = []

    async def complete(self, prompt, **kw):
        self.calls.append(prompt)
        return type("R", (), {"content": self.text})()


class _FakeEmbed:
    async def embed_queued(self, text):
        return [0.1] * 8


class _FakeVDB:
    def __init__(self, nodes):
        self._nodes = nodes
        self.upserted = []

    async def list_by_episode(self, episode_ids, **kw):
        want = set(episode_ids)
        return [n for n in self._nodes if (n.episode_id or "") in want]

    async def upsert(self, node):
        self.upserted.append(node)
        return node.node_id


def _mem(content, ep, *, tags=None, at=None):
    return MemoryNode(
        user_id="u", agent_id="a", session_id="s", episode_id=ep,
        layer=MemoryLayer.L2_FACT, content=content,
        tags=tags or [], memory_at=at or datetime(2026, 9, 13, 10, 0, 0),
    )


class TestEpisodeSummary:
    @pytest.mark.asyncio
    async def test_summarizes_episode_once_without_prev_summary(self):
        """关键差异：输入里**没有**上一条摘要 —— 不需要 merge/重写，所以省 token。"""
        from hy_memory.agent.episode_summary import (
            SUMMARY_KIND_EPISODE, summarize_closed_episode,
        )

        nodes = [
            _mem("用户周六去了公园", "ep1", tags=["outing"]),
            _mem("用户是骑车去公园的", "ep1", tags=["outing"]),
            _mem("用户在公园附近吃了午饭", "ep1", tags=["food"]),
        ]
        vdb, llm = _FakeVDB(nodes), _FakeLLM()

        nid = await summarize_closed_episode(
            vector_store=vdb, embed_service=_FakeEmbed(), llm_provider=llm,
            episode_id="ep1", user_id="u", agent_id="a",
        )

        assert nid and len(llm.calls) == 1, "一个 episode 只调一次 LLM"
        assert "prev" not in llm.calls[0].lower()
        assert "公园" in llm.calls[0] and "骑车" in llm.calls[0]

        node = vdb.upserted[0]
        assert node.layer == MemoryLayer.L3_SUMMARY
        assert node.custom["summary_kind"] == SUMMARY_KIND_EPISODE
        assert node.custom["episode_id"] == "ep1"
        assert node.custom["covered_count"] == 3
        assert node.episode_id == "ep1"
        # tag 继承，主题词也能召回到摘要
        assert "outing" in node.tags and "food" in node.tags

    @pytest.mark.asyncio
    async def test_tiny_episode_skipped(self):
        """一两条记忆的 episode 不值得单独摘要 —— 原记忆本身就够检索。"""
        from hy_memory.agent.episode_summary import summarize_closed_episode

        vdb, llm = _FakeVDB([_mem("用户喜欢猫", "ep1")]), _FakeLLM()
        nid = await summarize_closed_episode(
            vector_store=vdb, embed_service=_FakeEmbed(), llm_provider=llm,
            episode_id="ep1", user_id="u", min_memories=3,
        )
        assert nid is None and llm.calls == [], "应跳过，不调 LLM"

    @pytest.mark.asyncio
    async def test_memory_at_follows_episode_not_now(self):
        """摘要的事件时间取 episode 内最后一条，让时间过滤归到讨论发生的时点。"""
        from hy_memory.agent.episode_summary import summarize_closed_episode

        last = datetime(2026, 9, 10, 15, 0, 0)
        nodes = [
            _mem("a", "ep1", at=datetime(2026, 9, 10, 9, 0, 0)),
            _mem("b", "ep1", at=last),
            _mem("c", "ep1", at=datetime(2026, 9, 10, 12, 0, 0)),
        ]
        vdb = _FakeVDB(nodes)
        await summarize_closed_episode(
            vector_store=vdb, embed_service=_FakeEmbed(), llm_provider=_FakeLLM(),
            episode_id="ep1", user_id="u",
        )
        assert vdb.upserted[0].memory_at == last

    @pytest.mark.asyncio
    async def test_other_episodes_not_included(self):
        from hy_memory.agent.episode_summary import summarize_closed_episode

        nodes = [
            _mem("公园的事 1", "ep1"), _mem("公园的事 2", "ep1"),
            _mem("公园的事 3", "ep1"), _mem("完全无关的工作", "ep2"),
        ]
        vdb, llm = _FakeVDB(nodes), _FakeLLM()
        await summarize_closed_episode(
            vector_store=vdb, embed_service=_FakeEmbed(), llm_provider=llm,
            episode_id="ep1", user_id="u",
        )
        assert "完全无关的工作" not in llm.calls[0]

    @pytest.mark.asyncio
    async def test_token_cost_vs_rolling(self):
        """对一次跨 3 段的长讨论：rolling 要调 3 次且喂回旧摘要，episode 只调 1 次。"""
        from hy_memory.agent.episode_summary import summarize_closed_episode

        nodes = [_mem(f"记忆 {i}", "ep1") for i in range(12)]
        llm = _FakeLLM()
        await summarize_closed_episode(
            vector_store=_FakeVDB(nodes), embed_service=_FakeEmbed(),
            llm_provider=llm, episode_id="ep1", user_id="u",
        )
        assert len(llm.calls) == 1, (
            "12 条记忆的 episode 只应付一次 LLM；rolling summary 的窗口式触发会付多次"
        )


# ======================================================================
# 读侧：用 episode 框出候选
# ======================================================================

class TestEpisodeSupport:
    """episode 共属折算成结构支持度，形状与 reachable_within 一致，进同一漏斗。"""

    def _hit(self, nid, score, ep):
        return {
            "node_id": nid, "score": score,
            "node": MemoryNode(
                user_id="u", node_id=nid, content=nid,
                episode_id=ep, layer=MemoryLayer.L2_FACT,
            ),
        }

    def test_members_of_hit_episode_become_candidates(self):
        """用户例子：query 命中"吃饭"，同批写入的"公园"应被框进来。"""
        from hy_memory.pipelines._graph.rerank import episode_support

        hits = [self._hit("eat", 0.8, "ep1")]
        out = episode_support(
            hits, {"ep1": ["eat", "park", "bike"]}, seed_floor=0.6,
        )
        assert set(out) == {"park", "bike"}, "命中自身不重复入池"
        sup, via, etype, hops = out["park"]
        assert sup > 0 and via == "eat" and etype == "episode" and hops == 1

    def test_weak_hits_do_not_seed(self):
        """低分命中不能当 seed，否则会从噪声里框出一堆候选。"""
        from hy_memory.pipelines._graph.rerank import episode_support

        hits = [self._hit("eat", 0.3, "ep1")]
        assert episode_support(hits, {"ep1": ["eat", "park"]}, seed_floor=0.6) == {}

    def test_large_episode_yields_weaker_support(self):
        """episode 越大，单条共属越不具区分度（同 tag hub 的道理）。"""
        from hy_memory.pipelines._graph.rerank import episode_support

        hits = [self._hit("eat", 0.8, "ep1")]
        small = episode_support(hits, {"ep1": ["eat", "park", "bike"]})
        big = episode_support(
            hits, {"ep1": ["eat"] + [f"m{i}" for i in range(40)]}
        )
        assert small["park"][0] > big["m0"][0]

    def test_multi_seed_corroboration(self):
        """两个高分命中同属一个 episode → 该 episode 成员更可信。"""
        from hy_memory.pipelines._graph.rerank import episode_support

        one = episode_support(
            [self._hit("eat", 0.8, "ep1")], {"ep1": ["eat", "park", "bike"]}
        )
        two = episode_support(
            [self._hit("eat", 0.8, "ep1"), self._hit("bike", 0.75, "ep1")],
            {"ep1": ["eat", "park", "bike"]},
        )
        assert two["park"][0] > one["park"][0]

    def test_unrelated_episode_not_expanded(self):
        from hy_memory.pipelines._graph.rerank import episode_support

        hits = [self._hit("eat", 0.8, "ep1")]
        out = episode_support(hits, {"ep1": ["eat", "park"], "ep2": ["work", "code"]})
        assert "work" not in out and "code" not in out

    def test_empty_inputs(self):
        from hy_memory.pipelines._graph.rerank import episode_support

        assert episode_support([], {"ep1": ["a"]}) == {}
        assert episode_support([self._hit("a", 0.9, "ep1")], {}) == {}

    def test_episode_is_relational_not_temporal(self):
        """episode 是"跨批次延长的 co_occurs"（情节关系），不是时间边。"""
        from hy_memory.pipelines._graph.rerank import TYPE_PRIOR, edge_group

        assert edge_group("episode") == "relational"
        # 与 co_occurs 对齐，且高于 semantic_neighbor。
        assert TYPE_PRIOR["episode"] == TYPE_PRIOR["co_occurs"]
        assert TYPE_PRIOR["episode"] > TYPE_PRIOR["semantic_neighbor"]


class TestEpisodeIdPlumbing:
    """episode_id 要能穿过 payload 往返，否则读侧框不出东西。"""

    def test_node_dict_roundtrip(self):
        node = _mem("用户去了公园", "ep_abc")
        assert MemoryNode.from_dict(node.to_dict()).episode_id == "ep_abc"

    def test_payload_roundtrip(self):
        from hy_memory.data.vector_store_base import VectorStoreBase

        payload = VectorStoreBase._node_to_payload(_mem("x", "ep_xyz"))
        assert payload["episode_id"] == "ep_xyz"
        assert VectorStoreBase._payload_to_node(payload).episode_id == "ep_xyz"

    def test_episode_id_is_not_isolation_key(self):
        """episode_id 不参与隔离 —— 它是内部粒度，不是业务隔离键。"""
        a = _mem("x", "ep1")
        b = _mem("x", "ep2")
        assert a.get_isolation_key() == b.get_isolation_key()


class TestEpisodeRecallWithoutGraph:
    """Episode 框选只要 episode_id 字段 + 一次 VDB 过滤 —— graph 关掉也必须能用。"""

    def _reader(self, *, graph_store, episode_on, graph_rerank_on):
        from hy_memory.pipelines.reader_hybrid import HybridReadPipeline

        r = HybridReadPipeline.__new__(HybridReadPipeline)
        cfg = MemoryConfig()
        cfg.recall.episode_recall_enabled = episode_on
        cfg.graph_store.graph_rerank_enabled = graph_rerank_on
        r.config = cfg
        r._graph_store = graph_store
        return r

    def test_expansion_runs_with_episode_only(self):
        r = self._reader(graph_store=None, episode_on=True, graph_rerank_on=False)
        assert r._episode_recall_on() is True
        assert r._graph_rerank_on() is False
        assert r._expansion_on() is True, "graph 关闭时 episode 框选仍应跑"

    def test_expansion_off_when_both_off(self):
        r = self._reader(graph_store=None, episode_on=False, graph_rerank_on=False)
        assert r._expansion_on() is False

    def test_apply_graph_rerank_accepts_request(self):
        """回归：_apply_graph_rerank 少了 request 参数会 NameError，被外层 except 吞掉
        → 整个扩展静默失效。这里从签名上钉住。"""
        import inspect

        from hy_memory.pipelines.reader_hybrid import HybridReadPipeline

        sig = inspect.signature(HybridReadPipeline._apply_graph_rerank)
        assert "request" in sig.parameters

    def test_episode_candidates_gated_by_config(self):
        """开关关闭时不应发起 VDB 查询。"""
        class _Boom:
            async def list_by_episode(self, *a, **kw):
                raise AssertionError("开关关闭时不应查 VDB")

        r = self._reader(graph_store=None, episode_on=False, graph_rerank_on=False)
        from hy_memory.pipelines.base import ReadRequest

        out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            r._episode_candidates(
                _Boom(), [], {}, ReadRequest(query="q", user_id="u"), 0.6, 0.5,
            )
        )
        assert out == {}


class TestEpisodeCountsUserSideOnly:
    """episode 的计数与摘要输入都只看 user 侧 —— agent 侧记忆参与进来纯是烧 token。

    口径与 rolling summary 只数 pending_user_count 一致。
    """

    def test_count_helper_ignores_agent_and_defaults_to_user(self):
        from hy_memory.pipelines.writer import _count_user_owned

        metas = [
            {"content": "a", "owner": "user"},
            {"content": "b", "owner": "agent"},
            {"content": "c"},              # owner 缺省 → 视为 user
            {"content": "d", "owner": None},
        ]
        assert _count_user_owned(metas) == 3
        assert _count_user_owned([]) == 0

    @pytest.mark.asyncio
    async def test_agent_memories_do_not_burn_episode_cap(self):
        """agent 侧记忆若参与计数会把 episode 提前撑到上限、切得过碎。"""
        w = _writer(_FakeCache(), gap=1800.0, cap=4)
        t0 = datetime(2026, 9, 13, 10, 0, 0)

        # 每次写入 3 条 user + 5 条 agent；只数 user 的话两次才 6 > 4
        ep1 = await w._resolve_episode_id(_req(t0), 3)
        ep2 = await w._resolve_episode_id(_req(t0 + timedelta(minutes=1)), 3)
        assert ep1 == ep2, "两次 user 侧共 6 条，第二次写入时累计 3 仍未超 cap"

    @pytest.mark.asyncio
    async def test_summary_input_excludes_agent_memories(self):
        from hy_memory.agent.episode_summary import summarize_closed_episode

        def _owned(content, owner):
            n = _mem(content, "ep1")
            n.owner = owner
            return n

        nodes = [
            _owned("用户周六去了公园", "user"),
            _owned("助手推荐了三家餐厅", "agent"),
            _owned("用户骑车去的", "user"),
            _owned("助手解释了骑行路线", "agent"),
            _owned("用户在附近吃了午饭", "user"),
        ]
        vdb, llm = _FakeVDB(nodes), _FakeLLM()

        await summarize_closed_episode(
            vector_store=vdb, embed_service=_FakeEmbed(), llm_provider=llm,
            episode_id="ep1", user_id="u",
        )

        prompt = llm.calls[0]
        assert "用户周六去了公园" in prompt and "用户骑车去的" in prompt
        assert "助手推荐了三家餐厅" not in prompt, "agent 侧记忆不应进摘要输入"
        assert "助手解释了骑行路线" not in prompt
        # covered_count / source_memory_ids 也只统计 user 侧
        assert vdb.upserted[0].custom["covered_count"] == 3
        assert len(vdb.upserted[0].custom["source_memory_ids"]) == 3

    @pytest.mark.asyncio
    async def test_agent_only_episode_is_skipped_entirely(self):
        """整段都是 agent 侧 → 一次 LLM 都不该调。"""
        from hy_memory.agent.episode_summary import summarize_closed_episode

        nodes = []
        for i in range(5):
            n = _mem(f"助手说了第 {i} 句", "ep1")
            n.owner = "agent"
            nodes.append(n)

        llm = _FakeLLM()
        nid = await summarize_closed_episode(
            vector_store=_FakeVDB(nodes), embed_service=_FakeEmbed(),
            llm_provider=llm, episode_id="ep1", user_id="u",
        )
        assert nid is None and llm.calls == []

    @pytest.mark.asyncio
    async def test_missing_owner_treated_as_user(self):
        """owner 缺省要按 user 处理，否则老数据会被整段跳过。"""
        from hy_memory.agent.episode_summary import summarize_closed_episode

        nodes = [_mem(f"用户的事 {i}", "ep1") for i in range(3)]
        for n in nodes:
            n.owner = None

        llm = _FakeLLM()
        nid = await summarize_closed_episode(
            vector_store=_FakeVDB(nodes), embed_service=_FakeEmbed(),
            llm_provider=llm, episode_id="ep1", user_id="u",
        )
        assert nid is not None and len(llm.calls) == 1


# ======================================================================
# 埋点（pipeline log）
# ======================================================================

class TestInstrumentation:
    """新增环节必须有埋点：LLM 调用要能追到 prompt/产出，异步建图要能确认建没建。

    这些环节都是 fire-and-forget + 失败只告警的，没有埋点就等于线上不可观测 ——
    读侧"图没起作用"最常见的原因就是写侧压根没投影成功。
    """

    def test_episode_summary_reports_prompt_and_tokens(self):
        """_generate 要把实际 prompt 和 token 用量交出来，否则埋点只有结果没有输入。"""
        import inspect

        from hy_memory.agent import episode_summary

        src = inspect.getsource(episode_summary._generate)
        assert "return \"\", \"\", {}" in src or "prompt, tokens" in src
        assert "trace" in inspect.signature(
            episode_summary.summarize_closed_episode
        ).parameters

    def test_topic_summary_accepts_trace(self):
        """topic summary 从 dev4 起就在调 LLM，但一直没埋点。"""
        import inspect

        from hy_memory.pipelines._graph import topic_summary

        assert "trace" in inspect.signature(
            topic_summary.maybe_summarize_topics
        ).parameters

    @pytest.mark.asyncio
    async def test_episode_summary_emits_trace_with_prompt(self):
        from hy_memory.agent.episode_summary import summarize_closed_episode

        seen = []

        async def _trace(**kw):
            seen.append(kw)

        nodes = [_mem(f"用户的事 {i}", "ep1") for i in range(3)]
        await summarize_closed_episode(
            vector_store=_FakeVDB(nodes), embed_service=_FakeEmbed(),
            llm_provider=_FakeLLM("用户这次讨论了三件事。"),
            episode_id="ep1", user_id="u", trace=_trace,
        )

        assert len(seen) == 1
        rec = seen[0]
        assert "用户的事 0" in rec["prompt"], "prompt 必须完整落下来，badcase 才能复盘"
        assert rec["response"] == "用户这次讨论了三件事。"
        assert rec["parsed"]["episode_id"] == "ep1"
        assert rec["parsed"]["covered_count"] == 3
        assert len(rec["memory_ids"]) == 3
        assert rec["elapsed_ms"] >= 0

    @pytest.mark.asyncio
    async def test_no_trace_sink_is_harmless(self):
        """trace=None（trace 关闭）不能影响主流程。"""
        from hy_memory.agent.episode_summary import summarize_closed_episode

        nodes = [_mem(f"用户的事 {i}", "ep1") for i in range(3)]
        nid = await summarize_closed_episode(
            vector_store=_FakeVDB(nodes), embed_service=_FakeEmbed(),
            llm_provider=_FakeLLM(), episode_id="ep1", user_id="u", trace=None,
        )
        assert nid is not None

    def test_read_side_step_exists_and_funnel_is_observable(self):
        """READ_GRAPH_RERANK 的 stats 必须能区分"图起作用"和"池子变大"。"""
        import inspect

        from hy_memory.pipelines._retrieval.trace import (
            STEP_READ_GRAPH_RERANK, ReadTraceLogger,
        )

        assert STEP_READ_GRAPH_RERANK == "READ_GRAPH_RERANK"
        params = inspect.signature(ReadTraceLogger.log_graph_rerank).parameters
        # pool_size 是归因的关键：没有它无法判断变化是不是单纯池子变大
        for k in ("stats", "pool_size", "admitted_ids", "elapsed_ms"):
            assert k in params

    def test_writer_has_trace_sink_and_new_steps(self):
        import inspect

        from hy_memory.pipelines.writer import MemoryWriter

        assert hasattr(MemoryWriter, "_make_trace_sink")
        assert hasattr(MemoryWriter, "_log_episode")

        src = inspect.getsource(MemoryWriter)
        for step in ("EPISODE", "EPISODE_SUMMARY", "TOPIC_SUMMARY", "GRAPH_PROJECT"):
            assert f'"{step}"' in src, f"缺少 step={step} 的埋点"

    @pytest.mark.asyncio
    async def test_episode_partition_decision_is_logged(self):
        """划分决策（沿用/新开、距上次多久、累计几条）要能复盘。"""
        w = _writer(_FakeCache(), gap=1800.0)
        logged = []

        async def _log(request, **kw):
            logged.append(kw)

        w._log_episode = _log
        t0 = datetime(2026, 9, 13, 10, 0, 0)

        await w._resolve_episode_id(_req(t0), 3)
        await w._resolve_episode_id(_req(t0 + timedelta(minutes=5)), 2)
        await w._resolve_episode_id(_req(t0 + timedelta(hours=5)), 1)

        assert [r["reused"] for r in logged] == [False, True, False]
        assert logged[1]["idle_s"] == 300.0
        assert logged[1]["count"] == 5, "累计条数要落下来"
        assert logged[2]["reused"] is False
        assert all(r["gap"] == 1800.0 for r in logged)
