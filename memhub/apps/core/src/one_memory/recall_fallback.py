"""Recall that degrades to the original conversations instead of answering empty.

MemHub_recall searches the memories the SDK distilled from conversations. That
needs a configured generation model to have produced any, and a library that
holds some. Without either, the memory search can only come back empty, so
recall searches the SDK's raw layer (the imported conversations themselves)
instead: plain retrieval over what was imported, the same lane
`/api/v2/context/search` reads, under the same raw-source authority.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

from one_memory.hy_sdk_types import HySdkRawHit
from one_memory.recall import (
    PROBE_QUERY,
    RecallHit,
    RecallReceipt,
    RecallRequest,
    RecallState,
    TruncationBudget,
)

FallbackReason = Literal["model_unavailable", "no_memories"]

#: Raw sources this caller may read, or None for every source (a trusted local
#: caller). An empty set means the caller may read no raw conversation.
RawSources = frozenset[str] | None


async def recall_with_fallback(
    request: RecallRequest,
    *,
    model_ready: Callable[[], Awaitable[bool]],
    recall_memories: Callable[[RecallRequest], Awaitable[RecallReceipt]],
    has_memories: Callable[[], Awaitable[bool]],
    raw_sources: Callable[[], Awaitable[RawSources]],
    search_raw: Callable[[str, int, RawSources], Awaitable[tuple[HySdkRawHit, ...]]],
) -> RecallReceipt:
    """Memory recall when a model and memories exist, otherwise the raw layer.

    The library check runs only after an empty memory search: with memories
    present and nothing relevant, the answer stays an honest empty memory
    result rather than a different kind of hit.
    """
    if request.query == PROBE_QUERY:
        return await recall_memories(request)
    started = time.monotonic()
    if await model_ready():
        receipt = await recall_memories(request)
        if receipt.hits or await has_memories():
            return receipt
        reason: FallbackReason = "no_memories"
    else:
        reason = "model_unavailable"
    remaining = request.timeout_ms / 1_000 - (time.monotonic() - started)
    return await _raw_recall(
        request,
        reason=reason,
        remaining_seconds=remaining,
        raw_sources=raw_sources,
        search_raw=search_raw,
    )


async def _raw_recall(
    request: RecallRequest,
    *,
    reason: FallbackReason,
    remaining_seconds: float,
    raw_sources: Callable[[], Awaitable[RawSources]],
    search_raw: Callable[[str, int, RawSources], Awaitable[tuple[HySdkRawHit, ...]]],
) -> RecallReceipt:
    def receipt(
        hits: tuple[RecallHit, ...] = (),
        *,
        used: int = 0,
        truncated_by: TruncationBudget | None = None,
        unexamined: int = 0,
    ) -> RecallReceipt:
        return RecallReceipt(
            state=RecallState.GROUNDED if hits else RecallState.EMPTY,
            query=request.query,
            scope=request.scope,
            hits=hits,
            used_characters=used,
            truncated=truncated_by is not None,
            truncated_by=truncated_by,
            unexamined_count=unexamined,
            mode="raw",
            fallback_reason=reason,
        )

    sources = await raw_sources()
    if sources is not None and not sources:
        # The caller may read no original conversation: an empty answer, not
        # a wider grant than recall had.
        return receipt()
    if remaining_seconds <= 0:
        return receipt(truncated_by=TruncationBudget.TIMEOUT)
    try:
        found = await asyncio.wait_for(
            search_raw(request.query, request.limit, sources),
            timeout=remaining_seconds,
        )
    except TimeoutError:
        return receipt(truncated_by=TruncationBudget.TIMEOUT)
    hits: list[RecallHit] = []
    used = 0
    truncated_by: TruncationBudget | None = None
    for index, hit in enumerate(found):
        if sources is not None and hit.source not in sources:
            continue
        if len(hits) >= request.limit:
            truncated_by = TruncationBudget.COUNT
            return receipt(
                tuple(hits),
                used=used,
                truncated_by=truncated_by,
                unexamined=len(found) - index,
            )
        if used + len(hit.content) > request.character_budget:
            truncated_by = TruncationBudget.CHARACTER
            continue
        hits.append(
            RecallHit(
                memory_id=hit.raw_id,
                content=hit.content,
                kind="raw",
                scopes=(),
                evidence_ids=(),
                updated_at=(
                    datetime.fromtimestamp(hit.memory_at, tz=UTC)
                    if hit.memory_at is not None
                    else datetime(1970, 1, 1, tzinfo=UTC)
                ),
                score=hit.score,
                content_format="raw",
                source=hit.source,
                session_id=hit.session_id,
            )
        )
        used += len(hit.content)
    return receipt(tuple(hits), used=used, truncated_by=truncated_by)
