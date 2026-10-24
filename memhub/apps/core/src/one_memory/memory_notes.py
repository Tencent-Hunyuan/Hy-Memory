import asyncio
import json
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
MemoryNoteState = Literal[
    "waiting_for_model",
    "waiting_for_permission",
    "pending",
    "running",
    "failed",
    "ready",
    "no_memories",
]


class MemoryNoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    text: str = Field(min_length=1, max_length=12_000)
    scope: Literal["all-apps"]

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory_note_empty")
        return value


class MemoryNoteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    text: str
    scope: Literal["all-apps"] = "all-apps"
    created_at: datetime
    state: MemoryNoteState
    task_id: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class SavedMemoryNoteEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    available: Literal[True] = True
    evidence_id: str
    source_id: str
    revision: int
    imported_at: datetime
    role: str
    written_by: WrittenBy
    created_at: datetime | None
    verbatim: str


class LegacyMemoryNoteReader:
    """Read pre-cutover note receipts without retaining any write runtime."""

    def __init__(
        self,
        tasks: MemoryTaskStore,
        governance: MemoryGovernanceStore,
    ) -> None:
        self.tasks = tasks
        self.governance = governance


class MemoryNoteStore:
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
        # manual note reaches the durable queue like every other entry. It stays
        # optional so isolated tests can keep constructing the store directly.
        self.intake = intake
        self.sdk_authority = sdk_authority
        self.write_queue = write_queue

    def save(self, note: MemoryNoteInput) -> EvidenceRecord:
        now = datetime.now(UTC)
        value = EvidenceInput(
            source_id=SOURCE_ID,
            conversation_id=f"note:{note.request_id}",
            message_id=str(note.request_id),
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=now,
            verbatim=note.text,
            origin_meta={
                EvidenceOrigin.MANUAL_IMPORT: OriginMetadata(
                    raw_meta={
                        "input_mode": "direct_memory_note",
                        "recall_scope": note.scope,
                    }
                )
            },
        )
        if self.sdk_authority:
            if self.intake is None or self.write_queue is None:
                raise RuntimeError("sdk_memory_note_intake_unavailable")
            record = materialize_write_input(value, observed_at=now)
            with main_database(self.write_queue.database_path).read() as database:
                existing = database.execute(
                    "SELECT request_id FROM memory_note_requests WHERE note_id=?",
                    (record.evidence_id,),
                ).fetchone()
            if existing is not None:
                current = self.require(record.evidence_id)
                if current.verbatim != record.verbatim:
                    raise ValueError("memory_note_request_conflict")
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
                raise ValueError("memory_note_source_revoked")
            existing = db.execute(
                "SELECT verbatim FROM evidence WHERE evidence_id=?", (value.evidence_id,)
            ).fetchone()
            if existing:
                if existing[0] != note.text:
                    raise ValueError("memory_note_request_conflict")
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
            raise ValueError("memory_note_source_revoked")
        return record

    def receipt(self, evidence_id: str) -> MemoryNoteReceipt:
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
            mapped: MemoryNoteState = {
                "pending": "pending",
                "running": "running",
                "waiting_for_model": "waiting_for_model",
                "waiting_for_permission": "waiting_for_permission",
                "completed": (
                    "ready" if durable is not None and durable.sdk_memory_ids else "no_memories"
                ),
            }.get(durable_state, "failed")
            return MemoryNoteReceipt(
                evidence_id=evidence_id,
                text=sanitize_model_input(record.verbatim),
                created_at=record.created_at or record.imported_at,
                state=mapped,
                memory_ids=(list(durable.sdk_memory_ids) if durable is not None else []),
                error=durable.detail if durable is not None else "write_missing",
            )
        if self.processor is None:
            raise RuntimeError("legacy_memory_note_processor_unavailable")
        task = self.processor.tasks.task_for_evidence(evidence_id)
        memory_ids = []
        if task:
            for output in self.processor.tasks.outputs(task.task_id):
                memory = self.processor.governance.get(output.memory_id, user_id="local")
                if memory and memory.status is MemoryStatus.CONFIRMED:
                    memory_ids.append(memory.memory_id)
        state: MemoryNoteState
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
        return MemoryNoteReceipt(
            evidence_id=record.evidence_id,
            text=sanitize_model_input(record.verbatim),
            created_at=record.created_at or record.imported_at,
            state=state,
            task_id=task.task_id if task else None,
            memory_ids=memory_ids,
            error=error,
        )

    def list(self) -> list[MemoryNoteReceipt]:
        if self.sdk_authority:
            assert self.write_queue is not None
            with main_database(self.write_queue.database_path).read() as database:
                ids = [
                    str(row[0])
                    for row in database.execute(
                        "SELECT note_id FROM memory_note_requests ORDER BY created_at DESC LIMIT 50"
                    )
                ]
            return [self.receipt(key) for key in ids]
        with main_database(self.evidence.database_path).read() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT e.evidence_id,o.raw_meta_json FROM evidence e "
                    "JOIN source_grants g USING(user_id,source_id) "
                    "JOIN evidence_origins o USING(evidence_id) WHERE e.user_id='local' "
                    "AND e.source_id=? AND g.revoked_at IS NULL AND o.origin='manual_import' "
                    "ORDER BY e.imported_at DESC,e.evidence_id LIMIT 50",
                    (SOURCE_ID,),
                )
                if json.loads(row[1]).get("input_mode") == "direct_memory_note"
            ]
        return [self.receipt(key) for key in ids]

    def receipt_for_request(self, request_id: UUID) -> MemoryNoteReceipt:
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


