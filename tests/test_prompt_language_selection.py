"""Explicit prompt language: validation, precedence and threading.

With no language the existing zh/en auto-detection runs unchanged; with an
explicit language every LLM call of the request uses that language's prompt
plus its OUTPUT_LANGUAGE_DIRECTIVE.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from hy_memory.agent.extractor import Extractor, _system_prompt_without_intention
from hy_memory.agent.llm_provider import LLMResponse
from hy_memory.agent.reconciler import build_reconcile_prompt, build_search_query_prompt
from hy_memory.coding.extractor import CodingMemoryExtractor
from hy_memory.coding.fact_reconciler import (
    build_chain_summary_prompt,
    build_fact_reconcile_prompt,
)
from hy_memory.coding.reconciler import build_experience_reconcile_prompt
from hy_memory.coding.types import CodingMemoryDraft, WorkingFactDraft
from hy_memory.config import LLMConfig, MemoryConfig
from hy_memory.pipelines.base import (
    ChatMessage,
    WriteRequest,
    parse_user_name_line,
    render_user_name_line,
)
from hy_memory.prompts import (
    SUPPORTED_LANGUAGES,
    get_prompt,
    normalize_language,
    resolve_language,
)

from tests.coding._helpers import MockLLMProvider

ZH_INPUT = "我叫小明，住在北京，喜欢喝美式咖啡。"
EN_INPUT = "My name is Bob, I live in Seattle and I love hiking."


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _directive(code):
    return get_prompt("OUTPUT_LANGUAGE_DIRECTIVE", code)


# ── codes, aliases, validation ──

@pytest.mark.parametrize("value,expected", [
    ("en", "en"), ("zh-Hans", "zh-Hans"), ("zh-Hant", "zh-Hant"), ("ja", "ja"),
    ("ZH-hans", "zh-Hans"), ("zh_Hant", "zh-Hant"), (" ko ", "ko"),
    ("zh", "zh-Hans"), ("zh-CN", "zh-Hans"), ("zh-cn", "zh-Hans"),
    ("zh-TW", "zh-Hant"), ("zh-HK", "zh-Hant"), ("pt-BR", "pt"),
    (None, None), ("", None),
])
def test_normalize_language_accepts_codes_and_aliases(value, expected):
    assert normalize_language(value) == expected


@pytest.mark.parametrize("value", ["xx", "english", "zh-XX", "pt_BR_x", 3])
def test_unknown_language_raises_value_error_listing_supported_codes(value):
    with pytest.raises(ValueError) as exc:
        normalize_language(value)
    for code in SUPPORTED_LANGUAGES:
        assert code in str(exc.value)


def test_resolve_language_falls_back_to_zh_en_detection_only():
    assert resolve_language(None, ZH_INPUT) == "zh-Hans"
    assert resolve_language(None, EN_INPUT) == "en"
    # auto-detection never picks the other languages
    assert resolve_language(None, "ユーザーはコーヒーが好きです") in ("zh-Hans", "en")
    assert resolve_language(None, "Der Nutzer mag Kaffee.") == "en"
    assert resolve_language("de", ZH_INPUT) == "de"


# ── config + client precedence ──

def test_memory_config_language_from_env_and_dict(monkeypatch):
    monkeypatch.setenv("MEMORY_LANGUAGE", "zh-TW")
    assert MemoryConfig().language == "zh-Hant"
    monkeypatch.setenv("MEMORY_LANGUAGE", "")
    assert MemoryConfig().language is None
    assert MemoryConfig.from_dict({"language": "pt-BR"}).language == "pt"
    assert MemoryConfig(language="FR").language == "fr"
    monkeypatch.setenv("MEMORY_LANGUAGE", "klingon")
    with pytest.raises(ValueError):
        MemoryConfig()


def test_client_per_call_language_overrides_config(monkeypatch):
    from hy_memory.client import HyMemoryClient

    monkeypatch.delenv("MEMORY_LANGUAGE", raising=False)
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._config = MemoryConfig(language="ja")
    assert client._resolve_language(None) == "ja"
    assert client._resolve_language("zh-CN") == "zh-Hans"
    client._config = MemoryConfig()
    assert client._resolve_language(None) is None
    with pytest.raises(ValueError):
        client._resolve_language("xx")


@pytest.mark.asyncio
async def test_async_add_threads_language_and_rejects_unknown_before_claim():
    from hy_memory.client import HyMemoryClient

    client = HyMemoryClient.__new__(HyMemoryClient)
    client._config = MemoryConfig(language="es")
    client._claim_write_outcome = AsyncMock(return_value=None)
    client._finish_write_outcome = AsyncMock()
    client._async_add_impl = AsyncMock(return_value={"success": True})

    await HyMemoryClient.async_add.__wrapped__(client, "hola", user_id="u1")
    assert client._async_add_impl.await_args.kwargs["language"] == "es"
    await HyMemoryClient.async_add.__wrapped__(
        client, "hi", user_id="u1", language="zh-HK",
    )
    assert client._async_add_impl.await_args.kwargs["language"] == "zh-Hant"

    client._claim_write_outcome.reset_mock()
    with pytest.raises(ValueError):
        await HyMemoryClient.async_add.__wrapped__(
            client, "hi", user_id="u1", language="xx",
        )
    client._claim_write_outcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_add_extracted_threads_language():
    from hy_memory.client import HyMemoryClient

    client = HyMemoryClient.__new__(HyMemoryClient)
    client._config = MemoryConfig()
    client._claim_write_outcome = AsyncMock(return_value=None)
    client._finish_write_outcome = AsyncMock()
    client._async_add_extracted_impl = AsyncMock(return_value={"success": True})
    await HyMemoryClient.async_add_extracted.__wrapped__(
        client, ["a fact"], user_id="u1", language="ru",
    )
    assert client._async_add_extracted_impl.await_args.kwargs["language"] == "ru"
    await HyMemoryClient.async_add_extracted.__wrapped__(
        client, ["a fact"], user_id="u1",
    )
    assert client._async_add_extracted_impl.await_args.kwargs["language"] is None


# ── chat extraction ──

class _CapturingLLM:
    def __init__(self):
        self.last_messages = None

    async def complete_messages(self, *, messages, max_tokens, temperature=None,
                                tools=None, tool_choice=None, **kwargs):
        self.last_messages = messages
        return LLMResponse(
            content='{"memory": [], "intentions": [], "basic_info": {}}',
            tokens_used=1, prompt_tokens=1, completion_tokens=0, tool_calls=None,
        )

    def role(self, role):
        return next(m["content"] for m in self.last_messages if m["role"] == role)


def _extract(content, language=None, **cfg):
    llm = _CapturingLLM()
    run(Extractor(llm, LLMConfig(**cfg)).extract(
        content=content, current_time="2026-07-01", language=language,
    ))
    return llm


def test_extract_without_language_keeps_auto_detection_byte_identical():
    assert _extract(ZH_INPUT).role("system") == get_prompt("EXTRACT_SYSTEM_PROMPT", "zh-Hans")
    assert _extract(EN_INPUT).role("system") == get_prompt("EXTRACT_SYSTEM_PROMPT", "en")


@pytest.mark.parametrize("code", SUPPORTED_LANGUAGES)
def test_extract_with_explicit_language_uses_that_prompt_and_directive(code):
    llm = _extract(ZH_INPUT, language=code)
    assert llm.role("system") == (
        get_prompt("EXTRACT_SYSTEM_PROMPT", code) + _directive(code)
    )
    assert llm.role("user") == get_prompt("EXTRACT_PROMPT", code).format(
        content=ZH_INPUT, current_date=_today(),
        memory_at="2026-07-01", last_messages="(none)",
    )


def _today():
    from datetime import date

    return date.today().isoformat()


def test_extract_alias_and_few_shot_follow_explicit_language():
    llm = _extract(EN_INPUT, language="zh-TW", few_shot_enabled=True)
    assert llm.role("system").startswith(
        get_prompt("EXTRACT_SYSTEM_PROMPT", "zh-Hant")
        + get_prompt("EXTRACT_FEW_SHOT", "zh-Hant")
    )


def test_custom_system_prompt_still_applies_to_its_own_language_only():
    llm = _extract(EN_INPUT, language="en", extract_system_prompt_en="CUSTOM EN")
    assert llm.role("system") == "CUSTOM EN" + _directive("en")
    llm = _extract(EN_INPUT, language="de", extract_system_prompt_en="CUSTOM EN")
    assert llm.role("system").startswith(get_prompt("EXTRACT_SYSTEM_PROMPT", "de"))


@pytest.mark.parametrize("code", SUPPORTED_LANGUAGES)
def test_no_intention_rewrite_works_for_every_language(code):
    import re

    base = get_prompt("EXTRACT_SYSTEM_PROMPT", code)
    out = _system_prompt_without_intention(base, lang=code)
    assert get_prompt("NO_INTENTION_MEMORY_SECTION", code) in out
    assert not re.search(get_prompt("NO_INTENTION_SECTION_PATTERN", code), out, re.S)
    assert out.endswith(get_prompt("INTENTION_DISABLED_NOTE", code))


# ── chat reconcile ──

def _reconcile(memories, language=None):
    return build_reconcile_prompt(
        new_memories=memories, new_memories_block="NEW", existing_memories_block="OLD",
        current_date="2026-07-01 10:00", few_shot_enabled=True,
        structure_metadata_enabled=True, language=language,
    )


def test_reconcile_without_language_keeps_auto_detection():
    zh = _reconcile([ZH_INPUT])
    assert zh.startswith(get_prompt("RECONCILE_PROMPT", "zh-Hans")[:40])
    assert zh.endswith(get_prompt("RECONCILE_STRUCTURE_METADATA_PROMPT", "zh-Hans"))
    en = _reconcile([EN_INPUT])
    assert en.endswith(get_prompt("RECONCILE_STRUCTURE_METADATA_PROMPT", "en"))


@pytest.mark.parametrize("code", ["fr", "zh-Hant", "id"])
def test_reconcile_and_search_query_use_explicit_language(code):
    prompt = _reconcile([ZH_INPUT], language=code)
    expected = get_prompt("RECONCILE_PROMPT", code).format(
        current_date="2026-07-01 10:00", new_memories="NEW", existing_memories="OLD",
        few_shot_section="\n" + get_prompt("RECONCILE_FEW_SHOT", code) + "\n",
    ) + get_prompt("RECONCILE_STRUCTURE_METADATA_PROMPT", code) + _directive(code)
    assert prompt == expected
    sq = build_search_query_prompt([ZH_INPUT], language=code)
    assert sq.endswith(_directive(code))
    assert sq.startswith(get_prompt("SEARCH_QUERY_PROMPT", code)[:20])
    assert build_search_query_prompt([ZH_INPUT]) == get_prompt(
        "SEARCH_QUERY_PROMPT", "zh-Hans",
    ).format(new_memories=f"1. {ZH_INPUT}")


# ── working / coding path ──

@pytest.mark.parametrize("scene,name", [
    ("working", "CODING_EXTRACT_PROMPT"), ("coding", "CODING_EXTRACT_PROMPT_CODING"),
])
def test_coding_extractor_template_follows_explicit_language(scene, name):
    extractor = CodingMemoryExtractor(MockLLMProvider("{}"), scene=scene)
    messages = [ChatMessage(role="user", content="请修复 api_client.py 的 timeout")]
    template, code = extractor._select_prompt_template(messages, "ko")
    assert code == "ko" and template is get_prompt(name, "ko")
    template, code = extractor._select_prompt_template(messages)
    assert code == "zh-Hans" and template is get_prompt(name, "zh-Hans")


def test_coding_extract_call_uses_language_for_labels_and_directive():
    llm = MockLLMProvider('{"experiences": [], "fact": null}')
    extractor = CodingMemoryExtractor(llm, scene="working")
    run(extractor.extract(
        [ChatMessage(role="user", content="Fix the timeout"),
         ChatMessage(role="tool", content="ok")],
        user_id="u1", language="ja",
    ))
    prompt = llm.calls[-1]["prompt"]
    assert prompt.endswith(_directive("ja"))
    empty = get_prompt("CODING_EMPTY_BLOCK", "ja")
    head = get_prompt("CODING_CONVERSATION_BLOCK", "ja").split("{history}")[0]
    assert head + empty in prompt


def test_coding_extract_without_language_is_unchanged():
    llm = MockLLMProvider('{"experiences": [], "fact": null}')
    extractor = CodingMemoryExtractor(llm, scene="working")
    run(extractor.extract(
        [ChatMessage(role="user", content="Fix the timeout"),
         ChatMessage(role="tool", content="ok")],
        user_id="u1",
    ))
    prompt = llm.calls[-1]["prompt"]
    assert "LAST 5 USER TURNS (CONTEXT ONLY; do not extract them again):\n(none)" in prompt
    assert _directive("en") not in prompt


def test_coding_reconcilers_follow_explicit_language():
    drafts = [CodingMemoryDraft(task="修复搜索超时", solution="改 timeout", user_id="u1")]
    prompt, code = build_experience_reconcile_prompt(drafts, [[]], language="es")
    assert code == "es" and prompt.endswith(_directive("es"))
    assert prompt.startswith(get_prompt("CODING_RECONCILE_PROMPT", "es")[:30])
    _, code = build_experience_reconcile_prompt(drafts, [[]])
    assert code == "zh-Hans"

    facts = [WorkingFactDraft(subject="api.py", statement="修复了 timeout")]
    prompt, code = build_fact_reconcile_prompt(facts, [[]], language="pt-BR")
    assert code == "pt" and prompt.endswith(_directive("pt"))

    prompt, code = build_chain_summary_prompt(
        "(now) fixed", current_statement="fixed", language="ru",
    )
    assert code == "ru"
    assert prompt == get_prompt("CODING_CHAIN_SUMMARY_PROMPT", "ru").format(
        chain_block="(now) fixed",
    ) + _directive("ru")


@pytest.mark.asyncio
async def test_coding_writer_threads_language_to_every_step():
    from hy_memory.coding.writer import CodingWriter

    writer = CodingWriter.__new__(CodingWriter)
    writer.store = type("S", (), {})()
    writer.store.config = type("C", (), {
        "coding": type("CC", (), {"tool_result_max_bytes": 2048})(),
        "extractor": type("E", (), {"history_turns": 5})(),
    })()
    writer.store.get_recent_trace_messages = AsyncMock(return_value=[])
    writer.store.append_trace = AsyncMock()
    draft = CodingMemoryDraft(task="t", solution="s", user_id="u1")
    fact = WorkingFactDraft(subject="x", statement="y")
    writer.extractor = type("X", (), {})()
    writer.extractor.extract = AsyncMock(return_value=([draft], [fact]))
    # writer 先做两层的模型决策（plan），都成功后才落库（apply）
    writer.reconciler = type("R", (), {})()
    writer.reconciler.plan = AsyncMock(return_value=object())
    writer.reconciler.apply = AsyncMock(return_value=[])
    writer.fact_reconciler = type("F", (), {})()
    writer.fact_reconciler.plan = AsyncMock(return_value=object())
    writer.fact_reconciler.apply = AsyncMock(return_value=([], []))
    writer._crosslink = AsyncMock()
    writer.cache = None
    writer.variant = "working"

    await writer.write([ChatMessage(role="user", content="hi")], user_id="u1",
                       language="de")
    assert writer.extractor.extract.await_args.kwargs["language"] == "de"
    assert writer.reconciler.plan.await_args.kwargs["language"] == "de"
    assert writer.fact_reconciler.plan.await_args.kwargs["language"] == "de"


# ── summaries ──

@pytest.mark.asyncio
async def test_episode_and_topic_summary_prompts_follow_language():
    from hy_memory.agent import episode_summary
    from hy_memory.pipelines._graph import topic_summary

    llm = MockLLMProvider("summary")
    _, prompt, _ = await episode_summary._generate(
        llm_provider=llm, memories=["用户喜欢咖啡"], language="ja",
    )
    assert prompt == get_prompt("EPISODE_SUMMARY_PROMPT", "ja").format(
        memories="- 用户喜欢咖啡",
    ) + _directive("ja")
    _, prompt, _ = await episode_summary._generate(
        llm_provider=llm, memories=["用户喜欢咖啡"],
    )
    assert prompt == get_prompt("EPISODE_SUMMARY_PROMPT", "zh-Hans").format(
        memories="- 用户喜欢咖啡",
    )
    _, prompt, _ = await topic_summary._generate(
        llm_provider=llm, topic_name="coffee", memories=["likes coffee"],
        language="fr",
    )
    assert prompt.endswith(_directive("fr"))
    assert prompt.startswith(get_prompt("TOPIC_SUMMARY_PROMPT", "fr")[:20])


# ── System 2 ──

@pytest.mark.parametrize("code", SUPPORTED_LANGUAGES)
def test_system2_prompts_follow_language(code):
    from hy_memory.pipelines import system2_agent as s2

    system_prompt = s2._build_single_call_system_prompt(code)
    assert system_prompt.endswith(get_prompt("SYSTEM2_OUTPUT_INSTRUCTIONS", code))
    first_old = get_prompt("SYSTEM2_SINGLE_CALL_REPLACEMENTS", code)[0][0]
    assert first_old not in system_prompt
    message = s2._build_materials_message(
        {"stats": {"graph_total_schemas": 0}, "clusters": []}, lang=code,
    )
    materials = get_prompt("SYSTEM2_MATERIALS", code)
    assert message.startswith(materials["title"])
    assert message.endswith(materials["closing"])


@pytest.mark.asyncio
async def test_system2_agent_uses_explicit_language(monkeypatch):
    from hy_memory.pipelines import system2_agent as s2

    seen = {}

    class _Provider:
        def __init__(self, config):
            pass

        async def complete_messages(self, messages, **kwargs):
            seen["messages"] = messages
            return LLMResponse(content="```json\n[]\n```", tokens_used=1)

    monkeypatch.setattr("hy_memory.agent.llm_provider.LLMProvider", _Provider)
    materials = {"stats": {}, "clusters": [{"facts": [
        {"layer": "L2", "content": "用户每天跑步", "node_id": "f1"}], "centroid_text": "跑步"}]}
    async def _run(**kwargs):
        # Only the prompt sent to the model matters here; executing the
        # (empty) operation list needs a real tool executor.
        try:
            await s2.run_system2_agent(
                materials, tool_executor=None, config=MemoryConfig(), **kwargs,
            )
        except AttributeError:
            pass

    await _run(language="de")
    system = seen["messages"][0]["content"]
    assert system.startswith(get_prompt("SYSTEM2_AGENT_PROMPT", "de")[:30])
    assert system.endswith(_directive("de"))
    assert seen["messages"][1]["content"].startswith(
        get_prompt("SYSTEM2_MATERIALS", "de")["title"]
    )

    await _run()
    system = seen["messages"][0]["content"]
    assert system == s2._build_single_call_system_prompt("zh")


@pytest.mark.asyncio
async def test_sweeper_llm_call_gets_output_language_rule(monkeypatch):
    from hy_memory.pipelines import system2_writer

    prompts = []

    class _Sweeper:
        def __init__(self, *, llm_call, **kwargs):
            self.llm_call = llm_call

        async def sweep(self):
            await self.llm_call("Task: abstract")
            return {}

    async def llm_call(prompt):
        prompts.append(prompt)
        return "{}"

    monkeypatch.setattr(system2_writer, "_SWEEPER_ENABLED", True)
    monkeypatch.setattr(
        "hy_memory.pipelines.cross_domain_sweeper.CrossDomainSweeper", _Sweeper,
    )
    writer = system2_writer.System2Writer.__new__(system2_writer.System2Writer)
    writer._writer = type("W", (), {"embed_service": None})()
    writer._graph_store = None
    writer._cache = None
    await writer._run_cross_domain_sweeper("u1", "a1", llm_call, language="ko")
    await writer._run_cross_domain_sweeper("u1", "a1", llm_call)
    assert prompts == ["Task: abstract" + _directive("ko"), "Task: abstract"]


# ── basic profile name line + write request ──

@pytest.mark.parametrize("code", SUPPORTED_LANGUAGES)
def test_user_name_line_round_trips_in_every_language(code):
    line = render_user_name_line("Tim", language=code)
    assert line == get_prompt("USER_NAME_LINE", code).format(name="Tim")
    assert parse_user_name_line(line) == "Tim"


def test_write_request_name_line_uses_explicit_language():
    msgs = [ChatMessage(role="user", content="我喜欢咖啡", name="Tim")]
    assert WriteRequest(messages=msgs).get_flat_content().startswith("用户名叫Tim")
    assert WriteRequest(messages=msgs, language="ja").get_flat_content().startswith(
        get_prompt("USER_NAME_LINE", "ja").format(name="Tim")
    )


@pytest.mark.asyncio
async def test_mem_agent_passes_language_to_extractor():
    from hy_memory.agent.mem_agent import MemAgent

    agent = MemAgent.__new__(MemAgent)
    agent.extractor = type("X", (), {})()
    agent.extractor.extract = AsyncMock(side_effect=RuntimeError("stop"))
    await agent.process_add(content="hi", context={}, language="id")
    assert agent.extractor.extract.await_args.kwargs["language"] == "id"


# ── server ──

def test_server_language_arg_validates_and_normalizes():
    from hy_memory import server

    sent = {}
    original = server._json_response
    server._json_response = lambda handler, status, data: sent.update(
        status=status, data=data,
    )
    try:
        kwargs = {}
        assert server._language_arg(None, {"language": "zh-TW"}, kwargs)
        assert kwargs == {"language": "zh-Hant"}
        kwargs = {}
        assert server._language_arg(None, {}, kwargs) and kwargs == {}
        assert not server._language_arg(None, {"language": "xx"}, {})
        assert sent["status"] == 400 and "zh-Hans" in sent["data"]["error"]
    finally:
        server._json_response = original


@pytest.mark.parametrize("value", [
    "使用者", "ユーザー", "사용자", "El usuario", "Der Nutzer", "O usuário",
    "L'utilisateur", "Пользователь", "Pengguna",
])
def test_localized_user_placeholders_never_become_a_name(value):
    from hy_memory.agent.tools.basic_profile import is_placeholder_value

    assert is_placeholder_value("name", value)
    assert not is_placeholder_value("name", "Tim")
