# -*- coding: utf-8 -*-
"""scene="all"（search / list）与生产力条目的 update / delete。

客户端用 object.__new__(HyMemoryClient) 搭最小骨架，公开 async_* 方法通过
__wrapped__ 直接调（不需要 client 自己的 loop 线程），与
test_list_and_stats_by_scene.py 同一套写法。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hy_memory.client import _SCENE_RRF_K, HyMemoryClient
from hy_memory.coding.store import CodingMemoryStore
from hy_memory.coding.types import CodingMemory, WorkingFact
from hy_memory.models.memory import MemoryLayer, MemoryStatus
from hy_memory.retrieval import AmbiguousMemoryIdError

BASE = datetime(2026, 10, 20, 8, 0, 0)


def _unwrapped(name):
    return getattr(HyMemoryClient, name).__wrapped__


async def _store(coding_config, mock_embed, variant):
    store = CodingMemoryStore(coding_config, embed_service=mock_embed, variant=variant)
    await store.initialize()
    return store


async def _noop(*_args, **_kwargs):
    return None


def _client(coding_config, mock_embed, stores, *, nodes=()):
    client = object.__new__(HyMemoryClient)
    client._config = coding_config
    client._coding_stores = dict(stores)
    client._embed_service = mock_embed
    client._cache = None
    client._graph_store = None
    client._history_store = None
    client._vector_store = _FakeVectorStore(list(nodes))
    client._get_coding_writer = _noop
    client._maybe_store_op_log = _noop
    client._decorate_with_chain = lambda *a, **k: ("", [])
    return client


class _FakeVectorStore:
    def __init__(self, nodes):
        self.nodes = {n.node_id: n for n in nodes}

    async def list_by_user(self, *, layers=None, **_):
        if layers == [MemoryLayer.L0_BASIC_INFO]:
            return []
        return list(self.nodes.values())

    async def get_by_id(self, memory_id):
        return self.nodes.get(memory_id)


def _chat_node(node_id, *, created, modified=None, source="cursor"):
    return SimpleNamespace(
        node_id=node_id, layer=MemoryLayer.L2_FACT, status=MemoryStatus.ACTIVE,
        content=f"chat {node_id}", memory_at=None,
        gmt_created=created, gmt_modified=modified, valid_from=None,
        user_id="u", agent_id="a", session_id="s", source=source,
        speculate=None, tags=[], custom={},
    )


# ================================================================
# search(scene="all")
# ================================================================

def _chat_resp(*ids):
    return {
        "request_id": "r",
        "memories": {
            "normal": [
                {"memory_id": i, "content": f"chat {i}", "score": 0.9 - n * 0.1,
                 "gmt_created": 1_700_000_000 + n, "source": "cursor"}
                for n, i in enumerate(ids)
            ],
            "schema": [],
            "proactive": [],
        },
    }


def _prod_resp(facts=(), exps=()):
    return {
        "request_id": "r",
        "scene": "productivity",
        "facts": [
            {"fact_id": f, "statement": f"stmt {f}", "score": 0.5,
             "created_at": "2026-10-20T08:00:00", "source": "claude"}
            for f in facts
        ],
        "experiences": [
            {"experience_id": e, "task": f"task {e}", "solution": f"sol {e}",
             "score": 0.7, "updated_at": "2026-10-20T09:00:00", "source": "claude"}
            for e in exps
        ],
    }


def _fake_all_client(*, chat, working=None, coding=None, fail=(), enable=True,
                     seen=None):
    """_search_all_scenes 的依赖全部换成桩：只测融合与容错。"""
    client = object.__new__(HyMemoryClient)
    client._config = SimpleNamespace(coding=SimpleNamespace(enable=enable))

    class _Embed:
        calls = 0

        async def embed(self, text):
            _Embed.calls += 1
            return [0.1, 0.2]

    client._embed_service = _Embed()
    seen = seen if seen is not None else {}

    async def _impl(query, **kw):
        seen["chat"] = kw
        if "chat" in fail:
            raise RuntimeError("chat backend down")
        return chat

    async def _coding_store(variant):
        return object() if {"working": working, "coding": coding}[variant] is not None else None

    async def _search_coding(target_query, **kw):
        seen[kw["variant"]] = kw
        if kw["variant"] in fail:
            raise RuntimeError(f"{kw['variant']} store broken")
        return {"working": working, "coding": coding}[kw["variant"]]

    client._async_search_impl = _impl
    client._coding_store_for_exact_get = _coding_store
    client._search_coding = _search_coding
    return client, seen


async def _search_all(client, **kw):
    params = dict(
        queries=None, search_mode="default", user_ids=["u"], agent_ids=[],
        session_ids=[], include_sources=["claude"], exclude_sources=["mixed"],
        limit=10, min_score=0.4, working_fact_min_score=None,
        working_experience_min_score=None, schema_min_score=0.4, schema_limit=10,
        intention_limit=0, created_after=None, reader="", workspace_id=None,
        branch=None, request_id="req", layers=None, query_embedding=None,
        rerank=True, target_query="q", lite_mode=False, t0=0.0,
    )
    params.update(kw)
    return await HyMemoryClient._search_all_scenes(client, "q", **params)


async def test_search_all_fuses_by_lane_rank_with_rrf():
    client, seen = _fake_all_client(
        chat=_chat_resp("c1", "c2", "c3"),
        working=_prod_resp(facts=["wf1"], exps=["we1", "we2"]),
        coding=_prod_resp(exps=["ce1"]),
    )

    out = await _search_all(client, rerank=False)

    assert out["scene"] == "all"
    assert out["request_id"] == "req"
    assert out["lanes"] == {"chat": 3, "working": 3, "coding": 1}
    assert out["errors"] == {}
    order = [(m["scene"], m["memory_id"]) for m in out["memories"]]
    # 名次 1：chat → working → coding（同分按路序），名次 2：chat / working ...
    assert order == [
        ("chat", "c1"), ("working", "wf1"), ("coding", "ce1"),
        ("chat", "c2"), ("working", "we1"),
        ("chat", "c3"), ("working", "we2"),
    ]
    first = out["memories"][0]
    assert first["score"] == round(1.0 / (_SCENE_RRF_K + 1), 6)
    assert first["lane_score"] == pytest.approx(0.9)
    assert first["lane_rank"] == 1 and first["kind"] == "chat"
    fact = out["memories"][1]
    assert fact["kind"] == "fact" and fact["content"] == "stmt wf1"
    assert fact["lane_score"] == 0.5
    exp = out["memories"][4]
    assert exp["kind"] == "experience"
    assert exp["memory_id"] == "we1"
    assert exp["content"] == "task we1\nsol we1"
    assert exp["score"] == round(1.0 / (_SCENE_RRF_K + 2), 6)
    # 生产力时间戳是 naive UTC，按 UTC 换算成 epoch
    assert exp["updated_at"] == int(
        datetime(2026, 10, 20, 9, tzinfo=timezone.utc).timestamp()
    )
    # rerank / 来源过滤 / 阈值传到每一路，query 向量只算一次
    for lane in ("chat", "working", "coding"):
        assert seen[lane]["rerank"] is False
        assert seen[lane]["include_sources"] == ["claude"]
        assert seen[lane]["query_embedding"] == [0.1, 0.2]
    assert seen["chat"]["scene"] == "chat"
    assert client._embed_service.calls == 1


async def test_search_all_truncates_after_fusion():
    client, _ = _fake_all_client(
        chat=_chat_resp("c1", "c2", "c3"),
        working=_prod_resp(exps=["we1", "we2"]),
        coding=None,
    )
    out = await _search_all(client, limit=3)
    assert [m["memory_id"] for m in out["memories"]] == ["c1", "we1", "c2"]
    # coding 没写过：不贡献、不算错误
    assert out["lanes"]["coding"] == 0
    assert "coding" not in out["errors"]


async def test_search_all_isolates_a_failing_lane():
    client, _ = _fake_all_client(
        chat=_chat_resp("c1"),
        working=_prod_resp(exps=["we1"]),
        coding=_prod_resp(exps=["ce1"]),
        fail=("working",),
    )
    out = await _search_all(client)
    assert [m["memory_id"] for m in out["memories"]] == ["c1", "ce1"]
    assert out["lanes"] == {"chat": 1, "working": 0, "coding": 1}
    assert "store broken" in out["errors"]["working"]

    client, _ = _fake_all_client(
        chat=_chat_resp("c1"), working=_prod_resp(exps=["we1"]), fail=("chat",),
    )
    out = await _search_all(client)
    assert [m["memory_id"] for m in out["memories"]] == ["we1"]
    assert "chat backend down" in out["errors"]["chat"]


async def test_search_all_reports_disabled_productivity():
    client, seen = _fake_all_client(
        chat=_chat_resp("c1"), working=_prod_resp(exps=["we1"]), enable=False,
    )
    out = await _search_all(client)
    assert [m["memory_id"] for m in out["memories"]] == ["c1"]
    assert out["lanes"] == {"chat": 1, "working": 0, "coding": 0}
    assert set(out["errors"]) == {"working", "coding"}
    assert "working" not in seen


async def test_async_search_routes_scene_all(coding_config):
    seen = {}

    async def _spy(query, **kw):
        seen.update(kw)
        return {"scene": "all"}

    client = object.__new__(HyMemoryClient)
    client._config = coding_config
    client._search_all_scenes = _spy
    out = await _unwrapped("async_search")(
        client, "q", scene=" ALL ", user_ids=["u"], include_sources=["Claude"],
        rerank=False, limit=7,
    )
    assert out == {"scene": "all"}
    assert seen["rerank"] is False and seen["limit"] == 7
    assert seen["include_sources"] == ["claude"]
    assert seen["exclude_sources"] == ["mixed"]

    with pytest.raises(ValueError, match="all"):
        await _unwrapped("async_search")(client, "q", scene="every", user_ids=["u"])


async def test_search_all_productivity_lanes_respect_sources(coding_config, mock_embed):
    """真 store：两路生产力检索按 source 过滤，经验与事实都过。"""
    coding_config.coding.enable = True
    working = await _store(coding_config, mock_embed, "working")
    coding = await _store(coding_config, mock_embed, "coding")
    try:
        await working.insert(CodingMemory(
            experience_id="w-claude", user_id="u", task="deploy service",
            solution="kubectl apply", source="claude",
        ))
        await working.insert(CodingMemory(
            experience_id="w-cursor", user_id="u", task="deploy service",
            solution="helm upgrade", source="cursor",
        ))
        await working.insert_fact(WorkingFact(
            fact_id="f-cursor", user_id="u", statement="deploy service",
            source="cursor",
        ))
        await coding.insert(CodingMemory(
            experience_id="c-claude", user_id="u", task="deploy service",
            solution="make deploy", source="claude",
        ))
        client = _client(coding_config, mock_embed,
                         {"working": working, "coding": coding})

        async def _chat(query, **kw):
            return _chat_resp()

        client._async_search_impl = _chat
        out = await _search_all(
            client, target_query="deploy service",
            include_sources=["claude"], exclude_sources=["mixed"],
        )
        ids = {(m["scene"], m["memory_id"]) for m in out["memories"]}
        assert ids == {("working", "w-claude"), ("coding", "c-claude")}
        assert all(m["source"] == "claude" for m in out["memories"])
    finally:
        await working.close()
        await coding.close()


async def test_productivity_search_rerank_false_skips_fact_lexical_rerank(
    coding_config, mock_embed, monkeypatch,
):
    from hy_memory.coding import lexical

    calls = []
    real = lexical.rerank

    def _spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(lexical, "rerank", _spy)
    working = await _store(coding_config, mock_embed, "working")
    try:
        for index in range(8):
            await working.insert_fact(WorkingFact(
                fact_id=f"f{index}", user_id="u", statement="deploy service",
            ))
        client = _client(coding_config, mock_embed, {"working": working})

        async def _run(rerank):
            return await HyMemoryClient._search_coding(
                client, "deploy service", user_ids=["u"], workspace_id=None,
                branch=None, limit=2, request_id="r", t0=0.0,
                variant="working", rerank=rerank,
            )

        await _run(False)
        assert calls == []
        await _run(True)
        assert len(calls) == 1
    finally:
        await working.close()


# ================================================================
# list_memories(scene="all") 与生产力 list 的来源过滤
# ================================================================

async def test_list_productivity_scene_filters_sources_with_true_total(
    coding_config, mock_embed,
):
    coding = await _store(coding_config, mock_embed, "coding")
    try:
        for index, source in enumerate(["claude", "cursor", "claude", "Claude"]):
            await coding.insert(CodingMemory(
                experience_id=f"c{index}", user_id="u", task="T", solution="S",
                source=source,
            ))
        client = _client(coding_config, mock_embed, {"coding": coding})
        list_memories = _unwrapped("async_list_memories")

        page = await list_memories(
            client, user_id="u", scene="coding", include_sources=["CLAUDE"], limit=2,
        )
        assert page["total"] == 3
        assert len(page["experiences"]) == 2
        assert all(e["source"] == "claude" for e in page["experiences"])

        page = await list_memories(
            client, user_id="u", scene="coding", exclude_sources=["claude"],
        )
        assert page["total"] == 1
        assert [e["experience_id"] for e in page["experiences"]] == ["c1"]

        unfiltered = await list_memories(client, user_id="u", scene="coding")
        assert unfiltered["total"] == 4
    finally:
        await coding.close()


async def _seed_all(coding_config, mock_embed):
    coding = await _store(coding_config, mock_embed, "coding")
    working = await _store(coding_config, mock_embed, "working")
    # 生产力时间戳按 UTC 写；这里直接给 updated_at 再改回去（insert 会刷新）
    for eid, hours, source in (("c-old", 1, "claude"), ("c-new", 5, "cursor")):
        await coding.insert(CodingMemory(
            experience_id=eid, user_id="u", task=f"task {eid}", solution="S",
            source=source,
        ))
        await _set_updated(coding, eid, BASE + timedelta(hours=hours))
    await working.insert(CodingMemory(
        experience_id="w-mid", user_id="u", task="task w", solution="W",
        source="claude",
    ))
    await _set_updated(working, "w-mid", BASE + timedelta(hours=3))
    # chat 节点是本地时间 naive；与 UTC 的经验比较时要换算到同一 epoch
    utc_offset = datetime.now() - datetime.utcnow()
    nodes = [
        _chat_node("m-newest", created=BASE + utc_offset + timedelta(hours=6),
                   source="claude"),
        _chat_node("m-edited", created=BASE + utc_offset,
                   modified=BASE + utc_offset + timedelta(hours=4), source="cursor"),
        _chat_node("m-oldest", created=BASE + utc_offset, source="claude"),
    ]
    return coding, working, nodes


async def _set_updated(store, experience_id, when):
    def _sync():
        with store._lock:
            store._conn.execute(
                "UPDATE coding_memory_meta SET updated_at = ? WHERE experience_id = ?",
                (when.isoformat(), experience_id),
            )

    _sync()


async def test_list_all_merges_newest_first_and_paginates(coding_config, mock_embed):
    coding, working, nodes = await _seed_all(coding_config, mock_embed)
    try:
        client = _client(coding_config, mock_embed,
                         {"coding": coding, "working": working}, nodes=nodes)
        list_memories = _unwrapped("async_list_memories")

        out = await list_memories(client, user_id="u", scene="all", limit=10)
        assert out["scene"] == "all"
        assert out["total"] == 6
        assert out["totals"] == {"chat": 3, "working": 1, "coding": 2}
        assert [(m["scene"], m["memory_id"]) for m in out["memories"]] == [
            ("chat", "m-newest"),
            ("coding", "c-new"),
            ("chat", "m-edited"),
            ("working", "w-mid"),
            ("coding", "c-old"),
            ("chat", "m-oldest"),
        ]
        exp = out["memories"][1]
        assert exp["kind"] == "experience" and exp["content"] == "task c-new\nS"
        assert exp["status"] == "active"
        assert exp["updated_at"] > out["memories"][2]["updated_at"]
        assert out["memories"][0]["kind"] == "chat"

        pages = []
        for offset in (0, 2, 4):
            page = await list_memories(
                client, user_id="u", scene="all", limit=2, offset=offset,
            )
            assert page["total"] == 6
            pages.extend(m["memory_id"] for m in page["memories"])
        assert pages == [m["memory_id"] for m in out["memories"]]

        with pytest.raises(ValueError, match="desc"):
            await list_memories(client, user_id="u", scene="all", order="asc")
    finally:
        await coding.close()
        await working.close()


async def test_list_all_applies_source_and_status_filters(coding_config, mock_embed):
    coding, working, nodes = await _seed_all(coding_config, mock_embed)
    try:
        client = _client(coding_config, mock_embed,
                         {"coding": coding, "working": working}, nodes=nodes)
        list_memories = _unwrapped("async_list_memories")

        out = await list_memories(
            client, user_id="u", scene="all", include_sources=["claude"],
        )
        assert [m["memory_id"] for m in out["memories"]] == [
            "m-newest", "w-mid", "c-old", "m-oldest",
        ]
        assert out["totals"] == {"chat": 2, "working": 1, "coding": 1}
        assert out["total"] == 4

        out = await list_memories(
            client, user_id="u", scene="all", statuses=["archived"],
        )
        # 经验只有 ACTIVE：两路生产力不贡献；chat 假库不按状态过滤，原样返回
        assert out["totals"]["working"] == 0 and out["totals"]["coding"] == 0
        assert all(m["scene"] == "chat" for m in out["memories"])
    finally:
        await coding.close()
        await working.close()


async def test_list_all_does_not_create_unwritten_scene_databases(coding_config, mock_embed):
    client = _client(coding_config, mock_embed, {}, nodes=[
        _chat_node("m1", created=BASE),
    ])
    out = await _unwrapped("async_list_memories")(client, user_id="u", scene="all")
    assert out["total"] == 1
    assert out["totals"] == {"chat": 1, "working": 0, "coding": 0}
    for variant in ("working", "coding"):
        assert not CodingMemoryStore.database_exists(
            coding_config, db_path=coding_config.coding.db_path, variant=variant,
        )


# ================================================================
# update / delete 生产力条目
# ================================================================

async def test_update_experience_replaces_solution_and_keeps_task(coding_config, mock_embed):
    working = await _store(coding_config, mock_embed, "working")
    try:
        await working.insert(CodingMemory(
            experience_id="e1", user_id="u", task="deploy service",
            search_keys=["rollout"], solution="old way",
        ))
        await _set_updated(working, "e1", BASE)
        client = _client(coding_config, mock_embed, {"working": working})

        out = await _unwrapped("async_update")(client, "e1", "new way")
        assert out["success"] is True
        assert out["memory_id"] == "e1"
        assert out["kind"] == "experience" and out["variant"] == "working"
        assert {"request_id", "elapsed_ms"} <= set(out)

        stored = await working.get_by_id("e1")
        assert stored.task == "deploy service"
        assert stored.solution == "new way"
        assert stored.updated_at > BASE
        # solution 不进向量：仍能按原 task 召回
        hits = await working.search_by_query_embedding(
            await mock_embed.embed("deploy service"), user_id="u",
        )
        assert hits and hits[0]["experience_id"] == "e1"

        out = await _unwrapped("async_update")(
            client, "e1", "newer way", task="ship service",
        )
        assert out["success"] is True
        stored = await working.get_by_id("e1")
        assert stored.task == "ship service"
        hits = await working.search_by_query_embedding(
            await mock_embed.embed("ship service"), user_id="u",
        )
        assert hits[0]["experience_id"] == "e1"
        assert hits[0]["score"] == pytest.approx(1.0, abs=1e-4)
    finally:
        await working.close()


async def test_update_and_delete_fact(coding_config, mock_embed):
    coding = await _store(coding_config, mock_embed, "coding")
    try:
        await coding.insert_fact(WorkingFact(
            fact_id="f1", user_id="u", statement="build uses make",
        ))
        client = _client(coding_config, mock_embed, {"coding": coding})

        out = await _unwrapped("async_update")(
            client, "f1", "build uses bazel", kind="fact",
        )
        assert out["success"] is True and out["variant"] == "coding"
        assert (await coding.get_fact("f1")).statement == "build uses bazel"
        hits = await coding.search_facts(
            await mock_embed.embed("build uses bazel"), user_id="u",
        )
        assert [f.fact_id for f, _ in hits] == ["f1"]

        with pytest.raises(ValueError, match="task"):
            await _unwrapped("async_update")(client, "f1", "x", task="t")

        out = await _unwrapped("async_delete")(client, "f1")
        assert out["success"] is True and out["deleted_count"] == 1
        assert out["kind"] == "fact"
        assert await coding.get_fact("f1") is None
        assert await coding.search_facts(
            await mock_embed.embed("build uses bazel"), user_id="u",
        ) == []
    finally:
        await coding.close()


async def test_delete_experience_removes_row_and_vectors(coding_config, mock_embed):
    working = await _store(coding_config, mock_embed, "working")
    try:
        await working.insert(CodingMemory(
            experience_id="e1", user_id="u", task="deploy service",
            search_keys=["rollout"], solution="S",
        ))
        client = _client(coding_config, mock_embed, {"working": working})

        out = await _unwrapped("async_delete")(
            client, "e1", kind="experience", variant="working",
        )
        assert out["success"] is True
        assert out["deleted_count"] == 1
        assert out["memory_id"] == "e1"
        assert await working.get_by_id("e1") is None
        assert await working._vec.get_by_id("e1:0") is None
        assert await working._vec.get_by_id("e1:1") is None
    finally:
        await working.close()


async def test_productivity_not_found_and_ambiguity(coding_config, mock_embed):
    working = await _store(coding_config, mock_embed, "working")
    coding = await _store(coding_config, mock_embed, "coding")
    try:
        for store in (working, coding):
            await store.insert(CodingMemory(
                experience_id="same", user_id="u", task="T", solution="S",
            ))
        client = _client(coding_config, mock_embed,
                         {"working": working, "coding": coding})

        with pytest.raises(AmbiguousMemoryIdError):
            await _unwrapped("async_update")(client, "same", "x")
        with pytest.raises(AmbiguousMemoryIdError):
            await _unwrapped("async_delete")(client, "same")

        out = await _unwrapped("async_delete")(client, "same", variant="coding")
        assert out["success"] is True and out["variant"] == "coding"
        assert await working.get_by_id("same") is not None

        missing = await _unwrapped("async_update")(client, "nope", "x", kind="experience")
        assert missing["success"] is False and "not found" in missing["error_message"]
        missing = await _unwrapped("async_delete")(client, "nope", kind="fact")
        assert missing["success"] is False and missing["deleted_count"] == 0
        # variant 给了就不回落到 chat
        missing = await _unwrapped("async_delete")(client, "nope", variant="working")
        assert missing["success"] is False and missing["deleted_count"] == 0

        with pytest.raises(ValueError):
            await _unwrapped("async_delete")(client, "same", kind="chat", variant="coding")
    finally:
        await working.close()
        await coding.close()


async def test_update_and_delete_without_kind_still_handle_chat(coding_config, mock_embed):
    """默认行为不变：chat ID 走原 chat 路径（store 都没建时也不建库）。"""
    client = _client(coding_config, mock_embed, {})
    seen = []

    async def _chat_update(memory_id, content):
        seen.append(("update", memory_id, content))
        return {"success": True, "memory_id": memory_id}

    async def _chat_delete(memory_id):
        seen.append(("delete", memory_id))
        return {"success": True, "deleted_count": 1}

    client._update_chat = _chat_update
    client._delete_chat = _chat_delete
    client._vector_store = _FakeVectorStore([_chat_node("m1", created=BASE)])

    assert (await _unwrapped("async_update")(client, "m1", "new"))["success"]
    assert (await _unwrapped("async_delete")(client, "m1"))["success"]
    # 哪都没有：仍交给 chat 路径给出原来的 not-found 结果
    await _unwrapped("async_delete")(client, "ghost")
    assert seen == [("update", "m1", "new"), ("delete", "m1"), ("delete", "ghost")]
    for variant in ("working", "coding"):
        assert not CodingMemoryStore.database_exists(
            coding_config, db_path=coding_config.coding.db_path, variant=variant,
        )
