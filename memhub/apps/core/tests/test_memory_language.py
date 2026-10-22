"""R-03: memory language follows the message, so Kristin's gets written in English.

HY picks its extraction prompt from the content: `is_chinese(content)` in
`hy_memory/agent/extractor.py:868`, which is a CJK character ratio with a 0.3
threshold (`hy_memory/utils/lang_detect.py`). Kristin's everyday shape —
paste an English traceback, ask about it in Chinese — measures 0.09, so the
extractor is told to write in English and the memory comes out in English.

Read from HY's source rather than assumed, and it contradicts the plan of
record: `extract_system_prompt_zh` is only consulted *inside* the
`is_chinese(...)` true branch (`extractor.py:891-901`), so setting it changes
nothing for the case that is broken. The branch that actually runs reads
`extract_system_prompt_en` (`extractor.py:922-930`).

These tests drive HY's real `Extractor` with a stub LLM and look at the
system prompt it actually sends, so they pin HY's behaviour rather than a
copy of its logic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from hy_memory.agent.extractor import Extractor
from hy_memory.utils.lang_detect import _chinese_char_ratio, is_chinese

from one_memory import hy_memory_bridge
from one_memory.hy_memory_bridge import build_hy_memory_config
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind

#: A common shape: an English traceback pasted in, with
#: the question asked in Chinese. Measured CJK ratio 0.09.
CHINESE_QUESTION_WITH_ENGLISH_ERROR = """这个报错是什么意思，我该怎么修？
Traceback (most recent call last):
  File "/Users/example/one-memory/apps/core/src/one_memory/recall.py", line 177, in _recall
    for memory in self.governance.list(user_id=user_id, status=MemoryStatus.CONFIRMED, limit=500):
  File "/Users/example/one-memory/apps/core/src/one_memory/memory_governance.py", line 388
    raise ValueError("invalid pagination")
