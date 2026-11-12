"""reconcile 的模型调用失败时，整次写入报失败、一条新记忆都不落库。

此前 reconcile 失败（LLM 不可用 / 返回空 / 解析不出 / 链路异常）会走「全部 ADD」
兜底并报 success=True：调用方不会重试，而没经过判重 / 演化的整批 ADD 又会在之后
的写入里制造重复。现在：

- ReconcileResult(success=False, ops=[], error_code=...)，不再有 ADD-all 兜底；
- add（write）：agent_error_code=RECONCILE_FAILED，L1_RAW 按抽取失败同样回滚
  （l1_rollback.retry_safe=True），换个 request_id 重跑即可落库；
- add_extracted（write_extracted）：同样报失败、什么都不存；
- RECONCILE_ENABLED=false 是配置选择，不是失败，仍直写。
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List

import pytest

from hy_memory.agent.llm_provider import LLMResponse
from hy_memory.agent.reconciler import MemoryReconciler, ReconcileResult
from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.pipelines.base import WriteRequest


FACTS = [
    "The user took a road trip in July 2023 to Banff, where he went skiing.",
    "The user is in a new romantic relationship as of August 2023.",
]
EXISTING = "The user enjoys skiing."
DIMS = 16


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _vec(text: str) -> List[float]:
    seed = sum(ord(c) for c in text) or 1
    v = [((seed * (i + 7)) % 97) / 97.0 + 0.01 for i in range(DIMS)]
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


class _FakeEmbed:
    async def embed(self, text, **kwargs):
        return _vec(text)

    async def embed_batch(self, texts, **kwargs):
        return [_vec(t) for t in texts]

    async def embed_queued(self, text, use_cache=True):
        return _vec(text)

    async def embed_queued_with_meta(self, text, use_cache=True):
        return _vec(text), {"mock": True}


class _ReconcileLLM:
    """reconcile 用的假 LLM：mode 决定这一次怎么失败 / 恢复。"""

    def __init__(self, mode: str = "fail"):
        self.mode = mode
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.mode == "fail":
            raise RuntimeError("gateway 500")
        if self.mode == "empty":
            return LLMResponse(content="", tokens_used=3, prompt_tokens=3)
        if self.mode == "garbage":
            return LLMResponse(content="I cannot help with that.", tokens_used=3)
        if self.mode == "noop":
            return LLMResponse(content="[]", tokens_used=3)
        ops = [
            {"op": "ADD", "content": f, "layer": "L2_FACT", "owner": "user"}
            for f in FACTS
        ]
        return LLMResponse(content=json.dumps(ops), tokens_used=5)


class _AgentResult:
    success = True
    suggested_layer = None
    summary = ""
    conflicts: List[Dict[str, Any]] = []
    should_merge = False
    merge_target_id = None
    tokens_used = 0
    extract_elapsed_ms = 1.0
    summary_elapsed_ms = 0.0
    error = None
    error_code = None

    def __init__(self):
        self.extracted_info = {
            "memory": [
                {"content": f, "layer": "L2_FACT", "owner": "user", "tags": []}
                for f in FACTS
            ],
        }


class _FakeMemAgent:
    async def process_add(self, **kwargs):
        return _AgentResult()


@pytest.fixture
def store(tmp_path):
    from hy_memory.data.vector_store_chroma import ChromaVectorStore

    config = MemoryConfig.from_dict({"vector_store": {
        "provider": "chroma",
        "collection_name": f"test_recon_fail_{uuid.uuid4().hex[:8]}",
        "persist_directory": str(tmp_path / "chroma"),
        "embedding_dims": DIMS,
    }})
    vs = ChromaVectorStore(config)
    run(vs.initialize())
    # 一条已有记忆，与第一条新事实同向量：保证 reconcile 有候选、真的去调 LLM
    # （没有任何候选时 reconcile 不调 LLM，直接全部 ADD，那不是模型失败的场景）。
    run(vs.upsert(MemoryNode(
        node_id="existing-1",
        user_id="u1",
        agent_id="default_agent",
        session_id="s0",
        content=EXISTING,
        layer=MemoryLayer.L2_FACT,
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        embedding=_vec(FACTS[0]),
    )))
    yield config, vs
    run(vs.close())


def _writer(config, vs, llm):
    from hy_memory.pipelines.writer import MemoryWriter

    writer = MemoryWriter(config, embed_service=_FakeEmbed(), vector_store=vs, cache=None)
    run(writer.initialize())
    writer._embed_service = _FakeEmbed()
    writer._mem_agent = _FakeMemAgent()
    writer.reconciler._llm = llm
    writer.reconciler._enable_search_query = False
    return writer


def _add_request(request_id: str) -> WriteRequest:
    return WriteRequest(
        content="user: I went skiing in Banff last July.",
        user_id="u1",
        agent_id="default_agent",
        session_id="s1",
        extra={"agent_mode": "full"},
        request_id=request_id,
    )


def _nodes(vs, layer: MemoryLayer):
    nodes = run(vs.list_by_user(
        user_id="u1", layers=[layer], limit=100, allow_raw_layer=True,
    ))
    return [n for n in nodes if n.layer == layer]


def _reconcile(vs, llm, embed=None) -> ReconcileResult:
    rec = MemoryReconciler(MemoryConfig(), llm_provider=llm)
    rec._enable_search_query = False
    return run(rec.reconcile(
        new_memories=list(FACTS),
        user_id="u1",
        agent_id="default_agent",
        vector_store=vs,
        embed_service=embed or _FakeEmbed(),
    ))


def _new_facts(vs) -> List[str]:
    return sorted(
        n.content for n in _nodes(vs, MemoryLayer.L2_FACT) if n.node_id != "existing-1"
    )


class TestReconcilerReportsFailure:
    def test_llm_unavailable_is_failure_without_ops(self, store):
        llm = _ReconcileLLM("fail")
        r = _reconcile(store[1], llm)
        assert llm.calls >= 1
        assert r.success is False
        assert r.ops == []
        assert r.error_code == "LLM_UNAVAILABLE"

    def test_empty_200_reply_is_failure_not_zero_ops(self, store):
        llm = _ReconcileLLM("empty")
        r = _reconcile(store[1], llm)
        assert r.success is False
        assert r.ops == []
        assert r.error_code == "EMPTY_RESPONSE"
        assert llm.calls == 2, "空回复按解析失败重试一次"

    def test_unparseable_reply_is_failure(self, store):
        r = _reconcile(store[1], _ReconcileLLM("garbage"))
        assert r.success is False
        assert r.ops == []
        assert r.error_code == "JSON_PARSE_FAILED"

    def test_explicit_empty_array_is_a_decision(self, store):
        """字面量 [] 是模型判定「无需任何操作」，仍是成功。"""
        r = _reconcile(store[1], _ReconcileLLM("noop"))
        assert r.success is True
        assert r.ops == []

    def test_unexpected_exception_is_failure(self, store):
        class _BrokenEmbed(_FakeEmbed):
            async def embed_batch(self, texts, **kwargs):
                raise RuntimeError("embedding down")

        r = _reconcile(store[1], _ReconcileLLM("add"), embed=_BrokenEmbed())
        assert r.success is False
        assert r.ops == []
        assert r.error_code == "RECONCILE_EXCEPTION"

    def test_no_add_all_fallback_left(self):
        assert not hasattr(MemoryReconciler, "_fallback_add_ops")
        assert not hasattr(ReconcileResult(), "degraded")


class TestAddReconcileFailure:
    @pytest.mark.parametrize(
        "mode,reason",
        [("fail", "LLM_UNAVAILABLE"), ("empty", "EMPTY_RESPONSE"),
         ("garbage", "JSON_PARSE_FAILED")],
    )
    def test_failure_stores_nothing_and_rolls_back_raw(self, store, mode, reason):
        config, vs = store
        writer = _writer(config, vs, _ReconcileLLM(mode))

        resp = run(writer.write(_add_request("req-fail")))

        assert resp.success is False
        assert resp.error_code == 502
        assert resp.extra["agent_status"] == "failed"
        assert resp.extra["agent_error_code"] == "RECONCILE_FAILED"
        assert resp.extra["reconcile_error_code"] == reason
        assert resp.error_message.startswith("[RECONCILE_FAILED]")
        assert resp.extra.get("agent_stored_ids") == []
        assert _new_facts(vs) == [], "reconcile 失败不得落任何新 L2"
        rollback = resp.extra["l1_rollback"]
        assert rollback["deleted"] is True
        assert rollback["retry_safe"] is True
        assert _nodes(vs, MemoryLayer.L1_RAW) == [], "L1_RAW 应已回滚"

    def test_rerun_with_new_request_id_after_recovery_stores(self, store):
        config, vs = store
        llm = _ReconcileLLM("fail")
        writer = _writer(config, vs, llm)

        failed = run(writer.write(_add_request("req-1")))
        assert failed.success is False

        llm.mode = "add"
        ok = run(writer.write(_add_request("req-2")))

        assert ok.success is True
        assert ok.extra["agent_status"] == "success"
        assert _new_facts(vs) == sorted(FACTS)
        raws = _nodes(vs, MemoryLayer.L1_RAW)
        assert len(raws) == 1, "失败那次的 raw 已回滚，只剩重跑这次的"
        assert raws[0].status == MemoryStatus.SHADOW


class TestAddExtractedReconcileFailure:
    def _request(self, request_id: str) -> WriteRequest:
        return WriteRequest(
            content=FACTS[0],
            user_id="u1",
            agent_id="default_agent",
            session_id="s1",
            extra={"extracted_contents": list(FACTS), "agent_mode": "extracted"},
            request_id=request_id,
        )

    def test_failure_stores_nothing(self, store):
        config, vs = store
        writer = _writer(config, vs, _ReconcileLLM("empty"))

        resp = run(writer.write_extracted(self._request("req-x1")))

        assert resp.success is False
        assert resp.error_code == 502
        assert resp.extra["agent_error_code"] == "RECONCILE_FAILED"
        assert resp.extra["reconcile_error_code"] == "EMPTY_RESPONSE"
        assert _new_facts(vs) == []

    def test_rerun_after_recovery_stores(self, store):
        config, vs = store
        llm = _ReconcileLLM("fail")
        writer = _writer(config, vs, llm)
        assert run(writer.write_extracted(self._request("req-x1"))).success is False

        llm.mode = "add"
        resp = run(writer.write_extracted(self._request("req-x2")))

        assert resp.success is True
        assert _new_facts(vs) == sorted(FACTS)

    def test_reconcile_disabled_still_writes_directly(self, store, monkeypatch):
        """RECONCILE_ENABLED=false 是配置选择：不调 reconcile LLM，直写。"""
        import hy_memory.pipelines.writer as writer_mod

        monkeypatch.setattr(writer_mod, "_RECONCILE_ENABLED", False)
        config, vs = store
        llm = _ReconcileLLM("fail")
        writer = _writer(config, vs, llm)

        resp = run(writer.write_extracted(self._request("req-x3")))

        assert resp.success is True
        assert llm.calls == 0
        assert _new_facts(vs) == sorted(FACTS)


class TestClientReceipt:
    def test_reconcile_error_code_reaches_the_caller(self):
        from hy_memory.client import _attach_write_diagnostics

        resp: Dict[str, Any] = {}
        _attach_write_diagnostics(resp, {
            "agent_status": "failed",
            "agent_error_code": "RECONCILE_FAILED",
            "reconcile_error_code": "EMPTY_RESPONSE",
            "l1_rollback": {"retry_safe": True},
        })
        assert resp["agent_error_code"] == "RECONCILE_FAILED"
        assert resp["reconcile_error_code"] == "EMPTY_RESPONSE"
        assert resp["l1_rollback"]["retry_safe"] is True


class TestExtractorPromptRules:
    """新增的两条 extractor 规则（线上 badcase 直接对应）。"""

    def _prompts(self):
        from hy_memory.agent.extractor import EXTRACT_SYSTEM_PROMPT
        from hy_memory.agent import prompts_zh

        zh = next(
            v for k, v in vars(prompts_zh).items()
            if isinstance(v, str) and "时间规则" in v and "质量要求" in v
        )
        return EXTRACT_SYSTEM_PROMPT, zh

    def test_just_happened_events_rule_present_both_languages(self):
        """"我刚旅行回来" 的返回日期就是 LoCoMo 时序题的答案，此前被丢掉。"""
        en, zh = self._prompts()
        assert "just got back" in en
        assert "刚回来" in zh or "刚刚发生" in zh

    def test_no_generalisation_rule_present_both_languages(self):
        """Banff 被泛化成 "the Rocky Mountains"，具体地名丢失。"""
        en, zh = self._prompts()
        assert "Banff" in en and "Banff" in zh
        assert "Rocky Mountains" in en

    def test_cross_speaker_detail_rule_present(self):
        """细节由对方话轮给出、用户确认 —— 主体规则不该导致丢细节。"""
        en, zh = self._prompts()
        assert "other speaker" in en
        assert "对方话轮" in zh
