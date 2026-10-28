# -*- coding: utf-8 -*-
"""经验与事实记下对话时间 memory_at，并以秒级 epoch 对外返回（与 chat 一致）。

之前生产力链路丢了 add() 传进来的 memory_at，coding_memory_meta / working_fact
也没有这一列，MemHub 里所有经验都显示"时间未知"。
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.coding.extractor import CodingMemoryExtractor
from hy_memory.coding.reconciler import CodingMemoryReconciler
from hy_memory.coding.store import CodingMemoryStore, _CREATE_TABLE_SQL, _CREATE_FACT_TABLE_SQL
from hy_memory.coding.types import CodingMemory, WorkingFact
from hy_memory.coding.writer import CodingWriter

from tests.coding._helpers import MockLLMProvider
from tests.coding.test_list_and_stats_by_scene import _client, _unwrapped
from tests.coding.test_write_scene_routing import _messages
from tests.coding.test_writer_e2e import _coding_messages, _extract_resp, _reconcile_resp

T1 = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=3)


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _writer(store, embed, llm):
    return CodingWriter(
        store=store,
        extractor=CodingMemoryExtractor(llm),
        reconciler=CodingMemoryReconciler(store, embed, llm),
        llm_provider=llm, embed_service=embed,
    )


@pytest.mark.asyncio
class TestStore:
    async def test_round_trip_as_epoch(self, coding_store):
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u", task="T", solution="S", memory_at=T1,
        ))
        await coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u", statement="x", memory_at=T1,
        ))
        exp = await coding_store.get_by_id("e1", user_id="u")
        fact = await coding_store.get_fact("f1", user_id="u")
        assert _epoch(exp.memory_at) == _epoch(T1)
        assert exp.to_dict()["memory_at"] == _epoch(T1)
        assert fact.to_dict()["memory_at"] == _epoch(T1)

    async def test_missing_time_stays_unknown(self, coding_store):
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u", task="T", solution="S",
        ))
        exp = await coding_store.get_by_id("e1", user_id="u")
        assert exp.memory_at is None and exp.to_dict()["memory_at"] is None

    @pytest.mark.parametrize("old, new", [(T1, T2), (T2, T1), (T2, None)])
    async def test_update_keeps_the_later_time(self, coding_store, old, new):
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u", task="T", solution="S",
            session_id="s", memory_at=old,
        ))
        await coding_store.update(CodingMemory(
            experience_id="e1", user_id="u", task="T", solution="S2",
            session_id="s", memory_at=new,
        ))
        exp = await coding_store.get_by_id("e1", user_id="u")
        assert exp.solution == "S2"
        assert _epoch(exp.memory_at) == _epoch(T2)

    async def test_edit_keeps_the_time(self, coding_store):
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u", task="T", solution="S", memory_at=T1,
        ))
        await coding_store.edit_experience("e1", user_id="u", solution="S2")
        exp = await coding_store.get_by_id("e1", user_id="u")
        assert _epoch(exp.memory_at) == _epoch(T1)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["working", "coding"])
async def test_existing_database_gains_the_column_on_open(coding_config, mock_embed, variant):
    store = CodingMemoryStore(coding_config, embed_service=mock_embed, variant=variant)
    # 先按老 schema（没有 memory_at）建库并放一行
    conn = sqlite3.connect(store._db_path)
    conn.executescript(_CREATE_TABLE_SQL.replace("    memory_at        INTEGER,\n", ""))
    conn.executescript(_CREATE_FACT_TABLE_SQL.replace("    memory_at              INTEGER,\n", ""))
    conn.execute(
        "INSERT INTO coding_memory_meta (experience_id, user_id, task, solution, "
        "created_at, updated_at) VALUES ('old', 'u', 'T', 'S', '2026-01-01T00:00:00', "
        "'2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    await store.initialize()
    try:
        old = await store.get_by_id("old", user_id="u")
        assert old is not None and old.memory_at is None
        await store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u", statement="x", memory_at=T1,
        ))
        assert _epoch((await store.get_fact("f1")).memory_at) == _epoch(T1)
    finally:
        await store.close()
    # 再开一次不出错（ADD COLUMN 是幂等的）
    again = CodingMemoryStore(coding_config, embed_service=mock_embed, variant=variant)
    await again.initialize()
    await again.close()


@pytest.mark.asyncio
class TestWriter:
    def _llm(self):
        return MockLLMProvider([
            _extract_resp(
                [{"task": "打包发布", "solution": "S", "confidence": 0.9}],
                [{"statement": "发布脚本在 scripts/publish_package.sh"}],
            ),
            _reconcile_resp([{"draft_idx": 0, "action": "ADD"}]),
        ])

    async def test_conversation_time_reaches_both_layers(self, coding_store, mock_embed):
        resp = await _writer(coding_store, mock_embed, self._llm()).write(
            _coding_messages(), user_id="u", session_id="s", memory_at=T1,
        )
        exp = await coding_store.get_by_id(resp["experience_ids"][0], user_id="u")
        fact = await coding_store.get_fact(resp["fact_ids"][0], user_id="u")
        assert _epoch(exp.memory_at) == _epoch(T1)
        assert _epoch(fact.memory_at) == _epoch(T1)

    async def test_defaults_to_request_time_like_chat(self, coding_store, mock_embed):
        before = int(time.time())
        resp = await _writer(coding_store, mock_embed, self._llm()).write(
            _coding_messages(), user_id="u", session_id="s",
        )
        exp = await coding_store.get_by_id(resp["experience_ids"][0], user_id="u")
        fact = await coding_store.get_fact(resp["fact_ids"][0], user_id="u")
        for stamp in (_epoch(exp.memory_at), _epoch(fact.memory_at)):
            assert before <= stamp <= int(time.time()) + 1


@pytest.mark.asyncio
async def test_add_passes_the_time_to_the_productivity_writer(monkeypatch):
    seen = {}

    async def _judge(*args, **kwargs):
        return "working"

    class _Writer:
        async def write(self, *args, **kwargs):
            seen.update(kwargs)
            return {"success": True, "experience_ids": [], "fact_ids": []}

    async def _get_writer(*, variant):
        return _Writer()

    monkeypatch.setattr("hy_memory.client.classify_messages_scene", _judge)
    ns = SimpleNamespace(
        _config=SimpleNamespace(coding=SimpleNamespace(enable=True)),
        _coding_llm_provider=object(),
        _cache=None,
        _get_coding_writer=_get_writer,
        _normalize_scene=HyMemoryClient._normalize_scene,
        _scene_variant=HyMemoryClient._scene_variant,
        _parse_input=HyMemoryClient._parse_input,
    )
    await HyMemoryClient._async_add_impl(
        ns, _messages(), user_id="u1", agent_id="a1", memory_at=T1,
    )
    assert seen["memory_at"] == T1


@pytest.mark.asyncio
async def test_list_get_and_search_return_epoch_seconds(coding_config, mock_embed):
    coding = CodingMemoryStore(coding_config, embed_service=mock_embed, variant="coding")
    await coding.initialize()
    try:
        await coding.insert(CodingMemory(
            experience_id="e1", user_id="u", task="打包发布", solution="S", memory_at=T1,
        ))
        await coding.insert_fact(WorkingFact(
            fact_id="f1", user_id="u", statement="打包发布", memory_at=T2,
            derived_experience_ids=["e1"],
        ))
        client = _client([], {"coding": coding})
        list_memories = _unwrapped("async_list_memories")

        page = await list_memories(client, user_id="u", scene="coding")
        assert page["experiences"][0]["memory_at"] == _epoch(T1)

        merged = await list_memories(client, user_id="u", scene="all")
        assert merged["memories"][0]["memory_at"] == _epoch(T1)

        exp = await coding.get_by_id("e1", user_id="u")
        got = HyMemoryClient._format_productivity_get(
            exp, kind="experience", variant="coding", request_id="r", elapsed_ms=0,
        )
        assert got["memory_at"] == _epoch(T1)

        client._embed_service = mock_embed
        client._coding_store = coding

        async def _ready(variant="working"):
            return None

        client._get_coding_writer = _ready
        client._cache = None
        resp = await HyMemoryClient._search_coding(
            client, target_query="打包发布", user_ids=["u"], workspace_id=None,
            branch=None, limit=5, working_fact_min_score=0.0,
            working_experience_min_score=0.0, request_id="r",
            t0=time.perf_counter(), variant="coding",
        )
        assert resp["facts"][0]["memory_at"] == _epoch(T2)
        assert resp["experiences"][0]["memory_at"] == _epoch(T1)
        unified = HyMemoryClient._search_lane_items("coding", resp)
        assert {item["memory_at"] for item in unified} == {_epoch(T1), _epoch(T2)}
    finally:
        await coding.close()
