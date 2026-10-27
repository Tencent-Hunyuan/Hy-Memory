"""Add one memory directly: `POST /api/v2/memories` and its status read.

The public API has no "notes": a hand-added memory is filed as manual Evidence
and turned into memories by the durable write queue. Storage keeps its
pre-rename identifiers (`memory_note_requests`, source `one-memory-manual`,
`input_mode=direct_memory_note`, conversation `note:<request_id>`) because they
are persisted and feed evidence ids; renaming them would change stored data.
"""

import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.params import Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from one_memory.database_access import main_database
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    EvidenceRecord,
    OriginMetadata,
    WrittenBy,
    materialize_write_input,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.input_sanitization import sanitize_model_input
from one_memory.memory_governance import MemoryGovernanceStore, MemoryStatus
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.memory_server.unified_entry import UnifiedWriteIntake
from one_memory.memory_tasks import MemoryTaskStatus, MemoryTaskStore
from one_memory.secrets import SecretStoreUnavailableError

SOURCE_ID = "one-memory-manual"
MemoryAddState = Literal[
    "waiting_for_model",
    "waiting_for_permission",
    "pending",
    "running",
    "failed",
    "ready",
    "no_memories",
]


class MemoryAddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    text: str = Field(min_length=1, max_length=12_000)
    scope: Literal["all-apps"]

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory_add_empty")
        return value


class MemoryAddReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    text: str
    scope: Literal["all-apps"] = "all-apps"
    created_at: datetime
    state: MemoryAddState
    task_id: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class LegacyMemoryAdditionReader:
    """Read pre-cutover addition receipts without retaining any write runtime."""

    def __init__(
        self,
        tasks: MemoryTaskStore,
        governance: MemoryGovernanceStore,
    ) -> None:
        self.tasks = tasks
        self.governance = governance


