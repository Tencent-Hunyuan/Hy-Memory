"""Authenticated composite read route, without generation or reindex side effects."""

import asyncio
import sqlite3
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.params import Depends

from one_memory.background_work import (
    in_interactive_read,
    in_read_pool,
    memory_read_pool,
    raw_read_pool,
)
from one_memory.call_event_recorder import context_call_event
from one_memory.context_access import resolve_context_authority
from one_memory.context_memory import (
    merge_memory_candidates,
    read_memory_context,
    revalidate_memory_context,
)
from one_memory.context_models import (
    ContextItem,
    ContextKind,
    ContextLaneResult,
    ContextSearchRequest,
    ContextSearchResponse,
    ContextSemanticStatus,
    MemoryContextItem,
    RawContextItem,
    SdkContextItem,
    SdkContextSearchResponse,
    SdkMemoryContextItem,
    SdkRawContextItem,
)
from one_memory.context_raw import revalidate_raw_context
from one_memory.context_search import search_context
from one_memory.diagnostic_log import diagnostic_event, diagnostic_exception
from one_memory.external_memory_access import external_access
from one_memory.imports.storage import EvidenceStore
from one_memory.retrieval.raw_lane import read_raw_indexed_context
from one_memory.semantic_deadline import hy_wait_budget, search_within

CALL_EVENT_RESPONSE_BUDGET_MS = 250
RESPONSE_SERIALIZATION_RESERVE_MS = 100
INTERACTIVE_TOTAL_BUDGET_MS = 900
RAW_LANE_BUDGET_SECONDS = 0.3
MEMORY_LANE_BUDGET_SECONDS = 0.75
MEMORY_FINALIZE_RESERVE_SECONDS = 0.1


