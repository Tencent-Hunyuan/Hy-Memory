"""
Agent Memory - LLM Provider

统一的 LLM 调用接口，支持多种模型后端。

功能：
- 统一的调用接口
- 多模型支持（OpenAI、Hunyuan 等）
- 请求重试和错误处理
- Token 统计
- 模型路由

示例：
    provider = LLMProvider(config)
    
    # 调用 LLM。不传 temperature —— 由 config.llm 统一决定（含"完全不传"的情形，
    # 见 _resolve_temperature）。调用点写死会绕过用户配置。
    response = await provider.complete(
        prompt="请总结以下内容：...",
        max_tokens=500,
    )
    
    print(response.content)
    print(response.tokens_used)
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import asyncio
import hashlib
import logging
import os
import weakref

from ..config import MemoryConfig
from ..core.workload import llm_slot

logger = logging.getLogger(__name__)


def _log_llm_content_enabled() -> bool:
    """Return whether sensitive LLM prompt/response bodies may be logged."""
    return os.getenv("MEMORY_LOG_LLM_CONTENT", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _content_fingerprint(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]


class LLMRetryError(RuntimeError):
    """LLM 重试全部失败。attempts 携带每一次 attempt 的错误明细，供上层落进
    EXTRACT/RECONCILE 的 pipeline trace（parsed.attempts）。"""

    def __init__(self, message: str, attempts: List[Dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts


def _attempt_record(attempt_no: int, exc: BaseException) -> Dict[str, Any]:
    """把单次 attempt 的异常规范成可落库的 dict（含 HTTP 状态码等常见字段）。"""
    import time as _t
    rec: Dict[str, Any] = {
        "attempt": attempt_no,
        "error_type": type(exc).__name__,
        "error": str(exc)[:2000],
        "ts": _t.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # 尽力提取 HTTP 状态码 / 响应体（OpenAI / requests / httpx 风格）
    for _attr in ("status_code", "code"):
        _v = getattr(exc, _attr, None)
        if _v is not None:
            rec["status_code"] = _v
            break
    _resp = getattr(exc, "response", None)
    if _resp is not None:
        try:
            rec.setdefault("status_code", getattr(_resp, "status_code", None))
            _body = getattr(_resp, "text", None)
            if _body:
                body = str(_body)
                rec["response_body"] = (
                    body[:1000] if _log_llm_content_enabled()
                    else f"<redacted len={len(body)} sha256={_content_fingerprint(body)}>"
                )
        except Exception:
            pass
    return rec


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """Retry transient transport errors and 408/429/5xx; fail fast on other 4xx."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 408 or status == 429 or status >= 500
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)) or any(
        token in str(exc).lower()
        for token in ("timeout", "timed out", "connection", "read operation")
    )


def _build_retry_error(
    max_retries: int, model: str, backend: str,
    attempts: List[Dict[str, Any]], last_exc: Optional[BaseException],
) -> "LLMRetryError":
    actual_attempts = len(attempts)
    msg = (
        f"LLM call failed after {actual_attempts} attempt(s) "
        f"(configured max={max_retries}) "
        f"(model={model} backend={backend}); "
        f"last error: {type(last_exc).__name__}: {last_exc}"
    )
    err = LLMRetryError(msg, attempts)
    err.__cause__ = last_exc
    return err


def _parse_tool_calls_list(raw_calls: list, _json) -> List[Dict[str, Any]]:
    """
    解析 tool_calls 列表。

    保留原始完整结构，只规范化 arguments（dict → JSON str）。
    """
    result = []
    for tc in raw_calls:
        tc_id = tc.get("id") or ""
        if not tc_id:
            logger.error(
                f"[llm] tool_call missing 'id'! raw: "
                f"{_json.dumps(tc, ensure_ascii=False, default=str)[:500]}"
            )

        # 保留原始 tc 完整结构，只规范化 arguments
        tc_copy = dict(tc)  # 浅拷贝保留所有字段（signature 等）
        fn = tc_copy.get("function", {})
        if isinstance(fn, dict):
            args = fn.get("arguments", "")
            if isinstance(args, dict):
                fn["arguments"] = _json.dumps(args, ensure_ascii=False)
            tc_copy["function"] = fn

        # 确保基础字段存在
        tc_copy.setdefault("id", tc_id)
        tc_copy.setdefault("type", "function")

        result.append(tc_copy)
    return result


