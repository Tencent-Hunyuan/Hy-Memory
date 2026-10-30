"""A3-01: the HTTP entry to the unified write path.

`POST /api/v2/memory-writes` accepts OpenAI-style messages and returns a
request id; `GET /api/v2/memory-writes/{id}` reports its outcome.

Two rules shape this endpoint, both consequences of A0-03's measurement that HY
has no idempotency key:

- A replayed write (same key, same payload) returns the **existing** record with
  200, not a second write. Duplicating a memory is worse than admitting it
  already exists.
- A conflicting reuse (same key, different payload) is **409**. Overwriting
  would discard the earlier intent; accepting would leave two writes claiming
  one identity.

The endpoint never calls HY. It records acceptance and returns an id; the queue
does the writing. That separation is D52.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
    materialize_write_input,
)
from one_memory.memory_server.contracts import (
    BatchIdentity,
    WriteRequest,
    internal_session_id_for,
)
from one_memory.memory_server.durable_queue import (
    DurableQueueSchemaUnavailableError,
    DurableWriteRegistry,
)
from one_memory.memory_server.registry import WriteRegistry, WriteState
from one_memory.memory_server.submit import WriteConflictError


class MemoryWriteReceipt(BaseModel):
    request_id: str
    state: WriteState
    created_at: str
    updated_at: str
    detail: str = ""
    raw_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "tool"]
    content: str = Field(min_length=1, max_length=12_000)


class SdkMemoryWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=512)
    source_id: str = Field(default="memhub-cli", min_length=1, max_length=128)
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=200)
    memory_at: datetime | None = None
    priority: Literal["live", "normal"] = "normal"


def _registry(
    request: Request,
    *,
    write: bool = True,
) -> WriteRegistry | DurableWriteRegistry:
    # The durable path wins when configured: `api.py` always installs an
    # in-memory `WriteRegistry`, so preferring that first would silently keep
    # accepts non-durable even after `memory_write_queue_path` is set.
    database_path = getattr(request.app.state, "memory_write_queue_path", None)
    registry = getattr(request.app.state, "memory_write_registry", None)
    if database_path is not None and not write:
        # A status GET must not run the durable constructor's schema DDL or
        # `reclaim_expired()`. Use a non-initializing registry and do not cache
        # it over the writable one.
        evidence = getattr(request.app.state, "memory_write_evidence_store", None)
        return DurableWriteRegistry(database_path, evidence=evidence, initialize=False)
    if database_path is not None and not isinstance(registry, DurableWriteRegistry):
        # Evidence-first is only enforced when the queue is handed an
        # EvidenceStore. Passing the one the composition root already holds
        # makes `submit` reject ids the business database does not contain,
        # instead of deferring that to the drain as a late `failed` job.
        evidence = getattr(request.app.state, "memory_write_evidence_store", None)
        authority = getattr(request.app.state, "memory_authority", None)
        if getattr(authority, "value", authority) == "sdk":
            registry = DurableWriteRegistry(
                database_path,
                evidence=None,
                sdk_authority=True,
            )
        else:
            registry = DurableWriteRegistry(database_path, evidence=evidence)
        request.app.state.memory_write_registry = registry
    if registry is None:
        raise HTTPException(503, "memory write registry unavailable")
    return cast(WriteRegistry | DurableWriteRegistry, registry)


def sdk_router(dependencies: list[Any] | None = None) -> APIRouter:
    instance = APIRouter(
        prefix="/api/v2/memory-writes",
        tags=["memory-writes"],
        dependencies=dependencies or [],
    )

    @instance.post("", response_model=MemoryWriteReceipt, status_code=202)
    def submit_messages(
        payload: SdkMemoryWriteInput,
        request: Request,
    ) -> MemoryWriteReceipt:
        if not any(message.role == "user" for message in payload.messages):
            raise HTTPException(422, "messages_require_user_turn")
        observed_at = payload.memory_at or datetime(1970, 1, 1, tzinfo=UTC)
        written_by = {
            "user": WrittenBy.PERSON,
            "assistant": WrittenBy.AGENT,
            "tool": WrittenBy.TOOL,
        }
        records = tuple(
            materialize_write_input(
                EvidenceInput(
                    source_id=payload.source_id,
                    conversation_id=payload.session_id,
                    message_id=f"{payload.request_id}:{index}",
                    role=message.role,
                    written_by=written_by[message.role],
                    created_at=payload.memory_at,
                    verbatim=message.content,
                    origin_meta={
                        EvidenceOrigin.MANUAL_IMPORT: OriginMetadata(
                            raw_meta={
                                "input_mode": "openai_messages",
                                "request_id": payload.request_id,
                                "message_index": index,
                            }
                        )
                    },
                ),
                observed_at=observed_at,
            )
            for index, message in enumerate(payload.messages)
        )
        internal_session_id = internal_session_id_for(
            payload.session_id,
            payload.source_id,
        )
        contract = WriteRequest(
            internal_session_id=internal_session_id,
            source_id=payload.source_id,
            batch_identity=BatchIdentity(
                internal_session_id=internal_session_id,
                first_round=1,
                last_round=len(records),
            ),
            evidence_ids=[record.evidence_id for record in records],
            input_records=records,
            idempotency_key=payload.request_id,
            priority=payload.priority,
            event_at=payload.memory_at,
            time_quality="exact" if payload.memory_at is not None else "approximate",
        )
        try:
            record, enqueued = _registry(request).submit(contract)
        except (WriteConflictError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return MemoryWriteReceipt(
            request_id=record.request_id,
            state=record.state,
            created_at=record.created_at,
            updated_at=record.updated_at,
            detail="" if enqueued else "already accepted; not enqueued again",
        )

    @instance.get("/{request_id}", response_model=MemoryWriteReceipt)
    def status(request_id: str, request: Request) -> MemoryWriteReceipt:
        registry = _registry(request, write=False)
        try:
            record = registry.get(request_id)
        except DurableQueueSchemaUnavailableError as error:
            raise HTTPException(503, str(error)) from error
        if record is None:
            raise HTTPException(404, "unknown request id")
        return MemoryWriteReceipt(
            request_id=record.request_id,
            state=record.state,
            created_at=record.created_at,
            updated_at=record.updated_at,
            detail=record.detail,
            raw_ids=record.raw_ids,
            memory_ids=record.memory_ids,
        )

    return instance