def context_router(dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/context", dependencies=dependencies)

    @router.post("/search", response_model=ContextSearchResponse)
    async def search(
        value: ContextSearchRequest, request: Request, response: Response
    ) -> ContextSearchResponse:
        response.headers["Cache-Control"] = "no-store"
        started = time.monotonic()
        search_budget_ms = min(
            INTERACTIVE_TOTAL_BUDGET_MS,
            max(50, value.timeout_ms - RESPONSE_SERIALIZATION_RESERVE_MS),
        )
        deadline = started + search_budget_ms / 1000
        access = external_access(request.headers)
        try:
            database = cast(EvidenceStore, request.app.state.evidence_store).database_path
            authority = await asyncio.wait_for(
                in_interactive_read(
                    resolve_context_authority, value, access, database, deadline=deadline
                ),
                timeout=max(0, deadline - time.monotonic()),
            )
        except HTTPException:
            raise
        except Exception:
            # Authority could not be established; do not try an unbounded local lane.
            raise HTTPException(503, "context_authority_unavailable") from None

        lane_observations: dict[ContextKind, ContextLaneResult] = {}
        lane_failures: dict[ContextKind, tuple[str, str]] = {}
        stage_timings: dict[str, float] = {}

        async def raw(end: float) -> ContextLaneResult:
            lane_end = min(end, time.monotonic() + RAW_LANE_BUDGET_SECONDS)
            read = asyncio.create_task(
                in_read_pool(
                    raw_read_pool(),
                    read_raw_indexed_context,
                    database,
                    value,
                    source_ids=authority.raw_sources,
                    deadline=lane_end,
                    index=getattr(request.app.state, "retrieval_runtime", None),
                )
            )
            done, _ = await asyncio.wait({read}, timeout=max(0, lane_end - time.monotonic()))
            if read not in done:
                read.cancel()
                read.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None
                )
                result = ContextLaneResult(
                    status="degraded",
                    reason="timeout_budget",
                    searched_channels=("lexical", "semantic"),
                    timed_out=True,
                    truncated_by=("timeout_budget",),
                )
            else:
                result = read.result()
            lane_observations["raw"] = result
            return result

        async def memory(end: float) -> ContextLaneResult:
            lane_end = min(end, time.monotonic() + MEMORY_LANE_BUDGET_SECONDS)
            target = getattr(request.app.state, "recall_target", None)
            lexical = asyncio.create_task(
                in_read_pool(
                    memory_read_pool(),
                    read_memory_context,
                    database,
                    value,
                    scopes=authority.memory_scopes,
                    deadline=lane_end,
                    semantic_ids=(),
                    semantic_outcome="absent",
                )
            )
            semantic_hits, outcome = await search_within(
                getattr(target, "semantic_search", None),
                value.query,
                hy_wait_budget(lane_end - time.monotonic()),
            )
            done, _ = await asyncio.wait({lexical}, timeout=max(0, lane_end - time.monotonic()))
            if lexical not in done:
                lexical.cancel()
                lexical.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None
                )
                result = ContextLaneResult(
                    status="unavailable",
                    reason="timeout_budget",
                    searched_channels=("lexical", "semantic"),
                    semantic_status=cast(
                        ContextSemanticStatus,
                        {
                            "absent": "not_searched",
                            "answered": "completed",
                            "failed": "failed",
                            "timed_out": "timed_out",
                        }[outcome],
                    ),
                    timed_out=True,
                    truncated_by=("timeout_budget",),
                )
                lane_observations["memory"] = result
                return result
            try:
                lexical_result = lexical.result()
            except (sqlite3.Error, OSError, ValueError) as error:
                diagnostic_event(
                    level="warn",
                    component="context.search",
                    event="context.lexical_memory_failed",
                    outcome="degraded",
                    error_code="lexical_memory_read_failed",
                    **diagnostic_exception(error),
                )
                lexical_result = ContextLaneResult(
                    status="unavailable",
                    reason="read_unavailable",
                    searched_channels=("lexical",),
                    failure_site="memory_lexical",
                    error_class=type(error).__name__,
                )
            if outcome == "absent":
                result = lexical_result
            elif outcome in {"failed", "timed_out"}:
                truncated_by = list(lexical_result.truncated_by)
                has_lexical_memory = bool(lexical_result.items)
                if (
                    outcome == "timed_out"
                    and not has_lexical_memory
                    and "timeout_budget" not in truncated_by
                ):
                    truncated_by.append("timeout_budget")
                result = lexical_result.model_copy(
                    update={
                        "status": "degraded",
                        "reason": "lexical_only" if has_lexical_memory else "timeout_budget",
                        "truncated_by": tuple(truncated_by),
                        "searched_channels": ("lexical", "semantic"),
                        "semantic_status": "timed_out" if outcome == "timed_out" else "failed",
                        "timed_out": outcome == "timed_out" and not has_lexical_memory,
                    }
                )
            else:
                semantic_request = value.model_copy(update={"query": ""})
                enriched = asyncio.create_task(
                    in_interactive_read(
                        read_memory_context,
                        database,
                        semantic_request,
                        scopes=authority.memory_scopes,
                        deadline=lane_end,
                        semantic_hits=semantic_hits,
                        semantic_outcome=outcome,
                    )
                )
                done, _ = await asyncio.wait(
                    {enriched},
                    timeout=max(
                        0,
                        lane_end - MEMORY_FINALIZE_RESERVE_SECONDS - time.monotonic(),
                    ),
                )
                if enriched not in done:
                    enriched.cancel()
                    enriched.add_done_callback(
                        lambda task: task.exception() if not task.cancelled() else None
                    )
                    result = lexical_result.model_copy(
                        update={
                            "status": "degraded",
                            "reason": "lexical_only",
                            "searched_channels": ("lexical", "semantic"),
                            "semantic_status": "completed",
                            "timed_out": False,
                        }
                    )
                else:
                    try:
                        semantic_result = enriched.result()
                    except (sqlite3.Error, OSError, ValueError) as error:
                        diagnostic_event(
                            level="warn",
                            component="context.search",
                            event="context.semantic_enrichment_failed",
                            outcome="degraded",
                            error_code="semantic_enrichment_read_failed",
                            **diagnostic_exception(error),
                        )
                        result = lexical_result.model_copy(
                            update={
                                "status": "degraded",
                                "reason": "lexical_only",
                                "searched_channels": ("lexical", "semantic"),
                                "semantic_status": "completed",
                                "timed_out": False,
                            }
                        )
                        lane_observations["memory"] = result
                        return result
                    if semantic_result.status == "unavailable" and lexical_result.items:
                        result = lexical_result.model_copy(
                            update={
                                "status": "degraded",
                                "reason": "lexical_only",
                                "searched_channels": ("lexical", "semantic"),
                                "semantic_status": "completed",
                                "timed_out": False,
                            }
                        )
                        lane_observations["memory"] = result
                        return result
                    semantic_items = tuple(
                        item
                        for item in semantic_result.items
                        if isinstance(item, MemoryContextItem)
                    )
                    lexical_items = tuple(
                        item for item in lexical_result.items if isinstance(item, MemoryContextItem)
                    )
                    combined_count = len(
                        {item.memory_id for item in (*semantic_items, *lexical_items)}
                    )
                    merged = merge_memory_candidates(
                        lexical_items,
                        semantic_items,
                        limit=value.limit + 1,
                    )
                    truncated_by = list(semantic_result.truncated_by)
                    if combined_count > len(merged) and "count_budget" not in truncated_by:
                        truncated_by.append("count_budget")
                    result = semantic_result.model_copy(
                        update={
                            "items": tuple(merged),
                            # An answered semantic search may legitimately
                            # return no IDs. Lexical can still have a grounded
                            # hit; retaining semantic's `empty` status while
                            # attaching that hit violates the lane contract
                            # and makes the aggregator reject the whole lane.
                            "status": "ok" if merged else semantic_result.status,
                            "truncated_by": tuple(truncated_by),
                        }
                    )
            lane_observations["memory"] = result
            return result

        async def finalizer(
            kind: ContextKind, items: tuple[ContextItem, ...], end: float
        ) -> tuple[ContextItem, ...]:
            if kind == "raw":
                return await in_interactive_read(
                    revalidate_raw_context,
                    database,
                    tuple(item for item in items if isinstance(item, RawContextItem)),
                    source_ids=authority.raw_sources,
                    deadline=end,
                )
            return await in_interactive_read(
                revalidate_memory_context,
                database,
                tuple(item for item in items if isinstance(item, MemoryContextItem)),
                scopes=authority.memory_scopes,
                deadline=end,
            )

        result = await search_context(
            value,
            raw_reader=raw if authority.raw_allowed else None,
            memory_reader=memory if authority.memory_allowed else None,
            finalizer=finalizer,
            deadline=deadline,
            timing_observer=stage_timings.update,
            failure_observer=lambda kind, site, error_class: lane_failures.__setitem__(
                kind, (site, error_class)
            ),
        )
        final = result.model_copy(update={"total_ms": (time.monotonic() - started) * 1000})
        serialization_started = time.monotonic()
        final.model_dump_json()
        stage_timings["serialization_ms"] = (time.monotonic() - serialization_started) * 1000
        for phase, duration in stage_timings.items():
            diagnostic_event(
                level="info",
                component="context.search",
                event="context_stage.completed",
                outcome="succeeded",
                request_id=final.request_id,
                duration_ms=max(0, round(duration)),
                details={"phase": phase},
            )
        for kind in ("raw", "memory"):
            path = getattr(final.paths, kind)
            observed = lane_observations.get(kind)
            failure = lane_failures.get(kind)
            lexical_hits = (
                sum(
                    1
                    for item in observed.items
                    if isinstance(item, MemoryContextItem) and "lexical" in item.matched_by
                )
                if observed is not None and kind == "memory"
                else 0
            )
            semantic_hits = (
                sum(
                    1
                    for item in observed.items
                    if isinstance(item, MemoryContextItem)
                    and "governed_semantic" in item.matched_by
                )
                if observed is not None and kind == "memory"
                else 0
            )
            timed_out = path.reason == "timeout_budget" or (
                observed.timed_out if observed is not None else False
            )
            diagnostic_failure_site = failure[0] if failure is not None else None
            diagnostic_error_class = failure[1] if failure is not None else None
            if observed is not None:
                diagnostic_failure_site = diagnostic_failure_site or observed.failure_site
                diagnostic_error_class = diagnostic_error_class or observed.error_class
            diagnostic_event(
                level="warn" if path.status in {"degraded", "unavailable"} else "info",
                component="context.search",
                event="context_lane.completed",
                outcome=path.status,
                request_id=final.request_id,
                duration_ms=round(path.elapsed_ms),
                details={
                    "operation_kind": kind,
                    "mode": ",".join(observed.searched_channels) if observed else "none",
                    "status": path.status,
                    "index_complete": observed.index_complete if observed else None,
                    **(
                        {"lexical_hits": lexical_hits, "semantic_hits": semantic_hits}
                        if kind == "memory"
                        else {}
                    ),
                    "reason": path.reason,
                    "truncated": path.truncated,
                    "timed_out": timed_out,
                    **(
                        {
                            "failure_site": diagnostic_failure_site,
                            "error_class": diagnostic_error_class,
                        }
                        if diagnostic_failure_site is not None
                        and diagnostic_error_class is not None
                        else {}
                    ),
                },
            )
        store = getattr(request.app.state, "call_event_store", None)
        if store is not None and value.query != "__om_probe__":
            event = context_call_event(final, access)
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
        return final

    return router


