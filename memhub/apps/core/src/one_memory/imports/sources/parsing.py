import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import JsonValue

from one_memory.input_sanitization import sanitize_model_input

_REMINDER_TAGS = r"additional_data|system[-_]reminder|working_memory_reminder"
_CONTEXT_TAGS = (
    r"user_info|rules|agent_transcripts|attached_files|image_local_path|"
    r"system[-_]notification|pipeline_log"
)
_TAG_ATTRIBUTES = r"""(?:\s+[\w:-]+\s*=\s*(?:"[^"<>]*"|'[^'<>]*'))*\s*"""
_HOST_PREFIX = re.compile(rf"<(?P<tag>{_REMINDER_TAGS}|{_CONTEXT_TAGS}){_TAG_ATTRIBUTES}>")
_REMINDER_START = re.compile(rf"<(?P<tag>{_REMINDER_TAGS}){_TAG_ATTRIBUTES}>")
_USER_QUERY_START = re.compile(r"<(?P<tag>user_query)>")
_QUERY_SUFFIX = re.compile(rf"<(?P<tag>{_REMINDER_TAGS}|image_local_path){_TAG_ATTRIBUTES}>")
HOST_GENERATED_USER_TURN = re.compile(
    r"(?:"
    r"^<dynamic_tool_catalog>|"
    r"^Briefly inform the user about the task result and perform any follow-up actions|"
    r"^Perform any necessary follow-up actions in response to the subagent completion above"
    r")",
    re.IGNORECASE,
)
PIPELINE_JSON_SIGNATURE = re.compile(
    r'^\s*\{[\s\S]*"request_id"\s*:[\s\S]*'
    r'(?:(?:"type"\s*:\s*"(?:write|search|delete|digest)"[\s\S]*"steps"\s*:)|'
    r'(?:"reader"\s*:\s*"client"[\s\S]*"recommended_order"\s*:))',
    re.IGNORECASE,
)


def as_record(value: JsonValue | None) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    return value


def first_string(*values: JsonValue | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def text_content(
    value: JsonValue | None,
    *,
    allowed_block_types: frozenset[str],
) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for item in value:
        record = as_record(item)
        if record is None or record.get("type") not in allowed_block_types:
            continue
        text = record.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts) or None


#: Enough to show what ran and how it went; whole files and logs stay behind.
TOOL_EVIDENCE_CHARACTERS = 1600
TOOL_EVIDENCE_TRUNCATED = " …[truncated]"


def tool_evidence_text(event: dict[str, JsonValue]) -> str | None:
    """A tool call or result as `[name] body`: clipped, and with credentials removed."""
    name = first_string(event.get("name"), event.get("toolName"), event.get("tool_name"))
    body: JsonValue | None = next(
        (
            event[key]
            for key in ("output", "result", "content", "arguments", "input")
            if event.get(key) not in (None, "", [], {})
        ),
        None,
    )
    if body is not None and not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False)
    # Pre-clip well past the final cut so a multi-megabyte log is not scanned
    # whole; a secret split here lies beyond the final cut and is dropped with it.
    body = body.strip()[: TOOL_EVIDENCE_CHARACTERS * 4] if isinstance(body, str) else ""
    if name is None and not body:
        return None
    return _clip_tool_evidence(sanitize_model_input(f"[{name}] {body}".rstrip() if name else body))


def tool_blocks_evidence(
    value: JsonValue | None,
    *,
    block_types: frozenset[str],
) -> str | None:
    """Tool blocks inside a message's content list, one line each, as one clipped row.

    A nested content list keeps only its text: an image result is base64, not evidence.
    """
    if not isinstance(value, list):
        return None
    lines: list[str] = []
    for item in value:
        block = as_record(item)
        if block is None or block.get("type") not in block_types:
            continue
        if isinstance(block.get("content"), list):
            block = {
                **block,
                "content": text_content(block["content"], allowed_block_types=frozenset({"text"})),
            }
        if (line := tool_evidence_text(block)) is not None:
            lines.append(line)
    return _clip_tool_evidence("\n".join(lines)) if lines else None


def _clip_tool_evidence(text: str) -> str:
    if len(text) > TOOL_EVIDENCE_CHARACTERS:
        return text[:TOOL_EVIDENCE_CHARACTERS] + TOOL_EVIDENCE_TRUNCATED
    return text


def parse_timestamp(value: JsonValue | None) -> datetime | None:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def conversation_from_relative_path(relative_path: str) -> str:
    return PurePosixPath(relative_path).stem


