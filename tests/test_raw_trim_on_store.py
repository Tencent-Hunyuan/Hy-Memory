# -*- coding: utf-8 -*-
"""
raw 在写入时就裁剪好；历史拼接不再二次裁剪。

规则（hy_memory/utils/raw_trim.py）：
  - user / system 完整保留
  - assistant 与对方真人（非已知 role 的 label）strip 后超过 500 字符截成前 500 + "..."
  - tool 头尾保留、中段替换为 "[...N bytes omitted by truncate...]"
    （上限 coding.tool_result_max_bytes）
  - 全部幂等

覆盖：
  1. 规则本身 + 幂等（含 coding preproc 的两个截断函数）
  2. lite：L1_RAW 存的是裁剪后的文本（tool 头尾截断 + 标记），带 raw_trimmed 标记；
     审计 history 的 ADD 记录同样裁剪
  3. coding_trace 存的 tool result 已头尾截断
  4. pro：extractor 输入仍是完整 messages；历史 = 已存 raw 直接拼接；
     没有标记的旧 raw 仍按同一规则兼容裁剪
"""

import asyncio
import json
import sqlite3
import types
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from hy_memory.coding.preproc import (
    truncate_assistant_text,
    truncate_tool_result_text,
)
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.pipelines.base import ChatMessage, WriteRequest
from hy_memory.utils.raw_trim import (
    DEFAULT_RAW_ASSISTANT_MAX_CHARS,
    RAW_TRIMMED_KEY,
    trim_raw_body,
    trim_raw_messages,
)

_MARKER = "bytes omitted by truncate...]"
_LONG_USER = "我把整段日志贴给你：" + "U" * 5000
_LONG_ASSISTANT = "A" * 1200
_LONG_TOOL = "H" * 3000 + "MIDDLE" + "T" * 3000


def _messages() -> List[ChatMessage]:
    return [
        ChatMessage(role="system", content="S" * 900),
        ChatMessage(role="user", content=_LONG_USER),
        ChatMessage(role="assistant", content=_LONG_ASSISTANT),
        ChatMessage(role="tool", content=_LONG_TOOL, tool_call_id="c1", tool_name="Bash"),
        ChatMessage(role="assistant", name="John", content="J" * 800),
    ]


# ---------------------------------------------------------------- 1. 规则


class TestTrimRule:
    def test_user_kept_in_full(self):
        assert trim_raw_body("user", _LONG_USER) == _LONG_USER

    def test_system_kept_in_full(self):
        assert trim_raw_body("system", "S" * 900) == "S" * 900

    def test_long_assistant_cut_at_limit(self):
        out = trim_raw_body("assistant", _LONG_ASSISTANT)
        assert out == "A" * DEFAULT_RAW_ASSISTANT_MAX_CHARS + "..."

    def test_short_assistant_untouched(self):
        assert trim_raw_body("assistant", "  short  ") == "  short  "

    def test_other_speaker_cut_like_assistant(self):
        assert trim_raw_body("John", "J" * 800) == "J" * 500 + "..."

    def test_tool_head_tail_with_marker(self):
        out = trim_raw_body("tool", _LONG_TOOL, tool_max_bytes=2048)
        assert out.startswith("H" * 100) and out.endswith("T" * 100)
        assert "MIDDLE" not in out and _MARKER in out
        assert len(out.encode("utf-8")) <= 2048

    def test_messages_trimmed_without_mutating_input(self):
        msgs = _messages()
        out = trim_raw_messages(msgs, tool_max_bytes=512)
        assert [m.content for m in msgs][1] == _LONG_USER
        assert msgs[2].content == _LONG_ASSISTANT, "入参不能被修改"
        assert out[1].content == _LONG_USER
        assert out[2].content == "A" * 500 + "..."
        assert _MARKER in out[3].content and out[3].tool_call_id == "c1"
        assert out[4].content == "J" * 500 + "..." and out[4].name == "John"

    @pytest.mark.parametrize("max_bytes", [48, 64, 200, 512, 2048])
    def test_idempotent(self, max_bytes):
        once = trim_raw_messages(_messages(), tool_max_bytes=max_bytes)
        twice = trim_raw_messages(once, tool_max_bytes=max_bytes)
        assert [m.content for m in twice] == [m.content for m in once]
        for label in ("assistant", "John", "tool", "user", "system"):
            for body in (_LONG_ASSISTANT, _LONG_TOOL, "中文" * 2000, " x " * 400):
                t1 = trim_raw_body(label, body, tool_max_bytes=max_bytes)
                assert trim_raw_body(label, t1, tool_max_bytes=max_bytes) == t1

    def test_preproc_truncations_idempotent(self):
        t = truncate_tool_result_text(_LONG_TOOL, max_bytes=64)
        assert truncate_tool_result_text(t, max_bytes=64) == t
        a = truncate_assistant_text("x" * 10000, head_chars=30, tail_chars=30)
        assert truncate_assistant_text(a, head_chars=30, tail_chars=30) == a