def sdk_context_router(dependencies: Sequence[Depends]) -> APIRouter:
    """SDK-native context: no Evidence/Governance ids or fabricated revisions."""
    router = APIRouter(prefix="/api/v2/context", dependencies=dependencies)

    @router.post("/search", response_model=SdkContextSearchResponse)
    async def search(
        value: ContextSearchRequest,
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

        bridge = getattr(request.app.state, "sdk_library_bridge", None)
        if bridge is None:
            raise HTTPException(503, "sdk_context_unavailable")

        async def raw_lane() -> tuple[SdkRawContextItem, ...]:
            if "raw" not in value.kinds or not authority.raw_allowed:
                return ()
            hits = await bridge.search_sdk_raw(
                value.query,
                user_id="local",
                source_ids=tuple(authority.raw_sources or ()),
                limit=value.limit,
            )
            return tuple(
                SdkRawContextItem(
                    raw_id=hit.raw_id,
                    content=hit.content,
                    source=hit.source,
                    session_id=hit.session_id,
                    memory_at=(
                        datetime.fromtimestamp(hit.memory_at, tz=UTC)
                        if hit.memory_at is not None
                        else None
                    ),
                    score=hit.score,
                )
                for hit in hits
            )

        async def memory_lane() -> tuple[SdkMemoryContextItem, ...]:
            if "memory" not in value.kinds or not authority.memory_allowed:
                return ()
            hits = await bridge.search_memories(
                value.query,
                user_id="local",
                agent_id="*",
                limit=value.limit,
            )
            items: list[SdkMemoryContextItem] = []
            for hit in hits:
                memory = await bridge.get_sdk_memory(hit.memory_id)
                if memory is None or memory.status != "active":
                    continue
                items.append(
                    SdkMemoryContextItem(
                        memory_id=memory.memory_id,
                        content=(
                            hit.formatted_content
                            if hit.sdk_formatted
                            and hit.formatting_complete
                            and hit.formatted_content
                            else memory.content
                        ),
                        layer=memory.layer,
                        status=memory.status,
                        source=memory.source,
                        updated_at=(
                            datetime.fromtimestamp(memory.gmt_created, tz=UTC)
                            if memory.gmt_created is not None
                            else None
                        ),
                        score=hit.score,
                    )
                )
            return tuple(items)

        lanes = (raw_lane(), memory_lane())
        try:
            raw_items, memory_items = await asyncio.wait_for(
                asyncio.gather(*lanes, return_exceptions=True),
                timeout=value.timeout_ms / 1000,
            )
        except TimeoutError:
            return SdkContextSearchResponse(
                request_id=str(uuid4()),
                state="unavailable",
                items=(),
                used_characters=0,
                truncated=True,
                total_ms=(time.monotonic() - started) * 1000,
            )
        partial = isinstance(raw_items, Exception) or isinstance(memory_items, Exception)
        raw_candidates = (
            () if isinstance(raw_items, Exception) else cast(tuple[SdkContextItem, ...], raw_items)
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
            selected.append(item)
            used += len(item.content)
        return SdkContextSearchResponse(
            request_id=str(uuid4()),
            state=("partial" if partial else "grounded" if selected else "empty"),
            items=tuple(selected),
            used_characters=used,
            truncated=truncated,
            total_ms=(time.monotonic() - started) * 1000,
        )

    return router