def memory_note_router(dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/memory-notes", tags=["memory-notes"], dependencies=dependencies
    )

    def store(request: Request) -> MemoryNoteStore:
        return cast(MemoryNoteStore, request.app.state.memory_note_store)

    async def schedule(
        notes: MemoryNoteStore,
        record: EvidenceRecord,
        *,
        readmit: bool = False,
    ) -> MemoryNoteReceipt:
        try:
            if notes.sdk_authority:
                return await asyncio.to_thread(notes.receipt, record.evidence_id)
            if notes.processor is None:
                raise RuntimeError("legacy_memory_note_processor_unavailable")
            if notes.intake is None:
                # Isolated tests construct the store without a queue.
                await notes.processor.enqueue_conversation_off_thread(record)
            else:
                # Production: submit to the durable queue first. The drain runs
                # the same supervisor path, so create/process semantics are
                # unchanged; replaying the same request returns the original
                # `entry:<evidence_id>` job instead of a second HY call.
                if readmit:
                    # `/{evidence_id}/process` is the explicit retry signal.
                    # It also revives pre-upgrade permission waiters.
                    await asyncio.to_thread(notes.intake.queue.readmit_parked)
                submitted = await asyncio.to_thread(
                    notes.intake.submit_evidence,
                    record,
                    first_round=1,
                    last_round=1,
                    priority="live",
                )
                if readmit:
                    await asyncio.to_thread(
                        notes.intake.readmit_cancelled_evidence,
                        record,
                        job_id=submitted.record.job_id,
                        first_round=1,
                        last_round=1,
                        priority="live",
                    )
        except SecretStoreUnavailableError:
            saved = await asyncio.to_thread(notes.receipt, record.evidence_id)
            return saved.model_copy(
                update={"state": "waiting_for_model", "error": "secure_storage_unavailable"}
            )
        return await asyncio.to_thread(notes.receipt, record.evidence_id)

    @router.post("", response_model=MemoryNoteReceipt, status_code=201)
    async def create(
        note: MemoryNoteInput,
        request: Request,
        background: BackgroundTasks,
    ) -> MemoryNoteReceipt:
        notes = store(request)
        try:
            record = await asyncio.to_thread(notes.save, note)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        saved = await asyncio.to_thread(notes.receipt, record.evidence_id)
        background.add_task(schedule, notes, record)
        return saved

    @router.get("", response_model=list[MemoryNoteReceipt])
    def listing(request: Request) -> list[MemoryNoteReceipt]:
        return store(request).list()

    @router.get("/operations/{request_id}", response_model=MemoryNoteReceipt)
    def operation(request_id: UUID, request: Request) -> MemoryNoteReceipt:
        try:
            return store(request).receipt_for_request(request_id)
        except KeyError as error:
            raise HTTPException(404, "memory_note_operation_not_found") from error

    @router.get("/{evidence_id}", response_model=MemoryNoteReceipt)
    def get(evidence_id: str, request: Request) -> MemoryNoteReceipt:
        try:
            return store(request).receipt(evidence_id)
        except KeyError as error:
            raise HTTPException(404, "memory_note_not_found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/{evidence_id}/evidence", response_model=SavedMemoryNoteEvidence)
    def evidence(evidence_id: str, request: Request) -> SavedMemoryNoteEvidence:
        try:
            notes = store(request)
            if notes.sdk_authority:
                raise HTTPException(409, "memory_note_evidence_retired")
            with notes.evidence._connect() as connection:
                record = notes.evidence._get_with_connection(connection, evidence_id)
                metadata = record.origin_meta.get(EvidenceOrigin.MANUAL_IMPORT) if record else None
                if (
                    record is None
                    or record.user_id != "local"
                    or record.source_id != SOURCE_ID
                    or metadata is None
                    or metadata.raw_meta.get("input_mode") != "direct_memory_note"
                ):
                    raise KeyError(evidence_id)
                grant = connection.execute(
                    "SELECT revoked_at FROM source_grants WHERE user_id='local' AND source_id=?",
                    (SOURCE_ID,),
                ).fetchone()
                if grant is None or grant[0] is not None:
                    raise ValueError("memory_note_source_revoked")
                result = SavedMemoryNoteEvidence(
                    evidence_id=record.evidence_id,
                    source_id=record.source_id,
                    revision=record.revision,
                    imported_at=record.imported_at,
                    role=record.role,
                    written_by=record.written_by,
                    created_at=record.created_at,
                    verbatim=record.verbatim,
                )
            # The returned body and reference share one snapshot. Recheck authority
            # after that read so a revoked source is not released from a stale view.
            notes.require(evidence_id)
            return result
        except KeyError as error:
            raise HTTPException(404, "memory_note_not_found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/{evidence_id}/process", response_model=MemoryNoteReceipt)
    async def process(evidence_id: str, request: Request) -> MemoryNoteReceipt:
        notes = store(request)
        try:
            record = await asyncio.to_thread(notes.require, evidence_id)
        except KeyError as error:
            raise HTTPException(404, "memory_note_not_found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        # This route is the explicit "the condition that parked it may have
        # changed" signal (the person just granted consent or fixed the model),
        # which is when `readmit_parked` is meant to run.
        return await schedule(notes, record, readmit=True)

    return router
