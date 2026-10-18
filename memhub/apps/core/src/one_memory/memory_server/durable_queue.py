"""D56: the durable write queue on the SQLite business database.

`WriteRegistry` records acceptance in memory, which a restart loses. This store
writes the same acceptance into `memory_write_jobs`, so the unified write entry
is durable and can be drained by `WriteQueuePipelines`.

Only the enqueue boundary lives here. Claim/lease/renew/reclaim stay in
`queue.py`, whose SQL is the contract; this module just supplies the rows those
statements act on, in the engine actually in force.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from one_memory.background_work import in_the_background
from one_memory.database_access import main_database
from one_memory.diagnostic_log import diagnostic_event
from one_memory.imports.models import EvidenceOrigin, EvidenceRecord
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.contracts import WriteRequest
from one_memory.memory_server.data_safety import MIN_FREE_BYTES  # noqa: F401 - re-exported
from one_memory.memory_server.migrations import memory_write_jobs_sqlite_schema
from one_memory.memory_server.queue import build_reclaim_sqlite_statements
from one_memory.memory_server.registry import WriteRecord
from one_memory.memory_tasks import (
    POLICY_STOPPED_CODES as _TASK_POLICY_STOPPED_CODES,
)
from one_memory.memory_tasks import (
    MemoryTaskStatus,
)
from one_memory.service_levels import ServiceLevel, service_level_context

logger = logging.getLogger(__name__)

#: A machine error code we are willing to persist: `model_content_*`,
#: `hy_memory_*` and similar tokens. Anything else (a provider message, a URL,
#: a sentence quoting the user's own text) is NOT a code and must never be
#: written to `last_error`: that column is surfaced over HTTP as `detail`, and
#: docs/contracts/hy-failure-classification.md forbids storing provider or
#: user content. Codes are lowercase tokens; exception class names are a
#: separate, separately-safe shape.
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$")
_TYPE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def safe_error_code(error: BaseException) -> str:
    """Reduce an exception to a storable code, never its message text.

    A recognised code (the message *is* the code, as `HyMemoryBridgeError`
    and the permission guard raise them) is kept so callers can classify it.
    Everything else collapses to its exception type, which is a fixed
    identifier rather than content.
    """
    message = str(error)
    if _ERROR_CODE.fullmatch(message):
        return message
    name = type(error).__name__
    return name if _TYPE_NAME.fullmatch(name) else "error"


PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
UNKNOWN = "unknown"
DEAD = "dead"
CANCELLED = "cancelled"
#: Nothing reached HY and nothing is broken: the write is parked until a
#: permission snapshot admits it. It must not read as `failed`.
WAITING_FOR_PERMISSION = "waiting_for_permission"
#: Same, for a transient model/provider condition: parked, not failed.
WAITING_FOR_MODEL = "waiting_for_model"

#: States that are parked, not finished: they keep their payload and are not
#: eligible for hot claiming until a later re-admission pass.
PARKED_STATES = (WAITING_FOR_PERMISSION, WAITING_FOR_MODEL)

#: How long a `waiting_for_model` job waits before the drain tries it again.
#: A model outage clears without emitting an event in this process, so unlike
#: `waiting_for_permission` (which needs a grant change to matter) this one is
#: re-admitted on a timer. The gap keeps a still-down model from spinning.
DEFAULT_MODEL_READMIT_AFTER = timedelta(minutes=1)

#: Production drain-budget defaults. These are the single source consumed by
#: the composition root; callers must not invent a second formula or copy a
#: test probe's numbers into the product path.
#:
#: The values are deliberately conservative and independent of the ordinary
#: user-visible write path:
#: - `WRITE_LOCK_WAIT_MS=5000` remains the ordinary writer budget and is not
#:   reused here;
#: - the response-tail call-event budget remains 250ms and is not changed by
#:   `claim_budget_ms`;
#: - `cleanup_reserve` is counted once inside `drain_budget`.
PRODUCTION_DRAIN_HANDLE_BUDGET = timedelta(seconds=5)
PRODUCTION_DRAIN_CLEANUP_RESERVE = timedelta(milliseconds=250)
PRODUCTION_DRAIN_BUDGET = timedelta(seconds=5, milliseconds=250)
PRODUCTION_DRAIN_LEASE_TTL = timedelta(seconds=10)
PRODUCTION_DRAIN_RENEW_EVERY = timedelta(seconds=1)
PRODUCTION_DRAIN_CLAIM_BUDGET_MS = 5_000


@dataclass(frozen=True)
class DrainBudget:
    """The single source for drain deadlines and lease relations.

    `cleanup_reserve` is counted exactly once: it is part of the independently
    configured `drain_budget`, and the work window is derived by subtracting
    it. Admission never adds a second reserve.
    """

    handle_budget: timedelta
    drain_budget: timedelta
    cleanup_reserve: timedelta
    lease_ttl: timedelta
    renew_every: timedelta
    claim_budget_ms: int

    def __post_init__(self) -> None:
        if self.handle_budget <= timedelta(0):
            raise ValueError("handle_budget must be positive")
        if self.cleanup_reserve <= timedelta(0):
            raise ValueError("cleanup_reserve must be positive")
        if self.drain_budget < self.handle_budget + self.cleanup_reserve:
            raise ValueError("drain_budget must cover handle_budget + cleanup_reserve")
        if self.renew_every <= timedelta(0):
            raise ValueError("renew_every must be positive")
        if self.lease_ttl <= self.handle_budget + self.renew_every:
            raise ValueError("lease_ttl must exceed handle_budget + renew_every")
        if self.renew_every > self.lease_ttl / 3:
            raise ValueError("renew_every must be at most lease_ttl / 3")
        if type(self.claim_budget_ms) is not int or self.claim_budget_ms < 1:
            raise ValueError("claim_budget_ms must be a positive integer")

    @property
    def claim_budget(self) -> timedelta:
        return timedelta(milliseconds=self.claim_budget_ms)

    @property
    def work_window(self) -> timedelta:
        return self.drain_budget - self.cleanup_reserve

    @property
    def outer_timeout(self) -> timedelta:
        return self.drain_budget

    @property
    def drain_work_window(self) -> timedelta:
        return self.work_window

    @property
    def work_ceiling(self) -> timedelta:
        """The safe per-job hard ceiling for callers wiring ``DurableWriteDrain``.

        The approved production budget already bounds one handle to
        ``handle_budget``. Expose that same bound here so the composition root
        can pass it without inventing a second formula or needing a profile
        timeout at static startup. ``__post_init__`` guarantees the lease is
        longer than this value.
        """
        return self.handle_budget

    def work_deadline(self, begin: float) -> float:
        return begin + self.drain_work_window.total_seconds()

    def deadline(self, begin: float) -> float:
        return begin + self.drain_budget.total_seconds()

    def can_start_work(self, now: float, begin: float) -> bool:
        return now + self.handle_budget.total_seconds() <= self.work_deadline(begin)


def derive_drain_budget(
    *,
    handle_budget: timedelta,
    cleanup_reserve: timedelta,
    renew_every: timedelta,
    claim_budget_ms: int,
    lease_ttl: timedelta | None = None,
    drain_budget: timedelta | None = None,
) -> DrainBudget:
    lease = lease_ttl or (handle_budget + cleanup_reserve + renew_every)
    total = drain_budget or (handle_budget + cleanup_reserve)
    return DrainBudget(
        handle_budget=handle_budget,
        drain_budget=total,
        cleanup_reserve=cleanup_reserve,
        lease_ttl=lease,
        renew_every=renew_every,
        claim_budget_ms=claim_budget_ms,
    )


def production_drain_budget() -> DrainBudget:
    """The one approved production budget; C0 passes it to the drain."""
    return derive_drain_budget(
        handle_budget=PRODUCTION_DRAIN_HANDLE_BUDGET,
        cleanup_reserve=PRODUCTION_DRAIN_CLEANUP_RESERVE,
        renew_every=PRODUCTION_DRAIN_RENEW_EVERY,
        claim_budget_ms=PRODUCTION_DRAIN_CLAIM_BUDGET_MS,
        lease_ttl=PRODUCTION_DRAIN_LEASE_TTL,
        drain_budget=PRODUCTION_DRAIN_BUDGET,
    )


#: An authorization stop: the user turned the feature off. Reuses the task
#: layer's list so the marking, the migration of old rows, the counting and
#: this classification cannot drift apart.
POLICY_STOPPED_CODES = frozenset(_TASK_POLICY_STOPPED_CODES)
#: Nothing was sent, but the write is not rejected: it waits for a snapshot
#: that admits it.
PERMISSION_REQUIRED_CODES = frozenset(
    {
        "model_content_permission_required",
        "model_content_evidence_not_permitted",
    }
)

#: Model-side codes `process_records` raises *before* it can open the bridge
#: (no active profile, missing credential, secure storage down). They prove
#: nothing was sent, so the write parks instead of being lost. Transport codes
#: that can arrive after a request was in flight stay `unknown`.
MODEL_NOT_READY_CODES = frozenset(
    {
        "model_profile_not_active",
        "model_secret_unavailable",
        "secure_storage_unavailable",
    }
)

#: Deterministic, pre-bridge refusals: the frozen input no longer matches
#: current Evidence, or a turn exceeds the model's input window. Both are
#: raised while validating the records, before any bridge or HY call, so
#: nothing was sent and retrying the same intent changes nothing — a terminal
#: `failed`, not `unknown` (which would wrongly imply a possibly-applied
#: write) and not a retry (which would fail identically).
#:
PRE_SEND_REFUSAL_CODES = frozenset(
    {
        "hy_memory_input_exceeds_window",
    }
)

#: Terminal rows keep only a digest of their original intent. The request body
#: is not needed once a job can never run again, but a replayed submit must
#: still be able to tell "same write" from "same key, different content". The
#: sentinel is unreachable as a real payload because `WriteRequest` forbids
#: extra fields.
REAPED_PAYLOAD_MARKER = "_one_memory_reaped_payload"

#: The states `set_state` may be asked for. `accepted` is the registry's
#: spelling of a pending write (see `DurableWriteRegistry._record`).
_VALID_JOB_STATES = frozenset(
    {
        "accepted",
        PENDING,
        RUNNING,
        COMPLETED,
        FAILED,
        UNKNOWN,
        CANCELLED,
        DEAD,
        WAITING_FOR_PERMISSION,
        WAITING_FOR_MODEL,
    }
)

#: A row in one of these states can never be moved back to a live one.
_TERMINAL_WRITE_STATES = frozenset({COMPLETED, FAILED, DEAD, CANCELLED})

#: Columns a current schema declares but an older `memory_write_jobs` may lack.
#: `CREATE TABLE IF NOT EXISTS` is a no-op for an existing table, so without
#: this an upgraded install would keep an older queue that every claim/lease
#: query fails on. Declarations must include a default so existing rows stay
#: readable; `attempts`/`max_attempts` gate retry, and `hy_invoked` is the
#: no-blind-retry boundary.
LEGACY_MEMORY_WRITE_JOB_COLUMNS = (
    ("queue_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("source_key", "TEXT NOT NULL DEFAULT ''"),
    ("evidence_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("priority", "TEXT NOT NULL DEFAULT 'normal'"),
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
    ("lease_owner", "TEXT"),
    ("lease_expires_at", "TEXT"),
    ("hy_invoked", "INTEGER NOT NULL DEFAULT 0"),
    ("last_error", "TEXT"),
    ("event_at", "TEXT"),
    ("time_quality", "TEXT NOT NULL DEFAULT 'approximate'"),
)

_FIFO_BLOCKING_STATES = (PENDING, RUNNING, WAITING_FOR_PERMISSION, WAITING_FOR_MODEL)
EVENT_TIME_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True)
class DurableWriteRecord:
    job_id: str
    request_id: str
    state: str
    enqueued: bool
    created_at: str
    updated_at: str
    detail: str = ""


class ReconcileSchemaUnavailableError(RuntimeError):
    """The Evidence schema is not ready for a reconcile read.

    A queue file can exist before the migration runner has created `evidence`.
    That is an initialization state, not an empty reconcile result and not a
    generic SQLite failure. Composition roots can retry after migration
    instead of silently treating all Evidence as already queued.
    """


class _LeaseRenewalFatalError(RuntimeError):
    """A non-transient renewal failure that must stop the worker."""


@dataclass(frozen=True)
class UnknownWriteJob:
    """An `unknown` row that still needs an external inquiry.

    `request_id` is the durable queue's idempotency key. It identifies the row
    for queue inspection and is the value `DurableWriteQueue.get()` accepts.
    It is **not** the HY request id: `MemoryProcessingSupervisor` derives the
    HY handle from the task it creates, so an inquiry implementation must
    obtain that task-layer handle separately rather than passing this value to
    HY. This is discovery only: it does not decide whether HY has the write,
    and it deliberately exposes no transition method that could turn
    `unknown` back into retryable work.
    """

    job_id: str
    request_id: str
    created_at: str
    updated_at: str
    detail: str = ""


def _payload_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def _reaped_payload(digest: str) -> str:
    return json.dumps({REAPED_PAYLOAD_MARKER: digest}, sort_keys=True)


def _matches_stored_payload(stored: str, payload: str) -> bool:
    """Whether a stored payload matches a new submission's canonical payload.

    Terminal rows store a reaped sentinel instead of the full intent; compare
    against its digest so replay stays idempotent without keeping the body.
    """
    try:
        decoded = json.loads(stored)
    except (TypeError, ValueError):
        return stored == payload
    if isinstance(decoded, dict) and REAPED_PAYLOAD_MARKER in decoded:
        return bool(decoded[REAPED_PAYLOAD_MARKER] == _payload_digest(payload))
    return stored == payload


def _matches_payload_except_priority(stored: str, payload: str) -> bool:
    try:
        old = json.loads(stored)
        new = json.loads(payload)
    except (TypeError, ValueError):
        return False
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if REAPED_PAYLOAD_MARKER in old:
        return False
    old.pop("priority", None)
    new.pop("priority", None)
    return old == new


def _matches_legacy_reference_replay(stored: str, payload: str) -> bool:
    """Match an old id-only caller to the same job's frozen payload."""
    try:
        old = json.loads(stored)
        new = json.loads(payload)
    except (TypeError, ValueError):
        return False
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    old_inputs = old.get("input_records")
    new_inputs = new.get("input_records")
    if bool(old_inputs) == bool(new_inputs):
        return False
    old.pop("input_records", None)
    new.pop("input_records", None)
    return old == new


