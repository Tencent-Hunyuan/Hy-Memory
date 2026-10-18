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
from one_memory.context_models import ContextSearchResponse, MemoryContextItem, RawContextItem
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


def context_call_event(
    result: ContextSearchResponse,
    access: ExternalMemoryAccess | None,
) -> CallEvent:
    references: list[CallReference] = []
    seen: set[tuple[str, str, int]] = set()
    for item in result.items:
        if isinstance(item, RawContextItem):
            candidates = [
                CallReference(
                    kind="raw",
                    id=message.evidence_id,
                    revision=message.revision,
                    source_id=item.source_id,
                )
                for message in item.messages
            ]
        elif isinstance(item, MemoryContextItem):
            candidates = [
                CallReference(
                    kind="memory", id=item.memory_id, revision=item.revision, source_id=None
                )
            ]
        else:
            continue
        for reference in candidates:
            identity = (reference.kind, reference.id, reference.revision)
            if identity not in seen:
                seen.add(identity)
                references.append(reference)
    timed_out = any(
        path.reason == "timeout_budget" for path in (result.paths.raw, result.paths.memory)
    )
    state: CallState = (
        "partial" if timed_out and references else "timed_out" if timed_out else result.state
    )
    return CallEvent.model_validate(
        {
            "event_id": UUID(result.request_id),
            "occurred_at": datetime.now(UTC),
            "host_id": _host_id(access),
            "installation_id": UUID(access.installation_id) if access else None,
            "tool": _context_tool(result),
            "state": state,
            "stage": "core_result",
            "delivery": "unknown",
            "adoption": "unknown",
            "host_session_id": None,
            "injection_elapsed_ms": None,
            "elapsed_ms": result.total_ms,
            "raw_count": sum(isinstance(item, RawContextItem) for item in result.items),
            "memory_count": sum(isinstance(item, MemoryContextItem) for item in result.items),
            "references": tuple(references),
        }
    )


def record_context_result(
    store: CallEventStore,
    result: ContextSearchResponse,
    access: ExternalMemoryAccess | None,
) -> None:
    store.record(context_call_event(result, access))


def _context_tool(result: ContextSearchResponse) -> CallTool:
    if result.paths.memory.status not in {"not_requested", "not_authorized"}:
        return "om_recall"
    return "om_conversations"


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


def conversation_call_event(
    result: object,
    access: ExternalMemoryAccess | None,
    elapsed_ms: float,
) -> CallEvent:
    hits = tuple(getattr(result, "hits", ()) or ())
    conversations = {
        conversation_id
        for hit in hits
        if (conversation_id := getattr(hit, "conversation_id", None))
    }
    return _metadata_event(
        tool="om_conversations",
        state="grounded" if hits else "empty",
        elapsed_ms=elapsed_ms,
        access=access,
        raw_count=min(len(conversations), 20),
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
            raw_count=0,
            memory_count=len(receipt.hits),
            references=(),
        )
    )
