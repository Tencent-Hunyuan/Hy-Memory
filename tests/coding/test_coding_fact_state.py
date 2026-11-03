# -*- coding: utf-8 -*-
"""结构化事实状态（coding/fact_state.py）。

这层的价值全在"覆盖时不丢东西"和"渲染出来的文本能被检索到"，所以测试集中在
两个方向：状态规范化/渲染的边界，以及 carry_over 的机械继承（旧 current → rejected）。
"""

import json

from hy_memory.coding.fact_state import (
    MAX_DECISIONS,
    MAX_RENDER_CHARS,
    carry_over,
    decode,
    merge_retired,
    normalize_state,
    parse_state,
    render_state,
)


class TestNormalize:
    def test_requires_task(self):
        assert normalize_state({"goal": "x"}) is None
        assert normalize_state(None) is None
        assert normalize_state({"task": "   "}) is None

    def test_minimal(self):
        st = normalize_state({"task": "Reclaim disk space"})
        assert st["task"] == "Reclaim disk space"
        assert st["status"] == "in_progress"      # 缺省
        assert st["decisions"] == []

    def test_unknown_status_falls_back(self):
        st = normalize_state({"task": "t", "status": "DONE"})
        assert st["status"] == "in_progress"

    def test_branch_and_files_merge(self):
        st = normalize_state(
            {"task": "t", "files": ["a.py"]}, branch="feat/x", files=["b.py", "a.py"]
        )
        assert st["branch"] == "feat/x"
        assert st["files"] == ["a.py", "b.py"]     # 去重且保持先后

    def test_branch_from_state_wins(self):
        st = normalize_state({"task": "t", "branch": "feat/y"}, branch="feat/x")
        assert st["branch"] == "feat/y"

    def test_decisions_dropped_without_subject(self):
        st = normalize_state({"task": "t", "decisions": [{"current": "v"}, {"subject": "s"}]})
        assert [d["subject"] for d in st["decisions"]] == ["s"]

    def test_decisions_capped(self):
        raw = {"task": "t", "decisions": [{"subject": f"s{i}"} for i in range(MAX_DECISIONS + 5)]}
        st = normalize_state(raw)
        assert len(st["decisions"]) == MAX_DECISIONS
        assert st["decisions"][-1]["subject"] == f"s{MAX_DECISIONS + 4}"   # 保留最近的


class TestRender:
    def test_shape(self):
        st = normalize_state({
            "task": "vCard 4.0 import",
            "branch": "feat/tu0",
            "goal": "add support",
            "progress": "did the parse",
            "decisions": [{
                "subject": "src/a.ts::parse — EMAIL cases",
                "current": "one fallthrough block",
                "rejected": ["three separate cases"],
                "evidence": "tests run and passing",
            }],
            "constraints": ["do not rename DefaultGet"],
            "status": "in_progress",
            "open": ["tests for ITEM2"],
        })
        text = render_state(st)
        assert text.startswith("Task: vCard 4.0 import · branch feat/tu0 · in_progress")
        assert "Goal: add support" in text
        assert "src/a.ts::parse — EMAIL cases: one fallthrough block" in text
        assert "[tests run and passing]" in text
        assert "(rejected: three separate cases)" in text
        assert "Constraints: do not rename DefaultGet" in text
        assert "Open: tests for ITEM2" in text

    def test_truncated(self):
        st = normalize_state({"task": "t", "progress": "x" * (MAX_RENDER_CHARS * 2)})
        assert len(render_state(st)) <= MAX_RENDER_CHARS

    def test_undecided_placeholder(self):
        st = normalize_state({"task": "t", "decisions": [{"subject": "s"}]})
        assert "(undecided)" in render_state(st)


class TestParse:
    def test_plain(self):
        assert parse_state('{"task": "t"}') == {"task": "t"}

    def test_fenced(self):
        assert parse_state('```json\n{"task": "t"}\n```') == {"task": "t"}

    def test_surrounding_prose(self):
        assert parse_state('here you go:\n{"task": "t"}\nthanks') == {"task": "t"}

    def test_repairs_illegal_escape(self):
        """状态里常抄正则/Windows 路径，模型漏转义成 `\\.`；不修就永远退回散文。"""
        obj = parse_state(r'{"task": "gate /\\.svg$/i", "progress": "C:\dir"}')
        assert obj is not None
        assert "svg" in obj["task"]

    def test_keeps_valid_escapes(self):
        obj = parse_state(r'{"task": "a\tb", "progress": "line\\nnot newline"}')
        assert obj is not None
        assert "\t" in obj["task"]
        assert obj["progress"].startswith("line\\n")

    def test_garbage(self):
        assert parse_state("not json at all") is None
        assert parse_state("") is None


