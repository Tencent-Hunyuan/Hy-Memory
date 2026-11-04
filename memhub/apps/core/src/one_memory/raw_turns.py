"""Turn-level read projection for SDK raw batches.

HY keeps several complete conversation turns in one L1 raw node so model
writes stay batched. Product readers must not expose that storage batch as
one conversation item: this module gives every contained turn a stable
identity while retaining the parent raw id for traceability.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from one_memory.hy_sdk_types import HySdkRawRecord, raw_turn_messages


@dataclass(frozen=True)
class RawDisplayTurn:
    turn_id: str
    turn_index: int
    turn_count: int
    content: str
    messages: tuple[tuple[str, str], ...]


def raw_display_turns(record: HySdkRawRecord) -> tuple[RawDisplayTurn, ...]:
    """Split one stored raw batch into user-opened turns.

    A user message opens a new turn; assistant/tool/system messages stay with
    that user message until the next user message. Legacy content with no user
    marker remains one turn so it never disappears from the product.
    """
    messages = raw_turn_messages(record.content)
    grouped: list[list[tuple[str, str]]] = []
    for message in messages:
        role, _ = message
        has_user = bool(grouped) and any(item[0] == "user" for item in grouped[-1])
        if not grouped or (role == "user" and has_user):
            grouped.append([message])
        else:
            grouped[-1].append(message)
    if not grouped:
        grouped = [[("", record.content)]]

    digest = hashlib.sha256(record.raw_id.encode("utf-8")).hexdigest()
    count = len(grouped)
    return tuple(
        RawDisplayTurn(
            turn_id=f"raw-turn:{digest}:{index}",
            turn_index=index,
            turn_count=count,
            content=_turn_content(turn),
            messages=tuple(turn),
        )
        for index, turn in enumerate(grouped)
    )


def _turn_content(messages: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"[{role}]: {content}" if role else content for role, content in messages
    )