def _matches_reaped_legacy_payload(stored: str, payload: str) -> bool:
    """Compare a current frozen request with a pre-snapshot reaped payload."""
    try:
        old = json.loads(stored)
        current = json.loads(payload)
    except (TypeError, ValueError):
        return False
    if (
        not isinstance(old, dict)
        or not isinstance(current, dict)
        or not isinstance(old.get(REAPED_PAYLOAD_MARKER), str)
    ):
        return False
    current.pop("input_records", None)
    legacy_payload = json.dumps(current, sort_keys=True)
    return bool(old[REAPED_PAYLOAD_MARKER] == _payload_digest(legacy_payload))


class DurableQueueSchemaUnavailableError(RuntimeError):
    """The durable queue schema has not been created yet.

    A database file can exist before the migration runner (or the queue's own
    constructor) has created `memory_write_jobs`. That is an initialization
    state, not an empty queue and not a server fault. Read APIs translate this
    to a retryable "unavailable" response instead of a 500.
    """


def _require_queue_schema(connection: sqlite3.Connection) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_write_jobs'"
        ).fetchone()
        is None
    ):
        raise DurableQueueSchemaUnavailableError("durable queue schema is unavailable")


class DurableWriteQueue:
    """The accepted-write side of the unified entry, persisted in SQLite."""

    def __init__(
        self,
        database_path: Path,
        *,
        min_free_bytes: int = 0,
        evidence: EvidenceStore | None = None,
        initialize: bool = True,
    ) -> None:
        self.database_path = database_path
        #: Optional Evidence authority. When supplied, `submit` enforces the
        #: A3-03 "Evidence first, then one queue" rule at the durable boundary:
        #: a request may not create a queue row pointing at evidence the
        #: business database does not yet hold. The default keeps this access
        #: layer usable in isolation, matching `restore_from`'s optional
        #: product marker.
        self.evidence = evidence
        #: When non-zero, a submission is refused while the filesystem free
        #: space is below this reserve. The fault matrix's `disk_full` outcome
        #: is `refuse_writes` — a half-applied write is worse than a pause — so
        #: the check happens before the INSERT, not after a failure. Default 0
        #: keeps existing callers/tests unchanged; the Runtime passes the shared
        #: `data_safety.MIN_FREE_BYTES` (or a config value) to enforce it.
        self.min_free_bytes = min_free_bytes
        #: Read-only surfaces open the queue with ``initialize=False``: they
        #: must not run schema DDL or reclaim expired leases, or a GET would
        #: be a write. Default ``True`` keeps composition/drain behavior.
        if not initialize:
            return
        with main_database(database_path).write() as connection, connection:
            connection.row_factory = sqlite3.Row
            # First create the base table if absent. Then widen an older table
            # before replaying indexes that reference the new columns; a bare
            # CREATE INDEX would fail on a pre-priority queue.
            create_table = memory_write_jobs_sqlite_schema()[0]
            connection.execute(create_table)
            self._add_legacy_columns(connection)
            self._rebuild_wrongly_shaped_claim_index(connection)
            for statement in memory_write_jobs_sqlite_schema()[1:]:
                connection.execute(statement)
            self._backfill_legacy_references(connection)
            self._backfill_legacy_event_times(connection)
        # A process that died while holding a lease cannot run its own
        # recovery. Opening the queue is the first safe point to recover any
        # rows it left behind, before any new worker claims them.
        self.reclaim_expired()

    @staticmethod
    def _add_legacy_columns(connection: sqlite3.Connection) -> None:
        """Add columns an older `memory_write_jobs` predates.

        SQLite has no `ADD COLUMN IF NOT EXISTS` and raises on a duplicate, so
        each column is checked first. Every declaration carries a default, so
        older rows remain valid.
        """
        existing = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(memory_write_jobs)")
        }
        for column, declaration in LEGACY_MEMORY_WRITE_JOB_COLUMNS:
            if column in existing:
                continue
            connection.execute(f"ALTER TABLE memory_write_jobs ADD COLUMN {column} {declaration}")
        connection.execute(
            "UPDATE memory_write_jobs SET source_key=session_key WHERE source_key=''"
        )
        legacy_rows = connection.execute(
            "SELECT job_id FROM memory_write_jobs WHERE queue_seq=0 ORDER BY created_at, rowid"
        ).fetchall()
        next_seq = int(
            connection.execute(
                "SELECT COALESCE(MAX(queue_seq), 0) FROM memory_write_jobs"
            ).fetchone()[0]
        )
        for row in legacy_rows:
            next_seq += 1
            connection.execute(
                "UPDATE memory_write_jobs SET queue_seq=? WHERE job_id=?",
                (next_seq, str(row[0])),
            )

    @staticmethod
    def _rebuild_wrongly_shaped_claim_index(connection: sqlite3.Connection) -> None:
        """Replace an old claim index whose name matches but columns do not.

        `CREATE INDEX IF NOT EXISTS` is keyed only on the index name. A queue
        upgraded from a build that indexed `(state, created_at)` therefore
        keeps that shape forever, even after the current DDL asks for
        `(state, priority, created_at)`, and the ordered claim silently sorts.
        Compare the actual key first and drop only the stale shape; the normal
        schema replay below recreates it.
        """
        columns = [
            str(row[1])
            for row in connection.execute(
                "SELECT seqno, name FROM pragma_index_info('idx_write_jobs_claim') ORDER BY seqno"
            )
        ]
        if columns and columns != ["state", "priority", "created_at"]:
            connection.execute("DROP INDEX idx_write_jobs_claim")

    @staticmethod
    def _backfill_legacy_event_times(connection: sqlite3.Connection) -> None:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence'"
            ).fetchone()
            is None
        ):
            connection.execute(
                "UPDATE memory_write_jobs SET event_at=created_at WHERE event_at IS NULL"
            )
            return
        evidence_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(evidence)").fetchall()
        }
        if not {"created_at", "imported_at", "role"} <= evidence_columns:
            connection.execute(
                "UPDATE memory_write_jobs SET event_at=created_at WHERE event_at IS NULL"
            )
            return
        connection.execute(
            """
            UPDATE memory_write_jobs
            SET event_at = COALESCE(
                (
                    SELECT MAX(COALESCE(e.created_at, e.imported_at))
                    FROM memory_write_job_evidence AS linked
                    JOIN evidence AS e ON e.evidence_id = linked.evidence_id
                    WHERE linked.job_id = memory_write_jobs.job_id
                      AND e.role = 'user'
                ),
                (
                    SELECT MIN(COALESCE(e.created_at, e.imported_at))
                    FROM memory_write_job_evidence AS linked
                    JOIN evidence AS e ON e.evidence_id = linked.evidence_id
                    WHERE linked.job_id = memory_write_jobs.job_id
                ),
                created_at
            )
            WHERE event_at IS NULL
            """
        )

    @staticmethod
    def _backfill_legacy_references(connection: sqlite3.Connection) -> None:
        """Populate the reference table after its schema exists."""
        # The reference table is new alongside the column. Populate it from any
        # intact payload or already-backfilled reference list. A row whose
        # payload was already reaped and whose reference list is empty cannot
        # prove which Evidence it represented; leave it empty so discovery
        # fails closed instead of treating its Evidence as unqueued.
        # Do not stop at the first non-empty reference table. A restore,
        # partial external cleanup, or an interrupted backfill can leave one
        # row's references present while another row's are missing. Each row
        # is therefore reconciled independently; the per-reference existence
        # check below keeps this idempotent.
        rows = connection.execute(
            "SELECT job_id, payload_json, evidence_ids_json FROM memory_write_jobs"
        ).fetchall()
        existing_refs = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT job_id, evidence_id FROM memory_write_job_evidence"
            )
        }
        refs_to_insert: list[tuple[str, str]] = []
        refs_to_rewrite: list[tuple[str, str]] = []
        for job_id, payload_json, evidence_ids_json in rows:
            evidence_ids: list[str] = []
            try:
                decoded_refs = json.loads(str(evidence_ids_json))
            except ValueError:
                decoded_refs = None
            if (
                isinstance(decoded_refs, list)
                and decoded_refs
                and all(isinstance(item, str) and item for item in decoded_refs)
            ):
                evidence_ids = decoded_refs
            try:
                decoded = json.loads(str(payload_json))
            except ValueError:
                decoded = None
            if not isinstance(decoded, dict):
                decoded = {}
            payload_ids = decoded.get("evidence_ids")
            if (
                not evidence_ids
                and isinstance(payload_ids, list)
                and payload_ids
                and all(isinstance(item, str) and item for item in payload_ids)
            ):
                evidence_ids = payload_ids
            if evidence_ids and str(evidence_ids_json) != json.dumps(evidence_ids):
                refs_to_rewrite.append((json.dumps(evidence_ids), str(job_id)))
            # A hand-edited, restored, or pre-validation row can list the same
            # id twice. The reference table's primary key is
            # `(job_id, evidence_id)`, so inserting both would abort the whole
            # construction with an IntegrityError and leave the Runtime unable
            # to open the queue at all. Collapse duplicates per row and keep
            # the insert idempotent against the rows seen above.
            for evidence_id in dict.fromkeys(evidence_ids):
                if (str(job_id), evidence_id) not in existing_refs:
                    refs_to_insert.append((str(job_id), evidence_id))
        if refs_to_rewrite:
            connection.executemany(
                "UPDATE memory_write_jobs SET evidence_ids_json=? WHERE job_id=?",
                refs_to_rewrite,
            )
        if refs_to_insert:
            connection.executemany(
                "INSERT INTO memory_write_job_evidence (job_id, evidence_id) VALUES (?,?)",
                refs_to_insert,
            )

    def reclaim_expired(self) -> int:
        """Recover expired leases on the only engine D56 permits.

        A lease whose owner died before HY was invoked is safe to retry. Once
        `hy_invoked` is recorded, the outcome may already exist in HY and its
        requests have no idempotency key, so that row becomes `unknown`
        instead of `pending`. Unknown is intentionally sticky: a later reclaim
        pass cannot turn it back into retryable work.
        """
        now = datetime.now(UTC).isoformat()
        changed = 0
        with main_database(self.database_path).write() as connection, connection:
            for sql, params in build_reclaim_sqlite_statements(now):
                changed += connection.execute(sql, params).rowcount
        return changed

    def submit(self, request: WriteRequest) -> DurableWriteRecord:
        """Persist an accepted write, or reconcile it against one already queued.

        Same key + same payload returns the existing job with `enqueued=False`;
        a different payload under the same key is refused rather than silently
        replacing the earlier intent.
        """
        self._refuse_if_below_reserve()
        request = self._freeze_request_inputs(request)
        payload = json.dumps(
            request.model_dump(
                mode="json",
                exclude={"event_at", "time_quality"},
            ),
            sort_keys=True,
        )
        now = datetime.now(UTC).isoformat()
        resolved_event, resolved_quality = self._event_time_for_request(request)
        event = request.event_at or resolved_event or datetime.fromisoformat(now)
        if event.tzinfo is None:
            event = event.replace(tzinfo=UTC)
        event_at = event.astimezone(UTC).isoformat()
        key = request.request_key
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            existing = connection.execute(
                "SELECT job_id, payload_json, state, created_at, updated_at, "
                "hy_invoked, attempts, max_attempts, last_error "
                "FROM memory_write_jobs WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                already_settled = (
                    str(existing["state"]) == COMPLETED and int(existing["hy_invoked"]) == 1
                )
                stored_payload = str(existing["payload_json"])
                priority_replay = _matches_payload_except_priority(stored_payload, payload)
                legacy_reaped_match = bool(
                    already_settled
                    and _matches_reaped_legacy_payload(stored_payload, payload)
                )
                if (
                    not _matches_stored_payload(stored_payload, payload)
                    and not priority_replay
                    and not _matches_legacy_reference_replay(stored_payload, payload)
                    and not legacy_reaped_match
                ):
                    raise ValueError(f"idempotency key {key} already exists with different content")
                if already_settled:
                    return DurableWriteRecord(
                        job_id=str(existing["job_id"]),
                        request_id=key,
                        state=str(existing["state"]),
                        enqueued=False,
                        created_at=str(existing["created_at"]),
                        updated_at=str(existing["updated_at"]),
                        detail="already completed; replay treated as a hit",
                    )
                if priority_replay and request.priority == "live":
                    connection.execute(
                        "UPDATE memory_write_jobs SET priority='live', updated_at=? "
                        "WHERE job_id=? AND priority='normal' AND hy_invoked=0",
                        (now, str(existing["job_id"])),
                    )
                safe_identity_retry = (
                    str(existing["state"]) == FAILED
                    and int(existing["hy_invoked"]) == 0
                    and int(existing["attempts"]) < int(existing["max_attempts"])
                    and str(existing["last_error"])
                    in {
                        "model_content_ingest_identity_unverified",
                        "model_content_evidence_changed",
                    }
                )
                if safe_identity_retry:
                    connection.execute(
                        "UPDATE memory_write_jobs SET state=?, priority=?, "
                        "lease_owner=NULL, lease_expires_at=NULL, last_error=NULL, updated_at=? "
                        "WHERE job_id=? AND state=? AND hy_invoked=0",
                        (
                            PENDING,
                            request.priority,
                            now,
                            str(existing["job_id"]),
                            FAILED,
                        ),
                    )
                    return DurableWriteRecord(
                        job_id=str(existing["job_id"]),
                        request_id=key,
                        state=PENDING,
                        enqueued=True,
                        created_at=str(existing["created_at"]),
                        updated_at=now,
                        detail="safe pre-send identity retry requeued",
                    )
                return DurableWriteRecord(
                    job_id=str(existing["job_id"]),
                    request_id=key,
                    state=str(existing["state"]),
                    enqueued=False,
                    created_at=str(existing["created_at"]),
                    updated_at=str(existing["updated_at"]),
                    detail="already accepted; not enqueued again",
                )
            # Evidence-first applies to a new row. An identical replay of an
            # already-accepted write must still find its original record even
            # if that Evidence has since been removed.
            self._require_evidence(request)
            job_id = str(uuid4())
            queue_seq = int(
                connection.execute(
                    "SELECT COALESCE(MAX(queue_seq), 0) + 1 FROM memory_write_jobs"
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO memory_write_jobs ("
                " job_id, queue_seq, session_key, source_key, batch_id,"
                " idempotency_key, payload_json,"
                " evidence_ids_json,"
                " state, priority, attempts, max_attempts, lease_owner,"
                " lease_expires_at, hy_invoked, last_error, event_at, time_quality,"
                " created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    queue_seq,
                    request.internal_session_id,
                    request.source_id or request.internal_session_id,
                    request.batch_identity.batch_id,
                    key,
                    payload,
                    json.dumps(list(request.evidence_ids)),
                    PENDING,
                    request.priority,
                    0,
                    3,
                    None,
                    None,
                    0,
                    None,
                    event_at,
                    request.time_quality if request.event_at is not None else resolved_quality,
                    now,
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO memory_write_job_evidence (job_id, evidence_id) VALUES (?,?)",
                ((job_id, str(evidence_id)) for evidence_id in request.evidence_ids),
            )
        diagnostic_event(
            level="info",
            component="memory.write_queue",
            event="memory_write_job.queued",
            outcome="queued",
            job_id=job_id,
            details={
                "source_id": request.source_id or request.internal_session_id,
                "mode": request.priority,
                "count": len(request.evidence_ids),
            },
        )
        return DurableWriteRecord(
            job_id=job_id,
            request_id=key,
            state=PENDING,
            enqueued=True,
            created_at=now,
            updated_at=now,
        )

    def _freeze_request_inputs(self, request: WriteRequest) -> WriteRequest:
        if request.input_records or self.evidence is None:
            return request
        records: list[EvidenceRecord] = []
        for evidence_id in request.evidence_ids:
            record = self.evidence.get(evidence_id)
            if record is None:
                return request
            records.append(record)
        return request.model_copy(update={"input_records": tuple(records)})

    def _event_time_for_request(self, request: WriteRequest) -> tuple[datetime | None, str]:
        records = list(request.input_records)
        if not records and self.evidence is not None:
            records = [
                record
                for evidence_id in request.evidence_ids
                if (record := self.evidence.get(evidence_id)) is not None
            ]
        if not records:
            return None, "approximate"
        opener = next(
            (record for record in records if record.role == "user"),
            records[0],
        )
        if opener.created_at is None:
            return opener.imported_at, "approximate"
        origin = opener.origin_meta.get(EvidenceOrigin.FILE)
        basis = origin.raw_meta.get("time_basis") if origin is not None else None
        turn_event_at = origin.raw_meta.get("turn_event_at") if origin is not None else None
        if isinstance(turn_event_at, str):
            try:
                return datetime.fromisoformat(turn_event_at), "exact"
            except ValueError:
                pass
        if basis == "file_mtime":
            return opener.created_at, "session"
        return opener.created_at, "exact"

    def repair_pre_send_identity_failures(self, *, limit: int = 128) -> int:
        """Refresh stale Evidence journals and requeue only provably unsent jobs."""
        if self.evidence is None:
            return 0
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        from one_memory import ingest_journal
        from one_memory.database import take_the_write_lock_first

        now = datetime.now(UTC).isoformat()
        repaired = 0
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            take_the_write_lock_first(connection)
            rows = connection.execute(
                "SELECT job_id FROM memory_write_jobs "
                "WHERE state=? AND hy_invoked=0 "
                "AND last_error='model_content_ingest_identity_unverified' "
                "AND attempts < max_attempts ORDER BY updated_at,job_id LIMIT ?",
                (FAILED, limit),
            ).fetchall()
            for row in rows:
                job_id = str(row["job_id"])
                evidence_ids = [
                    str(link["evidence_id"])
                    for link in connection.execute(
                        "SELECT evidence_id FROM memory_write_job_evidence "
                        "WHERE job_id=? ORDER BY evidence_id",
                        (job_id,),
                    ).fetchall()
                ]
                if not evidence_ids or any(
                    EvidenceStore._get_with_connection(connection, evidence_id) is None
                    for evidence_id in evidence_ids
                ):
                    continue
                for evidence_id in evidence_ids:
                    ingest_journal.record(connection, evidence_id, cause="import")
                repaired += connection.execute(
                    "UPDATE memory_write_jobs SET state=?,lease_owner=NULL,"
                    "lease_expires_at=NULL,last_error=NULL,updated_at=? "
                    "WHERE job_id=? AND state=? AND hy_invoked=0",
                    (PENDING, now, job_id, FAILED),
                ).rowcount
        return repaired

    def _require_evidence(self, request: WriteRequest) -> None:
        """Enforce the Evidence-first rule when an Evidence authority is set."""
        if self.evidence is None:
            return
        missing = [
            evidence_id
            for evidence_id in request.evidence_ids
            if self.evidence.get(evidence_id) is None
        ]
        if missing:
            raise LookupError(f"write points at missing evidence: {missing[0]}")

    def unsubmitted_evidence_ids(
        self, *, limit: int = 512, after_cursor: str | None = None
    ) -> tuple[str, ...]:
        """Committed Evidence that has no durable write entry yet.

        This is the discovery half of the H23 reconcile. Writing Evidence and
        submitting the write are two steps; a crash between them leaves a row
        that no task will ever pick up. A startup/reconcile pass calls this to
        find those rows, ordered by `ingest_seq` so it can resume from a
        cursor, and then decides how to group them into a `WriteRequest`
        (product semantics C2 does not own).

        Read-only and bounded: it never mutates the queue, so a caller that
        errors mid-pass retries from the same `after_ingest_seq` without
        having half-enqueued anything.

        Ordering is by `(imported_at, evidence_id)` rather than
        `evidence.ingest_seq`: under the current write path a freshly inserted
        row leaves `ingest_seq` NULL (migration 34 only backfills pre-existing
        rows), and this must not add another SQLite-only construct to the code
        base. `imported_at` is present on every row; `evidence_id` breaks ties
        so the order is total. `after_cursor` is the last id a previous call
        returned; the scan resumes strictly after that row.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        if after_cursor is not None and (not isinstance(after_cursor, str) or not after_cursor):
            raise ValueError("after_cursor must be a non-empty string")
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM memory_write_jobs job "
                "WHERE NOT EXISTS (SELECT 1 FROM memory_write_job_evidence ref "
                "WHERE ref.job_id=job.job_id) "
                "AND (evidence_ids_json IS NULL OR evidence_ids_json IN ('', '[]')) "
                "AND payload_json LIKE ?",
                (f'%"{REAPED_PAYLOAD_MARKER}"%',),
            ).fetchone()[0]
            if int(unresolved):
                raise RuntimeError(
                    "evidence references unavailable for reaped queue rows: "
                    f"{unresolved}; refusing an incomplete reconcile"
                )
            evidence_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence'"
            ).fetchone()
            if evidence_table is None:
                raise ReconcileSchemaUnavailableError(
                    "evidence table is unavailable; run migrations before reconcile"
                )
            has_versions = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='evidence_versions'"
                ).fetchone()
                is not None
            )
            evidence_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(evidence)").fetchall()
            }
            if {"revision", "content_hash", "updated_at"} <= evidence_columns:
                revision_time = (
                    "COALESCE((SELECT version.changed_at FROM evidence_versions version "
                    "WHERE version.evidence_id=evidence.evidence_id "
                    "AND version.revision=evidence.revision),"
                    "evidence.updated_at,evidence.imported_at)"
                    if has_versions
                    else "COALESCE(evidence.updated_at,evidence.imported_at)"
                )
                where = (
                    "WHERE evidence.imported_at IS NOT NULL "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM memory_write_job_evidence linked "
                    "JOIN memory_write_jobs job ON job.job_id=linked.job_id, "
                    "json_each(job.payload_json,'$.input_records') frozen "
                    "WHERE linked.evidence_id=evidence.evidence_id "
                    "AND json_extract(frozen.value,'$.evidence_id')=evidence.evidence_id "
                    "AND json_extract(frozen.value,'$.revision')=evidence.revision "
                    "AND json_extract(frozen.value,'$.content_hash')=evidence.content_hash"
                    ") "
                    # Pre-snapshot builds only recorded an Evidence id. Treat
                    # that as coverage only when its Job was created after this
                    # exact revision.
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM memory_write_job_evidence legacy "
                    "JOIN memory_write_jobs legacy_job ON legacy_job.job_id=legacy.job_id "
                    "WHERE legacy.evidence_id=evidence.evidence_id "
                    "AND COALESCE(json_array_length("
                    "json_extract(legacy_job.payload_json,'$.input_records')),0)=0 "
                    f"AND legacy_job.created_at>=({revision_time}))"
                )
            else:
                # A genuinely old/minimal schema has no revision identity.
                where = (
                    "WHERE evidence.imported_at IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM memory_write_job_evidence legacy "
                    "WHERE legacy.evidence_id=evidence.evidence_id)"
                )
            params: tuple[Any, ...] = ()
            cursor_clause = ""
            if after_cursor is not None:
                anchor = connection.execute(
                    "SELECT imported_at, evidence_id FROM evidence WHERE evidence_id=?",
                    (after_cursor,),
                ).fetchone()
                if anchor is None:
                    # A cursor that no longer resolves must not silently reset
                    # to the first page. The caller would re-enqueue a prefix it
                    # believed it had already consumed, and the only thing
                    # preventing duplicate work would be the downstream key.
                    # Refuse the incomplete pass instead; the caller can restart
                    # from a fresh cursor deliberately.
                    raise RuntimeError(
                        "evidence reconcile cursor is unavailable; restart with a fresh cursor"
                    )
                cursor_clause = "AND (evidence.imported_at, evidence.evidence_id) > (?,?)"
                params = (*params, str(anchor["imported_at"]), str(anchor["evidence_id"]))
            rows = connection.execute(
                "SELECT evidence.evidence_id FROM evidence "
                f"{where} {cursor_clause} "
                "ORDER BY evidence.imported_at, evidence.evidence_id LIMIT ?",
                (*params, limit),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _refuse_if_below_reserve(self) -> None:
        """Refuse a new write when the disk reserve is breached.

        Off by default (`min_free_bytes=0`), so existing callers and tests are
        unaffected; the Runtime sets it to the shared
        `data_safety.MIN_FREE_BYTES` (1 GiB) or a configured value. When set,
        this is the `disk_full -> refuse_writes` outcome the fault matrix names:
        the check runs before the INSERT, so a refusal writes no row.
        """
        reserve = self.min_free_bytes
        if reserve <= 0:
            return
        free = shutil.disk_usage(self.database_path.parent).free
        if free < reserve:
            raise OSError(f"disk reserve breached: free {free} below {reserve}")

    def get(self, request_id: str) -> DurableWriteRecord | None:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            _require_queue_schema(connection)
            row = connection.execute(
                "SELECT job_id, state, created_at, updated_at, last_error "
                "FROM memory_write_jobs WHERE idempotency_key=?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return DurableWriteRecord(
            job_id=str(row["job_id"]),
            request_id=request_id,
            state=str(row["state"]),
            enqueued=False,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            detail=str(row["last_error"] or ""),
        )

    def payload_for(self, job_id: str) -> WriteRequest | None:
        """The stored write intent for a claimed job."""
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            row = connection.execute(
                "SELECT payload_json FROM memory_write_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        payload = str(row["payload_json"])
        try:
            decoded = json.loads(payload)
        except ValueError:
            return None
        if isinstance(decoded, dict) and REAPED_PAYLOAD_MARKER in decoded:
            # Only terminal rows are reaped, and those are never claimable;
            # this guards a stale claim from resurrecting a dead intent.
            return None
        return WriteRequest.model_validate_json(payload)

    def settle(
        self,
        job_id: str,
        *,
        state: str,
        error_code: str | None = None,
        hy_invoked: bool | None = None,
        owner: str | None = None,
    ) -> None:
        """Record a terminal outcome, and whether HY was reached.

        `hy_invoked` is the no-blind-retry boundary: once it is set, a lapsed
        lease must go to `unknown`, never back to `pending`.

        A row already in a terminal state is sticky against a stale worker's
        late settle: a worker whose lease lapsed can still finish and call back
        here, and overwriting would erase a real outcome.

        - `unknown` -> anything else: would discard "HY may already hold this"
          and allow a duplicate; only `resolve_unknown` may move it.
        - `completed`/`cancelled`/`dead` -> anything: the outcome already
          happened; a later settle is a stale report, not a new fact.

        - `hy_invoked=1` -> `pending`: refused. Once HY may have been reached,
          requeueing would re-send the write and duplicate the memory (HY has
          no idempotency key). A possibly-sent job may only leave through
          `unknown` (settle) or `resolve_unknown`.

        Re-asserting the same terminal state (or `unknown` -> `unknown`) is
        allowed so callers stay idempotent.

        `owner`, when given, is the lease holder that believes it owns this
        job. The update is then guarded on `lease_owner`, so a worker whose
        lease lapsed and the row was re-claimed elsewhere cannot settle a job
        it no longer owns and overwrite the new owner's outcome.
        """
        with main_database(self.database_path).write() as connection, connection:
            now = datetime.now(UTC).isoformat()
            current = connection.execute(
                "SELECT state, lease_owner, hy_invoked, lease_expires_at "
                "FROM memory_write_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if current is not None:
                current_state = str(current[0])
                if state == PENDING and int(current[2]):
                    # Possibly sent: never make it claimable again.
                    logger.warning(
                        "durable_write_settle_refused_possibly_sent job_id=%s state=%s",
                        job_id,
                        state,
                    )
                    return
                if current_state in _TERMINAL_WRITE_STATES | {UNKNOWN}:
                    if state != current_state:
                        return
                    # Re-asserting the same terminal state is idempotent, but
                    # must not overwrite the outcome that was recorded first.
                    # A stale worker can call back after its lease was
                    # reclaimed, and its later error text would otherwise
                    # replace the real result and its diagnostic detail.
                    return
                # Must mirror the SQL guard below exactly. Accepting NULL here
                # would let the update match zero rows while this early-return
                # logic behaved as if the settle had been applied.
                if owner is not None and (
                    current[1] != owner or current[3] is None or str(current[3]) < now
                ):
                    # The lease belongs to someone else now; this is a stale
                    # report, not a new fact. Say so: the row is left untouched,
                    # and without this line a refused settle is invisible to
                    # the log while the caller sees no error either.
                    logger.warning(
                        "durable_write_settle_rejected_stale_owner job_id=%s state=%s",
                        job_id,
                        state,
                    )
                    return
            where = "WHERE job_id=?"
            params: tuple[Any, ...] = ()
            if owner is not None:
                where = (
                    "WHERE job_id=? AND lease_owner=? "
                    "AND lease_expires_at IS NOT NULL AND lease_expires_at>=?"
                )
                params = (owner, now)
            if hy_invoked is None:
                updated = connection.execute(
                    "UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                    f"lease_expires_at=NULL, last_error=?, updated_at=? {where}",
                    (state, error_code, now, job_id, *params),
                )
            else:
                updated = connection.execute(
                    "UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                    f"lease_expires_at=NULL, last_error=?, hy_invoked=?, updated_at=? {where}",
                    (state, error_code, 1 if hy_invoked else 0, now, job_id, *params),
                )
            if owner is not None and updated.rowcount != 1:
                # Defensive: the guard above should already have caught this,
                # but a race between the SELECT and UPDATE would land here. Log
                # only the id and target state; never payload or error context.
                logger.warning("durable_write_settle_missed_row job_id=%s state=%s", job_id, state)

    def mark_invoked(self, job_id: str, *, owner: str | None = None) -> bool:
        """Record the no-blind-retry boundary without dropping the lease.

        This must not go through `settle(RUNNING, ...)`: that clears
        `lease_owner` and `lease_expires_at`, which would (a) make `renew()` a
        no-op so the lease stops being extended, and (b) leave a crashed row
        `running` with a null expiry, which reclaim can never see. The marker
        is written in place instead, so an in-flight job keeps renewing and a
        crash is later reclaimed to `unknown`.

        `owner` is the lease holder that believes it owns this job. When given,
        the update is guarded on it: a worker whose lease lapsed and the row was
        re-claimed by someone else must not succeed here, or it would go on to
        call HY for a job it no longer owns — the duplicate this boundary
        exists to prevent. This mirrors `build_mark_invoked_statement` on the
        MySQL path.
        """
        with main_database(self.database_path).write() as connection, connection:
            if owner is None:
                updated = connection.execute(
                    "UPDATE memory_write_jobs SET hy_invoked=1, updated_at=? "
                    "WHERE job_id=? AND state=?",
                    (datetime.now(UTC).isoformat(), job_id, RUNNING),
                )
            else:
                now = datetime.now(UTC).isoformat()
                updated = connection.execute(
                    "UPDATE memory_write_jobs SET hy_invoked=1, updated_at=? "
                    "WHERE job_id=? AND state=? AND lease_owner=? "
                    "AND lease_expires_at IS NOT NULL AND lease_expires_at>=?",
                    (now, job_id, RUNNING, owner, now),
                )
        return updated.rowcount == 1

    def release_unstarted(self, job_id: str, *, owner: str | None = None) -> bool:
        """Return a claimed job to pending when its source was paused pre-HY."""
        now = datetime.now(UTC).isoformat()
        where = "job_id=? AND state=? AND hy_invoked=0"
        params: tuple[Any, ...] = (job_id, RUNNING)
        if owner is not None:
            where += " AND lease_owner=?"
            params += (owner,)
        with main_database(self.database_path).write() as connection, connection:
            updated = connection.execute(
                "UPDATE memory_write_jobs SET state=?,lease_owner=NULL,"
                "attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END,"
                "lease_expires_at=NULL,updated_at=? WHERE " + where,
                (PENDING, now, *params),
            )
        return updated.rowcount == 1

    def settle_worker_error(self, job_id: str, *, error_code: str, owner: str | None = None) -> str:
        """Fallback settlement when the injected executor raises past its guard.

        `execute_job` normally catches its own failures, so reaching here means
        something unexpected escaped. The worker must not leave the row
        `running` until reclaim, so read the durable marker and choose
        conservatively: possibly-invoked is `unknown` (never a blind retry),
        never-invoked is `pending` while attempts remain and `dead` when they
        do not. Returns the state written, or the untouched state if the row is
        already terminal.

        `owner`, when given, must match the row's `lease_owner`; otherwise the
        row belongs to another worker now and this is a stale report that must
        not release the new owner's lease.
        """
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            now = datetime.now(UTC).isoformat()
            row = connection.execute(
                "SELECT state, hy_invoked, attempts, max_attempts, lease_owner, lease_expires_at "
                "FROM memory_write_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                return ""
            if str(row["state"]) != RUNNING:
                return str(row["state"])
            # The SQL below requires `lease_owner = ?`. An ownerless row is an
            # orphan reclaimed by `reclaim_expired`, not something this worker
            # may settle; accepting NULL here made the guard disagree with the
            # update, which matched zero rows while we still reported success.
            if owner is not None and (
                row["lease_owner"] != owner
                or row["lease_expires_at"] is None
                or str(row["lease_expires_at"]) < now
            ):
                return str(row["state"])
            if int(row["hy_invoked"]):
                state = UNKNOWN
            elif int(row["attempts"]) < int(row["max_attempts"]):
                state = PENDING
            else:
                state = DEAD
            connection.execute(
                "UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                "lease_expires_at=NULL, last_error=?, updated_at=? "
                f"WHERE job_id=? AND state=?"
                f"{' AND lease_owner=? AND lease_expires_at>=?' if owner is not None else ''}",
                (
                    state,
                    error_code,
                    now,
                    job_id,
                    RUNNING,
                    *((owner, now) if owner is not None else ()),
                ),
            )
        return state

    def clear_terminal_payloads(self, *, limit: int = 512) -> int:
        """Deprecated compatibility hook; durable inputs are immutable audit data."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        return 0

    def readmit_parked(self, *, states: tuple[str, ...] = PARKED_STATES) -> int:
        """Move parked jobs back to `pending` for a fresh claim.

        Parked jobs are deliberately not hot-claimed: without this, a job with
        no permission would spin. The composition root calls this when the
        condition that parked them may have changed (a new permission snapshot,
        a model that is reachable again), and the write is re-evaluated then.

        The attempt consumed by the claim that parked the job is given back.
        Parking is not a failed delivery attempt — nothing was sent — so
        letting it count would let a write that only ever waited for permission
        exhaust `max_attempts` and be dead-lettered without ever being tried.

        `states` is restricted to the parked states. Accepting an arbitrary
        tuple would let a caller pass `completed`/`dead`/`cancelled` and turn a
        finished write back into claimable work — the blind re-send the
        terminal states exist to prevent.
        """
        if not states:
            return 0
        invalid = [state for state in states if state not in PARKED_STATES]
        if invalid:
            raise ValueError(
                f"readmit_parked only accepts parked states {PARKED_STATES}; got {invalid!r}"
            )
        placeholders = ",".join("?" for _ in states)
        now = datetime.now(UTC).isoformat()
        with main_database(self.database_path).write() as connection, connection:
            changed = connection.execute(
                "UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                "lease_expires_at=NULL, attempts=CASE WHEN attempts>0 THEN attempts-1 "
                "ELSE 0 END, last_error=NULL, updated_at=? "
                # `hy_invoked=0` is a structural guard, not a convention: once
                # HY may have been reached the row must never become claimable
                # again, and a possibly-sent row must go to `unknown` instead.
                f"WHERE state IN ({placeholders}) AND hy_invoked=0",
                (PENDING, now, *states),
            )
        return changed.rowcount

    def readmit_model_waiters(
        self,
        *,
        now: datetime | None = None,
        after: timedelta = DEFAULT_MODEL_READMIT_AFTER,
    ) -> int:
        """Re-admit `waiting_for_model` jobs whose wait has elapsed.

        A model outage clears on its own, so this state is retried on a timer
        rather than waiting for a grant event like `waiting_for_permission`.
        Keyed on `updated_at` so repeated calls while a job is still parked do
        not reset its own clock; the attempt consumed by the parking claim is
        returned, the same way `readmit_parked` does.
        """
        now = now or datetime.now(UTC)
        threshold = (now - after).isoformat()
        with main_database(self.database_path).write() as connection, connection:
            changed = connection.execute(
                "UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                "lease_expires_at=NULL, attempts=CASE WHEN attempts>0 "
                "THEN attempts-1 ELSE 0 END, last_error=NULL, updated_at=? "
                "WHERE state=? AND updated_at <= ? AND hy_invoked=0",
                (PENDING, now.isoformat(), WAITING_FOR_MODEL, threshold),
            )
        return changed.rowcount

    def recover_obsolete_legacy_authority_failures(self) -> int:
        """Re-admit writes rejected by the retired legacy-revision coupling.

        The old continuous-policy check failed before opening HY, so only rows
        that explicitly record that code and ``hy_invoked=0`` are safe to
        retry automatically. Unknown or possibly-sent work is never touched.
        """
        code = "model_content_legacy_authority_changed"
        now = datetime.now(UTC).isoformat()
        with main_database(self.database_path).write() as connection, connection:
            changed = connection.execute(
                "UPDATE memory_write_jobs SET state=?, attempts=0, lease_owner=NULL, "
                "lease_expires_at=NULL, last_error=NULL, updated_at=? "
                "WHERE state=? AND last_error=? AND hy_invoked=0",
                (PENDING, now, FAILED, code),
            ).rowcount
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_tasks'"
            ).fetchone():
                connection.execute(
                    "UPDATE memory_tasks SET status=?, attempts=0, runner_token=NULL, "
                    "last_error_code='model_content_policy_rebased', updated_at=? "
                    "WHERE status=? AND last_error_code=?",
                    (MemoryTaskStatus.PENDING, now, MemoryTaskStatus.FAILED, code),
                )
        return changed

    def resolve_unknown(self, job_id: str, *, found: bool, detail: str = "") -> str | None:
        """Retire an `unknown` job after an inquiry, never by guessing.

        `unknown` means HY may already hold the write; the only safe way out is
        to ask by request id (`hy_capabilities` measured that the SDK supports
        this) and then record what the inquiry found. `found=True` closes it as
        `completed`; `found=False` gives up and marks it `dead`. The state
        machine's legal transitions are the authority, so this refuses any
        change that `job_state.next_state` would reject — in particular it can
        never turn an unknown back into retryable `pending`.

        Returns the new state, or `None` when the row is missing or not
        `unknown` (an already-resolved row is left untouched).
        """
        from one_memory.memory_server.contracts import WriteJobState as JobState
        from one_memory.memory_server.job_state import (
            IllegalTransition,
            JobEvent,
            next_state,
        )

        event = JobEvent.RESOLVED if found else JobEvent.GIVE_UP
        target = next_state(JobState.UNKNOWN, event)
        now = datetime.now(UTC).isoformat()
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            row = connection.execute(
                "SELECT state FROM memory_write_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None or str(row["state"]) != UNKNOWN:
                return None
            # Re-affirm the transition against the row we actually hold, so a
            # race that changed it cannot be forced into an illegal state.
            try:
                next_state(JobState(str(row["state"])), event)
            except IllegalTransition:
                return None
            connection.execute(
                "UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                "lease_expires_at=NULL, last_error=?, updated_at=? WHERE job_id=?",
                (
                    str(target),
                    detail or (None if found else "model_content_outcome_not_found"),
                    now,
                    job_id,
                ),
            )
        return str(target)

    def retry_unknown_confirmed(self, job_id: str) -> int:
        """Requeue one unknown write only after an explicit user decision.

        Automatic workers may never move ``unknown`` back to pending because
        HY may already contain the result. This separate command boundary is
        intentionally named for the confirmation it requires. The durable
        job and any task rows created from the same Evidence are reset in one
        short transaction, so the unified queue remains the only execution
        entry and a half-reset attempt cannot become permanently stranded.
        """
        now = datetime.now(UTC).isoformat()
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            row = connection.execute(
                "SELECT state FROM memory_write_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None or str(row["state"]) != UNKNOWN:
                return 0
            connection.execute(
                """
                UPDATE memory_tasks
                SET status='pending', runner_token=NULL, attempts=0,
                    last_error_code='model_content_manual_retry_requested', updated_at=?
                WHERE status='unknown' AND task_id IN (
                    SELECT inputs.task_id
                    FROM memory_task_inputs AS inputs, json_each(inputs.refs_json) AS ref
                    JOIN memory_write_job_evidence AS linked
                      ON linked.evidence_id=json_extract(ref.value, '$.evidence_id')
                    WHERE linked.job_id=?
                )
                """,
                (now, job_id),
            )
            changed = connection.execute(
                """
                UPDATE memory_write_jobs
                SET state='pending', attempts=0, lease_owner=NULL,
                    lease_expires_at=NULL, hy_invoked=0,
                    last_error='model_content_manual_retry_requested', updated_at=?
                WHERE job_id=? AND state='unknown'
                """,
                (now, job_id),
            ).rowcount
        return changed

    def readmit_cancelled_permission_confirmed(self, job_id: str, request: WriteRequest) -> int:
        """Requeue one unsent permission cancellation after explicit consent.

        Revocation is terminal for automatic workers. The manual process route
        is different: the person has just selected the exact Evidence under a
        fresh permission snapshot. Only a row proven not to have called HY may
        cross back to pending, so this cannot duplicate a possibly-sent write.
        """
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(
            request.model_dump(
                mode="json",
                exclude={"event_at", "time_quality"},
            ),
            sort_keys=True,
        )
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            row = connection.execute(
                "SELECT idempotency_key,payload_json,state,hy_invoked,last_error "
                "FROM memory_write_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if (
                row is None
                or str(row["idempotency_key"]) != request.request_key
                or not _matches_stored_payload(str(row["payload_json"]), payload)
                or str(row["state"]) != CANCELLED
                or int(row["hy_invoked"]) != 0
                or str(row["last_error"]) not in POLICY_STOPPED_CODES
            ):
                return 0
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_tasks'"
            ).fetchone():
                connection.execute(
                    "UPDATE memory_tasks SET status='pending',runner_token=NULL,attempts=0,"
                    "last_error_code='model_content_manual_retry_requested',updated_at=? "
                    "WHERE request_id=? AND status='cancelled'",
                    (now, str(row["idempotency_key"])),
                )
            changed = connection.execute(
                "UPDATE memory_write_jobs SET state='pending',payload_json=?,attempts=0,"
                "lease_owner=NULL,"
                "lease_expires_at=NULL,last_error=NULL,updated_at=? "
                "WHERE job_id=? AND state='cancelled' AND hy_invoked=0",
                (payload, now, job_id),
            ).rowcount
        return changed

    def retry_timed_out_unknown_after_reconcile(
        self,
        *,
        older_than: datetime,
        limit: int = 32,
    ) -> int:
        """Requeue bounded timeout unknowns only after the caller queried HY.

        This is not the general unknown retry path. Only a timed-out write with
        attempts remaining is eligible, and attempts are preserved so repeated
        timeouts stop at the durable ceiling instead of looping forever.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC).isoformat()
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            rows = connection.execute(
                "SELECT job_id FROM memory_write_jobs "
                "WHERE state=? AND last_error='hy_memory_timeout' "
                "AND attempts < max_attempts AND updated_at <= ? "
                "ORDER BY updated_at, job_id LIMIT ?",
                (UNKNOWN, older_than.isoformat(), limit),
            ).fetchall()
            job_ids = tuple(str(row["job_id"]) for row in rows)
            if not job_ids:
                return 0
            placeholders = ",".join("?" for _ in job_ids)
            connection.execute(
                f"UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                f"lease_expires_at=NULL, hy_invoked=0, "
                f"last_error='model_content_reconciled_absent_retry', updated_at=? "
                f"WHERE state=? AND job_id IN ({placeholders})",
                (PENDING, now, UNKNOWN, *job_ids),
            )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_tasks'"
            ).fetchone():
                connection.execute(
                    f"UPDATE memory_tasks SET status=?, runner_token=NULL, "
                    f"last_error_code='model_content_reconciled_absent_retry', updated_at=? "
                    f"WHERE status=? AND task_id IN ("
                    f"SELECT inputs.task_id FROM memory_task_inputs AS inputs "
                    f"JOIN memory_write_job_evidence AS linked ON EXISTS ("
                    f"SELECT 1 FROM json_each(inputs.refs_json) AS ref "
                    f"WHERE json_extract(ref.value, '$.evidence_id')=linked.evidence_id) "
                    f"WHERE linked.job_id IN ({placeholders}))",
                    (MemoryTaskStatus.PENDING, now, MemoryTaskStatus.UNKNOWN, *job_ids),
                )
        return len(job_ids)

    def retry_first_unknown_after_reconcile(
        self,
        *,
        older_than: datetime,
        limit: int = 32,
    ) -> int:
        """Retry one unresolved unknown outcome once, after reconciliation.

        The caller first asks HY and checks linked governed output. Rows still
        unknown may have lost the response after the SDK call, so one bounded
        retry is useful. `attempts=1` is the guard: the retry claim increments
        it to two, and a second unknown is left for explicit user recovery.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC).isoformat()
        with (
            main_database(self.database_path).write(row_factory=sqlite3.Row) as connection,
            connection,
        ):
            rows = connection.execute(
                "SELECT job_id FROM memory_write_jobs "
                "WHERE state=? AND attempts=1 AND attempts < max_attempts "
                "AND updated_at <= ? ORDER BY updated_at, job_id LIMIT ?",
                (UNKNOWN, older_than.isoformat(), limit),
            ).fetchall()
            job_ids = tuple(str(row["job_id"]) for row in rows)
            if not job_ids:
                return 0
            placeholders = ",".join("?" for _ in job_ids)
            connection.execute(
                f"UPDATE memory_write_jobs SET state=?, lease_owner=NULL, "
                f"lease_expires_at=NULL, hy_invoked=0, "
                f"last_error='model_content_unknown_auto_retry', updated_at=? "
                f"WHERE state=? AND attempts=1 AND job_id IN ({placeholders})",
                (PENDING, now, UNKNOWN, *job_ids),
            )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_tasks'"
            ).fetchone():
                connection.execute(
                    f"UPDATE memory_tasks SET status=?, runner_token=NULL, "
                    f"last_error_code='model_content_unknown_auto_retry', updated_at=? "
                    f"WHERE status=? AND task_id IN ("
                    f"SELECT inputs.task_id FROM memory_task_inputs AS inputs "
                    f"JOIN memory_write_job_evidence AS linked ON EXISTS ("
                    f"SELECT 1 FROM json_each(inputs.refs_json) AS ref "
                    f"WHERE json_extract(ref.value, '$.evidence_id')=linked.evidence_id) "
                    f"WHERE linked.job_id IN ({placeholders}))",
                    (MemoryTaskStatus.PENDING, now, MemoryTaskStatus.UNKNOWN, *job_ids),
                )
        return len(job_ids)

    def resolve_unknown_from_linked_output(self, job_id: str) -> bool:
        """Close an unknown durable row when local governance output proves success."""
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM memory_write_job_evidence AS linked
                JOIN memory_task_inputs AS inputs
                  ON EXISTS (
                    SELECT 1 FROM json_each(inputs.refs_json) AS ref
                    WHERE json_extract(ref.value, '$.evidence_id')=linked.evidence_id
                  )
                JOIN memory_tasks AS task ON task.task_id=inputs.task_id
                JOIN memory_task_outputs AS output ON output.task_id=task.task_id
                WHERE linked.job_id=? AND task.status='completed'
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return False
        return (
            self.resolve_unknown(
                job_id,
                found=True,
                detail="model_content_outcome_reconciled",
            )
            == COMPLETED
        )

    def unknown_jobs(
        self,
        *,
        limit: int = 128,
        after_cursor: tuple[str, str] | None = None,
    ) -> tuple[UnknownWriteJob, ...]:
        """List unknown rows that still need an inquiry, in stable order.

        `unknown` is deliberately non-terminal, so a Runtime cannot process it
        by claiming it again: retrying would duplicate a memory. A periodic
        reconcile still needs to find those rows and hand their stable durable
        identity to the task-layer inquiry. This method provides that bounded
        discovery without calling HY or choosing a retry policy. The returned
        `request_id` is the durable queue key, not the HY request handle.

        Ordering is `(updated_at, job_id)`; `after_cursor` is the previous
        page's last pair, so a caller can resume without treating the read as a
        cross-call snapshot. The read is bounded and never mutates a row.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        cursor_clause = ""
        params: tuple[Any, ...] = ()
        if after_cursor is not None:
            if (
                not isinstance(after_cursor, tuple)
                or len(after_cursor) != 2
                or not all(isinstance(value, str) and value for value in after_cursor)
            ):
                raise ValueError("after_cursor must be a (updated_at, job_id) pair")
            updated_at, job_id = after_cursor
            cursor_clause = "AND (updated_at, job_id) > (?, ?)"
            params = (updated_at, job_id)
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            rows = connection.execute(
                "SELECT job_id, idempotency_key, created_at, updated_at, last_error "
                "FROM memory_write_jobs "
                "WHERE state=? "
                f"{cursor_clause} "
                "ORDER BY updated_at, job_id LIMIT ?",
                (UNKNOWN, *params, limit),
            ).fetchall()
        return tuple(
            UnknownWriteJob(
                job_id=str(row["job_id"]),
                request_id=str(row["idempotency_key"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                detail=str(row["last_error"] or ""),
            )
            for row in rows
        )


class DurableWriteRegistry:
    """The HTTP route's registry shape backed by the durable SQLite queue.

    `routes.py` deliberately depends on a tiny `submit/get/set_state` shape,
    not on the queue implementation. This adapter supplies exactly that shape
    while keeping `DurableWriteQueue`'s durable API intact. The response
    vocabulary maps the queue's `pending` row to the route's historical
    `accepted` state; the row itself remains the authority.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        evidence: EvidenceStore | None = None,
        initialize: bool = True,
    ) -> None:
        self.queue = DurableWriteQueue(
            database_path,
            evidence=evidence,
            initialize=initialize,
        )

    @staticmethod
    def _record(record: DurableWriteRecord) -> WriteRecord:
        # `pending` is the historical `accepted`. `running` — a row a live drain
        # has claimed — is also `accepted` to a caller: the HTTP `WriteState`
        # vocabulary has no `running`, and mapping it here keeps `GET
        # /memory-writes/{id}` from failing validation (a 500) the moment a
        # consumer exists. `dead` is a durable-queue concept (retries exhausted)
        # and is a terminal failure, so it maps to `failed` with a
        # distinguishing detail. Parked and cancelled states keep their own
        # names — collapsing an authorization stop into `failed` is exactly the
        # pollution the task layer already rejects.
        if record.state in (PENDING, RUNNING):
            state = "accepted"
        elif record.state == DEAD:
            state = "failed"
        else:
            state = record.state
        detail = record.detail
        if record.state == DEAD and "dead" not in detail.lower():
            detail = (detail + "; " if detail else "") + "dead letter: attempts exhausted"
        # The mapping above only ever yields one of the route's literal states,
        # but a runtime `str` needs a cast for the `WriteState` Literal.
        from typing import cast

        from one_memory.memory_server.registry import WriteState

        return WriteRecord(
            request_id=record.request_id,
            state=cast(WriteState, state),
            fingerprint="",
            created_at=record.created_at,
            updated_at=record.updated_at,
            detail=detail,
        )

    def submit(
        self, request: WriteRequest, known: dict[str, str] | None = None
    ) -> tuple[WriteRecord, bool]:
        del known  # The unique durable key is the authority, not a caller map.
        durable = self.queue.submit(request)
        return self._record(durable), durable.enqueued

    def get(self, request_id: str) -> WriteRecord | None:
        durable = self.queue.get(request_id)
        return None if durable is None else self._record(durable)

    def set_state(self, request_id: str, state: str, detail: str = "") -> WriteRecord | None:
        """Record an outcome, refusing to move a terminal row back to live.

        `state` comes from the HTTP layer, so it is validated against the
        queue's own vocabulary rather than written through, and a terminal row
        can never be moved back to a live state. Letting a `completed`/`dead`/
        `cancelled` row become `pending` (or an unvalidated string) would allow
        exactly the blind re-send the terminal states exist to prevent.
        """
        if not isinstance(state, str):
            raise ValueError(f"unknown write job state: {state}")
        if state not in _VALID_JOB_STATES:
            raise ValueError(f"unknown write job state: {state}")
        current = self.queue.get(request_id)
        if current is None:
            return None
        # A `running` row is owned by a live lease. Settling it here would
        # clear that lease and write an outcome for work still in flight,
        # letting the drain complete later and call HY a second time (or
        # overwriting the real result). Only the lease owner's own settle may
        # move a running row; the HTTP setter leaves it untouched.
        if current.state == RUNNING:
            return None
        # `unknown` may only leave through `resolve_unknown`, which records
        # the inquiry result. This HTTP-facing setter has no evidence that HY
        # was queried, so allowing it to write completed/failed here would
        # turn "possibly delivered" into a guess.
        if current.state == UNKNOWN:
            return None
        # `dead` / `cancelled` / `completed` are terminal; `accepted` is this
        # route's spelling of the non-terminal `pending`.
        if (current.state in _TERMINAL_WRITE_STATES) and state not in _TERMINAL_WRITE_STATES:
            return None
        # `accepted` is this route's name for a pending row; store the durable
        # spelling so a claim can find it.
        durable_state = PENDING if state == "accepted" else state
        self.queue.settle(current.job_id, state=durable_state, error_code=detail or None)
        return self.get(request_id)