class TestCarryOver:
    def _state(self, current, rejected=(), constraints=()):
        return normalize_state({
            "task": "t",
            "decisions": [{"subject": "s", "current": current, "rejected": list(rejected)}],
            "constraints": list(constraints),
        })

    def test_old_current_moves_to_rejected(self):
        prev = self._state("v1")
        new = self._state("v2")
        out = carry_over(prev, new)
        assert out["decisions"][0]["current"] == "v2"
        assert out["decisions"][0]["rejected"] == ["v1"]

    def test_unchanged_current_not_duplicated(self):
        prev = self._state("v1", rejected=["v0"])
        new = self._state("v1")
        out = carry_over(prev, new)
        assert out["decisions"][0]["rejected"] == ["v0"]

    def test_rejected_unions(self):
        prev = self._state("v2", rejected=["v0", "v1"])
        new = self._state("v3", rejected=["v2.5"])
        out = carry_over(prev, new)
        assert out["decisions"][0]["rejected"] == ["v0", "v1", "v2", "v2.5"]

    def test_old_evidence_backfilled(self):
        prev = normalize_state({"task": "t", "decisions": [
            {"subject": "s", "current": "v1", "evidence": "tests run and passing"}]})
        new = self._state("v2")
        assert carry_over(prev, new)["decisions"][0]["evidence"] == "tests run and passing"

    def test_constraints_union(self):
        prev = self._state("v1", constraints=["keep A"])
        new = self._state("v2", constraints=["keep B"])
        assert carry_over(prev, new)["constraints"] == ["keep A", "keep B"]

    def test_unknown_subject_not_resurrected(self):
        """模型有意合并/删掉的 subject 不该被机械补回来。"""
        prev = normalize_state({"task": "t", "decisions": [
            {"subject": "gone", "current": "x"}]})
        new = self._state("v2")
        out = carry_over(prev, new)
        assert [d["subject"] for d in out["decisions"]] == ["s"]

    def test_no_prev_is_noop(self):
        new = self._state("v1")
        assert carry_over(None, new) is new

    def test_merge_retired_in_order(self):
        gens = [self._state("v1"), self._state("v2")]
        out = merge_retired(gens, self._state("v3"))
        assert out["decisions"][0]["rejected"] == ["v1", "v2"]


class TestDecode:
    def test_empty(self):
        assert decode("") is None
        assert decode(None) is None

    def test_invalid(self):
        assert decode("{oops") is None

    def test_roundtrip(self):
        st = normalize_state({"task": "t"})
        assert decode(json.dumps(st))["task"] == "t"


# ════════════════════════════════════════════════════════════════════
# 端到端：draft.task_state 真的落库，覆盖时真的继承
#
# 上面全是纯函数测试。这一组走 WorkingFactReconciler + 真 store，防的是
# "fact_state 写得再对，reconciler 构造新事实时没把 task_state 带上"——
# 那样列永远是空串，carry_over 永远不触发，而纯函数测试全绿。
# ════════════════════════════════════════════════════════════════════

import pytest  # noqa: E402

from hy_memory.coding.fact_reconciler import WorkingFactReconciler  # noqa: E402
from hy_memory.coding.types import WorkingFact, WorkingFactDraft  # noqa: E402

from tests.coding._helpers import MockLLMProvider  # noqa: E402


def _state_json(current, rejected=()):
    return json.dumps(normalize_state({
        "task": "vCard import",
        "branch": "feat/tu0",
        "decisions": [{"subject": "src/a.ts::parse — EMAIL cases",
                       "current": current, "rejected": list(rejected)}],
        "constraints": ["keep parseVCardLine signature"],
    }))


def _draft_with_state(current, rejected=()):
    st = _state_json(current, rejected)
    return WorkingFactDraft(statement=render_state(json.loads(st)), task_state=st)


@pytest.mark.asyncio
class TestReconcilerPersistsState:
    async def test_insert_keeps_task_state(self, coding_store, mock_embed):
        rec = WorkingFactReconciler(coding_store, mock_embed,
                                    MockLLMProvider(json.dumps({"ops": []})))
        facts, _ = await rec.reconcile([_draft_with_state("one fallthrough block")],
                                       user_id="u1", session_id="s")
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert decode(stored.task_state)["decisions"][0]["current"] == "one fallthrough block"

    async def test_supersede_carries_old_current_into_rejected(self, coding_store, mock_embed):
        old_state = _state_json("three separate cases")
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", session_id="s",
            statement=render_state(json.loads(old_state)), task_state=old_state,
        ))
        llm = MockLLMProvider([
            json.dumps({"ops": [{"draft_idx": 0, "supersedes": ["f_old"]}]}),
            "summary",
        ])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)
        # 新一段会话里模型**没有**把旧值写进 rejected —— 这正是要兜底的情形
        facts, _ = await rec.reconcile([_draft_with_state("one fallthrough block")],
                                       user_id="u1", session_id="s")
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        d = decode(stored.task_state)["decisions"][0]
        assert d["current"] == "one fallthrough block"
        assert d["rejected"] == ["three separate cases"]
        # statement 是 task_state 的渲染，必须同步更新（它也是检索向量）
        assert "(rejected: three separate cases)" in stored.statement

    async def test_prose_fact_still_works(self, coding_store, mock_embed):
        """没有结构化状态（老提示词 / 模型没按结构输出）时行为与改动前一致。"""
        rec = WorkingFactReconciler(coding_store, mock_embed,
                                    MockLLMProvider(json.dumps({"ops": []})))
        facts, _ = await rec.reconcile([WorkingFactDraft(statement="plain prose fact")],
                                       user_id="u1", session_id="s")
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert stored.statement == "plain prose fact"
        assert stored.task_state == ""
