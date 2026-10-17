from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from one_memory.database import take_the_write_lock_first
from one_memory.database_access import main_database
from one_memory.imports.models import EvidenceRecord
from one_memory.memory_server.migrations import memory_tasks_ddl
from one_memory.model_profiles import ModelProfile, ModelProfileSnapshot


class MemoryTaskKind(StrEnum):
    EXTRACT = "extract"
    SYSTEM2 = "system2"
    ULTRA = "ultra"
    SWEEPER = "sweeper"


class MemoryTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_PERMISSION = "waiting_for_permission"
    WAITING_FOR_MODEL = "waiting_for_model"
    UNKNOWN = "unknown"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: Evidence is older than the extraction window. Still in the store and
    #: searchable; automatic extraction will not take it until the window is
    #: widened or the user asks for it by hand.
    DEFERRED_OUTSIDE_WINDOW = "deferred_outside_window"


#: Error codes that mean processing intentionally stopped before HY, not that
#: extraction broke. This includes an input snapshot superseded by a newer
#: Evidence revision while it was waiting to run.
#:
#: A revoked or superseded continuous policy refuses every task still holding
#: the older receipt, and a revoked permission refuses those prepared under
#: it. In both cases the user turned the feature off: nothing was ever sent to
#: HY, so recording the refusal as `failed` reads as a broken pipeline
#: (measured: 1759 such rows in production). Those tasks are cancelled
#: instead, keeping this code so the reason stays readable.
#:
#: Deliberately excludes "never granted" codes such as
#: `model_content_permission_required`: that is a task that should not have
#: been created, not a feature the user switched off.
POLICY_STOPPED_CODES = (
    "model_content_policy_not_current_or_enabled",
    "model_content_continuous_binding_invalid",
    "model_content_permission_revoked",
    "model_content_evidence_outside_policy",
    "model_content_source_disabled",
    "hy_memory_evidence_changed",
)


class MemoryTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    request_id: str
    user_id: str
    kind: MemoryTaskKind
    input_ref: str = Field(min_length=1, max_length=512)
    status: MemoryTaskStatus
    profile_id: str
    profile_revision: int = Field(ge=1)
    profile_snapshot: ModelProfileSnapshot
    attempts: int = Field(ge=0)
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class MemoryTaskOwnershipError(RuntimeError):
    pass


class MemoryTaskRequestConflictError(RuntimeError):
    pass


#: How many times a `failed` task may be re-claimed before it stops trying.
#:
#: `failed` is retryable, but without a ceiling a permanently broken task is
#: re-queued on every dispatch forever, burning model calls and holding a
#: harness slot. The same `attempts` column the durable queue uses counts the
#: re-claims; once it reaches this, the task stays terminal (`failed`) and is
#: no longer offered to the scheduler.
TASK_MAX_ATTEMPTS = 3

PROFILE_STOP_CODES = (
    "hy_memory_model_authentication_failed",
    "hy_memory_model_payment_required",
    "hy_memory_model_access_denied",
    "model_secret_unavailable",
)

#: Out-of-window pending tasks are marked deferred in bounded batches. A
#: first-run corpus can hold hundreds of thousands of legacy rows; marking
#: them all while preparing the active window would hold the single writer.
ACTIVE_WINDOW_DEFER_LIMIT = 2048


@dataclass(frozen=True, slots=True)
class ActiveMemoryTask:
    """One task plus only the evidence fields the scheduler needs."""

    task: MemoryTask
    at: datetime | None
    imported_at: datetime | None
    workspace: str | None
    length: int
    session: tuple[str, str, str] | None
    fresh: bool
    live: bool


class MemoryTaskExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    content: str | None
    layer: str | None
    created_at: datetime


class TaskEvidenceRef(TypedDict):
    evidence_id: str
    revision: int
    content_hash: str
    identity_hash: NotRequired[str]
    segment_index: NotRequired[int]
    segment_start: NotRequired[int]
    segment_end: NotRequired[int]
    segment_hash: NotRequired[str]


_SEGMENT_META_KEY = "one_memory_segment_v1"


def _segment_marker(record: EvidenceRecord) -> dict[str, Any] | None:
    for metadata in record.origin_meta.values():
        marker = metadata.raw_meta.get(_SEGMENT_META_KEY)
        if isinstance(marker, dict):
            return marker
    return None


def evidence_ref(record: EvidenceRecord) -> TaskEvidenceRef:
    marker = _segment_marker(record)
    if marker is not None:
        return {
            "evidence_id": record.evidence_id,
            "revision": record.revision,
            "content_hash": str(marker["source_content_hash"]),
            "identity_hash": str(marker["source_identity_hash"]),
            "segment_index": int(marker["index"]),
            "segment_start": int(marker["start"]),
            "segment_end": int(marker["end"]),
            "segment_hash": str(marker["hash"]),
        }
    identity = record.model_dump(mode="json", exclude={"origin_meta", "imported_at", "updated_at"})
    return {
        "evidence_id": record.evidence_id,
        "revision": record.revision,
        "content_hash": record.content_hash,
        "identity_hash": sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
    }


def matches_evidence_ref(record: EvidenceRecord, ref: TaskEvidenceRef) -> bool:
    if "segment_hash" in ref:
        base = evidence_ref(record)
        if any(
            base.get(key) != ref.get(key)
            for key in ("evidence_id", "revision", "content_hash", "identity_hash")
        ):
            return False
        start, end = ref.get("segment_start"), ref.get("segment_end")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end):
            return False
        return sha256(record.verbatim[start:end].encode()).hexdigest() == ref["segment_hash"]
    current = evidence_ref(record)
    return all(current.get(key) == value for key, value in ref.items())


def segment_evidence_record(
    record: EvidenceRecord,
    *,
    index: int,
    start: int,
    end: int,
) -> EvidenceRecord:
    """Create an in-memory segment whose durable identity points at the source Evidence."""
    if not (0 <= start < end <= len(record.verbatim)):
        raise ValueError("invalid evidence segment bounds")
    source = evidence_ref(record)
    marker = {
        "index": index,
        "start": start,
        "end": end,
        "hash": sha256(record.verbatim[start:end].encode()).hexdigest(),
        "source_content_hash": source["content_hash"],
        "source_identity_hash": source.get("identity_hash", ""),
    }
    origins = {
        origin: metadata.model_copy(
            update={"raw_meta": {**metadata.raw_meta, _SEGMENT_META_KEY: marker}}
        )
        for origin, metadata in record.origin_meta.items()
    }
    return record.model_copy(
        update={"verbatim": record.verbatim[start:end], "origin_meta": origins}
    )


def materialize_evidence_ref(
    record: EvidenceRecord,
    ref: TaskEvidenceRef,
) -> EvidenceRecord | None:
    if "segment_hash" not in ref:
        return record if matches_evidence_ref(record, ref) else None
    if not matches_evidence_ref(record, ref):
        return None
    return segment_evidence_record(
        record,
        index=int(ref["segment_index"]),
        start=int(ref["segment_start"]),
        end=int(ref["segment_end"]),
    )


class MemoryTaskOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    memory_id: str
    memory_revision: int
    hy_memory_id: str
    owner: str | None
    layer: str | None
    engine: Literal["hy-memory"] = "hy-memory"
    engine_version: str
    support_refs: tuple[TaskEvidenceRef, ...] = ()


#: Schema owned by `memory_server.migrations` (migration 14). No local copy.
#:
#: The `CHECK` on `memory_task_extractions` is preserved there too: content and
#: layer must be both present or both absent, and a row with only one is a
#: contradiction rather than a partial write to tolerate.
MEMORY_TASK_SCHEMA = memory_tasks_ddl("sqlite")


#: Legacy transport refusals raised before the physical send, so provably
#: nothing reached HY: nothing can have been written and retrying cannot
#: duplicate. Kept to recover rows written by builds through 1.0.2.
#:
#: The retired request hook denied every name below before opening a socket.
#: This stays explicit: other old transport codes may have happened after HY
#: persisted a node and must not be blindly requeued.
_PRE_SEND_REFUSALS = (
    "model_content_transport_headers_mismatch",
    "model_content_transport_body_mismatch",
    "model_content_transport_body_invalid",
    "model_content_transport_method_mismatch",
    "model_content_transport_endpoint_mismatch",
    "model_content_transport_dimensions_mismatch",
    "model_content_transport_unsupported_request",
    "model_content_transport_unsupported_stream",
)


def _is_pre_send_refusal(error_code: str) -> bool:
    """Whether this transport code was refused before the request left."""
    return error_code in _PRE_SEND_REFUSALS


class MemoryTaskStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        with self._connect(writing=True) as connection:
            connection.executescript(MEMORY_TASK_SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(memory_tasks)")}
            if "duration_seconds" not in columns:
                connection.execute("ALTER TABLE memory_tasks ADD COLUMN duration_seconds REAL")
            if "hy_invocation_count" not in columns:
                connection.execute(
                    "ALTER TABLE memory_tasks ADD COLUMN hy_invocation_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
        self.migrate_legacy_unknown_statuses()
        self.migrate_unknown_transport_refusals()

    def migrate_legacy_unknown_statuses(self, *, limit: int = 2048) -> int:
        """Move at most `limit` legacy unknown markers to the explicit state.

        Older builds stored the no-blind-retry outcome as a `failed` row plus
        `model_content_outcome_unknown`. A first run over a large corpus could
        update every such row in one writer transaction; bounded passes keep
        that writer short while repeated opens resume until none remain.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        with self._connect(writing=True) as connection:
            rowids = connection.execute(
                "SELECT task_id FROM memory_tasks WHERE status=? AND last_error_code=? LIMIT ?",
                (
                    MemoryTaskStatus.FAILED,
                    "model_content_outcome_unknown",
                    limit,
                ),
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rowids]
            if not task_ids:
                return 0
            placeholders = ",".join("?" for _ in task_ids)
            changed = connection.execute(
                f"UPDATE memory_tasks SET status=? WHERE task_id IN ({placeholders}) "
                "AND status=? AND last_error_code=?",
                (
                    MemoryTaskStatus.UNKNOWN,
                    *task_ids,
                    MemoryTaskStatus.FAILED,
                    "model_content_outcome_unknown",
                ),
            )
        return changed.rowcount

    def migrate_unknown_transport_refusals(self, *, limit: int = 2048) -> int:
        """Requeue unknown tasks that were refused before the request left.

        D85: builds before this one recorded a pre-send refusal as UNKNOWN,
        and UNKNOWN is not claimable, so those conversations never got a
        memory — on Kristin's machine 85 of them, all
        `model_content_transport_headers_mismatch`. They are requeued with
        their attempt count cleared so the first try is not spent, and the
        `request_failed` code is left alone: it can mean HY already wrote.

        Bounded like the legacy pass: one short writer transaction per call.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        # Filtered in SQL, not after a blind `LIMIT`: a library with more
        # unknown tasks than the batch would otherwise spend every pass on
        # tasks that do not qualify and never reach the ones that do (D88).
        placeholders = ",".join("?" for _ in _PRE_SEND_REFUSALS)
        select = (
            "SELECT task_id FROM memory_tasks WHERE status=? "
            f"AND last_error_code IN ({placeholders}) LIMIT ?"
        )
        with self._connect(writing=True) as connection:
            rows = connection.execute(
                select, (MemoryTaskStatus.UNKNOWN, *_PRE_SEND_REFUSALS, limit)
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            if not task_ids:
                return 0
            update_placeholders = ",".join("?" for _ in task_ids)
            changed = connection.execute(
                f"UPDATE memory_tasks SET status=?, attempts=0 "
                f"WHERE task_id IN ({update_placeholders}) AND status=?",
                (MemoryTaskStatus.PENDING, *task_ids, MemoryTaskStatus.UNKNOWN),
            )
        return changed.rowcount

    def inputs(self, task_id: str) -> tuple[TaskEvidenceRef, ...]:
        with self._connect() as db:
            row = db.execute(
                "SELECT refs_json FROM memory_task_inputs WHERE task_id=?", (task_id,)
            ).fetchone()
        return tuple(cast(list[TaskEvidenceRef], json.loads(row[0]))) if row else ()

    def context_inputs(self, task_id: str) -> tuple[TaskEvidenceRef, ...]:
        with self._connect() as db:
            row = db.execute(
                "SELECT refs_json FROM memory_task_contexts WHERE task_id=?", (task_id,)
            ).fetchone()
        return tuple(cast(list[TaskEvidenceRef], json.loads(row[0]))) if row else ()

    def save_context_inputs(self, task_id: str, records: Sequence[EvidenceRecord]) -> None:
        body = json.dumps([evidence_ref(record) for record in records], sort_keys=True)
        with self._connect(writing=True) as db:
            db.execute(
                "INSERT INTO memory_task_contexts VALUES (?,?) ON CONFLICT DO NOTHING",
                (task_id, body),
            )
            saved = db.execute(
                "SELECT refs_json FROM memory_task_contexts WHERE task_id=?", (task_id,)
            ).fetchone()
            if saved is None or saved[0] != body:
                raise MemoryTaskRequestConflictError("task context snapshot differs")

    #: Statuses whose input snapshot nothing reads back afterwards.
    #:
    #: Deliberately narrow. A `completed` task's inputs are read after the fact:
    #: `memory_processing` compares them against the evidence actually sent to
    #: HY to prove coverage, and `matters/discovery` reads them to report how
    #: many records HY really saw. Clearing those would silently turn a
    #: verification into a pass and a report into a lie, which is far worse
    #: than the space saved — measured on the production corpus, inputs are
    #: 8.17 MB against evidence's 720 MB.
    PAYLOAD_FREE_STATUSES = (
        MemoryTaskStatus.CANCELLED,
        MemoryTaskStatus.FAILED,
    )

    def clear_terminal_payloads(self, *, limit: int = 512) -> int:
        """Drop input snapshots for tasks that reached a dead end.

        Mirrors why `drain.py` reaps undrainable pending jobs in the adjacent
        hy memory app: rows that can never run again should not keep their
        payload. A cancelled task never runs again, and a `FAILED` task at the
        retry ceiling is equally final (`claim()` refuses it), so both
        snapshots are dead weight. A `FAILED` task *below* the ceiling is
        still claimable and re-runs the same task, so its snapshot is kept.

        Bounded by `limit` so a large backlog is drained over several calls
        rather than in one long write, which would hold the single writer.

        Oversized-preparation failures are excluded on purpose. Their snapshot
        *is* the finding: it is the only durable record of which records did
        not fit a model input, and the oversized path groups later arrivals
        against it by comparing refs. Reaping it would drop the evidence and
        leave the task as a bare error code nobody can act on.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        placeholders = ",".join("?" for _ in self.PAYLOAD_FREE_STATUSES)
        with self._connect(writing=True) as connection:
            # Joined against either payload table, not just the status: a task
            # whose snapshots are already gone must not be selected again, or
            # every call reports work done while the backlog never drains.
            # LEFT/JOIN both tables because a partial prior cleanup can leave
            # contexts behind after inputs was removed; an inner join on inputs
            # alone would skip that row forever.
            rows = connection.execute(
                "SELECT DISTINCT memory_tasks.task_id FROM memory_tasks "
                "LEFT JOIN memory_task_inputs "
                "ON memory_task_inputs.task_id = memory_tasks.task_id "
                "LEFT JOIN memory_task_contexts "
                "ON memory_task_contexts.task_id = memory_tasks.task_id "
                f"WHERE status IN ({placeholders}) "
                # A `FAILED` task below the retry ceiling is still claimable and
                # re-runs the same task; `process_records` reads this snapshot
                # to decide what the retry sends. Reaping it here would silently
                # switch a retry from its frozen input to a rebuilt chunk.
                f"AND (status != ? OR attempts >= ?) "
                "AND memory_tasks.request_id NOT LIKE 'oversized:%' "
                "AND (memory_task_inputs.task_id IS NOT NULL "
                "OR memory_task_contexts.task_id IS NOT NULL) "
                "LIMIT ?",
                (
                    *self.PAYLOAD_FREE_STATUSES,
                    MemoryTaskStatus.FAILED,
                    TASK_MAX_ATTEMPTS,
                    limit,
                ),
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            if not task_ids:
                return 0
            placeholders = ",".join("?" for _ in task_ids)
            for table in ("memory_task_inputs", "memory_task_contexts"):
                connection.execute(
                    f"DELETE FROM {table} WHERE task_id IN ({placeholders})",
                    task_ids,
                )
        return len(task_ids)

    def completed_conversation_tasks(
        self, task: MemoryTask, seed: EvidenceRecord
    ) -> builtins.list[MemoryTask]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT task.* FROM memory_tasks task "
                "JOIN evidence seed ON seed.evidence_id=task.input_ref "
                "WHERE task.user_id=? AND task.status=? AND task.kind=? "
                "AND task.profile_id=? AND task.profile_revision=? AND task.created_at<? "
                "AND seed.source_id=? AND seed.conversation_id=? "
                "AND seed.workspace IS ? "
                "ORDER BY task.created_at DESC LIMIT 50",
                (
                    task.user_id,
                    MemoryTaskStatus.COMPLETED,
                    MemoryTaskKind.EXTRACT,
                    task.profile_id,
                    task.profile_revision,
                    task.created_at.isoformat(),
                    seed.source_id,
                    seed.conversation_id,
                    seed.workspace,
                ),
            ).fetchall()
        return [self._to_task(row) for row in reversed(rows)]

    def completed_inputs_before(
        self,
        seed: EvidenceRecord,
        *,
        active_prefix: str,
        profile_id: str,
        profile_revision: int,
    ) -> builtins.list[TaskEvidenceRef]:
        """Evidence an earlier ingest policy already extracted for this conversation.

        Only completed work counts; anything else from an earlier policy is
        cancelled on resume and prepared again.
        """
        with self._connect() as db:
            rows = db.execute(
                "SELECT inputs.refs_json FROM memory_tasks task "
                "JOIN memory_task_inputs inputs USING(task_id) "
                "JOIN evidence seed ON seed.evidence_id=task.input_ref "
                "WHERE task.user_id=? AND task.kind=? AND task.status=? "
                "AND task.request_id LIKE 'conversation:%' AND task.request_id NOT LIKE ? "
                "AND task.profile_id=? AND task.profile_revision=? "
                "AND seed.source_id=? AND seed.conversation_id=? "
                "AND seed.workspace IS ?",
                (
                    seed.user_id,
                    MemoryTaskKind.EXTRACT,
                    MemoryTaskStatus.COMPLETED,
                    f"{active_prefix}%",
                    profile_id,
                    profile_revision,
                    seed.source_id,
                    seed.conversation_id,
                    seed.workspace,
                ),
            ).fetchall()
        return [ref for row in rows for ref in cast(list[TaskEvidenceRef], json.loads(row[0]))]

    def save_output(self, output: MemoryTaskOutput) -> None:
        with self._connect(writing=True) as db:
            db.execute(
                "INSERT INTO memory_task_outputs VALUES (?,?,?) "
                "ON CONFLICT(task_id,memory_id) DO UPDATE SET body=excluded.body",
                (output.task_id, output.memory_id, output.model_dump_json()),
            )

    def outputs(self, task_id: str) -> tuple[MemoryTaskOutput, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT body FROM memory_task_outputs WHERE task_id=? ORDER BY memory_id",
                (task_id,),
            ).fetchall()
        return tuple(MemoryTaskOutput.model_validate_json(r[0]) for r in rows)

    def create(
        self,
        *,
        request_id: str,
        user_id: str,
        kind: MemoryTaskKind,
        input_ref: str,
        profile: ModelProfile,
        input_records: Sequence[EvidenceRecord] = (),
        preparation_error: Literal["hy_memory_input_exceeds_window"] | None = None,
        preparation_group: str | None = None,
    ) -> MemoryTask:
        if not request_id or len(request_id) > 256:
            raise ValueError("request_id must contain 1-256 characters")
        if not user_id or len(user_id) > 256:
            raise ValueError("user_id must contain 1-256 characters")
        if not input_ref or len(input_ref) > 512:
            raise ValueError("input_ref must contain 1-512 characters")
        if not profile.is_active:
            raise ValueError("Memory tasks require the active model profile")
        if preparation_error is not None and (
            preparation_error != "hy_memory_input_exceeds_window" or not input_records
        ):
            raise ValueError("Preparation failures require an explicit reason and input snapshot")
        if preparation_group is not None and (
            preparation_error is None
            or kind is not MemoryTaskKind.EXTRACT
            or len(preparation_group) != 64
            or any(character not in "0123456789abcdef" for character in preparation_group)
            or not request_id.startswith(f"oversized:{preparation_group}:")
        ):
            raise ValueError("Preparation grouping requires a scoped oversized extraction failure")
        snapshot = ModelProfileSnapshot(
            profile_id=profile.profile_id,
            revision=profile.revision,
            provider_kind=profile.provider_kind,
            base_url=profile.base_url,
            model=profile.model,
            embedding_model=profile.embedding_model,
            embedding_provider_kind=profile.embedding_provider_kind,
            embedding_base_url=profile.embedding_base_url,
            embedding_dimensions=profile.embedding_dimensions,
            timeout_seconds=profile.timeout_seconds,
            max_concurrency=profile.max_concurrency,
            context_window=profile.context_window,
            has_api_key=profile.has_configured_api_key or profile.has_api_key,
            disable_thinking=profile.disable_thinking,
        )
        task_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        refs = [evidence_ref(record) for record in input_records]
        with self._connect(writing=True) as connection:
            if preparation_group is not None:
                prefix = f"oversized:{preparation_group}"
                previous = connection.execute(
                    "SELECT task.*, inputs.refs_json FROM memory_tasks task "
                    "JOIN memory_task_inputs inputs ON inputs.task_id=task.task_id "
                    "WHERE task.user_id=? AND task.kind=? AND task.request_id>=? "
                    "AND task.request_id<? AND task.input_ref=? "
                    "AND task.profile_id=? AND task.profile_revision=? "
                    "AND task.profile_snapshot_json=? AND task.status=? "
                    "AND task.attempts=0 AND task.last_error_code=? "
                    "ORDER BY task.created_at DESC, task.task_id DESC LIMIT 1",
                    (
                        user_id,
                        kind,
                        f"{prefix}:",
                        f"{prefix};",
                        input_ref,
                        profile.profile_id,
                        profile.revision,
                        snapshot.model_dump_json(),
                        MemoryTaskStatus.FAILED,
                        preparation_error,
                    ),
                ).fetchone()
                if previous is not None:
                    saved_refs = json.loads(previous["refs_json"])
                    common_length = min(len(refs), len(saved_refs))
                    if common_length and saved_refs[:common_length] == refs[:common_length]:
                        # Keep the first immutable failure, even if concurrent arrivals
                        # reach the store out of order. This never accepts model work.
                        return self._to_task(previous)
            connection.execute(
                """
                INSERT INTO memory_tasks (
                    task_id, request_id, user_id, kind, input_ref, status,
                    profile_id, profile_revision, profile_snapshot_json, last_error_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, kind, request_id) DO NOTHING
                """,
                (
                    task_id,
                    request_id,
                    user_id,
                    kind,
                    input_ref,
                    MemoryTaskStatus.FAILED if preparation_error else MemoryTaskStatus.PENDING,
                    profile.profile_id,
                    profile.revision,
                    snapshot.model_dump_json(),
                    preparation_error,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM memory_tasks
                WHERE user_id = ? AND kind = ? AND request_id = ?
                """,
                (user_id, kind, request_id),
            ).fetchone()
            if input_records and row is not None:
                body = json.dumps(refs, sort_keys=True)
                connection.execute(
                    "INSERT INTO memory_task_inputs VALUES (?,?) ON CONFLICT DO NOTHING",
                    (row["task_id"], body),
                )
                saved = connection.execute(
                    "SELECT refs_json FROM memory_task_inputs WHERE task_id=?", (row["task_id"],)
                ).fetchone()
                if saved is None or saved[0] != body:
                    raise MemoryTaskRequestConflictError("task input snapshot differs")
        if row is None:
            raise RuntimeError("Memory task was not created")
        task = self._to_task(row)
        if (
            task.input_ref != input_ref
            or task.profile_id != profile.profile_id
            or task.profile_revision != profile.revision
        ):
            raise MemoryTaskRequestConflictError(
                "request_id was already used with different task input"
            )
        return task

    def claim(self, task_id: str) -> tuple[MemoryTask, str]:
        token = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            current = connection.execute(
                "SELECT status, attempts, last_error_code FROM memory_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if current is not None and current["last_error_code"] in {
                "hy_memory_input_exceeds_window",
                "model_content_outcome_unknown",
            }:
                raise MemoryTaskOwnershipError("Input requires preparation before it can run")
            # A `failed` task at the ceiling is terminal: rejecting the claim
            # keeps a permanently broken task from being re-run even if some
            # other path hands it back to the runner again.
            if (
                current is not None
                and current["status"] == MemoryTaskStatus.FAILED
                and int(current["attempts"]) >= TASK_MAX_ATTEMPTS
            ):
                raise MemoryTaskOwnershipError("retry budget exhausted")
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = ?, attempts = attempts + 1,
                    last_error_code = NULL, updated_at = ?
                WHERE task_id = ? AND status IN (?, ?, ?, ?)
                """,
                (
                    MemoryTaskStatus.RUNNING,
                    token,
                    now,
                    task_id,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                ),
            )
            if changed.rowcount != 1:
                raise MemoryTaskOwnershipError("Memory task is not claimable")
        return self.require(task_id), token

    def profile_stop_reason(self, profile_id: str, profile_revision: int) -> str | None:
        placeholders = ",".join("?" for _ in PROFILE_STOP_CODES)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT last_error_code FROM memory_tasks "
                f"WHERE profile_id=? AND profile_revision=? AND status=? "
                f"AND last_error_code IN ({placeholders}) "
                "ORDER BY updated_at DESC LIMIT 1",
                (
                    profile_id,
                    profile_revision,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    *PROFILE_STOP_CODES,
                ),
            ).fetchone()
        return str(row[0]) if row else None

    def resume_profile_after_probe(self, profile_id: str, profile_revision: int) -> int:
        """Resume profile-stopped work after a successful model probe."""
        placeholders = ",".join("?" for _ in PROFILE_STOP_CODES)
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                f"UPDATE memory_tasks SET status=?, runner_token=NULL, "
                f"last_error_code=NULL, updated_at=? "
                f"WHERE profile_id=? AND profile_revision=? AND status=? "
                f"AND last_error_code IN ({placeholders})",
                (
                    MemoryTaskStatus.PENDING,
                    now,
                    profile_id,
                    profile_revision,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    *PROFILE_STOP_CODES,
                ),
            )
        return changed.rowcount

    def record_hy_invocation(self, task_id: str, *, runner_token: str) -> None:
        """Persist one actual HY write attempt immediately before invocation."""
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                "UPDATE memory_tasks SET hy_invocation_count=hy_invocation_count+1 "
                "WHERE task_id=? AND status=? AND runner_token=?",
                (task_id, MemoryTaskStatus.RUNNING, runner_token),
            )
        if changed.rowcount != 1:
            raise MemoryTaskOwnershipError("Memory task ownership changed")

    def processing_metrics(self) -> dict[str, Any]:
        """Return stable pipeline denominators and terminal outcomes."""
        with self._connect() as connection:
            raw_evidence = int(
                connection.execute(
                    "SELECT COUNT(*) FROM evidence WHERE content_kind='raw_conversation'"
                ).fetchone()[0]
            )
            complete_turns = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT input_ref) FROM memory_tasks WHERE kind=?",
                    (MemoryTaskKind.EXTRACT,),
                ).fetchone()[0]
            )
            hy_calls = int(
                connection.execute(
                    "SELECT COALESCE(SUM(hy_invocation_count),0) FROM memory_tasks"
                ).fetchone()[0]
            )
            statuses = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS count FROM memory_tasks GROUP BY status"
                )
            }
            cancelled_reasons = {
                str(row["reason"] or "unknown"): int(row["count"])
                for row in connection.execute(
                    "SELECT last_error_code AS reason,COUNT(*) AS count FROM memory_tasks "
                    "WHERE status=? GROUP BY last_error_code",
                    (MemoryTaskStatus.CANCELLED,),
                )
            }
            memories = int(
                connection.execute(
                    "SELECT COUNT(*) FROM governed_memories WHERE deleted_at IS NULL"
                ).fetchone()[0]
            )
            workbuddy = {"injected": 0, "timed_out": 0, "unavailable": 0}
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='call_events'"
            ).fetchone():
                for row in connection.execute(
                    "SELECT json_extract(payload,'$.state') AS state, "
                    "json_extract(payload,'$.delivery') AS delivery, COUNT(*) AS count "
                    "FROM call_events WHERE host_id='workbuddy' GROUP BY state,delivery"
                ):
                    count = int(row["count"])
                    if row["delivery"] == "delivered":
                        workbuddy["injected"] += count
                    if row["state"] == "timed_out":
                        workbuddy["timed_out"] += count
                    elif row["state"] == "unavailable":
                        workbuddy["unavailable"] += count
        return {
            "raw_evidence": raw_evidence,
            "complete_turns": complete_turns,
            "hy_calls": hy_calls,
            "completed": statuses.get(str(MemoryTaskStatus.COMPLETED), 0),
            "unknown": statuses.get(str(MemoryTaskStatus.UNKNOWN), 0),
            "failed": statuses.get(str(MemoryTaskStatus.FAILED), 0),
            "cancelled": statuses.get(str(MemoryTaskStatus.CANCELLED), 0),
            "cancelled_reasons": cancelled_reasons,
            "governed_memories": memories,
            "workbuddy": workbuddy,
        }

    def supersede_unstarted(self, task: MemoryTask, *, request_prefix: str) -> int:
        """Retire unstarted tasks whose frozen input the given task fully covers.

        A live conversation re-chunks on every arrival; without this each
        growth step of the same turn stays queued and is sent to the model.
        """
        covered = {ref["evidence_id"] for ref in self.inputs(task.task_id)}
        if not covered:
            return 0
        with self._connect(writing=True) as connection:
            has_evidence = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence'"
            ).fetchone()
            seed = (
                connection.execute(
                    "SELECT source_id,conversation_id,workspace FROM evidence WHERE evidence_id=?",
                    (task.input_ref,),
                ).fetchone()
                if has_evidence
                else None
            )
            session_join = (
                "JOIN evidence seed ON seed.evidence_id=task.input_ref " if seed is not None else ""
            )
            session_clause = (
                "AND seed.source_id=? AND seed.conversation_id=? AND seed.workspace IS ? "
                if seed is not None
                else "AND task.input_ref=? "
            )
            session_values = (
                (seed["source_id"], seed["conversation_id"], seed["workspace"])
                if seed is not None
                else (task.input_ref,)
            )
            rows = connection.execute(
                "SELECT task.task_id, inputs.refs_json FROM memory_tasks task "
                "JOIN memory_task_inputs inputs ON inputs.task_id=task.task_id "
                f"{session_join}"
                "WHERE task.user_id=? AND task.kind=? "
                f"{session_clause}"
                "AND task.profile_id=? AND task.profile_revision=? "
                "AND task.request_id>=? AND task.request_id<? "
                # `PENDING` may be an untouched or a re-queued row, so it keeps
                # the attempt guard. The parked states are different: parking
                # happens after a claim (attempts > 0) yet before the bridge, so
                # the task provably never reached HY. They are superseded
                # regardless of attempts.
                "AND ("
                "(task.status=? AND task.attempts=0) "
                "OR task.status IN (?, ?)"
                ") AND task.task_id!=?",
                (
                    task.user_id,
                    task.kind,
                    *session_values,
                    task.profile_id,
                    task.profile_revision,
                    request_prefix,
                    request_prefix[:-1] + chr(ord(request_prefix[-1]) + 1),
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    task.task_id,
                ),
            ).fetchall()
            retired = [
                row["task_id"]
                for row in rows
                if {ref["evidence_id"] for ref in json.loads(row["refs_json"])} < covered
            ]
            now = datetime.now(UTC).isoformat()
            for task_id in retired:
                connection.execute(
                    "UPDATE memory_tasks SET status=?, runner_token=NULL, "
                    "last_error_code='superseded_by_longer_chunk', updated_at=? "
                    "WHERE task_id=? AND ("
                    "(status=? AND attempts=0) OR status IN (?, ?)"
                    ")",
                    (
                        MemoryTaskStatus.CANCELLED,
                        now,
                        task_id,
                        MemoryTaskStatus.PENDING,
                        MemoryTaskStatus.WAITING_FOR_PERMISSION,
                        MemoryTaskStatus.WAITING_FOR_MODEL,
                    ),
                )
        return len(retired)

    def wait_for_model(
        self,
        task_id: str,
        *,
        runner_token: str,
        error_code: str,
    ) -> MemoryTask:
        return self._finish_owned(
            task_id,
            runner_token=runner_token,
            status=MemoryTaskStatus.WAITING_FOR_MODEL,
            error_code=error_code,
        )

    def wait_for_permission(
        self,
        task_id: str,
        *,
        runner_token: str,
        error_code: str,
    ) -> MemoryTask:
        return self._finish_owned(
            task_id,
            runner_token=runner_token,
            status=MemoryTaskStatus.WAITING_FOR_PERMISSION,
            error_code=error_code,
        )

    def park_for_permission(self, task_id: str, *, error_code: str) -> bool:
        """Park unstarted work until a later permission snapshot admits it."""
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                "UPDATE memory_tasks SET status=?, runner_token=NULL, "
                "last_error_code=?, updated_at=? WHERE task_id=? "
                "AND status IN (?,?,?,?)",
                (
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    error_code,
                    now,
                    task_id,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                ),
            )
        return changed.rowcount == 1

    def park_source_for_permission(
        self, *, source_id: str, error_code: str, user_id: str = "local"
    ) -> int:
        """Park every unstarted task whose input came from one revoked source.

        Revoking a source is the user's "stop using this" node (D73): the
        already imported originals stay, but nothing from that source may reach
        the model until the person authorizes it again. Parking reuses the
        existing waiting state, so the work is neither deleted nor counted as a
        failure, and re-authorizing resumes it.
        """
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                "UPDATE memory_tasks SET status=?, runner_token=NULL, "
                "last_error_code=?, updated_at=? WHERE user_id=? "
                "AND status IN (?,?,?,?) "
                "AND input_ref IN (SELECT evidence_id FROM evidence WHERE source_id=?)",
                (
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    error_code,
                    now,
                    user_id,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                    source_id,
                ),
            )
        return int(changed.rowcount)

    def rebind_unstarted_profile(self, profile: ModelProfile) -> int:
        """Move queued work still holding an older model onto the active one.

        D75: a person who configures a new model expects the queue to continue
        with it, without confirming each task. Only work that never reached the
        model is re-bound; anything already sent keeps its own outcome.
        """
        snapshot = ModelProfileSnapshot(
            profile_id=profile.profile_id,
            revision=profile.revision,
            provider_kind=profile.provider_kind,
            base_url=profile.base_url,
            model=profile.model,
            embedding_model=profile.embedding_model,
            embedding_provider_kind=profile.embedding_provider_kind,
            embedding_base_url=profile.embedding_base_url,
            embedding_dimensions=profile.embedding_dimensions,
            timeout_seconds=profile.timeout_seconds,
            max_concurrency=profile.max_concurrency,
            context_window=profile.context_window,
            has_api_key=profile.has_configured_api_key or profile.has_api_key,
            disable_thinking=profile.disable_thinking,
        )
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                "UPDATE memory_tasks SET profile_id=?, profile_revision=?, "
                "profile_snapshot_json=?, status=?, runner_token=NULL, "
                "last_error_code=NULL, updated_at=? "
                "WHERE status IN (?,?,?) AND (profile_id != ? OR profile_revision != ?) "
                "AND coalesce(last_error_code, '') NOT IN (?, ?)",
                (
                    profile.profile_id,
                    profile.revision,
                    snapshot.model_dump_json(),
                    MemoryTaskStatus.PENDING,
                    now,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    profile.profile_id,
                    profile.revision,
                    "hy_memory_input_exceeds_window",
                    "model_content_outcome_unknown",
                ),
            )
        return int(changed.rowcount)

    def pause_profile_for_model(self, profile_id: str) -> int:
        """Hold queued work for a deleted model configuration; never delete it.

        D75: with the configuration gone there is nothing to send to, so the
        task waits in the existing `waiting_for_model` state. Configuring a
        model again lets the same work resume.
        """
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                "UPDATE memory_tasks SET status=?, runner_token=NULL, "
                "last_error_code=?, updated_at=? "
                "WHERE profile_id=? AND status IN (?,?,?) "
                "AND coalesce(last_error_code, '') NOT IN (?, ?)",
                (
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    "model_profile_unavailable",
                    now,
                    profile_id,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    "hy_memory_input_exceeds_window",
                    "model_content_outcome_unknown",
                ),
            )
        return int(changed.rowcount)

    def complete(self, task_id: str, *, runner_token: str) -> MemoryTask:
        return self._finish_owned(
            task_id,
            runner_token=runner_token,
            status=MemoryTaskStatus.COMPLETED,
            error_code=None,
        )

    def fail(
        self,
        task_id: str,
        *,
        runner_token: str,
        error_code: str,
    ) -> MemoryTask:
        return self._finish_owned(
            task_id,
            runner_token=runner_token,
            status=MemoryTaskStatus.FAILED,
            error_code=error_code,
        )

    def mark_unknown(
        self,
        task_id: str,
        *,
        runner_token: str,
        error_code: str,
    ) -> MemoryTask:
        """Record a possibly-sent HY outcome without making it claimable."""
        # D85: a refusal that provably happened before the request left is
        # safe to retry, and `_CLAIMABLE_TASK_STATUSES` has no UNKNOWN, so
        # recording it there strands the task forever. The permission
        # transport rejects a non-allowlisted request inside httpx, before the
        # socket. `request_failed` is the network code that can arrive after
        # HY has persisted a node: retrying that blind would duplicate, so it
        # stays unknown.
        if _is_pre_send_refusal(error_code):
            return self.fail(task_id, runner_token=runner_token, error_code=error_code)
        return self._finish_owned(
            task_id,
            runner_token=runner_token,
            status=MemoryTaskStatus.UNKNOWN,
            error_code=error_code,
        )

    def stop(
        self,
        task_id: str,
        *,
        runner_token: str,
        error_code: str,
    ) -> MemoryTask:
        """End a claimed task because the user stopped the policy behind it.

        Distinct from `fail` on purpose: nothing reached HY and nothing broke.
        The user turned the feature off, so the task ends as `cancelled` with
        `policy_stopped_by_user` rather than adding to the failure count.
        """
        return self._finish_owned(
            task_id,
            runner_token=runner_token,
            status=MemoryTaskStatus.CANCELLED,
            error_code=error_code,
        )

    def get(self, task_id: str) -> MemoryTask | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._to_task(row) if row else None

    def list(
        self,
        *,
        statuses: tuple[MemoryTaskStatus, ...] | None = None,
    ) -> list[MemoryTask]:
        with self._connect() as connection:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    f"""
                    SELECT * FROM memory_tasks
                    WHERE status IN ({placeholders})
                    ORDER BY created_at, task_id
                    """,
                    statuses,
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM memory_tasks ORDER BY created_at, task_id"
                ).fetchall()
        return [self._to_task(row) for row in rows]

    def active_window(
        self,
        *,
        now: datetime,
        fresh_window: timedelta,
        extraction_window: timedelta | None,
        live_task_ids: tuple[str, ...] = (),
        exclude_task_ids: tuple[str, ...] = (),
        fresh_before: tuple[datetime | None, str] | None = None,
        backlog_before: tuple[datetime | None, str] | None = None,
        live_limit: int = 16,
        fresh_limit: int = 16,
        backlog_limit: int = 8,
    ) -> builtins.list[ActiveMemoryTask]:
        """Read one bounded scheduling window with evidence metadata joined in SQL.

        Live work is identified by the caller because that fact is intentionally
        transient. Fresh and backlog lanes are selected by evidence time. Tasks
        already represented by the in-memory queue or a worker are excluded so a
        refill cannot repeatedly classify the same durable row.
        """
        limits = (live_limit, fresh_limit, backlog_limit)
        if any(type(value) is not int or value < 0 for value in limits):
            raise ValueError("active window limits must be non-negative integers")
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        fresh_since = (now - fresh_window).isoformat()
        extraction_since = (
            (now - extraction_window).isoformat() if extraction_window is not None else None
        )
        claimable = (
            MemoryTaskStatus.PENDING,
            MemoryTaskStatus.WAITING_FOR_PERMISSION,
            MemoryTaskStatus.WAITING_FOR_MODEL,
            MemoryTaskStatus.FAILED,
        )
        live_ids = tuple(dict.fromkeys(live_task_ids))[:live_limit]
        excluded = tuple(dict.fromkeys(exclude_task_ids))
        live_placeholders = ",".join("?" for _ in live_ids)
        excluded_placeholders = ",".join("?" for _ in excluded)
        live_clause = f"task.task_id IN ({live_placeholders})" if live_ids else "0"
        not_live_clause = f"task.task_id NOT IN ({live_placeholders})" if live_ids else "1"
        excluded_clause = f"task.task_id NOT IN ({excluded_placeholders})" if excluded else "1"
        common = f"""
            task.status IN (?,?,?,?)
            AND coalesce(task.last_error_code, '') NOT IN (
                'hy_memory_input_exceeds_window', 'model_content_outcome_unknown'
            )
            AND {excluded_clause}
        """
        joined_columns = """
            task.*,
            evidence.created_at AS evidence_created_at,
            evidence.imported_at AS evidence_imported_at,
            evidence.workspace AS evidence_workspace,
            evidence.user_id AS evidence_user_id,
            evidence.source_id AS evidence_source_id,
            evidence.conversation_id AS evidence_conversation_id,
            length(evidence.verbatim) AS evidence_length
        """
        moment = "coalesce(evidence.created_at, evidence.imported_at)"

        def run(connection: sqlite3.Connection, *, defer: bool) -> list[sqlite3.Row]:
            in_window = (
                "1"
                if extraction_since is None
                else f"({moment} IS NULL OR julianday({moment}) >= julianday(?) "
                "OR evidence.workspace IS NULL)"
            )
            if extraction_since is not None and defer:
                connection.execute(
                    f"""
                    UPDATE memory_tasks
                    SET status=?, runner_token=NULL,
                        last_error_code='outside_extraction_window', updated_at=?
                    WHERE task_id IN (
                        SELECT task.task_id
                        FROM memory_tasks AS task
                        LEFT JOIN evidence ON evidence.evidence_id=task.input_ref
                        WHERE {common}
                          AND {moment} IS NOT NULL
                          AND julianday({moment}) < julianday(?)
                          AND evidence.workspace IS NOT NULL
                        LIMIT ?
                    )
                    """,
                    (
                        MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,
                        now.isoformat(),
                        *claimable,
                        *excluded,
                        extraction_since,
                        ACTIVE_WINDOW_DEFER_LIMIT,
                    ),
                )

            def select(lane: str, limit: int) -> list[sqlite3.Row]:
                if limit == 0:
                    return []
                cursor = None
                if lane == "live":
                    lane_clause = live_clause
                    lane_parameters = (*live_ids,)
                elif lane == "fresh":
                    lane_clause = f"{not_live_clause} AND julianday({moment}) >= julianday(?)"
                    lane_parameters = (*live_ids, fresh_since)
                    cursor = fresh_before
                else:
                    lane_clause = (
                        f"{not_live_clause} AND "
                        f"({moment} IS NULL OR julianday({moment}) < julianday(?))"
                    )
                    lane_parameters = (*live_ids, fresh_since)
                    cursor = backlog_before
                if cursor is not None:
                    cursor_at, cursor_task_id = cursor
                    if cursor_at is None:
                        lane_clause += f" AND {moment} IS NULL AND task.task_id > ?"
                        lane_parameters = (*lane_parameters, cursor_task_id)
                    else:
                        cursor_stamp = cursor_at.isoformat()
                        lane_clause += (
                            f" AND (julianday({moment}) < julianday(?) OR "
                            f"(julianday({moment}) = julianday(?) AND task.task_id > ?))"
                        )
                        lane_parameters = (
                            *lane_parameters,
                            cursor_stamp,
                            cursor_stamp,
                            cursor_task_id,
                        )
                return connection.execute(
                    f"""
                    SELECT {joined_columns}
                    FROM memory_tasks AS task
                    LEFT JOIN evidence ON evidence.evidence_id=task.input_ref
                    WHERE {common} AND ({in_window}) AND ({lane_clause})
                    ORDER BY julianday({moment}) DESC, task.task_id
                    LIMIT ?
                    """,
                    (
                        *claimable,
                        *excluded,
                        *((extraction_since,) if extraction_since is not None else ()),
                        *lane_parameters,
                        limit,
                    ),
                ).fetchall()

            rows = [
                *(select("live", live_limit)),
                *(select("fresh", fresh_limit)),
                *(select("backlog", backlog_limit)),
            ]
            return rows

        def has_defer_candidate(connection: sqlite3.Connection) -> bool:
            if extraction_since is None:
                return False
            row = connection.execute(
                f"""
                SELECT 1
                FROM memory_tasks AS task
                LEFT JOIN evidence ON evidence.evidence_id=task.input_ref
                WHERE {common}
                  AND {moment} IS NOT NULL
                  AND julianday({moment}) < julianday(?)
                  AND evidence.workspace IS NOT NULL
                LIMIT 1
                """,
                (*claimable, *excluded, extraction_since),
            ).fetchone()
            return row is not None

        # Reading a scheduling window is normally a read. Only take the single
        # writer when there is an out-of-window row that actually needs the
        # defer UPDATE; otherwise every refill would contend with imports.
        with self._connect() as connection:
            needs_defer = has_defer_candidate(connection)
            if not needs_defer:
                rows = run(connection, defer=False)
        if needs_defer:
            with self._connect(writing=True) as connection:
                rows = run(connection, defer=True)

        selected: builtins.list[ActiveMemoryTask] = []
        live_set = set(live_ids)
        for row in rows:
            created = row["evidence_created_at"]
            imported = row["evidence_imported_at"]
            at = datetime.fromisoformat(created) if created else None
            imported_at = datetime.fromisoformat(imported) if imported else None
            stamp = at or imported_at
            if stamp is not None and stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            selected.append(
                ActiveMemoryTask(
                    task=self._to_task(row),
                    at=at,
                    imported_at=imported_at,
                    workspace=row["evidence_workspace"],
                    length=int(row["evidence_length"] or 1_000_000),
                    session=(
                        str(row["evidence_user_id"]),
                        str(row["evidence_source_id"]),
                        str(row["evidence_conversation_id"]),
                    )
                    if row["evidence_user_id"] is not None
                    else None,
                    fresh=stamp is not None and stamp >= now - fresh_window,
                    live=str(row["task_id"]) in live_set,
                )
            )
        return selected

    def cancel_superseded(self, *, profile_id: str, profile_revision: int) -> int:
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL,
                    last_error_code = 'model_profile_superseded', updated_at = ?
                WHERE status IN (?, ?, ?, ?, ?)
                  AND (profile_id != ? OR profile_revision != ?)
                  AND coalesce(last_error_code,'') != 'model_content_outcome_unknown'
                """,
                (
                    MemoryTaskStatus.CANCELLED,
                    now,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                    MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,
                    profile_id,
                    profile_revision,
                ),
            )
        return changed.rowcount

    def defer_outside_window(self, task_id: str) -> MemoryTask | None:
        """Mark a pending task as outside the automatic extraction window."""
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL,
                    last_error_code = 'outside_extraction_window', updated_at = ?
                WHERE task_id = ? AND status IN (?, ?, ?, ?)
                """,
                (
                    MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,
                    now,
                    task_id,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                ),
            )
        if changed.rowcount != 1:
            return None
        return self.require(task_id)

    def promote_deferred(
        self,
        *,
        limit: int,
        task_ids: tuple[str, ...] | None = None,
    ) -> builtins.list[MemoryTask]:
        """Move deferred tasks back to pending, newest evidence first.

        Manual extraction and window widening both go through here so neither
        path can dump the whole catch-up onto the scheduler in one shot.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._connect(writing=True) as connection:
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                rows = connection.execute(
                    f"""
                    SELECT task.task_id AS task_id
                    FROM memory_tasks AS task
                    LEFT JOIN evidence ON evidence.evidence_id = task.input_ref
                    WHERE task.status = ?
                      AND task.task_id IN ({placeholders})
                    ORDER BY evidence.created_at IS NULL,
                             julianday(evidence.created_at) DESC,
                             task.task_id
                    LIMIT ?
                    """,
                    (MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW, *task_ids, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT task.task_id AS task_id
                    FROM memory_tasks AS task
                    LEFT JOIN evidence ON evidence.evidence_id = task.input_ref
                    WHERE task.status = ?
                    ORDER BY evidence.created_at IS NULL,
                             julianday(evidence.created_at) DESC,
                             task.task_id
                    LIMIT ?
                    """,
                    (MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW, limit),
                ).fetchall()
            chosen = [str(row["task_id"]) for row in rows]
            if not chosen:
                return []
            now = datetime.now(UTC).isoformat()
            placeholders = ",".join("?" for _ in chosen)
            connection.execute(
                f"""
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, last_error_code = NULL,
                    updated_at = ?
                WHERE task_id IN ({placeholders}) AND status = ?
                """,
                (
                    MemoryTaskStatus.PENDING,
                    now,
                    *chosen,
                    MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,
                ),
            )
        return [self.require(task_id) for task_id in chosen]

    def task_for_evidence(self, evidence_id: str) -> MemoryTask | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_tasks
                WHERE input_ref = ?
                ORDER BY created_at DESC, task_id DESC
                LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
        return self._to_task(row) if row else None

    def cancel_superseded_policy(self, *, active_prefix: str) -> int:
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL,
                    last_error_code = 'ingest_policy_superseded', updated_at = ?
                WHERE status IN (?, ?, ?, ?, ?)
                  AND request_id LIKE 'conversation:%'
                  AND request_id NOT LIKE ?
                  AND coalesce(last_error_code,'') != 'model_content_outcome_unknown'
                """,
                (
                    MemoryTaskStatus.CANCELLED,
                    now,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                    MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW,
                    f"{active_prefix}%",
                ),
            )
        return changed.rowcount

    def reclassify_policy_stopped(self, *, limit: int = 2048) -> int:
        """Turn past "user stopped this" refusals into cancellations.

        Before this existed, a revoked continuous policy marked every task
        still holding the older receipt as `failed` with
        `model_content_policy_not_current_or_enabled` — measured at 1759 rows
        in production, none of which ever reached HY. Read as failures they
        look like a broken pipeline; what really happened is that the user
        turned the feature off. They are cancelled with
        `policy_stopped_by_user` instead.

        The error code is left as it was: it says which permission state
        refused the task, and that is the only thing worth reading back.

        Bounded by `limit` so a large backlog converts over several calls
        rather than in one long write holding the single writer.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC).isoformat()
        placeholders = ",".join("?" for _ in POLICY_STOPPED_CODES)
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                f"""
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, updated_at = ?
                WHERE task_id IN (
                    SELECT task_id FROM memory_tasks
                    WHERE status = ? AND last_error_code IN ({placeholders})
                    LIMIT ?
                )
                """,
                (
                    MemoryTaskStatus.CANCELLED,
                    now,
                    MemoryTaskStatus.FAILED,
                    *POLICY_STOPPED_CODES,
                    limit,
                ),
            )
        return changed.rowcount

    def counts(self) -> dict[MemoryTaskStatus, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM memory_tasks GROUP BY status
                """
            ).fetchall()
        counts: dict[MemoryTaskStatus, int] = {}
        for row in rows:
            try:
                status = MemoryTaskStatus(row["status"])
            except ValueError:
                # A newer build may have written a status this one does not
                # know. One unknown row must not turn the whole processing
                # summary into a 500; `unknown_status_counts()` exposes the
                # raw value separately instead of folding it into a known
                # state.
                continue
            counts[status] = int(row["count"])
        return counts

    def unknown_status_counts(self) -> dict[str, int]:
        """Raw statuses this build does not understand, for visibility."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM memory_tasks GROUP BY status"
            ).fetchall()
        unknown: dict[str, int] = {}
        for row in rows:
            value = str(row["status"])
            if value in {status.value for status in MemoryTaskStatus}:
                continue
            unknown[value] = int(row["count"])
        return unknown

    def unknown_kind_status_counts(self) -> dict[str, int]:
        """Raw `(kind,status)` groups this build cannot render as a block."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, status, COUNT(*) AS count FROM memory_tasks GROUP BY kind, status"
            ).fetchall()
        known_kinds = {kind.value for kind in MemoryTaskKind}
        unknown: dict[str, int] = {}
        for row in rows:
            kind = str(row["kind"])
            status = str(row["status"])
            if kind in known_kinds:
                continue
            unknown[f"{kind}:{status}"] = int(row["count"])
        return unknown

    def unexecuted_policy_failures(self) -> int:
        """Failed tasks that never ran because their policy was not in force.

        The user stopping the policy (turning a switch off, revoking
        permission) ends the tasks it had already queued without sending any
        of them to HY. Those are cancellations, not failures — measured at
        1759 rows in production, where counting them as failures made a
        deliberately stopped feature read as a broken pipeline.

        Both the legacy `failed` rows and the current `cancelled` ones are
        counted, because a database migrated before this change still holds
        the old marking; `reclassify_policy_stopped` converts them in place.
        The name is kept so existing readers and the API contract stay valid.

        Matched on the error code alone, not on `attempts`: most of these were
        refused before any attempt (measured: 1758 of 1759 had `attempts = 0`),
        but one was refused after a claim, and a count that silently dropped it
        would be wrong in the same way the total is.
        """
        placeholders = ",".join("?" for _ in POLICY_STOPPED_CODES)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM memory_tasks
                WHERE status IN (?, ?) AND last_error_code IN ({placeholders})
                """,
                (
                    MemoryTaskStatus.CANCELLED,
                    MemoryTaskStatus.FAILED,
                    *POLICY_STOPPED_CODES,
                ),
            ).fetchone()
        return int(row["count"]) if row else 0

    def blocked_stages(self) -> builtins.list[tuple[MemoryTaskKind, str, int]]:
        """Report which stage is stuck and why, most affected tasks first.

        A stalled pipeline otherwise reaches the reader as "no memories yet",
        which hides whether the model is unreachable or the work simply has
        not run.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT kind, status, last_error_code, COUNT(*) AS count
                FROM memory_tasks
                WHERE status IN (?, ?, ?, ?)
                  AND (status != ? OR attempts < ?)
                GROUP BY kind, status, last_error_code
                ORDER BY count DESC, kind
                """,
                (
                    MemoryTaskStatus.FAILED,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.UNKNOWN,
                    MemoryTaskStatus.FAILED,
                    TASK_MAX_ATTEMPTS,
                ),
            ).fetchall()
        blocks: builtins.list[tuple[MemoryTaskKind, str, int]] = []
        for row in rows:
            try:
                kind = MemoryTaskKind(row["kind"])
            except ValueError:
                # A newer build may add a kind this one cannot classify.
                # Keeping it out of the typed list keeps the summary alive;
                # `unknown_kind_status_counts()` exposes the raw group.
                continue
            blocks.append(
                (
                    kind,
                    str(row["last_error_code"] or row["status"]),
                    int(row["count"]),
                )
            )
        return blocks

    def list_completed_with_extraction(self) -> builtins.list[MemoryTask]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT task.* FROM memory_tasks AS task
                JOIN memory_task_extractions AS extraction
                  ON extraction.task_id = task.task_id
                WHERE task.status = ?
                ORDER BY task.created_at, task.task_id
                """,
                (MemoryTaskStatus.COMPLETED,),
            ).fetchall()
        return [self._to_task(row) for row in rows]

    def save_extraction(
        self,
        task_id: str,
        *,
        content: str | None,
        layer: str | None,
    ) -> MemoryTaskExtraction:
        if (content is None) != (layer is None):
            raise ValueError("content and layer must both be set or both be absent")
        if content is not None and (not content.strip() or len(content) > 160):
            raise ValueError("extracted memory must contain 1-160 characters")
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            connection.execute(
                """
                INSERT INTO memory_task_extractions (
                    task_id, content, layer, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO NOTHING
                """,
                (task_id, content, layer, now),
            )
        extraction = self.extraction(task_id)
        if extraction is None:
            raise RuntimeError("Memory task extraction was not saved")
        if extraction.content != content or extraction.layer != layer:
            raise MemoryTaskRequestConflictError(
                "task extraction was already saved with different output"
            )
        return extraction

    def extraction(self, task_id: str) -> MemoryTaskExtraction | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_task_extractions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return MemoryTaskExtraction(
            task_id=row["task_id"],
            content=row["content"],
            layer=row["layer"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def pending_by_recency(self, *, fresh_since: datetime) -> tuple[int, int]:
        """Waiting work split into new and catch-up, as `(fresh, backlog)`.

        Read from the store rather than from the in-memory queue on purpose:
        the two numbers have to be true straight after a restart, before the
        scheduler has picked anything up, and that is exactly when a user
        looks. SQLite does the classification with `julianday()` so ISO strings
        with different offsets are compared as instants, and the API does not
        pull every pending row into Python just to count two buckets.
        """
        if fresh_since.tzinfo is None:
            fresh_since = fresh_since.replace(tzinfo=UTC)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(
                        CASE
                            WHEN coalesce(evidence.created_at, evidence.imported_at)
                                 IS NOT NULL
                             AND julianday(
                                 coalesce(evidence.created_at, evidence.imported_at)
                             ) >= julianday(?)
                            THEN 1 ELSE 0
                        END
                    ) AS fresh,
                    SUM(
                        CASE
                            WHEN coalesce(evidence.created_at, evidence.imported_at)
                                 IS NULL
                             OR julianday(
                                 coalesce(evidence.created_at, evidence.imported_at)
                             ) < julianday(?)
                            THEN 1 ELSE 0
                        END
                    ) AS backlog
                FROM memory_tasks AS task
                LEFT JOIN evidence ON evidence.evidence_id = task.input_ref
                WHERE task.status IN (?, ?, ?, ?)
                  AND (task.status != ? OR task.attempts < ?)
                """,
                (
                    fresh_since.isoformat(),
                    fresh_since.isoformat(),
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.WAITING_FOR_PERMISSION,
                    MemoryTaskStatus.WAITING_FOR_MODEL,
                    MemoryTaskStatus.FAILED,
                    MemoryTaskStatus.FAILED,
                    TASK_MAX_ATTEMPTS,
                ),
            ).fetchone()
        return int(row["fresh"] or 0), int(row["backlog"] or 0)

    def recent_completion_times(self, *, sample: int = 20) -> tuple[datetime, ...]:
        """When work last landed, newest first.

        A queue depth on its own cannot say whether anything is moving: 672
        that is falling and 672 that is stuck look the same. R-02 asks for
        the two depths *and* this, so the app can say which it is.
        """
        if type(sample) is not int or sample < 1:
            raise ValueError("sample must be a positive integer")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT updated_at FROM memory_tasks
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (MemoryTaskStatus.COMPLETED, sample),
            ).fetchall()
        return tuple(datetime.fromisoformat(row["updated_at"]) for row in rows)

    def seconds_per_completion(self, *, sample: int = 25) -> float | None:
        """Estimate how long one task takes, so a long queue can state an ETA.

        A local model handles one extraction at a time and can take minutes
        each, so a bare "675 waiting" cannot tell the reader whether that means
        an hour or a week. This measures each attempt's own recorded duration,
        not the gap between completions: a desktop app spends most of any hour
        closed, and gap-based rates would blame the queue for time the worker
        was never running. The median keeps one pathological conversation from
        dominating the estimate.
        """
        if type(sample) is not int or sample < 1:
            raise ValueError("sample must be a positive integer")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT duration_seconds FROM memory_tasks
                WHERE status = ? AND duration_seconds IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (MemoryTaskStatus.COMPLETED, sample),
            ).fetchall()
        durations = sorted(float(row["duration_seconds"]) for row in rows)
        if not durations:
            return None
        middle = len(durations) // 2
        median = (
            durations[middle]
            if len(durations) % 2
            else (durations[middle - 1] + durations[middle]) / 2
        )
        return median

    @staticmethod
    def _is_running_after_invocation(connection: sqlite3.Connection, task_id: str) -> bool:
        """Whether a running task crossed the durable remote-invocation boundary."""
        row = connection.execute(
            "SELECT status,hy_invocation_count FROM memory_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        return bool(row is not None and row[0] == str(MemoryTaskStatus.RUNNING) and int(row[1]) > 0)

    def recover_one_running(self, task_id: str, *, reason: str) -> bool:
        """Return one task left `running` by a worker that gave up on it.

        A task is claimed as `running` before anything slow happens, so a
        worker that stops early — a timeout, a crash between steps — leaves it
        there. Nothing else looks at `running` again until the next process
        start, so without this the task holds its harness's slot for as long as
        the process lives.

        Narrowly scoped to one id, unlike `recover_running`, because the worker
        knows exactly which task it abandoned.

        A timeout is `unknown` only after the durable HY invocation marker was
        written. Before that boundary it is safe to return the task to pending:
        no provider/store call could have landed. Older code treated every
        timeout as possibly sent, which turned long bridge initialization into
        permanent unknown work even with `hy_invocation_count=0`.

        A non-timeout reason with no continuous record keeps the old
        `pending` retry: the caller is asserting the task stopped before any
        send, which is the retryable case.
        """
        with self._connect(writing=True) as connection:
            invocation = connection.execute(
                "SELECT hy_invocation_count FROM memory_tasks WHERE task_id=? AND status=?",
                (task_id, MemoryTaskStatus.RUNNING),
            ).fetchone()
            marked_invoked = invocation is not None and int(invocation[0]) > 0
            possibly_sent = marked_invoked
            if possibly_sent:
                rows = connection.execute(
                    """
                    UPDATE memory_tasks
                    SET status = ?, runner_token = NULL, last_error_code = ?
                    WHERE task_id = ? AND status = ?
                    """,
                    (
                        MemoryTaskStatus.UNKNOWN,
                        "model_content_outcome_unknown",
                        task_id,
                        MemoryTaskStatus.RUNNING,
                    ),
                ).rowcount
                return rows > 0
            rows = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, last_error_code = ?,
                    updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    MemoryTaskStatus.PENDING,
                    reason,
                    datetime.now(UTC).isoformat(),
                    task_id,
                    MemoryTaskStatus.RUNNING,
                ),
            ).rowcount
        return rows > 0

    def fail_unsettled(self, task_id: str, *, error_code: str) -> bool:
        """Fail one task a runner raised out of without settling.

        The runner owns every ordinary transition and holds the token for
        them. This is for the case where it never got that far — the call
        itself failed, or it crashed between claim and finish — so the task
        is still `pending` or `running` and nobody else will move it. A task
        the runner did settle is in some other state and is left alone.

        A `running` continuous task becomes `unknown`, not `failed`: `failed`
        is retryable, and the unclassified exception may have arrived after HY
        was already called, so retrying it would duplicate the memory. A task
        that never reached `running` (still `pending`) has provably not been
        sent and stays `failed`.
        """
        with self._connect(writing=True) as connection:
            if self._is_running_after_invocation(connection, task_id):
                rows = connection.execute(
                    """
                    UPDATE memory_tasks
                    SET status = ?, runner_token = NULL, last_error_code = ?
                    WHERE task_id = ? AND status = ?
                    """,
                    (
                        MemoryTaskStatus.UNKNOWN,
                        "model_content_outcome_unknown",
                        task_id,
                        MemoryTaskStatus.RUNNING,
                    ),
                ).rowcount
                return rows > 0
            rows = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, last_error_code = ?,
                    updated_at = ?
                WHERE task_id = ? AND status IN (?, ?)
                """,
                (
                    MemoryTaskStatus.FAILED,
                    error_code,
                    datetime.now(UTC).isoformat(),
                    task_id,
                    MemoryTaskStatus.PENDING,
                    MemoryTaskStatus.RUNNING,
                ),
            ).rowcount
        return rows > 0

    def resolve_unknown(
        self,
        task_id: str,
        *,
        found: bool,
        detail: str = "",
    ) -> MemoryTaskStatus | None:
        """Retire an `unknown` task after an explicit inquiry result.

        `unknown` means the task may already have reached HY. Its only safe
        exit is an inquiry: `found=True` closes it as completed, `found=False`
        gives up as failed. This method deliberately has no retry/resend
        branch; moving it back to pending would duplicate a memory because HY
        has no idempotency key. Returns the new state, or None when the row is
        missing or no longer unknown.
        """
        target = MemoryTaskStatus.COMPLETED if found else MemoryTaskStatus.FAILED
        recorded_detail = detail or (None if found else "model_content_outcome_not_found")
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, last_error_code = ?,
                    attempts = CASE WHEN ? THEN attempts ELSE ? END, updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    target,
                    recorded_detail,
                    found,
                    TASK_MAX_ATTEMPTS,
                    now,
                    task_id,
                    MemoryTaskStatus.UNKNOWN,
                ),
            )
        return target if changed.rowcount == 1 else None

    def unknown_tasks(
        self,
        *,
        limit: int = 128,
        after_cursor: tuple[str, str] | None = None,
    ) -> tuple[MemoryTask, ...]:
        """List `unknown` tasks that still need an external inquiry.

        `unknown` means the task may already have reached HY, so it is
        deliberately not claimable. A reconcile pass needs a bounded,
        read-only way to enumerate those rows and hand each durable/task pair
        to its inquiry. Ordering is `(updated_at, task_id)`; `after_cursor` is
        the previous page's last pair and is not a cross-call snapshot.

        This mirrors `DurableWriteQueue.unknown_jobs()` and never calls HY,
        clears an error, or changes a status.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        if after_cursor is not None:
            if (
                not isinstance(after_cursor, tuple)
                or len(after_cursor) != 2
                or not all(isinstance(value, str) and value for value in after_cursor)
            ):
                raise ValueError("after_cursor must be a (updated_at, task_id) pair")
            updated_at, task_id = after_cursor
            cursor_clause = "AND (updated_at, task_id) > (?, ?)"
            params: tuple[Any, ...] = (updated_at, task_id)
        else:
            cursor_clause = ""
            params = ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_tasks "
                "WHERE status=? "
                f"{cursor_clause} "
                "ORDER BY updated_at, task_id LIMIT ?",
                (MemoryTaskStatus.UNKNOWN, *params, limit),
            ).fetchall()
        return tuple(self._to_task(row) for row in rows)

    def unknown_error_counts(self) -> dict[str, int]:
        """Group sticky unknown tasks by their recorded terminal result code.

        `model_content_outcome_unknown` is the no-blind-retry state, not a
        success and not a retryable failure. In a large backlog every row can
        otherwise look like the same problem, so this read-only aggregate
        names the authorization/model codes that actually produced the
        unknown. It does not resolve, retry or clear any row.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT COALESCE(last_error_code,'') AS code, COUNT(*) AS count "
                "FROM memory_tasks WHERE status=? "
                "GROUP BY COALESCE(last_error_code,'') ORDER BY code",
                (MemoryTaskStatus.UNKNOWN,),
            ).fetchall()
        return {str(row["code"]): int(row["count"]) for row in rows}

    def retry_unknown_confirmed(self, *, limit: int = 2048) -> int:
        """Requeue task-only unknowns after the user explicitly confirms.

        Older and partially completed queue revisions can leave a valid task
        without a surviving durable write row. Reconciliation runs first; this
        bounded transition is only for the still-unproven remainder and never
        runs automatically.
        """
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status=?, runner_token=NULL, attempts=0,
                    last_error_code='model_content_manual_retry_requested', updated_at=?
                WHERE task_id IN (
                    SELECT task_id FROM memory_tasks
                    WHERE status=? ORDER BY updated_at, task_id LIMIT ?
                )
                """,
                (
                    MemoryTaskStatus.PENDING,
                    now,
                    MemoryTaskStatus.UNKNOWN,
                    limit,
                ),
            )
        return changed.rowcount

    def retry_first_unknown_after_reconcile(
        self,
        *,
        older_than: datetime,
        limit: int = 128,
    ) -> int:
        """Automatically retry a task-only unknown exactly once.

        Reconciliation runs before this transition. Attempts are preserved, so
        the retry claim moves `attempts=1` to two and another unknown is left
        for explicit user recovery.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status=?, runner_token=NULL,
                    last_error_code='model_content_unknown_auto_retry', updated_at=?
                WHERE task_id IN (
                    SELECT task_id FROM memory_tasks
                    WHERE status=? AND attempts=1 AND updated_at <= ?
                    ORDER BY updated_at, task_id LIMIT ?
                )
                """,
                (
                    MemoryTaskStatus.PENDING,
                    now,
                    MemoryTaskStatus.UNKNOWN,
                    older_than.isoformat(),
                    limit,
                ),
            )
        return changed.rowcount

    def recover_running(self) -> int:
        """Return tasks orphaned by a previous process to the pending queue."""
        return self.recover_orphan_running()

    def recover_orphan_running(
        self,
        *,
        exclude_task_ids: tuple[str, ...] = (),
        claimed_before: datetime | None = None,
    ) -> int:
        """Recover running rows not owned by this process's active workers."""
        placeholders = ",".join("?" for _ in exclude_task_ids)
        exclusion = f" AND task_id NOT IN ({placeholders})" if exclude_task_ids else ""
        age_clause = " AND updated_at <= ?" if claimed_before is not None else ""
        age_parameters = (claimed_before.isoformat(),) if claimed_before is not None else ()
        with self._connect(writing=True) as connection:
            unknown = connection.execute(
                "UPDATE memory_tasks SET status=?, runner_token=NULL, last_error_code=? "
                f"WHERE status=? {exclusion} {age_clause} AND hy_invocation_count>0",
                (
                    MemoryTaskStatus.UNKNOWN,
                    "model_content_outcome_unknown",
                    MemoryTaskStatus.RUNNING,
                    *exclude_task_ids,
                    *age_parameters,
                ),
            ).rowcount
            changed = connection.execute(
                f"""
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, last_error_code = ?,
                    updated_at = ?
                WHERE status = ? {exclusion} {age_clause}
                """,
                (
                    MemoryTaskStatus.PENDING,
                    "worker_restarted",
                    datetime.now(UTC).isoformat(),
                    MemoryTaskStatus.RUNNING,
                    *exclude_task_ids,
                    *age_parameters,
                ),
            )
        return changed.rowcount + unknown

    def requeue_completed(self, task_id: str, *, reason: str) -> MemoryTask:
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, last_error_code = ?,
                    updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    MemoryTaskStatus.PENDING,
                    reason,
                    datetime.now(UTC).isoformat(),
                    task_id,
                    MemoryTaskStatus.COMPLETED,
                ),
            )
        if changed.rowcount != 1:
            raise MemoryTaskOwnershipError("Completed memory task was not requeued")
        return self.require(task_id)

    def require(self, task_id: str) -> MemoryTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _finish_owned(
        self,
        task_id: str,
        *,
        runner_token: str,
        status: MemoryTaskStatus,
        error_code: str | None,
    ) -> MemoryTask:
        with self._connect(writing=True) as connection:
            # updated_at still holds the claim time, so this is the attempt's
            # real duration rather than a gap that also counts idle time.
            changed = connection.execute(
                """
                UPDATE memory_tasks
                SET status = ?, runner_token = NULL, last_error_code = ?,
                    duration_seconds = MAX(
                        0, (julianday(?) - julianday(updated_at)) * 86400.0
                    ),
                    updated_at = ?
                WHERE task_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    status,
                    error_code,
                    (now := datetime.now(UTC).isoformat()),
                    now,
                    task_id,
                    MemoryTaskStatus.RUNNING,
                    runner_token,
                ),
            )
            if changed.rowcount != 1:
                raise MemoryTaskOwnershipError("Memory task runner no longer owns this task")
        return self.require(task_id)

    @contextmanager
    def _connect(self, *, writing: bool = False) -> Iterator[sqlite3.Connection]:
        database = main_database(self.database_path)
        checkout = (
            database.write(row_factory=sqlite3.Row)
            if writing
            else database.read(row_factory=sqlite3.Row)
        )
        with checkout as connection, connection:
            self._begin(connection, writing=writing)
            yield connection

    @staticmethod
    def _begin(connection: sqlite3.Connection, *, writing: bool) -> None:
        # Waiting is not enough for the methods here that check before they
        # write, and every one of them does: whose task this is, what state it
        # is in. Read first and SQLite refuses the write outright rather than
        # waiting for it. That is not hypothetical here: recording the catch-up
        # while the runner worked through it lost the whole catch-up to
        # `database is locked`, and with it every memory it would have made.
        if writing:
            take_the_write_lock_first(connection)

    @staticmethod
    def _to_task(row: sqlite3.Row) -> MemoryTask:
        return MemoryTask(
            task_id=row["task_id"],
            request_id=row["request_id"],
            user_id=row["user_id"],
            kind=row["kind"],
            input_ref=row["input_ref"],
            status=row["status"],
            profile_id=row["profile_id"],
            profile_revision=row["profile_revision"],
            profile_snapshot=ModelProfileSnapshot.model_validate_json(row["profile_snapshot_json"]),
            attempts=row["attempts"],
            last_error_code=row["last_error_code"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
