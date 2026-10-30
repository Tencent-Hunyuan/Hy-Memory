"""Authenticated SDK context search route, without generation or reindex side effects."""

import asyncio
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
from one_memory.external_memory_access import external_access
from one_memory.hy_runtime_config import HY_MEMORY_READER
from one_memory.hy_sdk_runtime import sdk_relevance_floor
from one_memory.hy_sdk_types import (
    HySdkMemoryRecord,
    parse_sdk_raw_search,
    parse_sdk_search,
)
from one_memory.imports.storage import EvidenceStore

CALL_EVENT_RESPONSE_BUDGET_MS = 250


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

        async def raw_lane() -> tuple[SdkRawContextItem, ...]:
            if "raw" not in value.kinds or not authority.raw_allowed:
                return ()
            raw = await asyncio.to_thread(
                client.search,
                value.query,
                user_ids=["local"],
                agent_ids=[],
                include_sources=list(authority.raw_sources or ()),
                limit=value.limit,
                min_score=0.0,
                schema_min_score=0.0,
                schema_limit=0,
                intention_limit=0,
                scene="chat",
                reader=HY_MEMORY_READER,
                layers=["l1_raw"],
            )
            hits = parse_sdk_raw_search(raw)
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
            raw = await asyncio.to_thread(
                client.search,
                value.query,
                user_ids=["local"],
                agent_ids=[],
                limit=value.limit,
                min_score=sdk_relevance_floor(),
                schema_min_score=sdk_relevance_floor(),
                schema_limit=value.schema_limit,
                intention_limit=value.intention_limit,
                scene="normal",
                reader=HY_MEMORY_READER,
            )
            hits = parse_sdk_search(raw, error_code="hy_memory_search_shape_unknown")
            items: list[SdkMemoryContextItem] = []
            for hit in hits:
                record = await asyncio.to_thread(client.get, hit.memory_id)
                memory = None if record is None else HySdkMemoryRecord.from_sdk(record)
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
            )
            await record(final, timed_out=True)
            return final
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
        final = SdkContextSearchResponse(
            request_id=str(uuid4()),
            state=("partial" if partial else "grounded" if selected else "empty"),
            items=tuple(selected),
            used_characters=used,
            truncated=truncated,
            total_ms=(time.monotonic() - started) * 1000,
        )
        await record(final, timed_out=False)
        return final

    return router
