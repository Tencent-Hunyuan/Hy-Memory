# -*- coding: utf-8 -*-
"""Chat-scene extractor language routing: the JSON envelope must not pick the prompt.

The chat scene hands the extractor a JSON list of the new messages
(``hy_memory/pipelines/writer.py``: ``_extract_content = json.dumps(_msgs_dump)``).
Language detection used to run on that raw string, so the first 10
alphanumeric characters were the envelope keys ("roleuserco") and detection
answered "en" for a Chinese conversation.  The Chinese extractor prompt
(``EXTRACT_SYSTEM_PROMPT_ZH``) was therefore unreachable, and a compliant
model answered with ENGLISH facts, which then failed Chinese recall against a
Chinese embedding model.

Detection now runs on the message text, via the existing
``is_chinese(messages=...)`` path.
"""

import asyncio
import json

from hy_memory.agent.extractor import EXTRACT_SYSTEM_PROMPT, Extractor
from hy_memory.agent.llm_provider import LLMResponse
from hy_memory.agent.prompts_zh import EXTRACT_SYSTEM_PROMPT_ZH
from hy_memory.config import LLMConfig
from hy_memory.utils.lang_detect import is_chinese

# Distinctive openers of the two system prompts.  The full prompt is not
# compared verbatim: with the default config the intention section is rewritten
# in place, so only the opener identifies the language.
ZH_HEAD = EXTRACT_SYSTEM_PROMPT_ZH.split("\n")[0]
EN_HEAD = "\n\n".join(EXTRACT_SYSTEM_PROMPT.split("\n\n")[:2])   # "# ROLE\n\nYou are a Memory Extractor."


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _CapturingLLM:
    """Records the last prompt and returns a valid empty extraction."""

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


def _chat_blob(*turns):
    """The blob the chat scene builds: a JSON list of role/content dicts."""
    return json.dumps(
        [{"role": "user", "content": text} for text in turns],
        ensure_ascii=False,
    )


def _extract(llm, content):
    ex = Extractor(llm, LLMConfig())
    run(ex.extract(content=content, current_time="2026-07-01"))


def test_chinese_chat_blob_selects_chinese_prompt():
    llm = _CapturingLLM()
    _extract(llm, _chat_blob("我住在北京海淀区，平时用 Zoom 开会。"))
    assert llm._system().startswith(ZH_HEAD)
    assert EN_HEAD not in llm._system()


def test_english_chat_blob_selects_english_prompt():
    llm = _CapturingLLM()
    _extract(llm, _chat_blob("I live in Seattle and I love hiking."))
    assert llm._system().startswith(EN_HEAD)
    assert ZH_HEAD not in llm._system()


def test_detecting_on_the_raw_envelope_still_reports_english():
    """Guard the regression: the raw blob is the trap, not the messages."""
    blob = _chat_blob("我住在北京海淀区，平时用 Zoom 开会。")
    assert is_chinese(content=blob) is False
    assert is_chinese(messages=json.loads(blob)) is True


def test_plain_text_content_still_routes_on_the_raw_string():
    """Non-chat callers pass plain text; that path must not change."""
    llm = _CapturingLLM()
    _extract(llm, "我叫小明，住在北京，喜欢喝美式咖啡。")
    assert llm._system().startswith(ZH_HEAD)

    llm_en = _CapturingLLM()
    _extract(llm_en, "My name is Bob, I live in Seattle and I love hiking.")
    assert llm_en._system().startswith(EN_HEAD)