# ---------------------------------------------------------------- 2. lite 端到端


def _lite_config(tmp_path):
    from hy_memory import MemoryConfig

    return MemoryConfig.from_dict({
        "vector_store": {
            "provider": "chroma",
            "collection_name": f"test_raw_trim_{uuid.uuid4().hex[:8]}",
            "persist_directory": str(tmp_path / "chroma"),
            "embedding_dims": 8,
        },
        "embedder": {"embedding_dims": 8},
        "cache": {"backend": "sqlite", "sqlite_path": str(tmp_path / "cache.db")},
        "history": {"enable": True, "db_path": str(tmp_path / "history.db")},
        "coding": {"enable": True, "tool_result_max_bytes": 512},
    })


def _install_fixed_embed(client):
    vector = [1.0] + [0.0] * 7

    async def one(_self, _text, use_cache=True):
        return list(vector)

    async def one_meta(_self, _text, use_cache=True):
        return list(vector), {"cache_hit": False, "provider": "test"}

    async def batch(_self, texts, use_cache=True):
        return [list(vector) for _ in texts]

    s = client._embed_service
    s.embed = types.MethodType(one, s)
    s.embed_queued = types.MethodType(one, s)
    s.embed_with_meta = types.MethodType(one_meta, s)
    s.embed_queued_with_meta = types.MethodType(one_meta, s)
    s.embed_batch = types.MethodType(batch, s)


