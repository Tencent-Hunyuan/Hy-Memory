# -*- coding: utf-8 -*-
"""两步 LLM 筛选（coding/selection.py + search(agentic=True)）。

重点不是"模型说了什么"，而是**失败时不能少给内容**：任何一步出错都必须退回
"不筛"，否则失败会伪装成"记忆本来就没有这条"。
"""

from types import SimpleNamespace

from hy_memory.coding.selection import CodingSelector, _parse_json_object


class FakeProvider:
    """按顺序吐出预设回复；记录每次收到的提示词。"""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.prompts = []

    async def complete(self, prompt, max_tokens=0, temperature=None):
        self.prompts.append(prompt)
        reply = self._replies.pop(0) if self._replies else ""
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply)


FACTS = [
    {"statement": "Task: vCard import · branch feat/tu0", "branch": "feat/tu0", "files": ["a.ts"]},
    {"statement": "Task: calendar sync · branch feat/tu1", "branch": "feat/tu1", "files": ["b.ts"]},
    {"statement": "Task: unrelated telemetry · branch feat/tu9", "branch": "feat/tu9", "files": []},
]


class TestSelectFacts:
    async def test_keeps_indices(self):
        sel = CodingSelector(FakeProvider('{"useful": [0, 2], "why": "same file"}'))
        assert await sel.select_facts("fix a.ts", FACTS) == {0, 2}

    async def test_out_of_range_indices_ignored(self):
        sel = CodingSelector(FakeProvider('{"useful": [0, 99, -1]}'))
        assert await sel.select_facts("q", FACTS) == {0}

    async def test_unparsable_returns_none(self):
        sel = CodingSelector(FakeProvider("I think these are all useful"))
        assert await sel.select_facts("q", FACTS) is None

    async def test_llm_error_returns_none(self):
        sel = CodingSelector(FakeProvider(RuntimeError("boom")))
        assert await sel.select_facts("q", FACTS) is None

    async def test_empty_facts(self):
        sel = CodingSelector(FakeProvider())
        assert await sel.select_facts("q", []) == set()

    async def test_prompt_carries_branch_and_statement(self):
        provider = FakeProvider('{"useful": [0]}')
        await CodingSelector(provider).select_facts("do the thing", FACTS)
        prompt = provider.prompts[0]
        assert "feat/tu0" in prompt and "vCard import" in prompt
        assert "do the thing" in prompt


class TestChooseChains:
    def _picked(self):
        # (fact, threads)；thread = (tid, file, symbol, lo, hi, n)
        return [(FACTS[0], [("t1", "a.ts", "parse", 1, 9, 3)])]

    def _saids(self, tid):
        return ["first pass", "reworked", "settled"]

    async def test_need_diff_false(self):
        sel = CodingSelector(FakeProvider('{"threads": {"t1": {"need_diff": false}}}'))
        out = await sel.choose_chains("q", self._picked(), self._saids)
        assert out == {"t1": {"versions": None, "no_body": True}}

    async def test_specific_versions(self):
        sel = CodingSelector(FakeProvider(
            '{"threads": {"t1": {"need_diff": true, "versions": [3, 1]}}}'))
        out = await sel.choose_chains("q", self._picked(), self._saids)
        assert out["t1"] == {"versions": [1, 3], "no_body": False}

    async def test_unknown_thread_ignored(self):
        sel = CodingSelector(FakeProvider('{"threads": {"nope": {"need_diff": false}}}'))
        assert await sel.choose_chains("q", self._picked(), self._saids) == {}

    async def test_unparsable_returns_none(self):
        sel = CodingSelector(FakeProvider("no json here"))
        assert await sel.choose_chains("q", self._picked(), self._saids) is None

    async def test_no_threads_returns_none(self):
        sel = CodingSelector(FakeProvider())
        assert await sel.choose_chains("q", [], self._saids) is None


class TestParseJsonObject:
    def test_fenced_and_prose(self):
        assert _parse_json_object('ok:\n```json\n{"a": 1}\n```') == {"a": 1}

    def test_non_object(self):
        assert _parse_json_object("[1, 2]") is None
        assert _parse_json_object("") is None


class TestClientGate:
    """开关优先级：显式参数 > CodingConfig.agentic_selection > 默认关。"""

    def _client(self, configured=None):
        from hy_memory.client import HyMemoryClient

        config = SimpleNamespace(coding=SimpleNamespace(agentic_selection=configured))
        ns = SimpleNamespace(_config=config)
        ns._agentic_enabled = HyMemoryClient._agentic_enabled.__get__(ns)
        return ns

    def test_default_off(self):
        assert self._client(configured=None)._agentic_enabled(None) is False

    def test_config_on(self):
        assert self._client(configured=True)._agentic_enabled(None) is True

    def test_explicit_overrides_config(self):
        assert self._client(configured=True)._agentic_enabled(False) is False
        assert self._client(configured=False)._agentic_enabled(True) is True


class TestAgenticSelectFallbacks:
    """client._agentic_select 的退回语义：失败只能"不筛"，不能"少给"。"""

    def _ns(self, provider, *, chain_block=False, store=None):
        from hy_memory.client import HyMemoryClient

        config = SimpleNamespace(coding=SimpleNamespace(
            agentic_selection=True, chain_block_enabled=chain_block))
        ns = SimpleNamespace(
            _config=config,
            _coding_selector=CodingSelector(provider),
            _coding_llm_provider=provider,
            _get_chain_store=lambda owner: store,
        )
        return ns, HyMemoryClient

    async def test_step1_failure_returns_all_facts(self):
        ns, C = self._ns(FakeProvider(RuntimeError("down")))
        kept, selection, dropped = await C._agentic_select(
            ns, "q", FACTS, user_id="u", workspace_id="w")
        assert kept == FACTS and selection is None and dropped == 0

    async def test_dropping_everything_is_treated_as_failure(self):
        ns, C = self._ns(FakeProvider('{"useful": []}'))
        kept, selection, dropped = await C._agentic_select(
            ns, "q", FACTS, user_id="u", workspace_id="w")
        assert kept == FACTS and dropped == 0

    async def test_step1_works_without_chain_store(self):
        provider = FakeProvider('{"useful": [1]}')
        ns, C = self._ns(provider, chain_block=False, store=None)
        kept, selection, dropped = await C._agentic_select(
            ns, "q", FACTS, user_id="u", workspace_id="w")
        assert kept == [FACTS[1]] and dropped == 2
        assert selection is None
        assert len(provider.prompts) == 1   # 链块关着：不该再问第 2 步

    async def test_step2_runs_when_chain_block_on(self):
        class Store:
            def collect_threads(self, facts, instruction="", scope=None):
                return [(facts[0], [("t1", "a.ts", "parse", 1, 9, 2)])], [0]

            def thread_saids(self, tid):
                return ["v1", "v2"]

        provider = FakeProvider('{"useful": [0]}',
                                '{"threads": {"t1": {"need_diff": false}}}')
        ns, C = self._ns(provider, chain_block=True, store=Store())
        kept, selection, dropped = await C._agentic_select(
            ns, "q", FACTS, user_id="u", workspace_id="w")
        assert kept == [FACTS[0]] and dropped == 2
        assert selection == {"t1": {"versions": None, "no_body": True}}
        assert len(provider.prompts) == 2
