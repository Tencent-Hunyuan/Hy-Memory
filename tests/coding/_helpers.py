# -*- coding: utf-8 -*-
"""测试辅助类（不被 pytest 收集为 test）"""

import hashlib
import json
from typing import Any, Dict, List, Union


class MockLLMProvider:
    """
    用预先准备好的回复序列响应 complete() / complete_messages()。每次 pop 一个；用完后重复最后一个。

    response 项支持两种形式（向后兼容）:
      - str               : 纯文本回复（老用法）
      - Dict[str, Any]    : 结构化回复，可含 tool_calls，用于测试 tool calling agent
                            形如 {"content": "...", "tool_calls": [{"id": ..., "function": {...}}]}
    """

    def __init__(self, responses):
        if isinstance(responses, (str, dict)):
            responses = [responses]
        self._responses: List[Union[str, Dict[str, Any]]] = list(responses)
        self.calls: List[Dict[str, Any]] = []
        self._idx = 0
        # 兼容 coding 模块对 _llm_config.temperature 的 fallback 探测
        self._llm_config = type("_FakeCfg", (), {"temperature": 0.1, "model": "mock"})()

    def _next(self) -> Union[str, Dict[str, Any]]:
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
        else:
            r = self._responses[-1]
        return r

    def _build_response(self, raw: Union[str, Dict[str, Any]]):
        from hy_memory.agent.llm_provider import LLMResponse
        if isinstance(raw, str):
            return LLMResponse(content=raw, tokens_used=10)
        # Dict
        content = raw.get("content", "") or ""
        tool_calls = raw.get("tool_calls")
        # tool_calls 内的 function.arguments 既允许 dict 也允许 JSON string；统一成 OpenAI 风格 string
        if tool_calls:
            normalized = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                normalized.append({
                    "id": tc.get("id", "") or "",
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": args or "{}",
                    },
                })
            tool_calls = normalized
        return LLMResponse(
            content=content,
            tokens_used=10,
            tool_calls=tool_calls,
            finish_reason=str(raw.get("finish_reason", "") or ""),
        )

    async def complete(self, prompt, max_tokens=500, temperature=0.7, tools=None, tool_choice=None, **kwargs):
        self.calls.append({
            "method": "complete",
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tools": tools,
            "tool_choice": tool_choice,
        })
        return self._build_response(self._next())

    async def complete_messages(self, messages, max_tokens=500, temperature=0.7,
                                tools=None, tool_choice=None, **kwargs):
        self.calls.append({
            "method": "complete_messages",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tools": tools,
            "tool_choice": tool_choice,
        })
        return self._build_response(self._next())


class FailingLLMProvider:
    """每次 complete / complete_messages 都 raise，用于 fail-safe 路径测试"""

    def __init__(self):
        self.calls = 0
        self._llm_config = type("_FakeCfg", (), {"temperature": 0.1, "model": "mock"})()

    async def complete(self, **kwargs):
        self.calls += 1
        raise RuntimeError("mock LLM failure")

    async def complete_messages(self, **kwargs):
        self.calls += 1
        raise RuntimeError("mock LLM failure")


class MockEmbedService:
    async def embed(self, text):
        return self._hash_to_vec(text)

    async def embed_batch(self, texts):
        return [self._hash_to_vec(t) for t in texts]

    async def embed_with_meta(self, text, use_cache: bool = True):
        return self._hash_to_vec(text), {"cache_hit": False, "mock": True}

    async def embed_batch_with_meta(self, texts, use_cache: bool = True):
        vecs = [self._hash_to_vec(t) for t in texts]
        metas = [{"cache_hit": False, "mock": True} for _ in texts]
        return vecs, metas

    async def embed_queued(self, text, use_cache: bool = True):
        return self._hash_to_vec(text)

    async def embed_queued_with_meta(self, text, use_cache: bool = True):
        return self._hash_to_vec(text), {"cache_hit": False, "mock": True}

    @staticmethod
    def _hash_to_vec(t: str, dims: int = 1536):
        h = hashlib.md5(t.encode()).digest()  # 16 字节
        v = [b / 255.0 for b in h] * (dims // 16)
        return v[:dims]
