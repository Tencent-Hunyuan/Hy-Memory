"""Pure conversation-window helpers shared by capture, retrieval, and writes."""

from __future__ import annotations

import os
import re

from one_memory.imports.models import EvidenceContentKind, EvidenceOrigin, EvidenceRecord
from one_memory.memory_tasks import segment_evidence_record

MEMORY_TASK_TIMEOUT_MARGIN_SECONDS = 60.0
MEMORY_TASK_TIMEOUT_MIN_SECONDS = 180.0
MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES = 512

_HARNESS_PREFIXES = (
    "<codex_internal_context",
    "# agents.md instructions",
    "<environment_context>",
    "<permissions instructions>",
    "<system-reminder>",
    "<system_notification>",
    "<instructions>",
)
_HOST_ENVELOPE = re.compile(
    r"(?:"
    r"^<timestamp>.*?<system_(?:notification|reminder)>|"
    r"^<system_(?:notification|reminder)>|"
    r'^\{\s*"request_id"\s*:.*?"steps"\s*:'
    r")",
    re.IGNORECASE | re.DOTALL,
)
_WHOLE_TURN_CHARACTERS = 60_000
_WINDOW_BYTE_CEILING = 2_000_000
_HISTORY_MERGE_TURNS = 8
_HISTORY_MERGE_CHARACTERS = 8_000


def memory_task_timeout_seconds(model_timeout_seconds: float) -> float:
    """Bound a whole write without treating one model request as its budget."""
    return max(
        MEMORY_TASK_TIMEOUT_MIN_SECONDS,
        float(model_timeout_seconds) + MEMORY_TASK_TIMEOUT_MARGIN_SECONDS,
    )


def _coding_tool_result_max_bytes() -> int:
    raw = os.environ.get("MEMORY_CODING_TOOL_RESULT_MAX_BYTES")
    if raw is None:
        return MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES
    return value if value > 0 else MEMHUB_DEFAULT_CODING_TOOL_RESULT_MAX_BYTES


_HY_TOOL_CHARACTERS = _coding_tool_result_max_bytes()


def _conversation_chunks(
    records: list[EvidenceRecord],
    *,
    max_characters: int | None = None,
    max_turns: int | None = None,
    completed_only: bool = False,
) -> list[tuple[EvidenceRecord, ...]]:
    """Build bounded, complete user/assistant/tool windows."""
    turns: list[list[EvidenceRecord]] = []
    for record in records:
        if not _is_hy_turn_record(record):
            continue
        pending_proposal = bool(turns) and not _opens_turn(turns[-1][0])
        if not turns or (_opens_turn(record) and not pending_proposal):
            turns.append([record])
        else:
            turns[-1].append(record)

    if completed_only and turns and _trailing_user_turn_is_open(turns[-1]):
        turns.pop()

    whole = _whole_turns() and max_characters is None
    if whole:
        size = _hy_read_length
        max_characters = _WHOLE_TURN_CHARACTERS
        max_turns = max_turns or 1_000_000
    else:
        size = lambda record: len(record.verbatim)  # noqa: E731
        max_characters = max_characters or 12_000
        max_turns = max_turns or 24

    chunks: list[tuple[EvidenceRecord, ...]] = []
    for turn in turns:
        original_question = turn[0] if _opens_turn(turn[0]) else None
        expanded: list[EvidenceRecord] = []
        for record in turn:
            reserve = (
                size(original_question)
                if original_question is not None
                and record is not original_question
                and size(original_question) < max_characters
                else 0
            )
            segment_limit = max(1, max_characters - reserve)
            if size(record) <= segment_limit:
                expanded.append(record)
                continue
            spans = _semantic_segment_spans(record.verbatim, segment_limit)
            expanded.extend(
                segment_evidence_record(record, index=index, start=start, end=end)
                if len(spans) > 1
                else record
                for index, (start, end) in enumerate(spans)
            )
        question = expanded[0] if expanded and _opens_turn(expanded[0]) else None
        current: list[EvidenceRecord] = []
        for record in expanded:
            if current and (
                len(current) >= max_turns
                or sum(size(item) for item in current) + size(record) > max_characters
                or sum(_window_bytes(item) for item in current) + _window_bytes(record)
                > _WINDOW_BYTE_CEILING
            ):
                chunks.append(tuple(current))
                current = (
                    [question]
                    if question is not None
                    and max_turns > 1
                    and size(question) + size(record) <= max_characters
                    else []
                )
            current.append(record)
        chunks.append(tuple(current))
    return _merge_tool_only_chunks(chunks)


