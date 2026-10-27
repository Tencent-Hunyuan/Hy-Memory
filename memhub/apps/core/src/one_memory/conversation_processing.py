"""Pure conversation-window helpers shared by capture, retrieval, and writes."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from one_memory.imports.models import EvidenceContentKind, EvidenceRecord

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
#: The write rule (`turn_batches`): every 5 complete turns, or fewer when
#: the next turn would take the batch past 8000 HY-read characters.
TURN_BATCH_TURNS = 5
TURN_BATCH_CHARACTERS = 8_000
#: A live conversation's last turn counts as complete, and a short batch is
#: written, after this long without a new message in that conversation.
TURN_IDLE_FLUSH_SECONDS = 600


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


@dataclass(frozen=True)
class TurnBatches:
    """What a conversation's records become: batches to write, records to keep."""

    #: Each batch is whole turns; every batch holds at least one user message.
    batches: tuple[tuple[EvidenceRecord, ...], ...]
    #: Not written yet: the open last turn, complete turns short of a batch,
    #: or records with no user message before them. Fed back in, in order,
    #: with the conversation's next records.
    carry: tuple[EvidenceRecord, ...]


def conversation_turns(records: Sequence[EvidenceRecord]) -> list[tuple[EvidenceRecord, ...]]:
    """One user message and every record after it up to the next user message.

    Records before the first user message are not a turn of their own: they
    join the first turn (an assistant proposal and the user turn adopting it
    reach HY together). With no user message at all the result is one group
    that does not open with a user, which `turn_batches` never writes.
    """
    turns: list[list[EvidenceRecord]] = []
    for record in records:
        if not _is_hy_turn_record(record):
            continue
        # Until the first user message, the group is leading records only.
        leading = bool(turns) and not any(_opens_turn(item) for item in turns[-1])
        if not turns or (_opens_turn(record) and not leading):
            turns.append([record])
        else:
            turns[-1].append(record)
    return [tuple(turn) for turn in turns]


def turn_batches(records: Sequence[EvidenceRecord], *, flush: bool) -> TurnBatches:
    """Group a conversation's records into write batches by the turn rule.

    - A turn is complete once the next user message follows it; `flush`
      completes the last one too (history at end of file, or 10 idle minutes).
    - Every 5 complete turns are one batch. Before 5, a turn that would take
      the batch past 8000 characters starts the next batch; one turn over
      8000 is a batch by itself and is never split.
    - Characters are what HY reads: user/assistant text in full, a tool
      record up to `_HY_TOOL_CHARACTERS`.
    - Fewer than 5 complete turns within 8000 are written only on `flush`.
    - No batch is written without a user message; records with none before
      them are carried, and dropped on `flush`.
    """
    turns = conversation_turns(records)
    if turns and not any(_opens_turn(record) for record in turns[0]):
        # Records with no user message yet: nothing to write until one comes.
        return TurnBatches(batches=(), carry=() if flush else turns[0])
    complete = turns if flush else turns[:-1]
    open_tail = () if flush or not turns else turns[-1]
    batches: list[tuple[EvidenceRecord, ...]] = []
    current: list[tuple[EvidenceRecord, ...]] = []
    current_characters = 0
    for turn in complete:
        characters = sum(_hy_read_length(record) for record in turn)
        if current and current_characters + characters > TURN_BATCH_CHARACTERS:
            batches.append(tuple(record for item in current for record in item))
            current, current_characters = [], 0
        current.append(turn)
        current_characters += characters
        if len(current) >= TURN_BATCH_TURNS or current_characters > TURN_BATCH_CHARACTERS:
            batches.append(tuple(record for item in current for record in item))
            current, current_characters = [], 0
    if current and flush:
        batches.append(tuple(record for item in current for record in item))
        current = []
    carry = tuple(record for item in current for record in item) + tuple(open_tail)
    return TurnBatches(batches=tuple(batches), carry=carry)


def _opens_turn(record: EvidenceRecord) -> bool:
    return record.role == "user"


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


def _hy_read_length(record: EvidenceRecord) -> int:
    if record.role == "tool":
        return min(len(record.verbatim), _HY_TOOL_CHARACTERS)
    return len(record.verbatim)
