"""Authenticated SDK context search route, without generation or reindex side effects."""

import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.params import Depends

from one_memory.call_event_recorder import sdk_context_call_event
from one_memory.context_access import resolve_context_authority
from one_memory.context_models import (
    SdkContextItem,
    SdkContextSearchRequest,
    SdkContextSearchResponse,
    SdkMemoryContextItem,
    SdkRawContextItem,
)
from one_memory.embedding_health import EMBEDDING_UNAVAILABLE, EmbeddingUnavailableError
from one_memory.external_memory_access import external_access
from one_memory.hy_runtime_config import HY_MEMORY_READER
from one_memory.hy_sdk_runtime import sdk_relevance_floor
from one_memory.hy_sdk_types import (
    HySdkMemoryRecord,
    parse_sdk_lane_search,
    parse_sdk_raw_search,
    parse_sdk_search,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.raw_turns import raw_display_turns, turns_for_hit
from one_memory.sdk_search_options import SdkSearchFilters, SearchLane
from one_memory.semantic_deadline import SemanticMemoryHit
from one_memory.user_scope import owns

CALL_EVENT_RESPONSE_BUDGET_MS = 250
SDK_CONTEXT_CONTENT_LIMIT = 20_000
logger = logging.getLogger(__name__)

#: Pass-through filters the raw lane honours. Scene, layers, reader and the
#: schema/working floors describe memory lanes; the raw lane is always the
#: chat store's L1 raw layer.
_RAW_LANE_FILTERS = ("created_after", "session_ids", "agent_ids", "exclude_sources", "rerank")


def raw_lane_sources(
    allowed: Sequence[str] | frozenset[str] | None,
    requested: Sequence[str] | None,
) -> list[str] | None:
    """The raw lane's source filter: the caller's, never wider than its authority.

    `allowed` empty or None means every source (the call Core always made).
    None back means nothing is left to search.
    """
    allowed_list = list(allowed or ())
    if requested is None:
        return allowed_list
    chosen = [
        source for source in dict.fromkeys(requested) if not allowed_list or source in allowed_list
    ]
    return chosen or None


def raw_lane_overrides(value: SdkSearchFilters) -> dict[str, object]:
    """The caller's raw-lane overrides: its floor and its row filters."""
    overrides = value.filter_kwargs()
    found = {name: overrides[name] for name in _RAW_LANE_FILTERS if name in overrides}
    floor = value.lane_floor("raw")
    if floor is not None:
        found["min_score"] = floor
    return found


def raw_lane_statuses(*, lite: bool) -> dict[str, list[str]]:
    """Which raw statuses the raw lane searches.

    Lite extracts nothing, so its search is plain RAG over every raw,
    including the shadow raws pro already processed before the switch to
    lite. Pro and ultra keep the SDK default (active only): a shadow raw is
    already represented by the memories extracted from it, and returning it
    again would repeat that content in the caller's context budget.
    """
    return {"statuses": ["active", "shadow"]} if lite else {}


def hit_namespace(hit: SemanticMemoryHit) -> dict[str, str]:
    """The `get` namespace of a hit: nothing for chat, kind and variant otherwise."""
    if hit.kind in {"experience", "fact"} and hit.variant is not None:
        return {"kind": hit.kind, "variant": hit.variant}
    return {}


def bounded_context_content(content: str) -> tuple[str, bool]:
    """Fit SDK text into its wire contract before Pydantic validates it."""
    if len(content) <= SDK_CONTEXT_CONTENT_LIMIT:
        return content, False
    return content[:SDK_CONTEXT_CONTENT_LIMIT], True


def sdk_context_router(dependencies: Sequence[Depends]) -> APIRouter:
    """SDK-native context: no Evidence/Governance ids or fabricated revisions."""
    router = APIRouter(prefix="/api/v2/context", dependencies=dependencies)

    @router.post("/search", response_model=SdkContextSearchResponse)
    async def search(
        value: SdkContextSearchRequest,
        request: Request,
        response: Response,
    ) -> SdkContextSearchResponse:
        response.headers["Cache-Control"] = "no-store"
        started = time.monotonic()
        access = external_access(request.headers)
        database = cast(EvidenceStore, request.app.state.evidence_store).database_path
        try:
            authority = await asyncio.to_thread(
                resolve_context_authority,
                value,
                access,
                database,
                deadline=started + value.timeout_ms / 1000,
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "context_authority_unavailable") from None

        client_handle = getattr(request.app.state, "sdk_client_handle", None)
        if client_handle is None:
            raise HTTPException(503, "sdk_context_unavailable")
        try:
            client = await client_handle.client()
        except Exception:
            raise HTTPException(503, "sdk_context_unavailable") from None
        memory_mode = getattr(request.app.state, "memory_mode", None)
        lite = callable(memory_mode) and memory_mode() == "lite"

        async def raw_lane() -> tuple[SdkRawContextItem, ...]:
            if "raw" not in value.kinds or not authority.raw_allowed:
                return ()
            sources = raw_lane_sources(authority.raw_sources, value.include_sources)
            if sources is None:
                return ()
            # Plain RAG: every vector hit over the floor is an answer, ranked
            # by its score. Each hit yields at least one item, so the SDK
            # needs to return no more hits than the caller's limit.
            raw = await asyncio.to_thread(
                client.search,
                value.query,
                **{
                    "user_ids": [value.user_id],
                    "agent_ids": [],
                    "include_sources": sources,
                    "limit": value.limit,
                    "min_score": 0.0,
                    "schema_min_score": 0.0,
                    "schema_limit": 0,
                    "intention_limit": 0,
                    "scene": "chat",
                    "reader": HY_MEMORY_READER,
                    "layers": ["l1_raw"],
                    **raw_lane_statuses(lite=lite),
                    **raw_lane_overrides(value),
                },
            )
            hits = tuple(
                hit for hit in parse_sdk_raw_search(raw) if value.passes_floor("raw", hit.score)
            )
            # One item per hit first, so a hit is never crowded out by another
            # hit's sibling turns; the siblings fill whatever room is left.
            primary: list[SdkRawContextItem] = []
            siblings: list[SdkRawContextItem] = []
            for hit in hits:
                memory_at = (
                    datetime.fromtimestamp(hit.memory_at, tz=UTC)
                    if hit.memory_at is not None
                    else None
                )
                turns = raw_display_turns(hit) if value.raw_unit == "turn" else ()
                if not turns:
                    content, truncated = bounded_context_content(hit.content)
                    primary.append(
                        SdkRawContextItem(
                            raw_id=hit.raw_id,
                            content=content,
                            source=hit.source,
                            session_id=hit.session_id,
                            memory_at=memory_at,
                            score=hit.score,
                            truncated=truncated,
                        )
                    )
                    continue
                # The embedding is the batch's: the hit is shown by the turns
                # that contain the query, or else by its closest turn, each
                # with the hit's score.
                shown: list[SdkRawContextItem] = []
                for turn in turns_for_hit(turns, value.query):
                    content, truncated = bounded_context_content(turn.content)
                    shown.append(
                        SdkRawContextItem(
                            raw_id=hit.raw_id,
                            turn_id=turn.turn_id,
                            turn_index=turn.turn_index,
                            turn_count=turn.turn_count,
                            content=content,
                            source=hit.source,
                            session_id=hit.session_id,
                            memory_at=memory_at,
                            score=hit.score,
                            truncated=truncated,
                        )
                    )
                primary.append(shown[0])
                siblings.extend(shown[1:])
            chosen = primary[: value.limit]
            chosen.extend(siblings[: value.limit - len(chosen)])
            # Stable: a hit's turns stay together, in hit-score order.
            chosen.sort(key=lambda item: item.score, reverse=True)
            return tuple(chosen)

        async def memory_lane() -> tuple[SdkMemoryContextItem, ...]:
            # lite extracts nothing: its search answers raw turns, which the
            # raw lane already reads under the raw authority.
            if "memory" not in value.kinds or not authority.memory_allowed or lite:
                return ()
            overrides = value.search_overrides()
            raw = await asyncio.to_thread(
                client.search,
                value.query,
                **{
                    "user_ids": [value.user_id],
                    "agent_ids": [],
                    "limit": value.limit,
                    "min_score": sdk_relevance_floor(),
                    "schema_min_score": sdk_relevance_floor(),
                    "schema_limit": value.schema_limit,
                    "intention_limit": value.intention_limit,
                    "scene": "normal",
                    "reader": HY_MEMORY_READER,
                    **overrides,
                },
            )
            hits = (
                tuple(
                    hit
                    for hit in parse_sdk_lane_search(
                        raw, error_code="hy_memory_search_shape_unknown"
                    )
                    if value.passes_floor(cast(SearchLane, hit.lane), hit.score)
                )
                if overrides
                else parse_sdk_search(raw, error_code="hy_memory_search_shape_unknown")
            )
            items: list[SdkMemoryContextItem] = []
            for hit in hits:
                record = await asyncio.to_thread(client.get, hit.memory_id, **hit_namespace(hit))
                memory = None if record is None else HySdkMemoryRecord.from_sdk(record)
                # `get` takes no user: a hit is kept only if it is the caller's.
                if (
                    memory is None
                    or memory.status != "active"
                    or not owns(memory.user_id, value.user_id)
                ):
                    continue
                content, truncated = bounded_context_content(
                    hit.formatted_content
                    if hit.sdk_formatted and hit.formatting_complete and hit.formatted_content
                    else memory.content
                )
                items.append(
                    SdkMemoryContextItem(
                        memory_id=memory.memory_id,
                        content=content,
                        layer=memory.layer,
                        status=memory.status,
                        source=memory.source,
                        updated_at=(
                            datetime.fromtimestamp(memory.gmt_created, tz=UTC)
                            if memory.gmt_created is not None
                            else None
                        ),
                        score=hit.score,
                        truncated=truncated,
                    )
                )
            return tuple(items)

        async def record(final: SdkContextSearchResponse, *, timed_out: bool) -> None:
            # Same boundary as v2 ask: only a host's call is history. The
            # renderer's own searches are the person reading their library.
            store = getattr(request.app.state, "call_event_store", None)
            if store is None or access is None or value.query == "__om_probe__":
                return
            event = sdk_context_call_event(
                final,
                access,
                memory_searched=authority.memory_allowed,
                timed_out=timed_out,
            )
            try:
                await asyncio.to_thread(
                    store.record,
                    event,
                    busy_timeout_ms=CALL_EVENT_RESPONSE_BUDGET_MS,
                )
                response.headers["X-One-Memory-Call-Recorded"] = "true"
            except Exception:
                response.headers["X-One-Memory-Call-Recorded"] = "false"
                request.app.state.call_event_recording_error = True

        lanes = (raw_lane(), memory_lane())
        try:
            raw_items, memory_items = await asyncio.wait_for(
                asyncio.gather(*lanes, return_exceptions=True),
                timeout=value.timeout_ms / 1000,
            )
        except TimeoutError:
            final = SdkContextSearchResponse(
                request_id=str(uuid4()),
                state="unavailable",
                items=(),
                used_characters=0,
                truncated=True,
                total_ms=(time.monotonic() - started) * 1000,
                user_id=value.user_id,
            )
            await record(final, timed_out=True)
            return final
        for lane in (raw_items, memory_items):
            if isinstance(lane, EmbeddingUnavailableError):
                # No lexical fallback: the query could not be embedded.
                raise HTTPException(503, EMBEDDING_UNAVAILABLE)
        requested_lanes = {
            "raw": raw_items,
            "memory": memory_items,
        }
        failed_lanes = {
            name: lane
            for name, lane in requested_lanes.items()
            if name in value.kinds and isinstance(lane, Exception)
        }
        for name, error in failed_lanes.items():
            logger.warning(
                "sdk_context_lane_failed lane=%s error_class=%s",
                name,
                type(error).__name__,
            )
        all_requested_failed = len(failed_lanes) == len(value.kinds)
        partial = bool(failed_lanes)
        raw_candidates = (
            () if isinstance(raw_items, Exception) else cast(tuple[SdkContextItem, ...], raw_items)
        )
        if access is not None and raw_candidates:
            try:
                current = await asyncio.to_thread(
                    resolve_context_authority,
                    value,
                    access,
                    database,
                    deadline=started + value.timeout_ms / 1000,
                )
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "context_authority_unavailable") from None
            current_sources = current.raw_sources or frozenset()
            raw_candidates = tuple(
                item
                for item in raw_candidates
                if item.kind == "raw" and item.source in current_sources
            )
        memory_candidates = (
            ()
            if isinstance(memory_items, Exception)
            else cast(tuple[SdkContextItem, ...], memory_items)
        )
        candidates: list[SdkContextItem] = [
            *raw_candidates,
            *memory_candidates,
        ]
        candidates.sort(key=lambda item: item.score, reverse=True)
        selected: list[SdkContextItem] = []
        used = 0
        truncated = False
        for item in candidates:
            if len(selected) >= value.limit:
                truncated = True
                break
            content = item.content
            remaining = value.character_budget - used
            if remaining <= 0:
                truncated = True
                break
            if len(content) > remaining:
                item = item.model_copy(update={"content": content[:remaining], "truncated": True})
                truncated = True
            truncated = truncated or item.truncated
            selected.append(item)
            used += len(item.content)
        final = SdkContextSearchResponse(
            request_id=str(uuid4()),
            state=(
                "unavailable"
                if all_requested_failed
                else "partial"
                if partial
                else "grounded"
                if selected
                else "empty"
            ),
            items=tuple(selected),
            used_characters=used,
            truncated=truncated,
            total_ms=(time.monotonic() - started) * 1000,
            user_id=value.user_id,
        )
        await record(final, timed_out=False)
        return final

    return router
