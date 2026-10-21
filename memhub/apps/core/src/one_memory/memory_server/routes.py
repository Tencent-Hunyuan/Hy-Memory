"""A3-01: the HTTP entry to the unified write path.

`POST /api/v1/memory-writes` accepts a write and returns a request id;
`GET /api/v1/memory-writes/{id}` reports its outcome.

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

from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import (
    DurableQueueSchemaUnavailableError,
    DurableWriteRegistry,
)
from one_memory.memory_server.registry import WriteRegistry, WriteState
from one_memory.memory_server.submit import WriteConflictError


class MemoryWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_session_id: str = Field(min_length=1, max_length=128)
    first_round: int = Field(ge=1)
    last_round: int = Field(ge=1)
    evidence_ids: list[str] = Field(min_length=1)
    priority: Literal["live", "normal"] = "normal"
    idempotency_key: str | None = Field(default=None, max_length=256)


class MemoryWriteReceipt(BaseModel):
    request_id: str
    state: WriteState
    created_at: str
    updated_at: str
    detail: str = ""
    raw_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()


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
        registry = DurableWriteRegistry(database_path, evidence=evidence)
        request.app.state.memory_write_registry = registry
    if registry is None:
        raise HTTPException(503, "memory write registry unavailable")
    return cast(WriteRegistry | DurableWriteRegistry, registry)


def router(dependencies: list[Any] | None = None) -> APIRouter:
    instance = APIRouter(
        prefix="/api/v1/memory-writes",
        tags=["memory-writes"],
        dependencies=dependencies or [],
    )

    @instance.post("", response_model=MemoryWriteReceipt, status_code=202)
    def submit(payload: MemoryWriteInput, request: Request) -> MemoryWriteReceipt:
        registry = _registry(request)
        batch = BatchIdentity(
            internal_session_id=payload.internal_session_id,
            first_round=payload.first_round,
            last_round=payload.last_round,
        )
        request_contract = WriteRequest(
            internal_session_id=payload.internal_session_id,
            batch_identity=batch,
            evidence_ids=payload.evidence_ids,
            idempotency_key=payload.idempotency_key or batch.batch_id,
            priority=payload.priority,
        )
        try:
            record, enqueued = registry.submit(request_contract)
        except WriteConflictError as error:
            raise HTTPException(409, str(error)) from error
        except LookupError as error:
            # Evidence-first: the write points at Evidence the business
            # database does not hold. Rejecting it here is a caller error, not
            # a server fault, and it must not create a queue row.
            raise HTTPException(422, str(error)) from error
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