def _semantic_segment_spans(text: str, limit: int) -> list[tuple[int, int]]:
    if len(text) <= limit:
        return [(0, len(text))]
    spans: list[tuple[int, int]] = []
    start = 0
    separators = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ")
    while start < len(text):
        hard_end = min(len(text), start + limit)
        if hard_end == len(text):
            end = hard_end
        else:
            floor = start + max(1, limit // 2)
            candidates = [text.rfind(separator, floor, hard_end) for separator in separators]
            boundary = max(candidates)
            if boundary < floor:
                end = hard_end
            else:
                matched = next(
                    separator for separator in separators if text.startswith(separator, boundary)
                )
                end = boundary + len(matched)
        spans.append((start, end))
        start = end
    return spans


def _merge_tool_only_chunks(
    chunks: list[tuple[EvidenceRecord, ...]],
) -> list[tuple[EvidenceRecord, ...]]:
    if not any(chunk and all(record.role == "tool" for record in chunk) for chunk in chunks):
        return chunks
    merged: list[tuple[EvidenceRecord, ...]] = []
    leading_tools: list[EvidenceRecord] = []
    for chunk in chunks:
        if not chunk:
            continue
        if all(record.role == "tool" for record in chunk):
            if merged:
                merged[-1] = (*merged[-1], *chunk)
            else:
                leading_tools.extend(chunk)
            continue
        if leading_tools:
            chunk = (*leading_tools, *chunk)
            leading_tools = []
        merged.append(chunk)
    return [] if leading_tools else merged


def _merge_historical_chunks(
    chunks: list[tuple[EvidenceRecord, ...]],
    *,
    max_turns: int = _HISTORY_MERGE_TURNS,
    max_characters: int = _HISTORY_MERGE_CHARACTERS,
) -> list[tuple[EvidenceRecord, ...]]:
    if not chunks:
        return []
    merged: list[tuple[EvidenceRecord, ...]] = []
    current: list[EvidenceRecord] = []
    current_turns = 0
    current_characters = 0
    for chunk in chunks:
        chunk_characters = sum(len(record.verbatim) for record in chunk)
        if current and (
            current_turns >= max_turns or current_characters + chunk_characters > max_characters
        ):
            merged.append(tuple(current))
            current = []
            current_turns = 0
            current_characters = 0
        current.extend(chunk)
        current_turns += 1
        current_characters += chunk_characters
    if current:
        merged.append(tuple(current))
    return merged


def _chunk_input_ref(chunk: tuple[EvidenceRecord, ...]) -> str:
    user_records = tuple(item for item in chunk if item.role == "user")
    return user_records[-1].evidence_id if user_records else chunk[-1].evidence_id


def _opens_turn(record: EvidenceRecord) -> bool:
    return record.role == "user"


def _trailing_user_turn_is_open(turn: list[EvidenceRecord]) -> bool:
    if len(turn) != 1:
        return False
    opener = turn[0]
    return (
        _opens_turn(opener)
        and opener.content_kind is EvidenceContentKind.RAW_CONVERSATION
        and not _is_memory_note(opener)
    )


def _is_hy_turn_record(record: EvidenceRecord) -> bool:
    if record.role == "tool":
        return record.content_kind is EvidenceContentKind.RAW_CONVERSATION and bool(
            record.verbatim.strip()
        )
    return _is_hy_input_turn(record)


def _is_hy_input_turn(record: EvidenceRecord) -> bool:
    source = record.verbatim.strip()
    if not source:
        return False
    if record.content_kind is EvidenceContentKind.MEMORY_CONTENT:
        return True
    if record.role == "assistant":
        return True
    if record.role != "user":
        return False
    if source.casefold().startswith(_HARNESS_PREFIXES):
        return False
    return _HOST_ENVELOPE.search(source[:2_000]) is None


def _is_memory_note(record: EvidenceRecord) -> bool:
    meta = record.origin_meta.get(EvidenceOrigin.MANUAL_IMPORT)
    return (
        record.source_id == "one-memory-manual"
        and meta is not None
        and meta.raw_meta.get("input_mode") == "direct_memory_note"
    )


def _window_bytes(record: EvidenceRecord) -> int:
    return len(record.verbatim.encode("utf-8"))


def _whole_turns() -> bool:
    return os.environ.get("ONE_MEMORY_SPLIT_TURNS", "").strip() != "1"


def _hy_read_length(record: EvidenceRecord) -> int:
    if record.role == "tool":
        return min(len(record.verbatim), _HY_TOOL_CHARACTERS)
    return len(record.verbatim)