def test_lite_stores_trimmed_raw(tmp_path, monkeypatch):
    from hy_memory import HyMemoryClient

    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMORY_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")
    client = HyMemoryClient(_lite_config(tmp_path), mode="lite")
    try:
        _install_fixed_embed(client)
        resp = client.add(
            [
                {"role": "user", "content": _LONG_USER},
                {"role": "assistant", "content": _LONG_ASSISTANT,
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "Bash", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": _LONG_TOOL},
            ],
            user_id="u1",
        )
        assert resp["success"] is True, resp

        async def _raw_nodes():
            return await client._vector_store.list_by_user(
                user_id="u1", layers=[MemoryLayer.L1_RAW],
                with_vectors=False, allow_raw_layer=True,
            )

        nodes = client._loop_thread.run(_raw_nodes())
        assert len(nodes) == 1
        content = nodes[0].content
        assert f"[user]: {_LONG_USER}" in content, "user 必须完整保留"
        assert f"[assistant]: {'A' * 500}...\n" in content
        tool_line = content.split("[tool]: ", 1)[1]
        assert tool_line.startswith("H") and tool_line.endswith("T")
        assert _MARKER in tool_line and "MIDDLE" not in tool_line
        assert len(tool_line.encode("utf-8")) <= 512
        assert (nodes[0].custom or {}).get(RAW_TRIMMED_KEY) is True

        conn = sqlite3.connect(str(tmp_path / "history.db"))
        try:
            (audit,) = conn.execute(
                "SELECT new_memory FROM memory_history WHERE event='ADD'"
            ).fetchone()
        finally:
            conn.close()
        assert _LONG_USER in audit and "MIDDLE" not in audit
        assert "A" * 501 not in audit
    finally:
        client.close()


# ---------------------------------------------------------------- 3. coding_trace


def test_coding_trace_stores_truncated_tool_result(tmp_path):
    from hy_memory.coding.writer import CodingWriter

    captured: Dict[str, Any] = {}

    class _Store:
        config = types.SimpleNamespace(
            coding=types.SimpleNamespace(tool_result_max_bytes=512),
            extractor=types.SimpleNamespace(history_turns=5),
        )

        async def get_recent_trace_messages(self, **kw):
            return []

        async def append_trace(self, messages, **kw):
            captured["messages"] = messages

    class _Extractor:
        async def extract(self, messages, **kw):
            return [], []

    w = CodingWriter.__new__(CodingWriter)
    w.variant = "working"
    w.store = _Store()
    w.extractor = _Extractor()
    asyncio.run(w.write(_messages(), user_id="u1"))

    stored = {m.role: m.content for m in captured["messages"] if m.role != "assistant"}
    assert stored["user"] == _LONG_USER
    assert _MARKER in stored["tool"] and "MIDDLE" not in stored["tool"]
    assert len(stored["tool"].encode("utf-8")) <= 512


# ---------------------------------------------------------------- 4. pro writer


class _Store:
    supports_fulltext = False

    def __init__(self):
        self.nodes: Dict[str, MemoryNode] = {}

    async def initialize(self):
        pass

    async def upsert(self, node: MemoryNode) -> str:
        node.node_id = node.node_id or uuid.uuid4().hex
        if node.gmt_created is None:
            node.gmt_created = datetime.now()
        self.nodes[node.node_id] = node
        return node.node_id

    async def get_by_id(self, node_id):
        return self.nodes.get(node_id)

    async def list_by_user(self, *a, layers=None, **kw):
        return [
            n for n in self.nodes.values()
            if not layers or n.layer in layers
        ]

    async def search(self, *a, **kw):
        return []


class _Embed:
    async def embed_queued_with_meta(self, text):
        return [0.0] * 8, {"provider": "test"}

    async def embed_queued(self, text):
        return [0.0] * 8

    async def embed_batch(self, texts):
        return [[0.0] * 8 for _ in texts]


class _CapturingAgent:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def process_add(self, **kwargs):
        from tests.test_shadow_l1_raw_on_empty_extract import _FakeAgentResult

        self.calls.append(kwargs)
        return _FakeAgentResult(success=True, extracted_info={"memory": [], "intentions": []})


def _pro_writer(store):
    from hy_memory.config import MemoryConfig
    from hy_memory.pipelines.writer import MemoryWriter

    w = MemoryWriter(MemoryConfig(), embed_service=_Embed(), vector_store=store, cache=None)
    w._mem_agent = _CapturingAgent()
    w._vector_store = store

    async def _empty_tags(*a, **kw):
        return []

    w._collect_existing_tags = _empty_tags
    return w


def _write(w, msgs: List[ChatMessage]):
    req = WriteRequest(
        content="", messages=msgs, user_id="u1", agent_id="default_agent",
        session_id="s1", content_type="messages",
        extra={"agent_mode": "full", "mode": "pro"},
        request_id=uuid.uuid4().hex,
    )
    return asyncio.run(w.write(req))


def test_pro_extraction_input_unchanged_and_history_concatenates():
    store = _Store()
    w = _pro_writer(store)
    turn1 = [
        ChatMessage(role="user", content=_LONG_USER),
        ChatMessage(role="assistant", content=_LONG_ASSISTANT),
    ]
    turn2 = [
        ChatMessage(role="user", content="第二轮"),
        ChatMessage(role="assistant", content="B" * 900),
    ]
    r1 = _write(w, turn1)
    assert r1.memory_id
    r2 = _write(w, turn2)
    assert r2.memory_id

    calls = w._mem_agent.calls
    assert len(calls) == 2
    # extractor 收到的仍是完整 messages（没有被落库裁剪影响）
    for call, msgs in zip(calls, (turn1, turn2)):
        assert json.loads(call["content"]) == [
            {"role": m.role, "content": m.content} for m in msgs
        ]

    raw1 = store.nodes[r1.memory_id]
    assert raw1.content == f"[user]: {_LONG_USER}\n[assistant]: {'A' * 500}..."
    assert raw1.custom.get(RAW_TRIMMED_KEY) is True
    # 历史 = 已存 raw 直接拼接（第一次写入时没有历史）
    assert calls[0]["history_context"] == ""
    assert calls[1]["history_context"] == raw1.content


def test_history_does_not_retrim_new_raw_but_trims_legacy_raw():
    """新 raw（带标记）原样拼接；旧 raw（无标记）按同一规则兼容裁剪。"""
    from hy_memory.pipelines.writer import MemoryWriter

    store = _Store()
    base = datetime.now()
    # 新 raw：内容里刻意放一条超过 500 的 assistant——带标记就不应再被裁
    new_content = "[user]: 新问题\n[assistant]: " + "N" * 700
    legacy_content = "[user]: 旧问题\n[assistant]: " + "O" * 700 + "\n[tool]: " + _LONG_TOOL
    store.nodes["legacy"] = MemoryNode(
        node_id="legacy", user_id="u1", agent_id="default_agent",
        layer=MemoryLayer.L1_RAW, content=legacy_content,
        status=MemoryStatus.ACTIVE, gmt_created=base - timedelta(seconds=2),
    )
    store.nodes["new"] = MemoryNode(
        node_id="new", user_id="u1", agent_id="default_agent",
        layer=MemoryLayer.L1_RAW, content=new_content,
        status=MemoryStatus.ACTIVE, gmt_created=base - timedelta(seconds=1),
        custom={RAW_TRIMMED_KEY: True},
    )
    w = MemoryWriter.__new__(MemoryWriter)
    history = asyncio.run(w._get_recent_history(store, "u1", "default_agent", max_turns=20))

    assert history.endswith(new_content), "新 raw 必须原样拼接"
    assert "[assistant]: " + "O" * 500 + "...\n" in history
    assert "MIDDLE" not in history and _MARKER in history
