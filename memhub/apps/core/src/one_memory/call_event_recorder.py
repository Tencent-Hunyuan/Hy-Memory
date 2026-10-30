"""Project only confirmed return metadata, never copy a query or response body."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from fastapi import Request, Response

from one_memory.call_event_store import (
    CallEvent,
    CallEventStore,
    CallReference,
    CallState,
    CallTool,
    HostId,
)
from one_memory.context_models import SdkContextSearchResponse
from one_memory.external_memory_access import ExternalMemoryAccess
from one_memory.recall import RecallReceipt

HOSTS = frozenset(
    {
        "cursor",
        "codex",
        "claude-code",
        "workbuddy",
        "opencode",
        "qwen-code",
        "hermes",
        "pi",
        "deepseek-harness",
        "zcode",
    }
)


def _host_id(access: ExternalMemoryAccess | None) -> HostId | None:
    host = access.host_id if access else None
    if host and host.startswith("deepseek-harness:"):
        host = "deepseek-harness"
    return cast(HostId, host) if host in HOSTS else None


def sdk_context_call_event(
    result: SdkContextSearchResponse,
    access: ExternalMemoryAccess | None,
    *,
    memory_searched: bool,
    timed_out: bool,
) -> CallEvent:
    # SDK items carry no Evidence/Governance revision, and the reference
    # resolver reads the legacy store, so the event records counts only.
    raw_count = sum(item.kind == "raw" for item in result.items)
    memory_count = sum(item.kind == "memory" for item in result.items)
    state: CallState = "timed_out" if timed_out else result.state
    return _metadata_event(
        tool="om_recall" if memory_searched else "om_conversations",
        state=state,
        elapsed_ms=result.total_ms,
        access=access,
        raw_count=raw_count,
        memory_count=memory_count,
        event_id=UUID(result.request_id),
    )


def _metadata_event(
    *,
    tool: CallTool,
    state: CallState,
    elapsed_ms: float,
    access: ExternalMemoryAccess | None,
    raw_count: int = 0,
    memory_count: int = 0,
    references: tuple[CallReference, ...] = (),
    event_id: UUID | None = None,
) -> CallEvent:
    return CallEvent(
        event_id=event_id or uuid4(),
        occurred_at=datetime.now(UTC),
        host_id=_host_id(access),
        installation_id=UUID(access.installation_id) if access else None,
        tool=tool,
        state=state,
        stage="core_result",
        delivery="unknown",
        adoption="unknown",
        host_session_id=None,
        injection_elapsed_ms=None,
        elapsed_ms=elapsed_ms,
        raw_count=raw_count,
        memory_count=memory_count,
        references=references,
    )


def ask_call_event(
    result: object,
    access: ExternalMemoryAccess | None,
    elapsed_ms: float,
) -> CallEvent:
    memories = tuple(getattr(result, "memories", ()) or ())
    references: list[CallReference] = []
    seen: set[tuple[str, int]] = set()
    for memory in memories:
        memory_id = getattr(memory, "memory_id", None)
        revision = getattr(memory, "revision", None)
        if not isinstance(memory_id, str) or not isinstance(revision, int):
            continue
        identity = (memory_id, revision)
        if identity in seen:
            continue
        seen.add(identity)
        references.append(
            CallReference(kind="memory", id=memory_id, revision=revision, source_id=None)
        )
    return _metadata_event(
        tool="om_ask",
        state="empty" if getattr(result, "mode", "empty") == "empty" else "grounded",
        elapsed_ms=elapsed_ms,
        access=access,
        memory_count=min(len(memories), 20),
        references=tuple(references[:128]),
    )


def matter_call_event(
    contexts: Sequence[object],
    access: ExternalMemoryAccess | None,
    elapsed_ms: float,
) -> CallEvent:
    return _metadata_event(
        tool="om_matters",
        state="grounded" if contexts else "empty",
        elapsed_ms=elapsed_ms,
        access=access,
    )


def try_record(
    request: Request,
    record: Callable[[CallEventStore], object],
    response: Response | None = None,
) -> None:
    store = getattr(request.app.state, "call_event_store", None)
    if store is None:
        return
    try:
        record(store)
        if response is not None:
            response.headers["X-One-Memory-Call-Recorded"] = "true"
    except Exception:
        request.app.state.call_event_recording_error = True
        if response is not None:
            response.headers["X-One-Memory-Call-Recorded"] = "false"


def record_legacy_result(
    store: CallEventStore,
    receipt: RecallReceipt,
    access: ExternalMemoryAccess | None,
    elapsed_ms: float,
) -> None:
    if receipt.query == "__om_probe__" or any(hit.kind == "self_check" for hit in receipt.hits):
        return
    # Legacy hits do not attest a revision. Do not invent references by looking
    # up a potentially newer version after the actual return was constructed.
    store.record(
        CallEvent(
            event_id=uuid4(),
            occurred_at=datetime.now(UTC),
            host_id=_host_id(access),
            installation_id=UUID(access.installation_id) if access else None,
            tool="om_recall",
            state=(
                "timed_out"
                if receipt.truncated_by is not None
                and receipt.truncated_by.value == "timeout_budget"
                and not receipt.hits
                else "partial"
                if receipt.truncated
                else receipt.state.value
            ),
            stage="core_result",
            delivery="unknown",
            adoption="unknown",
            host_session_id=None,
            injection_elapsed_ms=None,
            elapsed_ms=elapsed_ms,
            # A raw-fallback recall returned conversation excerpts, not memories.
            raw_count=len(receipt.hits) if receipt.mode == "raw" else 0,
            memory_count=0 if receipt.mode == "raw" else len(receipt.hits),
            references=(),
        )
    )