class MemoryAdditionStore:
    def __init__(
        self,
        evidence: EvidenceStore,
        processor: Any | None,
        intake: UnifiedWriteIntake | None = None,
        *,
        sdk_authority: bool = False,
        write_queue: DurableWriteQueue | None = None,
    ) -> None:
        self.evidence = evidence
        self.processor = processor
        # D52: the production composition root passes a UnifiedWriteIntake so a
        # manual addition reaches the durable queue like every other entry. It stays
        # optional so isolated tests can keep constructing the store directly.
        self.intake = intake
        self.sdk_authority = sdk_authority
        self.write_queue = write_queue

    def save(self, addition: MemoryAddInput) -> EvidenceRecord:
        now = datetime.now(UTC)
        value = EvidenceInput(
            source_id=SOURCE_ID,
            conversation_id=f"note:{addition.request_id}",
            message_id=str(addition.request_id),
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=now,
            verbatim=addition.text,
            origin_meta={
                EvidenceOrigin.MANUAL_IMPORT: OriginMetadata(
                    raw_meta={
                        "input_mode": "direct_memory_note",
                        "recall_scope": addition.scope,
                    }
                )
            },
        )
        if self.sdk_authority:
            if self.intake is None or self.write_queue is None:
                raise RuntimeError("sdk_memory_add_intake_unavailable")
            record = materialize_write_input(value, observed_at=now)
            with main_database(self.write_queue.database_path).read() as database:
                # No grant row is written here: a missing row means allowed,
                # only an explicit revocation refuses (same as the claim gate).
                grant = database.execute(
                    "SELECT revoked_at FROM source_grants WHERE user_id='local' AND source_id=?",
                    (SOURCE_ID,),
                ).fetchone()
                existing = database.execute(
                    "SELECT request_id FROM memory_note_requests WHERE note_id=?",
                    (record.evidence_id,),
                ).fetchone()
            if grant is not None and grant[0] is not None:
                raise ValueError("memory_add_source_revoked")
            if existing is not None:
                current = self.require(record.evidence_id)
                if current.verbatim != record.verbatim:
                    raise ValueError("memory_add_conflict")
                return current
            receipt = self.intake.submit_records(
                (record,),
                priority="live",
            )
            with main_database(self.write_queue.database_path).write(autocommit=True) as database:
                database.execute(
                    "INSERT INTO memory_note_requests(note_id,request_id,created_at) "
                    "VALUES (?,?,?) ON CONFLICT(note_id) DO NOTHING",
                    (
                        record.evidence_id,
                        receipt.request_id,
                        now.isoformat(),
                    ),
                )
            return record
        with (
            main_database(self.evidence.database_path).write(
                foreign_keys=True, busy_timeout_ms=15_000
            ) as db,
            db,
        ):
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            grant = db.execute(
                "SELECT revoked_at FROM source_grants WHERE user_id='local' AND source_id=?",
                (SOURCE_ID,),
            ).fetchone()
            if grant and grant[0] is not None:
                raise ValueError("memory_add_source_revoked")
            existing = db.execute(
                "SELECT verbatim FROM evidence WHERE evidence_id=?", (value.evidence_id,)
            ).fetchone()
            if existing:
                if existing[0] != addition.text:
                    raise ValueError("memory_add_conflict")
            else:
                self.evidence.put(value, connection=db)
                db.execute(
                    "INSERT INTO source_grants "
                    "(user_id,source_id,root_path,requested_path,root_identity,candidate_kind,"
                    "granted_at,revoked_at,revision) "
                    "VALUES ('local',?,?,?,?,'default',?,NULL,1) "
                    "ON CONFLICT(user_id,source_id) DO NOTHING",
                    (
                        SOURCE_ID,
                        str(self.evidence.database_path.parent),
                        str(self.evidence.database_path.parent),
                        "manual",
                        now.isoformat(),
                    ),
                )
        return self.require(value.evidence_id)

    def require(self, evidence_id: str) -> EvidenceRecord:
        if self.sdk_authority:
            if self.write_queue is None:
                raise KeyError(evidence_id)
            with main_database(self.write_queue.database_path).read() as database:
                row = database.execute(
                    "SELECT request_id FROM memory_note_requests WHERE note_id=?",
                    (evidence_id,),
                ).fetchone()
            if row is None:
                raise KeyError(evidence_id)
            durable = self.write_queue.get(str(row[0]))
            payload = self.write_queue.payload_for(durable.job_id) if durable is not None else None
            if payload is None:
                raise KeyError(evidence_id)
            record = next(
                (item for item in payload.input_records if item.evidence_id == evidence_id),
                None,
            )
            if record is None:
                raise KeyError(evidence_id)
            return record
        record = self.evidence.get(evidence_id)
        if record is None or record.user_id != "local" or record.source_id != SOURCE_ID:
            raise KeyError(evidence_id)
        metadata = record.origin_meta.get(EvidenceOrigin.MANUAL_IMPORT)
        if metadata is None or metadata.raw_meta.get("input_mode") != "direct_memory_note":
            raise KeyError(evidence_id)
        with main_database(self.evidence.database_path).read() as db:
            grant = db.execute(
                "SELECT revoked_at FROM source_grants WHERE user_id='local' AND source_id=?",
                (SOURCE_ID,),
            ).fetchone()
        if grant is None or grant[0] is not None:
            raise ValueError("memory_add_source_revoked")
        return record

    def receipt(self, evidence_id: str) -> MemoryAddReceipt:
        record = self.require(evidence_id)
        if self.sdk_authority:
            assert self.write_queue is not None
            with main_database(self.write_queue.database_path).read() as database:
                request_id = database.execute(
                    "SELECT request_id FROM memory_note_requests WHERE note_id=?",
                    (evidence_id,),
                ).fetchone()[0]
            durable = self.write_queue.get(str(request_id))
            durable_state = durable.state if durable is not None else "failed"
            mapped: MemoryAddState = {
                "pending": "pending",
                "running": "running",
                "waiting_for_model": "waiting_for_model",
                "waiting_for_permission": "waiting_for_permission",
                "completed": (
                    "ready" if durable is not None and durable.sdk_memory_ids else "no_memories"
                ),
            }.get(durable_state, "failed")
            return MemoryAddReceipt(
                evidence_id=evidence_id,
                text=sanitize_model_input(record.verbatim),
                created_at=record.created_at or record.imported_at,
                state=mapped,
                memory_ids=(list(durable.sdk_memory_ids) if durable is not None else []),
                error=durable.detail if durable is not None else "write_missing",
            )
        if self.processor is None:
            raise RuntimeError("legacy_memory_add_processor_unavailable")
        task = self.processor.tasks.task_for_evidence(evidence_id)
        memory_ids = []
        if task:
            for output in self.processor.tasks.outputs(task.task_id):
                memory = self.processor.governance.get(output.memory_id, user_id="local")
                if memory and memory.status is MemoryStatus.CONFIRMED:
                    memory_ids.append(memory.memory_id)
        state: MemoryAddState
        if task is None or task.status == MemoryTaskStatus.WAITING_FOR_MODEL:
            state = "waiting_for_model"
        elif task.status == MemoryTaskStatus.WAITING_FOR_PERMISSION:
            # Upgrade compatibility: old rows are claimable and will leave
            # this state as soon as the direct SDK runner claims them.
            state = "waiting_for_permission"
        elif task.status == MemoryTaskStatus.COMPLETED:
            state = "ready" if memory_ids else "no_memories"
        elif task.status == MemoryTaskStatus.RUNNING:
            state = "running"
        elif task.status == MemoryTaskStatus.PENDING:
            state = "pending"
        else:
            state = "failed"
        error = task.last_error_code if task else None
        return MemoryAddReceipt(
            evidence_id=record.evidence_id,
            text=sanitize_model_input(record.verbatim),
            created_at=record.created_at or record.imported_at,
            state=state,
            task_id=task.task_id if task else None,
            memory_ids=memory_ids,
            error=error,
        )

    def receipt_for_request(self, request_id: UUID) -> MemoryAddReceipt:
        identity = str(request_id)
        if self.sdk_authority:
            probe = EvidenceInput(
                source_id=SOURCE_ID,
                conversation_id=f"note:{identity}",
                message_id=identity,
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim="lookup",
                origin_meta={
                    EvidenceOrigin.MANUAL_IMPORT: OriginMetadata(
                        raw_meta={"input_mode": "direct_memory_note"}
                    )
                },
            )
            return self.receipt(probe.evidence_id)
        with main_database(self.evidence.database_path).read() as db:
            row = db.execute(
                "SELECT evidence_id FROM evidence WHERE user_id='local' AND source_id=? "
                "AND conversation_id=? AND message_id=?",
                (SOURCE_ID, f"note:{identity}", identity),
            ).fetchone()
        if row is None:
            raise KeyError(identity)
        return self.receipt(str(row[0]))


