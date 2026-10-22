# -*- coding: utf-8 -*-
"""
端到端写入路径测试。

把 judge / preproc / extractor / reconciler / store 串起来，
验证 CodingWriter 协调正确。
"""

import json

import pytest

from hy_memory.coding.extractor import CodingMemoryExtractor
from hy_memory.coding.reconciler import CodingMemoryReconciler
from hy_memory.coding.writer import CodingWriter, is_coding_segment
from hy_memory.pipelines.base import ChatMessage, ToolCall

from tests.coding._helpers import MockLLMProvider, FailingLLMProvider


def _classify_resp(is_coding):
    return json.dumps({"is_coding": is_coding})


def _extract_resp(experiences, fact=None):
    if isinstance(fact, list):
        if len(fact) <= 1:
            fact = fact[0] if fact else None
        else:
            # 仅供兼容性回归：旧模型可能仍返回多元素 facts 数组。
            return json.dumps({"experiences": experiences, "facts": fact})
    return json.dumps({"experiences": experiences, "fact": fact})


def _reconcile_resp(ops):
    return json.dumps({"ops": ops})


def _coding_messages():
    return [
        ChatMessage(role="user", content="帮我打包发布到私有包索引"),
        ChatMessage(role="assistant", content="", tool_calls=[
            ToolCall(id="1", name="Read", arguments={"path": "scripts/publish_package.sh"}),
        ]),
        ChatMessage(role="tool", content="200 lines", tool_call_id="1", tool_name="Read"),
        ChatMessage(role="assistant", content="", tool_calls=[
            ToolCall(id="2", name="Bash", arguments={"command": "twine upload"}),
        ]),
        ChatMessage(role="tool", content="OK uploaded", tool_call_id="2", tool_name="Bash"),
    ]


@pytest.mark.asyncio
class TestIsCodingSegment:
    async def test_no_tool_short_circuit(self):
        msgs = [ChatMessage(role="user", content="hi"),
                ChatMessage(role="assistant", content="hello")]
        # 即使 LLM 返回 true，has_any_tool_message False 应短路 False
        llm = MockLLMProvider('{"is_coding": true}')
        result = await is_coding_segment(msgs, llm)
        assert result is False
        # LLM 不应被调用
        assert len(llm.calls) == 0

    async def test_with_tool_classify_coding(self):
        llm = MockLLMProvider(_classify_resp(True))
        result = await is_coding_segment(_coding_messages(), llm)
        assert result is True

    async def test_with_tool_classify_chat(self):
        llm = MockLLMProvider(_classify_resp(False))
        result = await is_coding_segment(_coding_messages(), llm)
        assert result is False


