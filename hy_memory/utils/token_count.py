"""
Input token counting via tiktoken.

仅用于「估算用户 input 的 token 数」（content / messages 原文），不追求与具体
LLM 的 tokenizer 完全对齐——cl100k_base 对中英文都比 len() 准得多，足够做用量
统计/计费近似。encoder 全局缓存（首次加载 BPE 词表 ~3s，之后 encode 仅亚毫秒级）。
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_ENCODING = "cl100k_base"
_encoder = None  # 懒加载 + 进程内缓存


def _get_encoder():
    """返回缓存的 tiktoken encoder；tiktoken 不可用时返回 None。"""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        import tiktoken
        _encoder = tiktoken.get_encoding(_DEFAULT_ENCODING)
    except Exception as e:
        logger.debug(f"[token_count] tiktoken unavailable, fallback to char heuristic: {e}")
        _encoder = False  # 标记不可用，避免反复重试
    return _encoder


def count_tokens(text: str) -> int:
    """对一段文本计 token 数。tiktoken 不可用时退化为 len(text)//4 粗估。"""
    if not text:
        return 0
    enc = _get_encoder()
    if enc:
        try:
            # Quoted control-token spellings are ordinary input text.
            return len(enc.encode(text, disallowed_special=()))
        except Exception as e:
            logger.debug(f"[token_count] encode failed, char fallback: {e}")
    return max(1, len(text) // 4)


def count_messages_tokens(messages: List["object"]) -> int:
    """
    对 messages 列表计 input token 数。

    仅计纯 input 文本（role + content 的拼接），不加 chat 模板的固定开销。
    messages 元素可为带 .role/.content 属性的对象，或 {"role","content"} dict。
    """
    if not messages:
        return 0
    parts: List[str] = []
    for m in messages:
        role = getattr(m, "role", None) if not isinstance(m, dict) else m.get("role")
        content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
        if content:
            parts.append(f"{role or ''}: {content}")
    return count_tokens("\n".join(parts))


def count_input_tokens(content: Optional[str], messages: Optional[List["object"]]) -> int:
    """add 接口统一入口：有 messages 则按 messages 计，否则按 content 计。"""
    if messages:
        return count_messages_tokens(messages)
    return count_tokens(content or "")
