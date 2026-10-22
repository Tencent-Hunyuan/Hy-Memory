# -*- coding: utf-8 -*-
"""Judge 模块测试"""

import pytest

from hy_memory.coding.judge import (
    classify_messages_scene,
    classify_queries_scene,
    classify_messages_is_coding,
    classify_queries_is_coding,
    _parse_is_coding_json,
)
from hy_memory.pipelines.base import ChatMessage, ToolCall

from tests.coding._helpers import MockLLMProvider, FailingLLMProvider


class TestParseIsCodingJSON:
    def test_pure_json(self):
        assert _parse_is_coding_json('{"is_coding": true}') == {"is_coding": True}

    def test_with_reason(self):
        out = _parse_is_coding_json('{"is_coding": false, "reason": "x"}')
        assert out["is_coding"] is False and out["reason"] == "x"

    def test_embedded_json(self):
        out = _parse_is_coding_json('foo {"is_coding": true} bar')
        assert out["is_coding"] is True

    def test_garbage(self):
        assert _parse_is_coding_json("garbage") is None
        assert _parse_is_coding_json("") is None
        assert _parse_is_coding_json("{}") is None  # 缺 is_coding 字段

    def test_markdown_fence(self):
        out = _parse_is_coding_json('```json\n{"is_coding": true}\n```')
        assert out["is_coding"] is True


@pytest.mark.asyncio
class TestClassifyMessages:
    async def test_three_way_coding(self):
        msgs = [
            ChatMessage(role="user", content="写 Python 脚本生成季度财务报表"),
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="Edit", arguments={"path": "report.py"}),
            ]),
        ]
        llm = MockLLMProvider('{"scene":"coding","reason":"writes code"}')
        assert await classify_messages_scene(msgs, llm) == "coding"

    async def test_three_way_working(self):
        msgs = [
            ChatMessage(role="user", content="整理季度财务数据并生成 PPT 汇报"),
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="Spreadsheet", arguments={}),
                ToolCall(id="2", name="Slides", arguments={}),
            ]),
        ]
        llm = MockLLMProvider('{"scene":"working","reason":"office deliverable"}')
        assert await classify_messages_scene(msgs, llm) == "working"

    async def test_search_tool_smalltalk_stays_chat(self):
        msgs = [
            ChatMessage(role="user", content="搜一下上海今天天气，咱们随便聊聊穿什么"),
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="web_search", arguments={"query": "上海天气"}),
            ]),
            ChatMessage(role="tool", content="晴，25 度", tool_name="web_search"),
        ]
        llm = MockLLMProvider('{"scene":"chat","reason":"incidental lookup"}')
        assert await classify_messages_scene(msgs, llm) == "chat"
        prompt = llm.calls[-1]["prompt"]
        assert "Search/browse tools do NOT make a task working" in prompt

    async def test_coding_path(self):
        msgs = [
            ChatMessage(role="user", content="重构 auth 用 JWT"),
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="Read", arguments={"path": "a"}),
            ]),
            ChatMessage(role="tool", content="ok", tool_call_id="1", tool_name="Read"),
        ]
        llm = MockLLMProvider('{"is_coding": true, "reason": "engineering"}')
        result = await classify_messages_is_coding(msgs, llm)
        assert result is True

    async def test_chat_path(self):
        msgs = [ChatMessage(role="user", content="你好")]
        llm = MockLLMProvider('{"is_coding": false}')
        result = await classify_messages_is_coding(msgs, llm)
        assert result is False

    async def test_failsafe_on_garbage(self):
        msgs = [ChatMessage(role="user", content="x")]
        llm = MockLLMProvider("not json")
        result = await classify_messages_is_coding(msgs, llm)
        assert result is False

    async def test_failsafe_on_llm_error(self):
        msgs = [ChatMessage(role="user", content="x")]
        result = await classify_messages_is_coding(msgs, FailingLLMProvider())
        assert result is False

    async def test_empty_messages(self):
        result = await classify_messages_is_coding([], MockLLMProvider("{}"))
        assert result is False

    async def test_prompt_contains_turn_summary(self):
        msgs = [
            ChatMessage(role="user", content="重构 auth"),
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="Read", arguments={"path": "a"}),
            ]),
        ]
        llm = MockLLMProvider('{"is_coding": true}')
        await classify_messages_is_coding(msgs, llm)
        last_prompt = llm.calls[-1]["prompt"]
        assert "Read" in last_prompt
        assert "重构 auth" in last_prompt or "user=" in last_prompt

    async def test_long_user_content_keeps_only_head_and_tail(self):
        head = "H" * 200
        middle = "M" * 800
        tail = "T" * 200
        llm = MockLLMProvider('{"scene":"coding"}')

        await classify_messages_scene(
            [ChatMessage(role="user", content=head + middle + tail)],
            llm,
        )

        prompt = llm.calls[-1]["prompt"]
        assert head in prompt
        assert tail in prompt
        assert "M" * 201 not in prompt
        assert "[...800 chars omitted...]" in prompt

    async def test_user_content_at_limit_is_not_truncated(self):
        content = "A" * 1_000
        llm = MockLLMProvider('{"scene":"chat"}')

        await classify_messages_scene([ChatMessage(role="user", content=content)], llm)

        assert content in llm.calls[-1]["prompt"]


@pytest.mark.asyncio
class TestClassifyQueries:
    async def test_three_way_query_scene(self):
        llm = MockLLMProvider('{"scene":"coding"}')
        assert await classify_queries_scene(["帮我修复这个 Python traceback"], llm) == "coding"

    async def test_target_is_last(self):
        llm = MockLLMProvider('{"is_coding": true}')
        result = await classify_queries_is_coding(
            ["凭证在哪", "上次发布命令", "为什么慢"], llm
        )
        assert result is True
        prompt = llm.calls[-1]["prompt"]
        assert "为什么慢" in prompt
        assert "target" in prompt

    async def test_single_query(self):
        llm = MockLLMProvider('{"is_coding": false}')
        result = await classify_queries_is_coding(["用户喜欢什么"], llm)
        assert result is False

    async def test_empty(self):
        result = await classify_queries_is_coding([], MockLLMProvider("{}"))
        assert result is False

    async def test_failsafe(self):
        result = await classify_queries_is_coding(["x"], FailingLLMProvider())
        assert result is False
