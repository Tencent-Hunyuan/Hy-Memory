"""
Language detection utility for prompt selection.

Uses a two-tier strategy:
1. Fast path: Unicode character-range heuristic (CJK ideographs → zh)
2. Fallback: langdetect (for non-CJK ambiguous cases)

This avoids langdetect's known weakness with short Chinese text.
"""

import json
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

# CJK Unified Ideographs ranges
_CJK_RE = re.compile(
    r'[\u4e00-\u9fff'        # CJK Unified Ideographs
    r'\u3400-\u4dbf'         # CJK Extension A
    r'\u2e80-\u2eff'         # CJK Radicals Supplement
    r'\uf900-\ufaff'         # CJK Compatibility Ideographs
    r'\ufe30-\ufe4f'         # CJK Compatibility Forms
    r'\U00020000-\U0002a6df' # CJK Extension B
    r']'
)

_LEADING_ROLE_PREFIX_RE = re.compile(
    r"^\s*(?:\[[^\]\n]{1,32}\]|user|assistant|system|tool)\s*:\s*",
    re.IGNORECASE,
)
_LEADING_LANGUAGE_CHARS = 10

# Lazy-load langdetect
_langdetect_initialized = False


def _ensure_langdetect():
    global _langdetect_initialized
    if _langdetect_initialized:
        return True
    try:
        from langdetect import DetectorFactory
        DetectorFactory.seed = 0
        _langdetect_initialized = True
        return True
    except ImportError:
        logger.warning(
            "langdetect not installed. Falling back to heuristic only. "
            "Install with: pip install langdetect"
        )
        return False


def _chinese_char_ratio(text: str) -> float:
    """Return the ratio of CJK characters in text."""
    if not text:
        return 0.0
    # Only count non-whitespace, non-punctuation chars
    chars = re.sub(r'\s+', '', text)
    if not chars:
        return 0.0
    cjk_count = len(_CJK_RE.findall(chars))
    return cjk_count / len(chars)


def _leading_language_window(text: str, limit: int = _LEADING_LANGUAGE_CHARS) -> str:
    """Return the first language-bearing characters, ignoring a role prefix.

    Agent traces often start with a short natural-language instruction followed
    by long paths, code, JSON, or error output.  Looking at the complete string
    lets that machine text drown out the user's language.  The leading window
    captures the instruction instead, while skipping whitespace and punctuation.
    """
    value = _LEADING_ROLE_PREFIX_RE.sub("", str(text or ""), count=1)
    chars = [char for char in value if char.isalnum()]
    return "".join(chars[:limit])


def detect_language(text: str) -> str:
    """
    Detect the language of text content.

    Strategy:
    1. Inspect the first 10 language-bearing characters. Any CJK there → "zh";
       a complete Latin/digit window → "en".
    2. Otherwise, if the full-text CJK ratio >= 0.3 → "zh".
    3. Use langdetect for the remaining ambiguous cases.

    Returns:
        "zh" for Chinese, "en" for English, or other ISO 639-1 codes.
        Returns "en" as fallback if detection fails.
    """
    if not text or not text.strip():
        return "en"

    text = text.strip()

    leading = _leading_language_window(text)
    if _CJK_RE.search(leading):
        return "zh"
    if len(leading) >= _LEADING_LANGUAGE_CHARS and leading.isascii():
        return "en"

    # Fast path: CJK character ratio
    ratio = _chinese_char_ratio(text)
    if ratio >= 0.3:
        return "zh"
    if ratio == 0:
        # No CJK chars at all — likely English or other Latin-based
        # Use langdetect for accuracy if available, else default "en"
        if _ensure_langdetect():
            try:
                from langdetect import detect
                lang = detect(text)
                if lang.startswith("zh"):
                    return "zh"
                return lang
            except Exception:
                pass
        return "en"

    # Mixed content (0 < ratio < 0.3): use langdetect
    if _ensure_langdetect():
        try:
            from langdetect import detect
            lang = detect(text)
            if lang.startswith("zh"):
                return "zh"
            return lang
        except Exception:
            pass

    # If langdetect unavailable and some CJK present, lean toward zh
    return "zh" if ratio > 0.1 else "en"


def _spoken_text(value: str) -> str:
    """Ignore text-block metadata when a message content is structured JSON."""
    text = value.strip()
    if text.startswith("["):
        try:
            blocks = json.loads(text)
        except (ValueError, TypeError):
            pass
        else:
            if (
                isinstance(blocks, list)
                and blocks
                and all(
                    isinstance(block, dict)
                    and isinstance(block.get("type"), str)
                    and block["type"] in {"text", "input_text", "output_text"}
                    and isinstance(block.get("text"), str)
                    for block in blocks
                )
            ):
                return "\n".join(block["text"] for block in blocks)
    return text


def extract_content_for_detection(
    content: str = "",
    messages: Optional[List] = None,
) -> str:
    """
    Extract pure content text for language detection.

    For messages, only extracts the content field (not role/metadata)
    to avoid 'user', 'assistant' etc. biasing the detection toward English.

    Args:
        content: plain text content
        messages: list of {"role": ..., "content": ...} dicts or ChatMessage objects

    Returns:
        Concatenated content text suitable for language detection.
    """
    if content and content.strip():
        value = content.strip()
        # Writer serializes message batches to JSON before calling Extractor.
        # Detect the spoken text, not the leading ASCII keys (role/content),
        # without changing the raw evidence sent to the model.
        if value.startswith("["):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                pass
            else:
                if (
                    isinstance(decoded, list)
                    and decoded
                    and all(
                        isinstance(item, dict)
                        and isinstance(item.get("role"), str)
                        and isinstance(item.get("content"), str)
                        for item in decoded
                    )
                ):
                    # In a mixed batch, the latest user turn is the clearest
                    # instruction for the language; assistant/tool metadata
                    # must not decide it first.
                    user_text = [
                        _spoken_text(item["content"])
                        for item in decoded
                        if item["role"] == "user" and item["content"].strip()
                    ]
                    if user_text:
                        return user_text[-1]
                    spoken = "\n".join(_spoken_text(item["content"]) for item in decoded)
                    if spoken.strip():
                        return spoken
        return _spoken_text(value)

    if messages:
        parts = []
        user_parts = []
        for msg in messages:
            if isinstance(msg, dict):
                c, role = msg.get("content", ""), msg.get("role")
            elif hasattr(msg, "content"):
                c, role = msg.content, getattr(msg, "role", None)
            else:
                continue
            if isinstance(c, str) and c.strip():
                text = _spoken_text(c)
                parts.append(text)
                if role == "user":
                    user_parts.append(text)
        return user_parts[-1] if user_parts else "\n".join(parts)

    return ""


def is_chinese(
    content: str = "",
    messages: Optional[List] = None,
) -> bool:
    """
    Quick check: is the input content Chinese?

    Args:
        content: plain text content
        messages: optional messages list (only content fields are used)

    Returns:
        True if detected language is Chinese.
    """
    sample = extract_content_for_detection(content, messages)
    if not sample:
        return False
    return detect_language(sample) == "zh"
