"""
Tool 抽象基类 + 注册表 + 调度器。

设计原则：
- Schema 用 OpenAI function calling 兼容结构（便于未来真正走原生 function calling）
- ToolHandler.execute() 异步，接收 arguments + context（由调用方注入，含 user_id / agent_id
  等业务隔离信息；handler 不得自己假设这些来自哪里）
- 同一个 Tool 可以被多个 agent 共享，由调用方决定注册哪些工具到 LLM
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ================================================================
# 数据结构
# ================================================================

@dataclass
class ToolDefinition:
    """
    Tool 的 schema 定义。

    遵循 OpenAI function calling 风格：
      {
        "name": "my_tool",
        "description": "...",
        "parameters": {
            "type": "object",
            "properties": {...},
            "required": [...]
        }
      }
    """
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_openai_schema(self) -> Dict[str, Any]:
        """转为 OpenAI function calling tools 数组中的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def to_prompt_snippet(self) -> str:
        """
        转为可嵌入 prompt 的简洁描述（用于 function-calling 不可用时的 prompt 模拟方案）。
        """
        params_json = json.dumps(self.parameters or {"type": "object", "properties": {}}, ensure_ascii=False, indent=2)
        return f"- **{self.name}**: {self.description}\n  parameters schema:\n{params_json}"


@dataclass
class ToolCall:
    """LLM 发起的一次 tool 调用请求。"""
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""   # LLM function-calling API 返回的 call id，prompt 模拟时可为空


@dataclass
class ToolResult:
    """tool 执行结果。"""
    success: bool
    tool_name: str = ""
    call_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# ================================================================
# 抽象接口
# ================================================================

class ToolHandler(ABC):
    """
    Tool 执行器抽象接口。

    子类需要实现：
    - definition() → ToolDefinition（描述 schema）
    - execute(arguments, context) → ToolResult

    execute 的 context 是调用方注入的 business context，通常包含：
      user_id / agent_id / session_id / request_id
    handler 需要什么就取什么。
    """

    @abstractmethod
    def definition(self) -> ToolDefinition:
        ...

    @property
    def name(self) -> str:
        return self.definition().name

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ToolResult:
        ...


# ================================================================
# 注册表 + 调度器
# ================================================================

class ToolRegistry:
    """
    Tool 注册表。

    一个 agent 按需创建一个 ToolRegistry 实例，注册所需的 tools，
    然后把 schemas 传给 LLM，再用 dispatch() 执行 LLM 返回的 tool_calls。
    """

    def __init__(self):
        self._handlers: Dict[str, ToolHandler] = {}

    def register(self, handler: ToolHandler) -> None:
        name = handler.name
        if name in self._handlers:
            logger.warning(f"[tools] handler '{name}' already registered, overriding")
        self._handlers[name] = handler

    def get(self, name: str) -> Optional[ToolHandler]:
        return self._handlers.get(name)

    def names(self) -> List[str]:
        return list(self._handlers.keys())

    def definitions(self) -> List[ToolDefinition]:
        return [h.definition() for h in self._handlers.values()]

    def openai_schemas(self) -> List[Dict[str, Any]]:
        return [d.to_openai_schema() for d in self.definitions()]

    def prompt_snippets(self) -> str:
        """拼接所有 tools 的 prompt snippet，用于 prompt 模拟方案。"""
        if not self._handlers:
            return ""
        return "\n".join(d.to_prompt_snippet() for d in self.definitions())

    async def dispatch(self, call: ToolCall, context: Dict[str, Any]) -> ToolResult:
        """
        执行一次 tool_call。

        未知 tool name → 返回 success=False 的 ToolResult，而不是抛异常；
        由调用方自己决定要不要把错误回传给 LLM 或是吞掉 warning。
        """
        handler = self._handlers.get(call.name)
        if handler is None:
            msg = f"tool '{call.name}' not registered"
            logger.warning(f"[tools] dispatch failed: {msg}")
            return ToolResult(
                success=False,
                tool_name=call.name,
                call_id=call.call_id,
                error=msg,
            )
        try:
            result = await handler.execute(call.arguments or {}, context or {})
            result.tool_name = call.name
            result.call_id = call.call_id
            return result
        except Exception as e:
            logger.error(f"[tools] dispatch '{call.name}' raised: {e}", exc_info=True)
            return ToolResult(
                success=False,
                tool_name=call.name,
                call_id=call.call_id,
                error=str(e),
            )


# ================================================================
# 解析工具：从 LLM 输出里提取 tool_calls
# ================================================================

def parse_tool_calls_from_json(raw: Any) -> List[ToolCall]:
    """
    从 LLM JSON 输出里的 `tool_calls` 字段解析出 ToolCall 列表。

    兼容两种常见格式：
    1) OpenAI 风格：[{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}]
       其中 arguments 可能是 JSON 字符串
    2) 简化风格：[{"name": "...", "arguments": {...}}]
    """
    calls: List[ToolCall] = []
    if not raw:
        return calls
    if not isinstance(raw, list):
        return calls
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        # OpenAI 风格
        if "function" in entry and isinstance(entry["function"], dict):
            fn = entry["function"]
            name = str(fn.get("name") or "").strip()
            args_raw = fn.get("arguments")
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except json.JSONDecodeError:
                    logger.warning(
                        f"[tools] failed to parse arguments as JSON ({len(args_raw)} chars)"
                    )
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
            call_id = str(entry.get("id") or "")
            if name:
                calls.append(ToolCall(name=name, arguments=args, call_id=call_id))
            continue
        # 简化风格
        name = str(entry.get("name") or "").strip()
        args = entry.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        if name:
            calls.append(ToolCall(name=name, arguments=args, call_id=str(entry.get("id") or "")))
    return calls
