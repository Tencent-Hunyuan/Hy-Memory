"""MemHub extraction follows HY's input-language branches without losing both speakers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from hy_memory.agent.extractor import EXTRACT_SYSTEM_PROMPT, Extractor
from hy_memory.agent.prompts_zh import EXTRACT_SYSTEM_PROMPT_ZH
from hy_memory.utils.lang_detect import is_chinese

from one_memory import hy_memory_bridge
from one_memory.hy_memory_bridge import HyMemoryBridgeError, build_hy_memory_config
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind

CHINESE_QUESTION_WITH_ENGLISH_ERROR = """这个报错是什么意思，我该怎么修？
Traceback (most recent call last):
  File "/Users/example/one-memory/apps/core/src/one_memory/recall.py", line 177, in _recall
    for memory in self.governance.list(user_id=user_id, status=MemoryStatus.CONFIRMED, limit=500):
  File "/Users/example/one-memory/apps/core/src/one_memory/memory_governance.py", line 388
    raise ValueError("invalid pagination")
ValueError: invalid pagination
"""
ENGLISH_QUESTION = "The assistant confirmed the deployment needs a signed installer."


@dataclass
class StubResponse:
    content: str = '{"memories": [], "intentions": [], "basic_info": {}}'
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: Any = None


@dataclass
class RecordingLLM:
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
    asyncio.run(Extractor(llm_provider=llm, llm_config=config.llm).extract(content=content))
    return llm


def test_sdk_detects_chinese_question_before_long_english_traceback() -> None:
    # A leading-language window replaced the old full-text-ratio assumption.
    assert is_chinese(CHINESE_QUESTION_WITH_ENGLISH_ERROR)
    assert not is_chinese(ENGLISH_QUESTION)


def test_each_branch_preserves_native_language_and_changes_only_speaker_scope(tmp_path) -> None:
    config = build_hy_memory_config(_profile(), api_key=None, data_dir=tmp_path)
    assert config.llm.extract_system_prompt_zh == EXTRACT_SYSTEM_PROMPT_ZH.replace(
        hy_memory_bridge._HY_READS_ONLY_THE_USER_ZH,
        hy_memory_bridge._BOTH_SPEAKERS_ZH,
    )
    assert config.llm.extract_system_prompt_en == EXTRACT_SYSTEM_PROMPT.replace(
        hy_memory_bridge._HY_READS_ONLY_THE_USER_EN,
        hy_memory_bridge._BOTH_SPEAKERS_EN,
    )
    for prompt in (
        config.llm.extract_system_prompt_zh,
        config.llm.extract_system_prompt_en,
    ):
        assert "始终是简体中文" not in prompt
        assert "OWNER" in prompt


@pytest.mark.parametrize(
    ("content", "native_prompt", "speaker_rule", "language_rule"),
    [
        (
            CHINESE_QUESTION_WITH_ENGLISH_ERROR,
            EXTRACT_SYSTEM_PROMPT_ZH,
            "既提取用户说的，也提取 AI 助手提供的",
            "输出语言必须与输入语言一致",
        ),
        (
            ENGLISH_QUESTION,
            EXTRACT_SYSTEM_PROMPT,
            "from both the user and the AI assistant",
            "Output language must match input language",
        ),
    ],
)
def test_real_extractor_selects_native_prompt_and_preserves_input(
    content: str, native_prompt: str, speaker_rule: str, language_rule: str, tmp_path
) -> None:
    llm = _extract(content, tmp_path)
    system_prompt = llm.system_prompt()
    assert system_prompt.startswith(native_prompt.splitlines()[0])
    assert speaker_rule in system_prompt
    assert language_rule in system_prompt
    assert "始终是简体中文" not in system_prompt
    assert content in llm.user_prompt()


def test_writer_message_dump_routes_chinese_and_english(tmp_path) -> None:
    for message, language_rule in (
        (CHINESE_QUESTION_WITH_ENGLISH_ERROR, "输出语言必须与输入语言一致"),
        (ENGLISH_QUESTION, "Output language must match input language"),
    ):
        # The production writer sends a JSON dump, not a bare message string.
        content = json.dumps([{"role": "user", "content": message}], ensure_ascii=False)
        llm = _extract(content, tmp_path)
        assert language_rule in llm.system_prompt()
        assert content in llm.user_prompt()


def test_writer_batch_uses_latest_user_turn_even_after_english_assistant(tmp_path) -> None:
    content = json.dumps(
        [
            {"role": "assistant", "content": "The deployment needs a signed installer."},
            {"role": "user", "content": "好的，请把这个决定记下来。"},
        ],
        ensure_ascii=False,
    )
    llm = _extract(content, tmp_path)
    assert "输出语言必须与输入语言一致" in llm.system_prompt()
    assert "OWNER" in llm.system_prompt()
    assert content in llm.user_prompt()


def test_chinese_error_retains_untranslated_terms(tmp_path) -> None:
    llm = _extract(CHINESE_QUESTION_WITH_ENGLISH_ERROR, tmp_path)
    assert "ValueError: invalid pagination" in llm.user_prompt()
    assert "保留原始术语、品牌名、公式、代码" in llm.system_prompt()
    assert "不要转写或翻译专有名词" in llm.system_prompt()


@pytest.mark.parametrize("branch", ["zh", "en"])
def test_reworded_sdk_scope_fails_closed(tmp_path, monkeypatch, branch: str) -> None:
    real_import = hy_memory_bridge.import_module
    target = "hy_memory.agent.prompts_zh" if branch == "zh" else "hy_memory.agent.extractor"
    original = (
        hy_memory_bridge._HY_READS_ONLY_THE_USER_ZH
        if branch == "zh"
        else hy_memory_bridge._HY_READS_ONLY_THE_USER_EN
    )
    attr = "EXTRACT_SYSTEM_PROMPT_ZH" if branch == "zh" else "EXTRACT_SYSTEM_PROMPT"
    source = real_import(target)
    fake = type("RewordedPrompts", (), {attr: getattr(source, attr).replace(original, "changed")})
    monkeypatch.setattr(
        hy_memory_bridge,
        "import_module",
        lambda name: fake if name == target else real_import(name),
    )
    with pytest.raises(HyMemoryBridgeError, match="hy_extraction_scope_rule_not_found"):
        build_hy_memory_config(_profile(), api_key=None, data_dir=tmp_path)


def test_custom_prompts_keep_previous_few_shot_and_intention_behavior(tmp_path) -> None:
    config = build_hy_memory_config(_profile(), api_key=None, data_dir=tmp_path)
    assert config.llm.few_shot_enabled is True
    assert config.llm.enable_intention is False
    for content in (CHINESE_QUESTION_WITH_ENGLISH_ERROR, ENGLISH_QUESTION):
        prompt = _extract(content, tmp_path).system_prompt()
        assert "# EXAMPLES" not in prompt  # HY skips few-shot for custom prompts.
        assert "intentions" in prompt  # HY skips prompt cleanup for custom prompts.
