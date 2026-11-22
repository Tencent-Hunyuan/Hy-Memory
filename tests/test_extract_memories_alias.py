"""The chat extractor must accept the legacy plural key "memories" (and the
"text" field per item) that some models still return, instead of silently
extracting nothing. The writer only reads "memory" / "content"."""

import pytest

from hy_memory.agent.extractor import Extractor
from hy_memory.agent.llm_provider import LLMResponse
from hy_memory.config import LLMConfig


class _FixedProvider:
    def __init__(self, content: str):
        self._content = content

    async def complete_messages(self, **kwargs):
        return LLMResponse(
            content=self._content,
            tokens_used=10,
            prompt_tokens=8,
            completion_tokens=2,
            model="mock-alias-model",
            finish_reason="stop",
            response_id="resp-alias-1",
            choices_count=1,
            tool_calls=None,
        )


@pytest.mark.asyncio
async def test_memories_alias_is_normalised():
    # Shape observed from DeepSeek-V4.1-Flash / Muse-Spark: top-level "memories".
    extractor = Extractor(
        _FixedProvider(
            '{"memories": [{"content": "The user uses CocoIndex for 2M lines of '
            'Erlang/C.", "tags": ["cocoindex"], "owner": "user"}], "intentions": []}'
        ),
        LLMConfig(),
    )
    result = await extractor.extract(
        content="I use CocoIndex to index 2M lines of Erlang/C.",
        current_time="2026-11-22T00:00:00",
    )
    assert result.success is True
    assert result.info.get("memory") == [
        {"content": "The user uses CocoIndex for 2M lines of Erlang/C.",
         "tags": ["cocoindex"], "owner": "user"}
    ]


@pytest.mark.asyncio
async def test_text_field_is_copied_to_content():
    # Shape observed from Qwen3-32B at temperature 0.7: "text" instead of "content".
    extractor = Extractor(
        _FixedProvider(
            '{"memory": [{"text": "The user prefers offline-first tools.", '
            '"tags": ["tools"], "owner": "user"}], "intentions": []}'
        ),
        LLMConfig(),
    )
    result = await extractor.extract(
        content="I prefer offline-first tools.",
        current_time="2026-11-22T00:00:00",
    )
    assert result.success is True
    assert result.info["memory"][0]["content"] == "The user prefers offline-first tools."


@pytest.mark.asyncio
async def test_canonical_key_wins_over_alias():
    extractor = Extractor(
        _FixedProvider(
            '{"memory": [{"content": "canonical", "tags": [], "owner": "user"}], '
            '"memories": [{"content": "alias", "tags": [], "owner": "user"}], '
            '"intentions": []}'
        ),
        LLMConfig(),
    )
    result = await extractor.extract(
        content="some input",
        current_time="2026-11-22T00:00:00",
    )
    assert result.success is True
    assert [m["content"] for m in result.info["memory"]] == ["canonical"]