def evidence_for_request(store: EvidenceStore, request: WriteRequest) -> tuple[EvidenceRecord, ...]:
    """The Evidence a stored write points at, in the request's own order.

    Current jobs are self-contained: their immutable records are the model
    input even if a Harness later rewinds or edits its live transcript. The
    store lookup remains only for payloads written by pre-upgrade builds.
    """
    if request.input_records:
        return request.input_records
    records = []
    for evidence_id in request.evidence_ids:
        record = store.get(evidence_id)
        if record is None:
            raise LookupError(f"write points at missing evidence: {evidence_id}")
        records.append(record)
    return tuple(records)


async def execute_job(
    job_id: str,
    *,
    queue: DurableWriteQueue,
    supervisor: Any,
    evidence: EvidenceStore,
    owner: str | None = None,
    source_writes_permitted: Callable[[str], bool] | None = None,
    admit_hy_invocation: Callable[[str, Callable[[], Awaitable[bool]]], Awaitable[bool]]
    | None = None,
) -> None:
    """Run one durable job through the existing authorized processing path.

    The executor does not call HY directly: `process_records` owns the
    permission guard, the runner and governance. `hy_invoked` is recorded
    before that call, so a crash mid-write is later reclaimed to `unknown`
    instead of being retried blindly.
    """
    # Every store call here is synchronous SQLite work on the single writer or
    # a reader; made from the loop it would block health checks and the API for
    # as long as the write lock is held.
    request = await in_the_background(queue.payload_for, job_id)
    if request is None:
        # A claimed row whose payload is missing or unreadable (reaped while
        # still claimable, corrupted, or a foreign row) must not be left
        # `running`: nothing was sent, so it is a terminal failure rather than
        # an unknown, and the lease must be released so it is not held until it
        # lapses.
        await in_the_background(
            queue.settle,
            job_id,
            state=FAILED,
            error_code="durable_write_payload_unreadable",
            hy_invoked=False,
            owner=owner,
        )
        return
    try:
        records = await in_the_background(evidence_for_request, evidence, request)
    except LookupError as error:
        await in_the_background(
            queue.settle,
            job_id,
            state=FAILED,
            error_code=safe_error_code(error),
            hy_invoked=False,
            owner=owner,
        )
        return

    hy_invoked = False

    async def mark_hy_invocation() -> None:
        """Persist the durable boundary immediately before the real HY call."""
        nonlocal hy_invoked
        if hy_invoked:
            # Pause fences a job only before its first provider call. Once this
            # durable job has crossed that boundary, let the whole job finish;
            # interrupting a later chunk would leave a partial, unknown write.
            return
        source_ids = {getattr(record, "source_id", None) for record in records}
        source_ids.discard(None)
        source_id = request.source_id
        if source_id is None and len(source_ids) == 1:
            source_id = next(iter(source_ids))
        if source_id is None:
            source_id = request.internal_session_id
        if source_ids and source_ids != {source_id}:
            raise RuntimeError("durable_write_multiple_sources")

        async def mark() -> bool:
            return await in_the_background(queue.mark_invoked, job_id, owner=owner)

        if admit_hy_invocation is not None:
            admitted = await admit_hy_invocation(source_id, mark)
        else:
            if source_writes_permitted is not None and not source_writes_permitted(source_id):
                raise RuntimeError("model_content_source_disabled")
            admitted = await mark()
        if not admitted:
            # The lease was lost: this row is no longer ours and may already
            # have been reclaimed or re-claimed. Calling HY here would write
            # the memory twice, so abort before crossing the provider boundary.
            raise RuntimeError("durable_write_lease_lost_before_invocation")
        hy_invoked = True

    try:
        level = ServiceLevel.REALTIME if request.priority == "live" else ServiceLevel.BACKGROUND
        with service_level_context(level):
            await supervisor.process_records(
                records,
                before_hy_invocation=mark_hy_invocation,
            )
    except Exception as error:  # noqa: BLE001 - classified below
        # Never persist the raw message: it may be a provider response or the
        # user's own text, and `last_error` is shown over HTTP as `detail`.
        code = safe_error_code(error)
        if (
            code
            in {
                "model_content_source_disabled",
                "model_sedimentation_disabled",
            }
            and not hy_invoked
        ):
            await in_the_background(queue.release_unstarted, job_id, owner=owner)
            return
        # The task layer classifies these before any bridge is opened, so HY
        # was provably not called. They must not read as `failed`:
        # - authorization stop -> cancelled (the user turned it off);
        # - no permission yet -> waiting_for_permission (parked, re-admitted
        #   when a snapshot admits it);
        # - transient model/provider trouble -> waiting_for_model (parked).
        # Everything else may have crossed into HY, and HY has no idempotency
        # key, so it is `unknown` rather than a blind retry.
        if code in PRE_SEND_REFUSAL_CODES:
            # The frozen input window is checked before the payload reaches
            # HY. This one refusal therefore has a proven pre-send boundary.
            state, invoked_marker = FAILED, False
        elif hy_invoked:
            # Error names do not prove execution phase: permission and
            # evidence rechecks also run after HY returns. Once the durable
            # marker is set, every failure is possibly-sent and must stay out
            # of the automatic retry path.
            state, invoked_marker = UNKNOWN, True
        elif code in POLICY_STOPPED_CODES:
            state, invoked_marker = CANCELLED, False
        elif code in PERMISSION_REQUIRED_CODES or code.startswith("model_content_policy"):
            state, invoked_marker = WAITING_FOR_PERMISSION, False
        elif code in MODEL_NOT_READY_CODES:
            state, invoked_marker = WAITING_FOR_MODEL, False
        elif code == "model_content_transport_request_failed":
            # The generation or embedding HTTP exchange can fail after HY has
            # already persisted a node. Treating that as a pre-send failure
            # permits a blind duplicate; the confirmed unknown flow first
            # reconciles by request identity and only then requeues absence.
            state, invoked_marker = UNKNOWN, True
        elif code.startswith("model_content_") and code != "model_content_outcome_unknown":
            state, invoked_marker = FAILED, False
        else:
            # Force the marker even if this row was not claimed first (a direct
            # call): the outcome is only `unknown` because HY may have been
            # reached, and reclaim must treat it as possibly-sent.
            state, invoked_marker = UNKNOWN, True
        await in_the_background(
            queue.settle,
            job_id,
            state=state,
            error_code=code,
            hy_invoked=invoked_marker,
            owner=owner,
        )
        # Never `logger.exception` here: it prints the exception's message,
        # which may be a provider response or the user's own text. Log the
        # already-sanitised code instead, matching `hy-failure-classification`.
        logger.error("durable_write_job_failed job_id=%s error_code=%s", job_id, code)
        return
    await in_the_background(queue.settle, job_id, state=COMPLETED, owner=owner)