@pytest.mark.asyncio
class TestCodingWriterE2E:
    async def test_trace_history_shared_across_working_and_coding_variants(
        self, coding_config, mock_embed,
    ):
        from hy_memory.coding.store import CodingMemoryStore

        working = CodingMemoryStore(
            coding_config, embed_service=mock_embed, variant="working",
        )
        coding = CodingMemoryStore(
            coding_config, embed_service=mock_embed, variant="coding",
        )
        await working.initialize()
        await coding.initialize()
        try:
            await working.append_trace(
                [ChatMessage(role="user", content="先整理财务报表")],
                request_id="shared-trace", user_id="u1", agent_id="a1",
            )
            recent = await coding.get_recent_trace_messages(
                user_id="u1", agent_id="a1", max_user_turns=5,
            )
            assert [m.content for m in recent if m.role == "user"] == [
                "先整理财务报表"
            ]
            assert working._db_path != coding._db_path
            assert working._trace_db_path == coding._trace_db_path
        finally:
            await coding.close()
            await working.close()

    async def test_trace_history_keeps_last_five_user_turns(self, coding_store):
        msgs = []
        for i in range(7):
            msgs.extend([
                ChatMessage(role="user", content=f"history-{i}"),
                ChatMessage(role="assistant", content="A" * 8000),
                ChatMessage(role="tool", content="T" * 5000, tool_name="Read"),
            ])
        await coding_store.append_trace(
            msgs, request_id="trace-1", user_id="u1", agent_id="a1",
        )
        recent = await coding_store.get_recent_trace_messages(
            user_id="u1", agent_id="a1", max_user_turns=5,
        )
        assert [m.content for m in recent if m.role == "user"] == [
            "history-2", "history-3", "history-4", "history-5", "history-6"
        ]
        assert all("chars omitted" in m.content for m in recent if m.role == "assistant")
        assert all("bytes omitted" in m.content for m in recent if m.role == "tool")

    async def test_trace_history_retention_is_bounded(self, coding_store):
        for i in range(105):
            await coding_store.append_trace(
                [ChatMessage(role="user", content=f"turn-{i}")],
                request_id=f"trace-{i}", user_id="u1", agent_id="a1",
            )
        with coding_store._trace_lock:
            count = coding_store._trace_conn.execute(
                "SELECT COUNT(*) AS n FROM coding_trace WHERE user_id=? AND agent_id=?",
                ("u1", "a1"),
            ).fetchone()["n"]
        assert count == 100

    async def test_full_path_add(self, coding_store, mock_embed):
        """完整 e2e：含 tool 消息 → judge=True → extract → reconcile=ADD → 持久化"""
        # 用同一个 LLM mock 序列响应 extract + reconcile（注意：我们这里直接调
        # writer.write，不经 judge；所以 LLM 序列只需覆盖 extract + reconcile）
        ext_resp = _extract_resp([{
            "task": "打包发布到私有包索引",
            "search_keys": ["凭证位置", "上传命令"],
            "solution": "运行 scripts/publish_package.sh X.Y.Z；凭证通过环境变量提供",
            "boundary_envs": "twine>=6\\nurl: packages.example.com",
            "confidence": 0.9,
        }])
        rec_resp = _reconcile_resp([{"draft_idx": 0, "action": "ADD"}])
        llm = MockLLMProvider([ext_resp, rec_resp])

        ext = CodingMemoryExtractor(llm)
        rec = CodingMemoryReconciler(coding_store, mock_embed, llm)
        writer = CodingWriter(
            store=coding_store, extractor=ext, reconciler=rec,
            llm_provider=llm, embed_service=mock_embed,
        )
        resp = await writer.write(
            _coding_messages(),
            user_id="u1", agent_id="a1",
            workspace_id="github.com/x/y", branch="main",
            session_id="s1",
        )
        assert resp["success"] is True
        assert resp["scene"] == "productivity"
        assert len(resp["ops"]) == 1
        assert resp["ops"][0]["action"] == "ADD"
        assert len(resp["experience_ids"]) == 1

        # 验证持久化
        experience_id = resp["experience_ids"][0]
        got = await coding_store.get_by_id(experience_id, user_id="u1")
        assert got is not None
        assert got.task == "打包发布到私有包索引"
        assert got.search_keys == ["凭证位置", "上传命令"]
        assert got.boundary_envs.startswith("twine>=6")
        assert got.workspace_id == "github.com/x/y"
        assert got.files == ["scripts/publish_package.sh"]

    async def test_value_bar_returns_empty(self, coding_store, mock_embed):
        """extractor 返回 0 条 memory（价值门槛未达）"""
        llm = MockLLMProvider(_extract_resp([]))
        ext = CodingMemoryExtractor(llm)
        rec = CodingMemoryReconciler(coding_store, mock_embed, llm)
        writer = CodingWriter(
            store=coding_store, extractor=ext, reconciler=rec,
            llm_provider=llm, embed_service=mock_embed,
        )
        resp = await writer.write(
            _coding_messages(), user_id="u1", workspace_id="ws/a",
        )
        assert resp["success"] is True
        assert resp["ops"] == []
        assert resp["experience_ids"] == []

    async def test_no_workspace_still_writes(self, coding_store, mock_embed):
        """缺 workspace_id 不再拦稿，两条 draft 都应落库"""
        ext_resp = _extract_resp([
            {"task": "T1", "solution": "S", "confidence": 0.9},
            {"task": "T2", "solution": "S", "confidence": 0.9},
        ])
        rec_resp = _reconcile_resp([
            {"draft_idx": 0, "action": "ADD"},
            {"draft_idx": 1, "action": "ADD"},
        ])
        llm = MockLLMProvider([ext_resp, rec_resp])
        ext = CodingMemoryExtractor(llm)
        rec = CodingMemoryReconciler(coding_store, mock_embed, llm)
        writer = CodingWriter(
            store=coding_store, extractor=ext, reconciler=rec,
            llm_provider=llm, embed_service=mock_embed,
        )
        resp = await writer.write(
            _coding_messages(), user_id="u1", workspace_id=None,
        )
        assert len(resp["experience_ids"]) == 2
        assert sorted(await coding_store.list_user_tasks("u1")) == ["T1", "T2"]

    async def test_extractor_failure(self, coding_store, mock_embed):
        """extractor LLM 失败 → 空 drafts → 空 ops"""
        ext = CodingMemoryExtractor(FailingLLMProvider())
        rec = CodingMemoryReconciler(coding_store, mock_embed, MockLLMProvider("{}"))
        writer = CodingWriter(
            store=coding_store, extractor=ext, reconciler=rec,
            llm_provider=FailingLLMProvider(), embed_service=mock_embed,
        )
        resp = await writer.write(_coding_messages(), user_id="u1", workspace_id="ws")
        assert resp["success"] is True
        assert resp["ops"] == []

    async def test_existing_tasks_not_shown_to_extractor(self, coding_store, mock_embed):
        """库里已有的 task 不得进抽取 prompt——抽取只读这一段，去重是 reconcile 的事"""
        from hy_memory.coding.types import CodingMemory
        await coding_store.insert(CodingMemory(
            experience_id="e_old", user_id="u1", task="老 task", solution="S",
        ))

        llm = MockLLMProvider([_extract_resp([]), _reconcile_resp([])])
        ext = CodingMemoryExtractor(llm)
        rec = CodingMemoryReconciler(coding_store, mock_embed, llm)
        writer = CodingWriter(
            store=coding_store, extractor=ext, reconciler=rec,
            llm_provider=llm, embed_service=mock_embed,
        )
        await writer.write(_coding_messages(), user_id="u1", workspace_id="ws/a")
        assert "老 task" not in llm.calls[0]["prompt"]