def _serialize_tool_calls(tool_calls_raw) -> Optional[List[Dict[str, Any]]]:
    """OpenAI SDK 的 tool_call 对象 → 纯 dict 列表（便于传递 / 落库）。

    单条序列化失败只跳过该条并告警，不让整次 LLM 调用失败。
    """
    if not tool_calls_raw:
        return None
    result: List[Dict[str, Any]] = []
    for tc in tool_calls_raw:
        try:
            fn = getattr(tc, "function", None)
            result.append({
                "id": getattr(tc, "id", "") or "",
                "type": getattr(tc, "type", "function") or "function",
                "function": {
                    "name": getattr(fn, "name", "") if fn else "",
                    "arguments": getattr(fn, "arguments", "") if fn else "",
                },
            })
        except Exception as e:
            logger.warning(f"[llm] failed to serialize tool_call: {e}")
    return result or None


class LLMBackend(Enum):
    """LLM protocol backend."""
    OPENAI = "openai"


@dataclass
class LLMResponse:
    """
    LLM 响应
    """
    content: str
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    finish_reason: str = ""
    response_id: str = ""
    choices_count: int = 0
    # 推理模型（hy3 / kimi-k2.5 / deepseek-v4 等）的思维链内容与 token 数。
    # content 为空但 reasoning_tokens 打满 max_tokens 时，说明推理吃光了输出配额，
    # 需要关闭 thinking（hy3: reasoning_effort=no_think）或调大 agent_max_tokens。
    reasoning_content: str = ""
    reasoning_tokens: int = 0
    # function/tool calling：当 LLM 决定调用工具时填充，结构为 OpenAI 风格：
    #   [{"id": "...", "type": "function", "function": {"name": "...", "arguments": "<json str>"}}]
    # 调用方应使用 tools.base.parse_tool_calls_from_json 解析
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class LLMConfig:
    """
    LLM 配置
    """
    backend: LLMBackend = LLMBackend.OPENAI
    model: str = "gpt-3.5-turbo"
    api_key: str = ""
    base_url: str = ""
    # None = **请求里完全不带 temperature**。
    # 有些网关（如 Opus 5）只接受 temperature=1 或干脆不传，传 0/0.1/0.3 会 400。
    temperature: Optional[float] = 0.7
    timeout: int = 30
    max_retries: int = 5
    retry_delay: float = 1.0  # 初始 delay，每次递增
    extra_headers: Optional[Dict[str, str]] = None
    extra_body: Optional[Dict[str, Any]] = None


