# -*- coding: utf-8 -*-
"""Extractor 自定义 system prompt 覆盖（client 级 zh/en）单元测试。

覆盖：
  - 中文输入用 extract_system_prompt_zh；英文输入用 extract_system_prompt_en
  - None（默认）时用内置 EXTRACT_SYSTEM_PROMPT[_ZH]
  - 自定义 prompt 生效时不追加内置 few-shot（即便 few_shot_enabled=True）
  - user 模板（数据注入段）不受影响
"""

import asyncio

from hy_memory.config import LLMConfig
from hy_memory.agent.extractor import (
    Extractor,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_FEW_SHOT_EN,
)
from hy_memory.agent.prompts_zh import EXTRACT_SYSTEM_PROMPT_ZH
from hy_memory.agent.llm_provider import LLMResponse


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _CapturingLLM:
    """记录最近一次 complete_messages 的 messages，返回一个合法空提取。"""

    def __init__(self):
        self.last_messages = None

    async def complete_messages(self, *, messages, max_tokens, temperature=None,
                                tools=None, tool_choice=None, **kwargs):
        self.last_messages = messages
        return LLMResponse(
            content='{"memory": [], "intentions": [], "basic_info": {}}',
            tokens_used=1, prompt_tokens=1, completion_tokens=0, tool_calls=None,
        )

    def _system(self):
        return next(m["content"] for m in self.last_messages if m["role"] == "system")

    def _user(self):
        return next(m["content"] for m in self.last_messages if m["role"] == "user")


ZH_INPUT = "我叫小明，住在北京，喜欢喝美式咖啡。"
EN_INPUT = "My name is Bob, I live in Seattle and I love hiking."


def _extract(llm, cfg, content):
    ex = Extractor(llm, cfg)
    run(ex.extract(content=content, current_time="2026-07-01"))


def test_default_uses_builtin_system_prompt():
    llm = _CapturingLLM()
    _extract(llm, LLMConfig(), EN_INPUT)
    assert llm._system() == EXTRACT_SYSTEM_PROMPT
    llm2 = _CapturingLLM()
    _extract(llm2, LLMConfig(), ZH_INPUT)
    assert llm2._system() == EXTRACT_SYSTEM_PROMPT_ZH


def test_code_heavy_chinese_instruction_uses_chinese_prompt():
    llm = _CapturingLLM()
    _extract(
        llm,
        LLMConfig(),
        "请修复 search_client.py 里的 timeout bug: TypeError in parse_response(request_id)",
    )
    assert llm._system() == EXTRACT_SYSTEM_PROMPT_ZH


def test_english_instruction_with_later_chinese_term_uses_english_prompt():
    llm = _CapturingLLM()
    _extract(llm, LLMConfig(), "Please update the 中文 README file.")
    assert llm._system() == EXTRACT_SYSTEM_PROMPT


def test_custom_en_prompt_used_for_english():
    custom = "You are a CUSTOM English extractor. Output JSON with {\"memory\": []}."
    llm = _CapturingLLM()
    _extract(llm, LLMConfig(extract_system_prompt_en=custom), EN_INPUT)
    assert llm._system() == custom
    # user 模板仍注入了原始 content
    assert EN_INPUT in llm._user()


def test_custom_zh_prompt_used_for_chinese():
    custom = "你是一个自定义中文提取器，只输出 JSON。"
    llm = _CapturingLLM()
    _extract(llm, LLMConfig(extract_system_prompt_zh=custom), ZH_INPUT)
    assert llm._system() == custom
    assert "小明" in llm._user()


def test_language_selects_correct_override():
    """只配 zh 覆盖：中文用自定义，英文仍用内置。"""
    cfg = LLMConfig(extract_system_prompt_zh="仅中文自定义")
    llm_zh = _CapturingLLM()
    _extract(llm_zh, cfg, ZH_INPUT)
    assert llm_zh._system() == "仅中文自定义"

    llm_en = _CapturingLLM()
    _extract(llm_en, cfg, EN_INPUT)
    assert llm_en._system() == EXTRACT_SYSTEM_PROMPT  # 英文没配 → 内置


def test_custom_prompt_suppresses_few_shot():
    custom = "CUSTOM en prompt"
    cfg = LLMConfig(extract_system_prompt_en=custom, few_shot_enabled=True)
    llm = _CapturingLLM()
    _extract(llm, cfg, EN_INPUT)
    # 自定义生效时不追加内置 few-shot
    assert llm._system() == custom
    assert EXTRACT_FEW_SHOT_EN not in llm._system()


def test_few_shot_still_appends_to_builtin_when_no_override():
    cfg = LLMConfig(few_shot_enabled=True)
    llm = _CapturingLLM()
    _extract(llm, cfg, EN_INPUT)
    assert llm._system() == EXTRACT_SYSTEM_PROMPT + EXTRACT_FEW_SHOT_EN