@pytest.mark.asyncio
class TestDualLayerWrite:
    """双层写入 + 同批全互链（§4）"""

    def _writer(self, store, embed, llm, *, verbatim_fallback_enabled=False):
        return CodingWriter(
            store=store,
            extractor=CodingMemoryExtractor(
                llm,
                verbatim_fallback_enabled=verbatim_fallback_enabled,
            ),
            reconciler=CodingMemoryReconciler(store, embed, llm),
            llm_provider=llm, embed_service=embed,
        )

    async def test_both_layers_written_and_crosslinked(self, coding_store, mock_embed):
        llm = MockLLMProvider([
            _extract_resp(
                [{"task": "打包发布到私有包索引", "solution": "S", "confidence": 0.9}],
                [{"statement": "发布脚本在 scripts/publish_package.sh", "files": ["scripts/publish_package.sh"]}],
            ),
            _reconcile_resp([{"draft_idx": 0, "action": "ADD"}]),
        ])
        writer = self._writer(coding_store, mock_embed, llm)

        resp = await writer.write(
            _coding_messages(), user_id="u1", workspace_id="ws/a", session_id="s1",
        )

        assert len(resp["experience_ids"]) == 1
        assert len(resp["fact_ids"]) == 1
        assert resp["fact_ops_summary"] == {"insert": 1, "supersede": 0, "total": 1}

        eid, fid = resp["experience_ids"][0], resp["fact_ids"][0]
        exp = await coding_store.get_by_id(eid, user_id="u1")
        fact = await coding_store.get_fact(fid, user_id="u1")
        assert exp.source_fact_ids == [fid]
        assert fact.derived_experience_ids == [eid]
        assert fact.statement == "发布脚本在 scripts/publish_package.sh"

    async def test_all_experiences_linked_to_unique_fact(self, coding_store, mock_embed):
        """一个 trace 的多条经验都与唯一会话 fact 双向关联。"""
        llm = MockLLMProvider([
            _extract_resp(
                [{"task": "T1", "solution": "S"}, {"task": "T2", "solution": "S"}],
                [{"statement": "事实 A"}, {"statement": "事实 B"}],
            ),
            _reconcile_resp([
                {"draft_idx": 0, "action": "ADD"},
                {"draft_idx": 1, "action": "ADD"},
            ]),
        ])
        writer = self._writer(coding_store, mock_embed, llm)

        resp = await writer.write(_coding_messages(), user_id="u1")
        eids, fids = resp["experience_ids"], resp["fact_ids"]
        assert len(eids) == 2 and len(fids) == 1

        for eid in eids:
            exp = await coding_store.get_by_id(eid, user_id="u1")
            assert exp.source_fact_ids == fids
        fact = await coding_store.get_fact(fids[0], user_id="u1")
        assert sorted(fact.derived_experience_ids) == sorted(eids)

    async def test_skip_not_linked(self, coding_store, mock_embed):
        """SKIP 的经验没真入库，不该被连上这批事实。"""
        llm = MockLLMProvider([
            _extract_resp(
                [{"task": "T1", "solution": "S"}],
                [{"statement": "事实 A"}],
            ),
            _reconcile_resp([{"draft_idx": 0, "action": "SKIP", "reason": "dup"}]),
        ])
        writer = self._writer(coding_store, mock_embed, llm)

        resp = await writer.write(_coding_messages(), user_id="u1")
        assert resp["experience_ids"] == []
        assert len(resp["fact_ids"]) == 1
        fact = await coding_store.get_fact(resp["fact_ids"][0], user_id="u1")
        assert fact.derived_experience_ids == []

    async def test_update_keeps_old_fact_link(self, coding_store, mock_embed):
        """UPDATE 覆盖正文不留痕，但事实链取并集，旧链要保住。"""
        from hy_memory.coding.types import CodingMemory
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u1", task="老 task", solution="旧 solution",
            source_fact_ids=["f_before"], session_id="test-session",
        ))
        llm = MockLLMProvider([
            _extract_resp(
                [{"task": "老 task", "solution": "新 solution"}],
                [{"statement": "本轮新事实"}],
            ),
            _reconcile_resp([
                {"draft_idx": 0, "action": "UPDATE", "target_experience_id": "e1"},
            ]),
        ])
        writer = self._writer(coding_store, mock_embed, llm)

        resp = await writer.write(
            _coding_messages(), user_id="u1", session_id="test-session"
        )
        exp = await coding_store.get_by_id("e1", user_id="u1")
        assert exp.solution == "新 solution"
        assert exp.source_fact_ids == ["f_before", resp["fact_ids"][0]]

    async def test_facts_only_no_experience(self, coding_store, mock_embed):
        """经验层空、事实层非空：事实照样落库，不触发经验层 reconcile。"""
        llm = MockLLMProvider(_extract_resp([], [{"statement": "只有事实"}]))
        writer = self._writer(coding_store, mock_embed, llm)

        resp = await writer.write(_coding_messages(), user_id="u1")
        assert resp["experience_ids"] == []
        assert len(resp["fact_ids"]) == 1
        # extract 一次；事实层库里没候选所以不调 LLM；经验层 reconcile 完全没跑
        assert len(llm.calls) == 1

    async def test_empty_experiences_do_not_persist_verbatim_fact_by_default(
        self, coding_store, mock_embed,
    ):
        llm = MockLLMProvider(_extract_resp([], []))
        writer = self._writer(coding_store, mock_embed, llm)

        resp = await writer.write(_coding_messages(), user_id="u1")
        assert resp["ops"] == []
        assert resp["fact_ids"] == []
        assert resp["fact_ops"] == []

    async def test_empty_experiences_can_persist_verbatim_fact_when_enabled(
        self, coding_store, mock_embed,
    ):
        """显式开启时，两层都抽空仍可落一条原文事实。

        端到端确认开关开启后，fallback 能穿过 reconcile 真正落库。
        """
        llm = MockLLMProvider(_extract_resp([], []))
        writer = self._writer(
            coding_store,
            mock_embed,
            llm,
            verbatim_fallback_enabled=True,
        )

        resp = await writer.write(_coding_messages(), user_id="u1")
        assert resp["ops"] == []            # 经验层不兜底
        assert len(resp["fact_ids"]) == 1
        assert [op["action"] for op in resp["fact_ops"]] == ["INSERT"]

        fact = await coding_store.get_fact(resp["fact_ids"][0], user_id="u1")
        assert fact.statement == "帮我打包发布到私有包索引"
        assert "twine upload" in fact.details      # 轨迹原文进了 details