ValueError: invalid pagination
"""


@dataclass
class StubResponse:
    content: str = '{"memories": [], "intentions": [], "basic_info": {}}'
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: Any = None


@dataclass
class RecordingLLM:
    """Stands in for the model, and keeps what it was told to do."""

    messages: list[list[dict[str, Any]]] = field(default_factory=list)

    async def complete_messages(self, *, messages, **_kwargs) -> StubResponse:
        self.messages.append(messages)
        return StubResponse()

    async def complete(self, **_kwargs) -> StubResponse:
        return StubResponse()

    def system_prompt(self) -> str:
        return next(item["content"] for item in self.messages[0] if item["role"] == "system")

    def user_prompt(self) -> str:
        return next(item["content"] for item in self.messages[0] if item["role"] == "user")


def _profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="profile",
        revision=1,
        provider_kind=ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        model="memory-model",
        embedding_model="embedding-model",
        embedding_provider_kind=None,
        embedding_base_url=None,
        embedding_dimensions=768,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8192,
        has_api_key=False,
    )


def _extract(content: str, tmp_path) -> RecordingLLM:
    config = build_hy_memory_config(_profile(), api_key=None, data_dir=tmp_path)
    llm = RecordingLLM()
    extractor = Extractor(llm_provider=llm, llm_config=config.llm)
    asyncio.run(extractor.extract(content=content))
    return llm


def _reads_as_chinese(prompt: str) -> bool:
    return _chinese_char_ratio(prompt) > 0.2


def _follows_the_input_language(prompt: str) -> bool:
    return (
        "输出语言必须与输入语言一致" in prompt
        or "Output language must match input language" in prompt
    )


def test_hy_keeps_a_chinese_question_with_an_english_traceback_in_chinese(
    tmp_path,
) -> None:
    """The premise, pinned to HY's own detector rather than described.

    Green before the fix and after: it is the control. R-03 must not be
    "made to pass" by pushing the detector over its threshold — the input is
    genuinely 9% CJK, and it is the memory language that has to stop
    depending on that.
    """
    assert _chinese_char_ratio(CHINESE_QUESTION_WITH_ENGLISH_ERROR) < 0.3
    assert is_chinese(CHINESE_QUESTION_WITH_ENGLISH_ERROR) is True


def test_the_extractor_is_told_to_write_chinese_even_when_the_input_reads_as_english(
    tmp_path,
) -> None:
    """The memory language is a property of the user, not of one message.

    Before the fix this sample takes HY's English branch and gets the English
    system prompt, so the memory is written in English — for a user whose
    every other memory is Chinese, and who never asked for either.
    """
    llm = _extract(CHINESE_QUESTION_WITH_ENGLISH_ERROR, tmp_path)

    assert _reads_as_chinese(llm.system_prompt()), (
        f"extractor was given an English system prompt: {llm.system_prompt()[:120]!r}"
    )
    assert not _follows_the_input_language(llm.system_prompt()), (
        "the prompt still hands the language decision back to the input"
    )
    assert "始终" in llm.system_prompt() and "中文" in llm.system_prompt()


@pytest.mark.parametrize("ratio", [0.05, 0.3, 0.7])
def test_the_memory_language_does_not_depend_on_the_cjk_ratio(ratio: float, tmp_path) -> None:
    """Three samples either side of the 0.3 threshold, all Chinese memories.

    0.3 is the extraction threshold and 0.7 is System 2's, so a sample at
    each says whether the outcome still moves with the ratio.
    """
    chinese_length = max(1, round(40 * ratio))
    content = "我" * chinese_length + "x" * (40 - chinese_length)
    llm = _extract(content, tmp_path)

    assert _reads_as_chinese(llm.system_prompt()), (
        f"CJK ratio {ratio} produced an English system prompt"
    )
    assert not _follows_the_input_language(llm.system_prompt()), (
        f"CJK ratio {ratio} left the language up to the input"
    )


def test_the_input_reaches_hy_untouched(tmp_path) -> None:
    """No Chinese prefix glued onto the content to fool the detector.

    That shortcut would work and is forbidden: the content HY extracts from
    is the person's own words, and padding it pollutes the verbatim the whole
    evidence chain rests on. Green before and after — a guard, not a repair.
    """
    llm = _extract(CHINESE_QUESTION_WITH_ENGLISH_ERROR, tmp_path)

    assert CHINESE_QUESTION_WITH_ENGLISH_ERROR in llm.user_prompt()
    assert "ValueError: invalid pagination" in llm.user_prompt()


def test_hys_own_chinese_prompt_hands_the_language_back_to_the_input(tmp_path) -> None:
    """Why shipping HY's Chinese prompt as-is is not the fix.

    Both of HY's extraction system prompts carry one rule — "输出语言必须与输入
    语言一致" / "Output language must match input language" — so a Chinese
    prompt over an English-reading input still asks for English. Serving HY's
    text verbatim would have made every assertion about the *prompt* pass
    while the memory came out in English anyway. Green before and after: it
    pins the SDK text this fix has to work around, and goes red the day HY
    drops that rule, which is the day the fix can shrink.
    """
    prompts_zh = pytest.importorskip("hy_memory.agent.prompts_zh")
    extractor_module = pytest.importorskip("hy_memory.agent.extractor")

    assert _follows_the_input_language(prompts_zh.EXTRACT_SYSTEM_PROMPT_ZH)
    assert _follows_the_input_language(extractor_module.EXTRACT_SYSTEM_PROMPT)


def test_the_english_traceback_is_still_not_to_be_translated(tmp_path) -> None:
    """Fixing the language must not start rewriting what was pasted in.

    HY's other two language rules keep terms, brands, formulas and code
    exactly as written. Pinning the memory language to Chinese must leave
    those standing, or a traceback becomes a translated traceback and stops
    matching anything.
    """
    llm = _extract(CHINESE_QUESTION_WITH_ENGLISH_ERROR, tmp_path)

    assert "保留原始术语、品牌名、公式、代码" in llm.system_prompt()
    assert "不要转写或翻译专有名词" in llm.system_prompt()


def test_a_reworded_sdk_prompt_still_gets_a_chinese_instruction(
    tmp_path, monkeypatch, caplog
) -> None:
    """The fix edits one line of HY's prompt, so it has to survive a reword.

    If a future SDK changes that line's wording the replacement silently
    stops matching, and silently going back to "follow the input" is the
    failure mode worth spending a branch on: the override is appended
    instead, and it says so in the log.
    """

    from hy_memory.agent.prompts_zh import EXTRACT_SYSTEM_PROMPT_ZH as native_prompt

    class RewordedPrompts:
        EXTRACT_SYSTEM_PROMPT_ZH = native_prompt.replace(
            "* 输出语言必须与输入语言一致", "* HY 换了语言规则的措辞"
        )

    real_import = hy_memory_bridge.import_module
    monkeypatch.setattr(
        hy_memory_bridge,
        "import_module",
        lambda name: RewordedPrompts if name == "hy_memory.agent.prompts_zh" else real_import(name),
    )
    config = build_hy_memory_config(_profile(), api_key=None, data_dir=tmp_path)

    assert "始终是简体中文" in config.llm.extract_system_prompt_en
    assert "hy_language_rule_not_found" in caplog.text


def test_setting_the_chinese_override_controls_the_mixed_traceback_prompt(tmp_path) -> None:
    """Pins why R-03 is not the one-line config change it was planned as.

    `extract_system_prompt_zh` sits inside the `is_chinese(...)` true branch,
    so on the failing input it is never read. If a later HY version moves the
    override outside the branch this test goes red, which is the moment to
    simplify the fix rather than keep carrying it.
    """
    config = build_hy_memory_config(_profile(), api_key=None, data_dir=tmp_path)
    llm = RecordingLLM()
    config.llm.extract_system_prompt_en = None
    config.llm.extract_system_prompt_zh = "只用中文写记忆。"
    extractor = Extractor(llm_provider=llm, llm_config=config.llm)

    asyncio.run(extractor.extract(content=CHINESE_QUESTION_WITH_ENGLISH_ERROR))

    assert llm.system_prompt() == "只用中文写记忆。"