class DurableWriteDrain:
    """Run SQLite durable jobs with lease, periodic renewal and no blind retry.

    This is the SQLite counterpart of `WriteQueuePipelines`. It deliberately
    stays small: the queue table already exists through `DurableWriteQueue`,
    and `execute_job` owns the authorized processing path. The drain adds the
    parts the table alone cannot provide: atomic claim under the single writer,
    lease renewal while work is in flight, and the no-retry boundary before
    invoking HY.
    """

    def __init__(
        self,
        queue: DurableWriteQueue,
        *,
        execute: Callable[[str], Awaitable[None]],
        execute_owned: Callable[[str, str], Awaitable[None]] | None = None,
        owner: str | None = None,
        lease: timedelta = timedelta(seconds=120),
        renew_every: timedelta | None = None,
        reap_interval: timedelta = timedelta(seconds=30),
        work_ceiling: timedelta | Callable[[], timedelta] | None = None,
        claim_budget_ms: int = 250,
        drain_handle_budget: timedelta | None = None,
        drain_cleanup_reserve: timedelta | None = None,
        max_concurrent_sources: int = 10,
        reconcile_unknown: Callable[[], Awaitable[None]] | None = None,
        source_writes_permitted: Callable[[str], bool] | None = None,
        source_claim_enabled: Callable[[str], bool] | None = None,
    ) -> None:
        self.queue = queue
        self.execute = execute
        #: Optional executor that also receives the lease owner, so it can pass
        #: it to `mark_invoked` and refuse to run a job it no longer owns.
        self.execute_owned = execute_owned
        self.owner = owner or f"durable-drain:{uuid4()}"
        self.lease = lease
        self.renew_every = renew_every or timedelta(seconds=max(0.1, lease.total_seconds() / 4))
        self.running = False
        self._run_lock = asyncio.Lock()
        #: Reaping is a write; it runs at most once per this interval on the
        #: idle path so an empty queue does not issue one write per poll.
        self._reap_interval = reap_interval
        # Unknown recovery is user-visible and lightweight enough to check
        # sooner than general database housekeeping. Tests that set the common
        # interval to zero keep immediate maintenance.
        self._unknown_reconcile_interval = min(
            reap_interval,
            timedelta(seconds=10),
        )
        #: The maintenance writes below are recoverability/retention work, not
        #: idle housekeeping, so each has its own interval and runs at the start
        #: of every drain tick. Sharing one timestamp would let a busy tick
        #: starve the others.
        self._last_reclaim: datetime | None = None
        #: Terminal-payload reaping shares the same recoverability concern: a
        #: sustained backlog must not keep finished rows' request bodies in the
        #: business database indefinitely. Its own gate so neither interval can
        #: starve the other.
        #: `waiting_for_model` re-admission is also time-based recoverability:
        #: a model outage clears on its own, and a queue that never goes idle
        #: must still re-admit the write once the wait has elapsed.
        self._last_model_readmit: datetime | None = None
        #: Idle polls do not need to hit the single writer every 50ms. A
        #: 1m Evidence table makes a miss expensive enough that the tail
        #: call-event write can be behind it; back off only while idle and
        #: reset immediately when a claim finds work.
        self._idle_streak = 0
        self._database_failure_streaks: dict[str, int] = {}
        #: The model profile owns this wall-clock ceiling. The lease is only a
        #: renewable liveness marker and may be shorter than a healthy model
        #: call; coupling the two made the production 5s drain slice cancel
        #: normal HY work.
        self.work_ceiling = work_ceiling
        self.budget: DrainBudget | None = None
        if drain_handle_budget is not None or drain_cleanup_reserve is not None:
            handle = drain_handle_budget or (
                work_ceiling if isinstance(work_ceiling, timedelta) else lease
            )
            reserve = drain_cleanup_reserve or timedelta(milliseconds=250)
            self.budget = derive_drain_budget(
                handle_budget=handle,
                cleanup_reserve=reserve,
                renew_every=self.renew_every,
                claim_budget_ms=claim_budget_ms,
                lease_ttl=lease,
            )
        if type(claim_budget_ms) is not int or claim_budget_ms < 1:
            raise ValueError("claim_budget_ms must be a positive integer")
        #: Claim is optional background admission, not the user's write. It
        #: must not consume the full 5s writer budget while a tail metadata
        #: write has only 250ms; a short budget returns the writer promptly
        #: and the loop simply retries.
        self.claim_budget_ms = claim_budget_ms
        if not 1 <= max_concurrent_sources <= 10:
            raise ValueError("max_concurrent_sources must be between 1 and 10")
        self.max_concurrent_sources = max_concurrent_sources
        self.reconcile_unknown = reconcile_unknown
        self.source_writes_permitted = source_writes_permitted
        self.source_claim_enabled = source_claim_enabled or source_writes_permitted
        self._last_unknown_reconcile: datetime | None = None

    def claim(
        self,
        *,
        limit: int = 1,
        now: datetime | None = None,
        budget_ms: int | None = None,
    ) -> tuple[str, ...]:
        """Claim event-time heads, with one running job per enabled Harness."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        now = now or datetime.now(UTC)
        expires_at = now + self.lease
        # Cheap reader-side gate before taking the writer. WAL lets this read
        # proceed while another writer (a live import) holds the lock, so an
        # empty queue costs a ~0.3ms read instead of a full claim-budget wait
        # that can only fail. On the R5.6 full-corpus run the drain's 250ms
        # claim was refused 2000+ times against an import while the queue held
        # nothing; this skips those. It is a gate, not the decision: the write
        # transaction below re-derives claimability under the lock, so a row
        # inserted between this read and the write is not missed for a queued
        # job (it is simply claimed on the next tick).
        #
        # The predicate mirrors the claim's own state+lease clause, so it is a
        # true claimability check and not just a presence check. It fails OPEN:
        # if the probe itself errors (a non-WAL mode, a transient reader
        # refusal), fall through to the write path rather than skipping a
        # claimable row.
        try:
            with main_database(self.queue.database_path).read() as probe:
                claimable_sql = (
                    "SELECT 1 FROM memory_write_jobs candidate WHERE state=? "
                    "AND (lease_expires_at IS NULL OR lease_expires_at < ?) LIMIT 1"
                )
                claimable = probe.execute(
                    claimable_sql,
                    (PENDING, now.isoformat()),
                ).fetchone()
            if claimable is None:
                return ()
        except sqlite3.Error:
            pass
        enabled_sources: set[str] | None = None
        if self.source_writes_permitted is not None:
            with main_database(self.queue.database_path).read() as connection:
                source_rows = connection.execute(
                    "SELECT DISTINCT source_key FROM memory_write_jobs WHERE state=?",
                    (PENDING,),
                ).fetchall()
            enabled_sources = {
                str(row[0]) for row in source_rows if self.source_writes_permitted(str(row[0]))
            }
            if not enabled_sources:
                return ()
        database = main_database(self.queue.database_path)
        checkout = (
            database.write(row_factory=sqlite3.Row)
            if budget_ms is None
            else database.write(busy_timeout_ms=budget_ms, row_factory=sqlite3.Row)
        )
        with checkout as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            blocking = ",".join("?" for _ in _FIFO_BLOCKING_STATES)
            source_clause = ""
            source_params: tuple[Any, ...] = ()
            if enabled_sources is not None:
                placeholders = ",".join("?" for _ in enabled_sources)
                source_clause = f"AND candidate.source_key IN ({placeholders}) "
                source_params = tuple(sorted(enabled_sources))
            window = connection.execute(
                "SELECT window_anchor,window_end FROM memory_write_schedule_state WHERE singleton=1"
            ).fetchone()
            if window is not None:
                anchor_text, end_text = str(window[0]), str(window[1])
                active_source_clause = ""
                active_source_params: tuple[Any, ...] = ()
                if enabled_sources is not None:
                    placeholders = ",".join("?" for _ in enabled_sources)
                    active_source_clause = f"AND active.source_key IN ({placeholders}) "
                    active_source_params = tuple(sorted(enabled_sources))
                # The contract retires a window when every *eligible*
                # pending/running job in it has settled or parked. A pending row
                # that scheduling already holds back (an unexpired lease, a
                # busy Harness, or an earlier event in its Session) is not eligible, so it
                # must not keep the anchor alive: those blockers usually sit
                # outside this ten-minute range and can never be claimed from
                # inside it, which pinned the anchor and starved the whole
                # queue. Reuse the claim's own predicate so "the window is
                # busy" and "the window can still be worked" agree.
                active = connection.execute(
                    "SELECT 1 FROM memory_write_jobs AS active "
                    "WHERE active.event_at>=? AND active.event_at<? AND ("
                    "active.state=? OR (active.state=? "
                    "AND (active.lease_expires_at IS NULL "
                    "OR active.lease_expires_at < ?) " + active_source_clause + "AND NOT EXISTS ("
                    "SELECT 1 FROM memory_write_jobs AS held "
                    "WHERE held.source_key=active.source_key AND held.state=? "
                    "AND held.lease_owner IS NOT NULL "
                    "AND held.lease_expires_at>=?) "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM memory_write_jobs AS earlier "
                    "WHERE earlier.session_key=active.session_key "
                    f"AND earlier.state IN ({blocking}) "
                    "AND (earlier.event_at<active.event_at OR "
                    "(earlier.event_at=active.event_at "
                    "AND earlier.queue_seq<active.queue_seq))) "
                    ")) LIMIT 1",
                    (
                        anchor_text,
                        end_text,
                        RUNNING,
                        PENDING,
                        now.isoformat(),
                        *active_source_params,
                        RUNNING,
                        now.isoformat(),
                        *_FIFO_BLOCKING_STATES,
                    ),
                ).fetchone()
                if active is None:
                    connection.execute("DELETE FROM memory_write_schedule_state WHERE singleton=1")
                    window = None
            if window is None:
                anchor = connection.execute(
                    "SELECT candidate.event_at "
                    "FROM memory_write_jobs AS candidate "
                    "WHERE candidate.state=? "
                    "AND (candidate.lease_expires_at IS NULL "
                    "OR candidate.lease_expires_at < ?) " + source_clause + "AND NOT EXISTS ("
                    "SELECT 1 FROM memory_write_jobs AS held "
                    "WHERE held.source_key=candidate.source_key AND held.state=? "
                    "AND held.lease_owner IS NOT NULL "
                    "AND held.lease_expires_at>=?) "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM memory_write_jobs AS earlier "
                    "WHERE earlier.session_key=candidate.session_key "
                    f"AND earlier.state IN ({blocking}) "
                    "AND (earlier.event_at<candidate.event_at OR "
                    "(earlier.event_at=candidate.event_at "
                    "AND earlier.queue_seq<candidate.queue_seq))) "
                    "ORDER BY candidate.event_at,candidate.queue_seq LIMIT 1",
                    (
                        PENDING,
                        now.isoformat(),
                        *source_params,
                        RUNNING,
                        now.isoformat(),
                        *_FIFO_BLOCKING_STATES,
                    ),
                ).fetchone()
                if anchor is None:
                    return ()
                anchor_at = datetime.fromisoformat(str(anchor[0]))
                if anchor_at.tzinfo is None:
                    anchor_at = anchor_at.replace(tzinfo=UTC)
                anchor_text = anchor_at.astimezone(UTC).isoformat()
                end_text = (anchor_at + EVENT_TIME_WINDOW).astimezone(UTC).isoformat()
                connection.execute(
                    "INSERT INTO memory_write_schedule_state "
                    "(singleton,window_anchor,window_end,updated_at) "
                    "VALUES (1,?,?,?)",
                    (anchor_text, end_text, now.isoformat()),
                )
            rows = connection.execute(
                "SELECT candidate.job_id,candidate.source_key "
                "FROM memory_write_jobs AS candidate "
                "WHERE candidate.state=? "
                "AND (candidate.lease_expires_at IS NULL OR candidate.lease_expires_at < ?) "
                + source_clause
                + "AND candidate.event_at>=? AND candidate.event_at<? "
                "AND NOT EXISTS ("
                "SELECT 1 FROM memory_write_jobs AS held "
                "WHERE held.source_key=candidate.source_key AND held.state=? "
                "AND held.lease_owner IS NOT NULL AND held.lease_expires_at>=?) "
                "AND NOT EXISTS ("
                "SELECT 1 FROM memory_write_jobs AS earlier "
                "WHERE earlier.session_key=candidate.session_key "
                f"AND earlier.state IN ({blocking}) "
                "AND (earlier.event_at<candidate.event_at OR "
                "(earlier.event_at=candidate.event_at "
                "AND earlier.queue_seq<candidate.queue_seq))) "
                "ORDER BY candidate.event_at,candidate.queue_seq",
                (
                    PENDING,
                    now.isoformat(),
                    *source_params,
                    anchor_text,
                    end_text,
                    RUNNING,
                    now.isoformat(),
                    *_FIFO_BLOCKING_STATES,
                ),
            ).fetchall()
            selected: list[str] = []
            selected_sources: set[str] = set()
            for row in rows:
                source_key = str(row["source_key"])
                if source_key in selected_sources:
                    continue
                if self.source_claim_enabled is not None and not self.source_claim_enabled(
                    source_key
                ):
                    continue
                selected.append(str(row["job_id"]))
                selected_sources.add(source_key)
                if len(selected) == min(limit, self.max_concurrent_sources):
                    break
            if not selected:
                return ()
            placeholders = ",".join("?" for _ in selected)
            connection.execute(
                "UPDATE memory_write_jobs SET state=?,lease_owner=?,lease_expires_at=?,"
                f"attempts=attempts+1 WHERE state=? AND job_id IN ({placeholders})",
                (
                    RUNNING,
                    self.owner,
                    expires_at.isoformat(),
                    PENDING,
                    *selected,
                ),
            )
        return tuple(selected)

    def renew(self, job_id: str, *, now: datetime | None = None) -> bool:
        with main_database(self.queue.database_path).write() as connection, connection:
            now = now or datetime.now(UTC)
            updated = connection.execute(
                "UPDATE memory_write_jobs SET lease_expires_at=? "
                "WHERE job_id=? AND state='running' AND lease_owner=? "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at>=?",
                ((now + self.lease).isoformat(), job_id, self.owner, now.isoformat()),
            )
        return updated.rowcount == 1

    def lease_deadline(self, job_id: str) -> datetime | None:
        with main_database(self.queue.database_path).read() as connection:
            row = connection.execute(
                "SELECT lease_expires_at FROM memory_write_jobs "
                "WHERE job_id=? AND state='running' AND lease_owner=?",
                (job_id, self.owner),
            ).fetchone()
        return datetime.fromisoformat(str(row[0])) if row is not None and row[0] else None

    def still_owns(self, job_id: str, *, now: datetime | None = None) -> bool:
        """Whether this drain's unexpired lease still owns `job_id`.

        A batched claim leases every selected row at once, but executes them
        one at a time. A later row can therefore outlive its lease while it
        waits for its turn; re-checking before that turn prevents the original
        drain from running work another drain has already reclaimed.
        """
        now = now or datetime.now(UTC)
        with main_database(self.queue.database_path).read() as connection:
            row = connection.execute(
                "SELECT 1 FROM memory_write_jobs "
                "WHERE job_id=? AND state='running' AND lease_owner=? "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?",
                (job_id, self.owner, now.isoformat()),
            ).fetchone()
        return row is not None

    async def run_one(self, *, limit: int = 1) -> int:
        """Claim and execute one bounded batch; return the number executed."""
        async with self._run_lock:
            # Claim is a blocking SQLite write on the single writer connection.
            # Run it off the loop, like every other DB call in this chain; a
            # busy writer would otherwise stall the whole event loop.
            ids = await in_the_background(
                self.claim,
                limit=limit,
                budget_ms=self.claim_budget_ms,
            )
            await self._run_claimed(ids)
            return len(ids)

    async def _run_claimed(self, ids: tuple[str, ...]) -> None:
        """Execute already-claimed jobs with per-job lease renewal."""
        await asyncio.gather(*(self._run_claimed_job(job_id) for job_id in ids))

    async def _run_claimed_job(self, job_id: str) -> None:
        if not await in_the_background(self.still_owns, job_id):
            logger.warning("durable_write_drain_lease_lost_before_execution job_id=%s", job_id)
            return
        deadline = await in_the_background(self.lease_deadline, job_id)
        if deadline is None:
            return
        renewal = asyncio.create_task(self._renew_loop(job_id, deadline))
        execution = asyncio.create_task(self._execute_with_ceiling(job_id))
        try:
            done, _ = await asyncio.wait((execution, renewal), return_when=asyncio.FIRST_COMPLETED)
            if execution in done:
                await execution
            elif renewal in done and await renewal is False:
                logger.warning("durable_write_drain_lease_lost job_id=%s", job_id)
                # Cancellation cannot stop work already running in a thread or
                # remote SDK. Keep this drain/source slot occupied until the
                # attempt actually ends; its expired-owner settle is fenced.
                await execution
        except _LeaseRenewalFatalError as renewal_error:
            # A coroutine cancellation cannot prove that thread/SDK work has
            # stopped. Keep this source slot until the bounded execution has
            # actually returned, then fail the drain visibly.
            with contextlib.suppress(Exception):
                await execution
            raise renewal_error
        except TimeoutError:
            logger.error(
                "durable_write_drain_job_ceiling_exceeded job_id=%s",
                job_id,
            )
            await in_the_background(
                self.queue.settle_worker_error,
                job_id,
                error_code="durable_work_ceiling_exceeded",
                owner=self.owner,
            )
        except Exception as error:
            logger.error(
                "durable_write_drain_executor_failed job_id=%s error_type=%s",
                job_id,
                type(error).__name__,
            )
            self._record_database_error(error)
            await in_the_background(
                self.queue.settle_worker_error,
                job_id,
                error_code="durable_executor_error",
                owner=self.owner,
            )
        finally:
            execution.cancel()
            renewal.cancel()
            for task in (execution, renewal):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _execute_with_ceiling(self, job_id: str) -> None:
        ceiling = (
            await in_the_background(self.work_ceiling)
            if callable(self.work_ceiling)
            else self.work_ceiling
        )
        if ceiling is None:
            await self._execute_job(job_id)
            return
        await asyncio.wait_for(self._execute_job(job_id), timeout=ceiling.total_seconds())

    async def _execute_job(self, job_id: str) -> None:
        """Run one claimed job, passing the lease owner when the executor takes it."""
        if self.execute_owned is not None:
            await self.execute_owned(job_id, self.owner)
            return
        await self.execute(job_id)

    async def run_forever(self, *, idle_pause: float = 0.05) -> None:
        self.running = True
        active: set[asyncio.Task[None]] = set()
        # The composition root starts this worker once and stops it at
        # shutdown, so `drain_budget` bounds one drain *cycle*, not the
        # worker's lifetime. When the current window can no longer admit a
        # full handle, close the cycle with the cleanup reserve intact and
        # schedule a fresh one. Returning here instead would strand every job
        # submitted after the first window in `pending` forever: with the
        # production budget `work_window == handle_budget == 5s`, the check is
        # already true on the very first tick.
        cycle_begin = _time.monotonic()
        work_deadline = self.budget.work_deadline(cycle_begin) if self.budget else None
        admitted_this_cycle = False
        try:
            while self.running:
                finished = {task for task in active if task.done()}
                active.difference_update(finished)
                for task in finished:
                    await task
                if self.budget is not None and work_deadline is not None:
                    now = _time.monotonic()
                    # Always admit the first unit of a cycle even when the
                    # window is exactly one handle budget: that guarantees
                    # progress instead of a cycle that can never claim.
                    if (
                        admitted_this_cycle
                        and now + self.budget.handle_budget.total_seconds() > work_deadline
                    ):
                        cycle_begin = now
                        work_deadline = self.budget.work_deadline(cycle_begin)
                        admitted_this_cycle = False
                try:
                    available = self.max_concurrent_sources - len(active)
                    ids = (
                        await in_the_background(
                            self.claim,
                            limit=available,
                            budget_ms=self.claim_budget_ms,
                        )
                        if available > 0
                        else ()
                    )
                    for job_id in ids:
                        active.add(asyncio.create_task(self._run_claimed_job(job_id)))
                    executed = len(ids)
                    self._database_failure_streaks.pop("claim", None)
                except asyncio.CancelledError:
                    raise
                except sqlite3.OperationalError as error:
                    # A transient claim/DB error must not kill the worker: the
                    # pending rows are still there, so the loop retries after
                    # a pause instead of leaving the queue undrained forever.
                    if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                        raise
                    streak = self._sample_database_failure("claim", error)
                    await asyncio.sleep(min(0.05 * (2 ** (streak - 1)), 5.0))
                    continue
                if executed:
                    admitted_this_cycle = True
                # Reclaiming an abandoned `running` job is recoverability, not
                # housekeeping: it must not wait for an idle queue. A sustained
                # backlog keeps `executed != 0` forever, and the old idle-only
                # gate left a job whose worker died (or was cancelled)
                # unclaimable until the next process start — an unbounded
                # accumulation exactly when the queue is busiest. Its own coarse
                # interval keeps the extra write off every poll; the write is a
                # single UPDATE. It runs anchored to the *start* of the tick so
                # a slow execution does not itself delay recovery.
                tick_started = datetime.now(UTC)
                if (
                    self._last_reclaim is None
                    or tick_started - self._last_reclaim >= self._reap_interval
                ):
                    self._last_reclaim = tick_started
                    await self._run_idle_maintenance(
                        self.queue.reclaim_expired,
                        operation_name="reclaim_expired",
                    )
                # A model outage clears on its own, so `waiting_for_model` jobs
                # are re-admitted on a timer. That must not depend on idleness: a
                # queue busy for longer than the wait window would otherwise keep
                # the write parked even after the model is healthy again. The
                # coarse interval keeps this write off every poll.
                if (
                    self._last_model_readmit is None
                    or tick_started - self._last_model_readmit >= self._reap_interval
                ):
                    self._last_model_readmit = tick_started
                    await self._run_idle_maintenance(
                        self.queue.readmit_model_waiters,
                        operation_name="readmit_model_waiters",
                    )
                if self.reconcile_unknown is not None and (
                    self._last_unknown_reconcile is None
                    or tick_started - self._last_unknown_reconcile
                    >= self._unknown_reconcile_interval
                ):
                    self._last_unknown_reconcile = tick_started
                    try:
                        await self.reconcile_unknown()
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        self._sample_database_failure("reconcile_unknown", error)
                if executed == 0:
                    self._idle_streak += 1
                    backoff = min(max(idle_pause, 0.05) * (2 ** min(self._idle_streak, 4)), 0.5)
                    if active:
                        await asyncio.wait(
                            active,
                            timeout=backoff,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    else:
                        await asyncio.sleep(backoff)
                else:
                    self._idle_streak = 0
        finally:
            self.running = False
            if active:
                await asyncio.gather(*active, return_exceptions=True)

    def stop(self) -> None:
        self.running = False

    async def _run_idle_maintenance(
        self,
        operation: Callable[[], Any],
        *,
        operation_name: str,
    ) -> None:
        """Run one best-effort maintenance write without hiding its failure.

        A transient failure must not kill the drain, but silently swallowing a
        persistent `database is locked` makes queue maintenance look healthy
        while it is starved by an import peak. SQLite failures therefore use
        the same `memory.write_queue` counter as the claim/renew paths; any
        other exception is logged by type only so a bug is visible without
        persisting provider or user content.
        """
        try:
            await in_the_background(operation)
            self._database_failure_streaks.pop(f"maintenance:{operation_name}", None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._sample_database_failure(f"maintenance:{operation_name}", error)

    async def _renew_loop(self, job_id: str, deadline: datetime) -> bool:
        """Extend the lease periodically; never let renewal kill the drain.

        A transient database error while renewing must not escape this task: it
        is awaited in the caller's `finally`, where an exception would replace
        the job's real outcome and abort the whole drain. The job is still
        settled by its executor; a missed renewal only shortens the lease, which
        reclaim handles.
        """
        delay = self.renew_every.total_seconds()
        while True:
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(delay, remaining))
            if datetime.now(UTC) >= deadline:
                return False
            try:
                renewed_at = datetime.now(UTC)
                remaining = (deadline - renewed_at).total_seconds()
                if remaining <= 0 or not await asyncio.wait_for(
                    in_the_background(self.renew, job_id), timeout=remaining
                ):
                    return False
                deadline = renewed_at + self.lease
                self._database_failure_streaks.pop(f"renew:{job_id}", None)
                delay = self.renew_every.total_seconds()
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                return False
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                    raise _LeaseRenewalFatalError from error
                streak = self._sample_database_failure(f"renew:{job_id}", error)
                delay = min(0.05 * (2 ** (streak - 1)), 5.0)
                if datetime.now(UTC) >= deadline:
                    return False
            except Exception as error:
                raise _LeaseRenewalFatalError from error

    def _sample_database_failure(self, operation: str, error: BaseException) -> int:
        streak = self._database_failure_streaks.get(operation, 0) + 1
        self._database_failure_streaks[operation] = streak
        if streak & (streak - 1) == 0:
            logger.warning(
                "durable_write_database_retry operation=%s attempt=%s error_type=%s",
                operation,
                streak,
                type(error).__name__,
            )
            diagnostic_event(
                level="warn",
                component="memory.write_queue",
                event="memory.write_queue.retry_scheduled",
                outcome="retrying",
                error_type=type(error).__name__,
                details={"phase": operation, "count": streak},
            )
        return streak

    @staticmethod
    def _record_database_error(error: BaseException) -> str | None:
        """Classify a SQLite failure so it reaches the shared error counter.

        `database is locked` / busy / corruption in the durable path must be
        visible like they are in the scheduler, not only in a traceback: the
        counter is what tells the operator whether an import peak starved a
        writer. Non-SQLite errors are left to the exception log.
        """
        if not isinstance(error, sqlite3.Error):
            return None
        from one_memory.diagnostic_log import record_database_error

        return record_database_error("memory.write_queue", error)
