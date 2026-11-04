"""Turn-level read projection for SDK raw batches.

HY keeps several complete conversation turns in one L1 raw node so model
writes stay batched. Product readers must not expose that storage batch as
one conversation item: this module gives every contained turn a stable
identity while retaining the parent raw id for traceability.
"""

from __future__ import annotations

import hashlib
import re
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
    return "\n".join(f"[{role}]: {content}" if role else content for role, content in messages)


def raw_prefix_added(
    cached: tuple[tuple[str, str | None], ...],
    current: tuple[tuple[str, str | None], ...],
    cached_total: int,
    current_total: int,
) -> int | None:
    """How many raws were only put on top of a cached newest-first listing.

    `cached` and `current` are the (raw id, status) of the first page of each
    listing. The answer is a count when the current page is the cached one
    with new raws before it and nothing else changed; None when anything else
    happened (a delete, a status change, a raw dated into the middle), which
    needs a full re-listing.
    """
    added = current_total - cached_total
    if added <= 0 or added > len(current):
        return None
    kept = current[added:]
    if not kept and cached_total:
        return None
    if kept != cached[: len(kept)]:
        return None
    return added


_WORD = re.compile(r"\w+")


def _query_terms(query: str) -> frozenset[str]:
    """Words of the query, plus character bigrams of unspaced (CJK) words."""
    terms: set[str] = set()
    for word in _WORD.findall(query.casefold()):
        terms.add(word)
        if not word.isascii() and len(word) > 1:
            terms.update(word[index : index + 2] for index in range(len(word) - 1))
    return frozenset(terms)


def turns_for_hit(turns: tuple[RawDisplayTurn, ...], query: str) -> tuple[RawDisplayTurn, ...]:
    """The turns one semantic raw hit shows, never none while it has turns.

    The SDK embeds a whole raw batch, so a vector hit cannot say which of its
    turns matched. Every turn containing the query text is shown; when none
    does, the turn sharing the most query terms (the earliest on a tie) stands
    for the hit. A semantic hit is never dropped for lacking the literal query.
    """
    if not turns:
        return ()
    needle = query.casefold().strip()
    if needle:
        lexical = tuple(turn for turn in turns if needle in turn.content.casefold())
        if lexical:
            return lexical
    terms = _query_terms(query)

    def overlap(turn: RawDisplayTurn) -> tuple[int, int]:
        content = turn.content.casefold()
        return (sum(1 for term in terms if term in content), -turn.turn_index)

    return (max(turns, key=overlap),)