def memory_add_router(dependencies: Sequence[Depends]) -> APIRouter:
    """`POST /api/v2/memories` and `GET /api/v2/memories/additions/{request_id}`.

    Both paths are outside the single-segment `/api/v2/memories/{memory_id}`
    routes; external (enveloped) callers are refused by
    `require_external_endpoint` because neither is a broker read route.
    """
    router = APIRouter(prefix="/api/v2/memories", tags=["memories"], dependencies=dependencies)

    def store(request: Request) -> MemoryAdditionStore:
        return cast(MemoryAdditionStore, request.app.state.memory_addition_store)

    async def schedule(additions: MemoryAdditionStore, record: EvidenceRecord) -> None:
        try:
            if additions.sdk_authority:
                return
            if additions.processor is None:
                raise RuntimeError("legacy_memory_add_processor_unavailable")
            if additions.intake is None:
                # Isolated tests construct the store without a queue.
                await additions.processor.enqueue_conversation_off_thread(record)
            else:
                # Production: submit to the durable queue. Replaying the same
                # request returns the original `entry:<evidence_id>` job
                # instead of a second HY call.
                await asyncio.to_thread(
                    additions.intake.submit_evidence,
                    record,
                    first_round=1,
                    last_round=1,
                    priority="live",
                )
        except SecretStoreUnavailableError:
            # The receipt already reports the saved text; the queue retries
            # once secure storage is back.
            return

    @router.post("", response_model=MemoryAddReceipt, status_code=201)
    async def add_memory(
        addition: MemoryAddInput,
        request: Request,
        background: BackgroundTasks,
    ) -> MemoryAddReceipt:
        additions = store(request)
        try:
            record = await asyncio.to_thread(additions.save, addition)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        saved = await asyncio.to_thread(additions.receipt, record.evidence_id)
        background.add_task(schedule, additions, record)
        return saved

    @router.get("/additions/{request_id}", response_model=MemoryAddReceipt)
    def memory_add_status(request_id: UUID, request: Request) -> MemoryAddReceipt:
        try:
            return store(request).receipt_for_request(request_id)
        except KeyError as error:
            raise HTTPException(404, "memory_add_not_found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router