@dataclass(frozen=True)
class HostEnvelope:
    text: str | None
    prefix_blocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionedHostEnvelope:
    text: str | None
    prefix_blocks: tuple[str, ...]
    status: Literal["identity", "unwrapped", "ambiguous"]
    start_cp: int | None
    end_cp: int | None


def unwrap_host_envelope(value: str) -> HostEnvelope:
    """Unwrap only outer host blocks; the selected query body stays opaque."""
    result = unwrap_host_envelope_with_positions(value)
    return HostEnvelope(result.text, result.prefix_blocks)


def unwrap_host_envelope_with_positions(value: str) -> PositionedHostEnvelope:
    """Same legacy text, with exact body coordinates only for unambiguous parsing."""
    original = value.strip()
    original_start = len(value) - len(value.lstrip())
    remaining = original
    offset = original_start
    prefix: list[str] = []
    has_context = False

    def finish(start: int, end: int) -> PositionedHostEnvelope:
        body = value[start:end]
        left = start + len(body) - len(body.lstrip())
        text = body.strip()
        right = left + len(text)
        status: Literal["identity", "unwrapped", "ambiguous"] = (
            "identity" if not prefix and text == original else "unwrapped"
        )
        return PositionedHostEnvelope(
            text or None, tuple(prefix), status, left if text else None, right if text else None
        )

    def ambiguous() -> PositionedHostEnvelope:
        return PositionedHostEnvelope(original or None, tuple(prefix), "ambiguous", None, None)

    while opening := _HOST_PREFIX.match(remaining):
        closing = _closing_tag(remaining, opening)
        if closing is None:
            return ambiguous()
        prefix.append(remaining[: closing.end()])
        has_context |= _REMINDER_START.match(remaining) is None
        suffix = remaining[closing.end() :]
        offset += closing.end() + len(suffix) - len(suffix.lstrip())
        remaining = suffix.lstrip()

    if query := _USER_QUERY_START.match(remaining):
        closing = _closing_tag(remaining, query)
        if closing is not None and _only_query_suffix(remaining[closing.end() :]):
            return finish(offset + query.end(), offset + closing.start())
        # Multiple top-level queries or unclosed/quoted markup are not one envelope.
        return ambiguous()
    if remaining and has_context:
        return ambiguous()
    end, valid = _reminder_suffix_end(remaining)
    if not valid:
        # Legacy strips already-consumed prefix blocks even when a later suffix
        # is malformed; preserve that text but give it no indexable coordinates.
        return PositionedHostEnvelope(
            remaining.strip() or None, tuple(prefix), "ambiguous", None, None
        )
    return finish(offset, offset + end)


def strip_host_wrappers(value: str) -> str | None:
    return unwrap_host_envelope(value).text


def _closing_tag(value: str, opening: re.Match[str]) -> re.Match[str] | None:
    # These are text envelopes, not XML: host prose can contain a literal
    # <user_query> or comparisons. Only balance the enclosing tag's own name.
    tag = re.escape(opening.group("tag"))
    tokens = re.compile(rf"</{tag}\s*>|<{tag}{_TAG_ATTRIBUTES}>")
    depth = 1
    for token in tokens.finditer(value, opening.end()):
        depth += -1 if token.group().startswith("</") else 1
        if depth == 0:
            return token
    return None


def _strip_reminder_suffix(value: str) -> str:
    end, _valid = _reminder_suffix_end(value)
    return value[:end].strip()


def _reminder_suffix_end(value: str) -> tuple[int, bool]:
    position = 0
    suffix_start: int | None = None
    while opening := _REMINDER_START.search(value, position):
        closing = _closing_tag(value, opening)
        if closing is None:
            return len(value), False
        if suffix_start is None or value[position : opening.start()].strip():
            suffix_start = opening.start()
        position = closing.end()
    if suffix_start is not None and not value[position:].strip():
        return suffix_start, True
    return len(value), True


def _only_query_suffix(value: str) -> bool:
    remaining = value.strip()
    while opening := _QUERY_SUFFIX.match(remaining):
        closing = _closing_tag(remaining, opening)
        if closing is None:
            return False
        remaining = remaining[closing.end() :].lstrip()
    return not remaining


def is_host_generated_user_turn(value: str) -> bool:
    stripped = value.lstrip()
    if HOST_GENERATED_USER_TURN.match(stripped) is not None:
        return True
    if not stripped.startswith("{"):
        return False
    if PIPELINE_JSON_SIGNATURE.match(stripped) is not None:
        return True
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("request_id"), str)
        and payload.get("type") in {"write", "search", "delete", "digest"}
        and isinstance(payload.get("steps"), list)
    )