class LLMProvider:
    """
    LLM 提供器

    统一的 LLM 调用接口。

    _extract_response: 安全提取 choices[0].message，当 API 返回空 choices 时给出
    明确错误信息而非 'NoneType' object is not subscriptable。
    """

    @staticmethod
    def _extract_response(response) -> tuple:
        """
        从 OpenAI 兼容 response 中提取 message、usage、finish_reason。
        当 choices 为空时抛出明确的 ValueError。

        Returns:
            (msg, usage, finish_reason, choices_count)
        """
        choices = getattr(response, "choices", None)
        if not choices:
            # 尝试拿到更多诊断信息
            raw = ""
            try:
                raw = str(response)[:500]
            except Exception:
                pass
            raise ValueError(
                f"LLM returned empty choices (no completion). "
                f"Response: {raw}"
            )
        msg = choices[0].message
        usage = response.usage
        finish_reason = choices[0].finish_reason
        return msg, usage, finish_reason, len(choices)

    @staticmethod
    def _extract_reasoning(msg, usage) -> tuple:
        """
        提取推理模型的思维链内容与 token 数。

        不同平台字段位置不一：
        - hy3 / kimi-k2.5：message.reasoning_content，兼容
          message.provider_specific_fields.reasoning_content
        - reasoning_tokens：usage.completion_tokens_details.reasoning_tokens，
          部分网关平铺在 usage.reasoning_tokens

        Returns:
            (reasoning_content, reasoning_tokens)
        """
        rc = getattr(msg, "reasoning_content", None) or ""
        if not rc:
            psf = getattr(msg, "provider_specific_fields", None)
            if isinstance(psf, dict):
                rc = psf.get("reasoning_content") or ""

        rt = 0
        if usage is not None:
            details = getattr(usage, "completion_tokens_details", None)
            if details is not None:
                rt = getattr(details, "reasoning_tokens", 0) or 0
            if not rt:
                rt = getattr(usage, "reasoning_tokens", 0) or 0
        return rc, int(rt or 0)

    def __init__(self, config: Optional[MemoryConfig] = None):
        """
        初始化 LLM Provider
        
        Args:
            config: 配置对象
        """
        self.config = config or MemoryConfig.from_env()
        self._llm_config = self._build_llm_config()
        self._client = None
        self._clients = weakref.WeakKeyDictionary()
        
        # 统计
        self._total_calls = 0
        self._total_tokens = 0
        self._errors = 0
        
        logger.debug(f"LLMProvider initialized, backend={self._llm_config.backend.value}")
    
    def _build_llm_config(self) -> LLMConfig:
        """从配置构建 LLM 配置"""
        llm = self.config.llm
        return LLMConfig(
            backend=LLMBackend.OPENAI,
            model=llm.model,
            api_key=llm.api_key,
            base_url=llm.base_url,
            # 不再把 None 兜成 0.7：None 是"不传"的合法表达，兜掉会让
            # MEMORY_LLM_TEMPERATURE=none 这类配置失效
            temperature=(
                None if getattr(llm, "omit_temperature", False)
                else llm.temperature
            ),
            timeout=llm.timeout or 120,
            max_retries=llm.max_retries if llm.max_retries is not None else 5,
            retry_delay=llm.retry_delay if llm.retry_delay is not None else 1.0,
            extra_headers=llm.extra_headers,
            extra_body=llm.extra_body,
        )
    
    def _resolve_temperature(self, temperature: Optional[float]) -> Optional[float]:
        """把调用点的 temperature 收敛到**配置**上。

        规则：调用点传了就用它；没传（None）就用 `config.llm.temperature`。
        配置本身为 None 时返回 None，`_chat_completions` 会把该参数整个省略。

        为什么必须收敛到一处：此前各调用点自己写死 0.1/0.2/0.3/0.4，`complete()` 的
        默认值又是 0.7 —— 结果用户配的 `MEMORY_LLM_TEMPERATURE` 只对少数几条路径生效。
        换成只接受特定 temperature 的网关（Opus 5 只收 1 或不传）时，这些写死的路径全 400。
        """
        if temperature is not None:
            return temperature
        return getattr(self._llm_config, "temperature", None)

    async def complete(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: Optional[float] = None,
        stop: List[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        **kwargs
    ) -> LLMResponse:
        """
        调用 LLM 完成文本生成

        Args:
            prompt: 提示词
            max_tokens: 最大 token 数
            temperature: 温度参数
            stop: 停止词列表
            tools: 可选，OpenAI function-calling 风格的 tools schema 列表
                   （[{"type":"function","function":{"name":..., "parameters":...}}, ...]）。
                   传入时 LLM 可以在响应中返回 tool_calls，由调用方决定如何 dispatch。
            tool_choice: 可选，OpenAI 风格 tool_choice（"auto" / "none" / {"type":"function","function":{"name":...}}）
            **kwargs: 其他参数

        Returns:
            LLM 响应（成功时 response.tool_calls 可能非空）
        """
        _last_exc: Optional[BaseException] = None
        _attempts: List[Dict[str, Any]] = []
        for attempt in range(self._llm_config.max_retries):
            try:
                if attempt == 0:
                    logger.debug(
                        f"[llm] model={self._llm_config.model} "
                        f"backend={self._llm_config.backend.value} "
                        f"base_url={self._llm_config.base_url or 'default'} "
                        f"prompt_len={len(prompt)} tools={len(tools) if tools else 0}"
                    )
                if _log_llm_content_enabled():
                    logger.debug(f"[llm] prompt:\n{prompt}")
                else:
                    logger.debug(
                        "[llm] prompt content hidden; len=%d sha256=%s",
                        len(prompt), _content_fingerprint(prompt),
                    )

                response = await self._call_llm(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop=stop,
                    tools=tools,
                    tool_choice=tool_choice,
                    **kwargs
                )

                self._total_calls += 1
                self._total_tokens += response.tokens_used

                logger.debug(
                    f"[llm] response: {len(response.content)} chars, "
                    f"tokens={response.tokens_used}, "
                    f"tool_calls={len(response.tool_calls) if response.tool_calls else 0}"
                )
                if _log_llm_content_enabled():
                    logger.debug(f"[llm] response content: {response.content!r}")
                else:
                    logger.debug(
                        "[llm] response content hidden; len=%d sha256=%s",
                        len(response.content), _content_fingerprint(response.content),
                    )

                return response

            except Exception as e:
                self._errors += 1
                _last_exc = e
                _attempts.append(_attempt_record(attempt + 1, e))
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{self._llm_config.max_retries}): "
                    f"{type(e).__name__}: {e}"
                )

                if not _is_retryable_llm_error(e):
                    break

                if attempt < self._llm_config.max_retries - 1:
                    delay = self._llm_config.retry_delay * (attempt + 1)
                    await asyncio.sleep(delay)

        # 所有重试都失败 —— 异常携带每一次 attempt 的错误（供上层落进 EXTRACT trace）
        raise _build_retry_error(
            self._llm_config.max_retries, self._llm_config.model,
            self._llm_config.backend.value, _attempts, _last_exc,
        )

    def _resolve_endpoint(self) -> tuple:
        """Return the configured OpenAI-compatible endpoint unchanged."""
        return self._llm_config.api_key, (self._llm_config.base_url or None)

    async def _call_llm(
        self,
        prompt: str,
        max_tokens: int,
        temperature: Optional[float] = None,
        stop: List[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        **kwargs
    ) -> LLMResponse:
        """单轮 prompt → 统一走 Chat Completions。

        The configured endpoint is expected to implement Chat Completions.
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": ""},
            {"role": "user", "content": prompt},
        ]
        return await self._chat_completions(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def _chat_completions(
        self,
        messages: List[Dict[str, Any]],
        *,
        max_tokens: int,
        temperature: Optional[float] = None,
        stop: Optional[List[str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> LLMResponse:
        """OpenAI Chat Completions 的唯一实现。

        All supported endpoints use the OpenAI-compatible Chat Completions API.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai is required. Install with: pip install openai")

        api_key, base_url = self._resolve_endpoint()
        # complete()/complete_messages() already own the retry loop. Disable the
        # OpenAI SDK's hidden retries so one configured attempt maps to one HTTP
        # request and the configured timeout is actually observable.
        loop = asyncio.get_running_loop()
        base_api_key = (api_key or "").split("?", 1)[0]
        client_key = (base_url or "", base_api_key, self._llm_config.timeout)
        clients = self._clients.setdefault(loop, {})
        client = clients.get(client_key)
        if client is None:
            client_options: Dict[str, Any] = {
                "api_key": base_api_key,
                "base_url": base_url,
                "timeout": self._llm_config.timeout,
                "max_retries": 0,
            }
            transport_factory = getattr(self.config, "http_transport_factory", None)
            if callable(transport_factory):
                import httpx

                client_options["http_client"] = httpx.AsyncClient(
                    transport=transport_factory()
                )
            client = AsyncOpenAI(
                **client_options,
            )
            clients[client_key] = client

        # 确保有 system 消息（部分内部平台要求）
        if not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": ""}] + list(messages)

        kwargs: Dict[str, Any] = {
            "model": self._llm_config.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        # 解析到 None → **整个参数不发**（Opus 5 等网关只接受 1 或不传）
        _temp = self._resolve_temperature(temperature)
        if _temp is not None:
            kwargs["temperature"] = _temp
        if stop:
            kwargs["stop"] = stop
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        request_headers = dict(self._llm_config.extra_headers or {})
        if api_key:
            request_headers["Authorization"] = f"Bearer {api_key}"
        if request_headers:
            kwargs["extra_headers"] = request_headers
        if self._llm_config.extra_body:
            kwargs["extra_body"] = self._llm_config.extra_body

        try:
            async with llm_slot():
                response = await client.chat.completions.create(**kwargs)
        except Exception:
            # A transport failure may poison a pooled connection.  Evict only
            # this endpoint so the outer retry creates a clean client.
            clients.pop(client_key, None)
            try:
                closed = client.close()
                if asyncio.iscoroutine(closed):
                    await closed
            except Exception:
                pass
            raise

        msg, usage, _finish, _choices_count = self._extract_response(response)
        _reasoning_content, _reasoning_tokens = self._extract_reasoning(msg, usage)

        return LLMResponse(
            content=msg.content or "",
            tokens_used=usage.total_tokens if usage else 0,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            model=getattr(response, "model", None) or self._llm_config.model,
            finish_reason=_finish,
            response_id=getattr(response, "id", "") or "",
            choices_count=_choices_count,
            reasoning_content=_reasoning_content,
            reasoning_tokens=_reasoning_tokens,
            tool_calls=_serialize_tool_calls(getattr(msg, "tool_calls", None)),
        )

    async def close(self) -> None:
        clients = [
            client
            for per_loop in list(self._clients.values())
            for client in list(per_loop.values())
        ]
        self._clients.clear()
        for client in clients:
            try:
                closed = client.close()
                if asyncio.iscoroutine(closed):
                    await closed
            except Exception:
                pass

    async def complete_messages(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> LLMResponse:
        """
        多轮 messages 调用（支持 tool results 回传）。

        Args:
            messages: 完整的 messages 列表，可包含：
                - {"role": "system", "content": "..."}
                - {"role": "user", "content": "..."}
                - {"role": "assistant", "content": "...", "tool_calls": [...]}
                - {"role": "tool", "tool_call_id": "...", "content": "..."}
            max_tokens, temperature, tools, tool_choice: 同 complete()

        Returns:
            LLMResponse
        """
        _last_exc: Optional[BaseException] = None
        _attempts: List[Dict[str, Any]] = []
        for attempt in range(self._llm_config.max_retries):
            try:
                if attempt == 0:
                    logger.debug(
                        f"[llm] model={self._llm_config.model} "
                        f"backend={self._llm_config.backend.value} "
                        f"base_url={self._llm_config.base_url or 'default'} "
                        f"msgs={len(messages)} tools={len(tools) if tools else 0}"
                    )

                response = await self._chat_completions(
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                    tool_choice=tool_choice,
                )

                self._total_calls += 1
                self._total_tokens += response.tokens_used

                logger.debug(
                    f"[llm] response: {len(response.content)} chars, "
                    f"tokens={response.tokens_used}, "
                    f"tool_calls={len(response.tool_calls) if response.tool_calls else 0}"
                )

                return response

            except Exception as e:
                self._errors += 1
                _last_exc = e
                _attempts.append(_attempt_record(attempt + 1, e))
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{self._llm_config.max_retries}): "
                    f"{type(e).__name__}: {e}"
                )
                if not _is_retryable_llm_error(e):
                    break
                if attempt < self._llm_config.max_retries - 1:
                    delay = self._llm_config.retry_delay * (attempt + 1)
                    await asyncio.sleep(delay)

        raise _build_retry_error(
            self._llm_config.max_retries, self._llm_config.model,
            self._llm_config.backend.value, _attempts, _last_exc,
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 500,
        temperature: Optional[float] = None,
        **kwargs
    ) -> LLMResponse:
        """
        多轮对话
        
        Args:
            messages: 消息列表 [{"role": "user/assistant", "content": "..."}]
            max_tokens: 最大 token 数
            temperature: 温度参数
        
        Returns:
            LLM 响应
        """
        # 将多轮消息转换为单个 prompt
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")
        
        prompt = "\n".join(prompt_parts)
        
        return await self.complete(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_calls": self._total_calls,
            "total_tokens": self._total_tokens,
            "errors": self._errors,
            "avg_tokens_per_call": (
                self._total_tokens / self._total_calls
                if self._total_calls > 0 else 0
            ),
            "error_rate": (
                self._errors / self._total_calls
                if self._total_calls > 0 else 0
            ),
        }
