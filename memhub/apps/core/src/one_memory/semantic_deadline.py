"""Ask HY for ranked ids without letting it outlive the caller's deadline.

The one place host-facing reads wait for HY, so recall and context cannot
drift apart on what "HY did not answer" means.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

SemanticSearch = Callable[[str], Awaitable[Sequence[object]]]

#: `absent` — no HY is wired in, so keywords are the design, not a fallback.
SemanticOutcome = Literal["answered", "failed", "timed_out", "absent"]


@dataclass(frozen=True)
class SemanticMemoryHit:
    """One HY-ranked memory, including SDK-rendered answer context when available."""

    memory_id: str
    score: float
    formatted_content: str | None = None
    member_ids: tuple[str, ...] = ()
    sdk_formatted: bool = False
    formatting_complete: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "score": self.score,
            "formatted_content": self.formatted_content,
            "member_ids": list(self.member_ids),
            "sdk_formatted": self.sdk_formatted,
            "formatting_complete": self.formatting_complete,
        }


def normalize_semantic_hits(values: object) -> tuple[SemanticMemoryHit, ...] | None:
    """Accept current structured hits plus legacy ids and ``(id, score)`` pairs."""
    if not isinstance(values, list | tuple):
        return None
    if not values:
        return ()
    hits: list[SemanticMemoryHit] = []
    seen: set[str] = set()
    total = len(values)
    for index, value in enumerate(values):
        hit: SemanticMemoryHit | None = None
        if isinstance(value, SemanticMemoryHit):
            hit = value
        elif isinstance(value, str):
            hit = SemanticMemoryHit(value, 1.0 - index / max(1, total))
        elif (
            isinstance(value, tuple | list)
            and len(value) == 2
            and isinstance(value[0], str)
            and isinstance(value[1], int | float)
        ):
            hit = SemanticMemoryHit(value[0], float(value[1]))
        elif isinstance(value, Mapping):
            memory_id = value.get("memory_id") or value.get("id")
            score = value.get("score", 0.0)
            content = value.get("formatted_content")
            raw_members = value.get("member_ids", ())
            if (
                isinstance(memory_id, str)
                and isinstance(score, int | float)
                and (content is None or isinstance(content, str))
                and isinstance(raw_members, list | tuple)
                and all(isinstance(member_id, str) for member_id in raw_members)
            ):
                hit = SemanticMemoryHit(
                    memory_id=memory_id,
                    score=float(score),
                    formatted_content=content,
                    member_ids=tuple(dict.fromkeys(raw_members)),
                    sdk_formatted=value.get("sdk_formatted") is True,
                    formatting_complete=value.get("formatting_complete") is True,
                )
        if hit is None or not hit.memory_id:
            return None
        if hit.memory_id not in seen:
            seen.add(hit.memory_id)
            hits.append(hit)
    return tuple(hits)


#: Kept back from HY for what has to follow it inside the same deadline:
#: reading HY's ids back from the ledger and filtering them. On a
#: 1,500-memory library that measured p95 ≈ 55 ms under load.
_LEDGER_RESERVE_S = 0.25
# Semantic ranking is an enhancement to the local governed-memory read. Under
# import pressure HY can take seconds even when the ledger already has useful
# results, so never spend the host's one-second interaction target waiting for
# it. The unfinished read is reported as partial and completes in isolation.
_HY_WAIT_CAP_S = 0.5


def ledger_wait_budget(remaining_s: float) -> float:
    """How long a read may run before the ledger reserve is all that is left."""
    return max(0.0, remaining_s - min(_LEDGER_RESERVE_S, remaining_s / 4))


def hy_wait_budget(remaining_s: float) -> float:
    return min(_HY_WAIT_CAP_S, ledger_wait_budget(remaining_s))


async def search_within(
    search: SemanticSearch | None, query: str, wait_s: float
) -> tuple[tuple[SemanticMemoryHit, ...], SemanticOutcome]:
    """HY's ids in HY's order, first occurrence kept, and whether HY answered.

    `asyncio.wait`, not `wait_for`: the bridge will not abandon an HY call
    half-way (`_joined_thread`), so a cancelled search keeps running until HY
    answers, and `wait_for` waits for it — measured at 3–5 s past a 2 s budget.
    The abandoned search finishes on its own; the bridge's lock keeps that to
    one per bridge.
    """
    if search is None:
        return (), "absent"
    try:
        pending = asyncio.ensure_future(search(query))
    except Exception:
        return (), "failed"
    try:
        done, _ = await asyncio.wait({pending}, timeout=wait_s)
    except BaseException:
        _abandon(pending)
        raise
    if pending not in done:
        _abandon(pending)
        return (), "timed_out"
    # The caller is not the one cancelled here (that surfaced from the wait
    # above), so a search that cancelled itself is HY not answering.
    if pending.cancelled():
        return (), "failed"
    error = pending.exception()
    if error is not None:
        return (), "failed"
    found = pending.result()
    normalized = normalize_semantic_hits(found)
    if normalized is None:
        return (), "failed"
    return normalized, "answered"


def _abandon(pending: asyncio.Future[Sequence[object]]) -> None:
    pending.cancel()
    pending.add_done_callback(_consume)


def _consume(pending: asyncio.Future[Sequence[object]]) -> None:
    if not pending.cancelled():
        pending.exception()
