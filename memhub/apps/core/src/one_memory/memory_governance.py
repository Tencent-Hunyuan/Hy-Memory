from __future__ import annotations

import builtins
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from one_memory.database import enable_write_ahead_log
from one_memory.database_access import main_database
from one_memory.legacy_lifecycle_types import HyLifecycle
from one_memory.library_upgrade import LibraryUpgradeError, PreparedLibrary, holds_legacy_rows
from one_memory.memory_server.migrations import governance_ddl

#: Bump when `content_candidate_predicate` changes shape. Version 1 is the
#: OR-preserving union over every indexed query term.
CONTENT_CANDIDATE_UNION_VERSION = 1
#: Version 1 of the coverage-aware plan API. Consumers that need to know
#: whether the candidate set was truncated must use this instead of the
#: legacy two-tuple predicate helper.
CONTENT_CANDIDATE_PLAN_VERSION = 1

#: A term this frequent is not selective enough to scan through a derived
#: candidate index; keep it on the exact LIKE path instead.
_MAX_CANDIDATE_TERM_DOCS = 5_000

#: Bounded backfill batches. One batch is one short transaction: at most this
#: many rows, and at most this many characters of their content. A single huge
#: row is still processed alone so it can never be split across transactions.
_CONTENT_BACKFILL_BATCH_ROWS = 250
_CONTENT_BACKFILL_BATCH_CHARS = 250_000


def _shadow_terms(text: str) -> frozenset[str]:
    """The exact term shape recall uses for content candidate lookup."""
    normalized = text.casefold()
    latin = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", normalized))
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    chinese = {
        run[index : index + 2] for run in chinese_runs for index in range(max(0, len(run) - 1))
    }
    return frozenset(latin | chinese)


def _shadow_content_hash(content: str) -> str:
    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()


def content_candidate_predicate(
    connection: sqlite3.Connection,
    terms: Sequence[str],
    *,
    alias: str = "m",
) -> tuple[str | None, tuple[str, ...]]:
    """A bounded SQL candidate predicate for the maintained content shadow.

    Returns `(None, ())` when the shadow is absent or has no usable term, so
    callers keep their existing `LIKE` fallback. The caller must still apply
    its own user/status/lifecycle/scope/authority predicates after this
    candidate narrowing.
    """
    plan = content_candidate_plan(connection, terms, alias=alias)
    if plan is None:
        return None, ()
    return plan.predicate, plan.values


@dataclass(frozen=True)
class ContentCandidatePlan:
    """A bounded candidate SQL fragment and whether it covers every match."""

    predicate: str
    values: tuple[str, ...]
    complete: bool
    candidate_count: int
    limit: int


def content_candidate_plan(
    connection: sqlite3.Connection,
    terms: Sequence[str],
    *,
    alias: str = "m",
    limit: int = 200,
) -> ContentCandidatePlan | None:
    """Coverage-aware candidate plan for the maintained content shadow.

    `complete=False` means the shadow has more matching rows than `limit`;
    callers must fall back to the exact `LIKE` path rather than report a
    complete result from the bounded candidate set.
    """
    if type(limit) is not int or not 1 <= limit <= 100_000:
        raise ValueError("invalid_candidate_limit")
    shadow = MemoryGovernanceStore._matching_shadow_clause(connection, terms, limit=limit)
    if shadow is None:
        return None
    predicate, values, count, all_terms_present = shadow
    return ContentCandidatePlan(
        predicate=predicate.replace("m.", f"{alias}."),
        values=values,
        complete=count <= limit and all_terms_present,
        candidate_count=count,
        limit=limit,
    )


def ensure_content_candidate_index(connection: sqlite3.Connection) -> None:
    """Create/backfill the derived candidate index on a caller-owned transaction.

    This is the public initialization hook for read-only composition roots and
    benchmark harnesses that do not construct `MemoryGovernanceStore`.
    """
    MemoryGovernanceStore._ensure_content_shadow(connection)


class MemoryStatus(StrEnum):
    CONFIRMED = "confirmed"
    DELETED = "deleted"
    #: HY replaced it with a newer memory. Kept and readable by id, but no
    #: longer current: only the head of its chain is recalled or listed.
    SUPERSEDED = "superseded"


class MemoryOrigin(StrEnum):
    """How a memory came to exist.

    `kind` says what a memory is about; this says who wrote the sentence.
    They are different questions and one field cannot answer both: a decision
    the person stated and a summary a model composed can both be `working`.

    The distinction is load-bearing in recall, where other AI apps read a
    memory as the person's own judgement, so a model's prose must stay
    recognisable as such.
    """

    #: HY pulled it out of something the person said. The ordinary case, and
    #: what every memory written before this field existed must read as.
    EXTRACTED = "extracted"
    #: A model composed it while answering a question about the library.
    #: Traceable to the memories it drew on, and never true on its own.
    ANSWERED = "answered"
    #: HY attributed it to the assistant's output rather than the person —
    #: a plan it drafted, an answer it looked up.
    AGENT = "agent"
    #: The person typed it themselves.
    MANUAL = "manual"


class MemoryKind(StrEnum):
    CHAT = "chat"
    WORKING = "working"
    CODING = "coding"
    SCHEMA = "schema"
    INTENTION = "intention"


class MemoryMutationConflictError(RuntimeError):
    pass


class MemoryMutationRequestConflictError(RuntimeError):
    pass


class MemoryEvidenceNotFoundError(RuntimeError):
    pass


class MemoryEvidenceOverriddenError(MemoryMutationConflictError):
    """A model wrote from evidence whose memory the person has since deleted or corrected."""


class MemoryDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=256)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=1_000_000)
    scopes: tuple[str, ...] = Field(min_length=1, max_length=128)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    origin: MemoryOrigin = MemoryOrigin.EXTRACTED
    #: HY's own topic tags. It collects the tags already in the library before
    #: extracting and puts them in the prompt so the model reuses them, so
    #: this clustering has been running all along — it just never left the
    #: boundary. Bounded because an unbounded tag list on one memory is the
    #: shape tag explosion takes.
    tags: tuple[str, ...] = Field(default=(), max_length=32)


class GovernedMemory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    user_id: str
    kind: MemoryKind
    status: MemoryStatus
    content: str
    scopes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    origin: MemoryOrigin = MemoryOrigin.EXTRACTED
    tags: tuple[str, ...] = ()
    #: Memories this one replaced, and memories that replaced this one.
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()
    revision: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class MemoryVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    revision: int
    action: str
    status: MemoryStatus
    content: str
    scopes: tuple[str, ...]
    created_at: datetime


class DeletionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryDeletion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    status: DeletionStatus
    checkpoint: int = Field(ge=0)
    attempts: int = Field(ge=0)
    last_error_code: str | None
    updated_at: datetime


class IndexSyncStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"


class MemoryIndexSync(BaseModel):
    """Whether HY's index still holds the text this memory used to say.

    Only two states, because only two are actionable: the search index either
    matches what the user reads here, or it does not and someone has to try
    again. `last_error_code` carries why the last attempt failed, so a state
    that retrying cannot fix stays distinguishable from one that can.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    status: IndexSyncStatus
    attempts: int = Field(ge=0)
    last_error_code: str | None
    updated_at: datetime


class SupersessionIntegrity(BaseModel):
    """Structural problems in a user's supersession graph, read-only.

    Each entry is a sorted tuple of memory ids. `cycles` holds strongly
    connected components of size > 1 (plus any self-loop); `headless` holds
    acyclic components with no `confirmed` member. Both are empty for a
    well-formed set of replacement chains.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cycles: tuple[tuple[str, ...], ...] = ()
    headless: tuple[tuple[str, ...], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.cycles and not self.headless


class _NothingReadable:
    """The caller may read no scope at all.

    Its own type rather than an empty clause or `None`, because `None` means
    "do not narrow" and the two must never be reachable by the same falsy
    check: reading one as the other is exactly what turns "you may see
    nothing" into "you may see everything".
    """


_NOTHING_READABLE = _NothingReadable()


def _scope_narrowing(
    scopes: Collection[str] | None,
) -> tuple[str, tuple[str, ...]] | _NothingReadable | None:
    """A SQL predicate limiting rows to scopes the caller may read.

    Narrowing has to reach the query rather than filter its result: every
    unreadable row the query returns is one the caller's page could have
    spent on a readable one, which is how a few hundred newer memories from
    another project made an older readable one unreachable.

    `None` in, `None` out — no narrowing, for the internal wildcard only.
    """
    if scopes is None:
        return None
    readable = tuple(dict.fromkeys(scopes))
    if not readable:
        return _NOTHING_READABLE
    # Membership over the stored JSON array rather than a match against its
    # text, so one scope cannot read another by being a prefix of it.
    placeholders = ",".join("?" * len(readable))
    return (
        "EXISTS (SELECT 1 FROM json_each(governed_memories.scopes_json)"
        f" WHERE json_each.value IN ({placeholders}))",
        readable,
    )


GOVERNANCE_SCHEMA = governance_ddl("sqlite")


#: States older versions wrote and nothing writes any more: a memory used to
#: wait for confirmation, and a rejected one was as gone as a deleted one.
_LEGACY_STATUSES = {"candidate": MemoryStatus.CONFIRMED, "rejected": MemoryStatus.DELETED}
#: The operations that went with them. A rejection removed the memory, so it
#: reads as the delete it amounted to; a confirmation has no counterpart any
#: more and reads as `legacy`. The version row keeps which one it was.
_LEGACY_OPERATIONS = {"reject": "delete", "confirm": "legacy"}


_MemoryLinks = tuple[builtins.list[str], builtins.list[str], builtins.list[str]]


def _memory_links(
    connection: sqlite3.Connection,
    memory_ids: builtins.list[str],
) -> dict[str, _MemoryLinks]:
    """Active evidence, what each memory replaced, and what replaced it.

    One statement for all three: readers are held to a fixed statement count
    per page, and a replacement chain is part of reading a memory, not a
    second question about it.
    """
    links: dict[str, _MemoryLinks] = {key: ([], [], []) for key in memory_ids}
    if not memory_ids:
        return links
    placeholders = ",".join("?" * len(memory_ids))
    for kind, memory_id, other in connection.execute(
        f"""
        SELECT 0, memory_id, evidence_id FROM governed_memory_evidence
        WHERE memory_id IN ({placeholders}) AND active = 1
        UNION ALL
        SELECT 1, superseded_by, memory_id FROM governed_memory_supersessions
        WHERE superseded_by IN ({placeholders})
        UNION ALL
        SELECT 2, memory_id, superseded_by FROM governed_memory_supersessions
        WHERE memory_id IN ({placeholders})
        ORDER BY 1, 2, 3
        """,
        (*memory_ids, *memory_ids, *memory_ids),
    ):
        links[memory_id][kind].append(other)
    return links


def _strongly_connected_components(
    edges: Mapping[str, set[str]],
) -> builtins.list[tuple[str, ...]]:
    """Tarjan's SCCs of size > 1, plus any self-loop, each sorted.

    Iterative so a long chain cannot overflow the recursion limit. Nodes reach
    only through an edge are included so a component is never split by a node
    that happens to have no outgoing edge of its own.
    """
    nodes: set[str] = set(edges)
    for targets in edges.values():
        nodes.update(targets)
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: builtins.list[str] = []
    components: builtins.list[tuple[str, ...]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index_of:
            continue
        work: builtins.list[tuple[str, Iterator[str]]] = [(root, iter(sorted(edges.get(root, ()))))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, neighbours = work[-1]
            advanced = False
            for neighbour in neighbours:
                if neighbour not in index_of:
                    index_of[neighbour] = low[neighbour] = counter
                    counter += 1
                    stack.append(neighbour)
                    on_stack.add(neighbour)
                    work.append((neighbour, iter(sorted(edges.get(neighbour, ())))))
                    advanced = True
                    break
                if neighbour in on_stack:
                    low[node] = min(low[node], index_of[neighbour])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: builtins.list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in edges.get(node, ()):
                    components.append(tuple(sorted(component)))
    return components


def _weakly_connected_components(edges: Mapping[str, set[str]]) -> builtins.list[tuple[str, ...]]:
    """Undirected components of the supersession graph, each sorted."""
    neighbours: dict[str, set[str]] = {node: set(targets) for node, targets in edges.items()}
    for node, targets in edges.items():
        for target in targets:
            neighbours.setdefault(target, set()).add(node)
    seen: set[str] = set()
    components: builtins.list[tuple[str, ...]] = []
    for start in sorted(neighbours):
        if start in seen:
            continue
        component: builtins.list[str] = []
        todo = [start]
        seen.add(start)
        while todo:
            node = todo.pop()
            component.append(node)
            for other in neighbours[node]:
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
        components.append(tuple(sorted(component)))
    return components


def _memory_from_row(
    row: sqlite3.Row,
    evidence_ids: tuple[str, ...],
    supersedes: tuple[str, ...] = (),
    superseded_by: tuple[str, ...] = (),
    *,
    lifecycle: HyLifecycle | None = None,
) -> GovernedMemory:
    """One place that turns a stored row into a memory, so a page of them and
    a single one cannot drift apart in what they report.

    With HY's `lifecycle`, whether a memory is current and what replaced it
    are HY's answer; the row only keeps whether the person deleted it.
    """
    status = MemoryStatus(row["status"])
    if lifecycle is not None and status is not MemoryStatus.DELETED:
        memory_id = row["memory_id"]
        hidden = memory_id in lifecycle.hidden
        status = MemoryStatus.SUPERSEDED if hidden else MemoryStatus.CONFIRMED
        superseded_by = lifecycle.replaced_by(memory_id)
        supersedes = lifecycle.replacing(memory_id)
    return GovernedMemory(
        memory_id=row["memory_id"],
        user_id=row["user_id"],
        kind=row["kind"],
        status=status,
        content=row["content"],
        scopes=tuple(json.loads(row["scopes_json"])),
        evidence_ids=evidence_ids,
        origin=MemoryOrigin(row["origin"]) if row["origin"] else MemoryOrigin.EXTRACTED,
        tags=tuple(json.loads(row["tags_json"])) if row["tags_json"] else (),
        supersedes=supersedes,
        superseded_by=superseded_by,
        revision=row["revision"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=(datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None),
    )


class _FromSource(Enum):
    """Read HY's lifecycle from the store's source; explicit None reads the ledger."""

    SOURCE = "source"


FROM_SOURCE = _FromSource.SOURCE
LifecycleArg = HyLifecycle | None | _FromSource


class LifecycleSource(Protocol):
    def __call__(
        self, user_id: str, ids: tuple[str, ...] | None = None, /
    ) -> HyLifecycle | None: ...


def lifecycle_predicate(
    lifecycle: HyLifecycle | None,
    status: MemoryStatus | None,
    *,
    alias: str = "governed_memories",
    include_deleted: bool = False,
) -> tuple[str | None, tuple[str, ...]]:
    """The SQL condition selecting memories in `status`, or current ones when None.

    Applied before any page is cut, so hidden rows never spend a page slot.
    Without HY's `lifecycle` the row's own status decides, as it always has;
    with it, only a person's deletion is read from the row and HY decides the
    rest.
    """
    if lifecycle is None:
        if status is not None:
            return f"{alias}.status = ?", (status.value,)
        if include_deleted:
            return None, ()
        return f"{alias}.status NOT IN (?, ?)", (
            MemoryStatus.DELETED.value,
            MemoryStatus.SUPERSEDED.value,
        )
    hidden = json.dumps(sorted(lifecycle.hidden))
    set_aside = f"{alias}.memory_id IN (SELECT value FROM json_each(?))"
    if status is MemoryStatus.DELETED:
        return f"{alias}.status = ?", (MemoryStatus.DELETED.value,)
    if status is MemoryStatus.SUPERSEDED:
        return f"{alias}.status != ? AND {set_aside}", (MemoryStatus.DELETED.value, hidden)
    if status is None and include_deleted:
        return None, ()
    return f"{alias}.status != ? AND NOT {set_aside}", (MemoryStatus.DELETED.value, hidden)


def _index_sync_from_row(row: sqlite3.Row) -> MemoryIndexSync:
    return MemoryIndexSync(
        memory_id=row["memory_id"],
        status=IndexSyncStatus(row["status"]),
        attempts=row["attempts"],
        last_error_code=row["last_error_code"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


#: How many deletions one round of the deletion sync sends to HY, so a
#: backlog is worked through in turns rather than in one long burst.
DELETION_BATCH = 20
#: A failed deletion waits this long, twice as long after each further
#: failure, up to the ceiling.
DELETION_RETRY_FIRST = timedelta(seconds=30)
DELETION_RETRY_CEILING = timedelta(hours=6)
#: Attempts before a deletion stops being retried and waits for a person:
#: about two days of backoff. Past that, trying again on a timer only hides
#: that something is wrong; the memory stays deleted here either way.
DELETION_MAX_ATTEMPTS = 16
#: Why a deletion is no longer retried; the cause of the last failure is logged.
DELETION_NEEDS_ATTENTION = "hy_memory_delete_needs_attention"
#: How long a claimed deletion is someone's before another runner may take it
#: over, for when the one holding it died mid-call.
DELETION_LEASE = timedelta(minutes=10)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class DeletionClaim:
    """One runner's hold on one deletion; only its holder may record how it went."""

    memory_id: str
    token: str


@dataclass(frozen=True)
class HyDerivation:
    """What a model call worked from, fixed when the call began.

    `watermark` is how far the ledger had got by then. A write is refused if
    the person has since deleted, corrected or undone a memory resting on any
    of `evidence_ids`: what was overruled before the call was already kept out
    of what the model was shown, so only what happened during it can be new.
    """

    evidence_ids: tuple[str, ...]
    watermark: int

    def citing(self, evidence_ids: Collection[str]) -> HyDerivation:
        return replace(self, evidence_ids=tuple(dict.fromkeys(evidence_ids)))


@dataclass(frozen=True)
class HyCreate:
    draft: MemoryDraft
    request_id: str
    memory_id: str


@dataclass(frozen=True)
class HyRevise:
    memory_id: str
    request_id: str
    expected_revision: int
    content: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class HyLink:
    memory_id: str
    evidence_id: str


@dataclass(frozen=True)
class HyRetire:
    """HY deleted this memory itself, so there is nothing left for it to forget."""

    memory_id: str
    request_id: str
    expected_revision: int


@dataclass(frozen=True)
class HySupersede:
    memory_id: str
    request_id: str
    expected_revision: int
    superseded_by: tuple[str, ...]


HyChange = HyCreate | HyRevise | HyLink | HyRetire | HySupersede


def _written_evidence(change: HyChange) -> tuple[str, ...]:
    match change:
        case HyCreate():
            return change.draft.evidence_ids
        case HyRevise():
            return change.evidence_ids
        case HyLink():
            return (change.evidence_id,)
        case HyRetire() | HySupersede():
            return ()


#: Who a person's write comes through: Core's API, or the person directly.
PersonActor = Literal["api", "user"]
PERSON_ACTORS: frozenset[str] = frozenset({"api", "user"})


def _statements(script: str) -> tuple[str, ...]:
    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ""
    if pending.strip():
        raise ValueError("incomplete SQL statement in schema")
    return tuple(statements)


class MemoryGovernanceStore:
    def __init__(
        self,
        database_path: Path,
        *,
        prepared: PreparedLibrary | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Open the ledger, migrating what an older version left in it.

        A library holding rows that migration rewrites is only opened once
        `prepare_library` has backed it up; without that proof nothing here
        touches it, not even the switch to WAL.
        """
        self._write_guard: ContextVar[Callable[[], object] | None] = ContextVar(
            "governance_write_guard", default=None
        )
        self.database_path = database_path
        self.clock = clock
        self._person_key = object()
        self._person_claimed = False
        self._person_claim_lock = threading.Lock()
        self._lifecycle_source: LifecycleSource | None = None
        self._content_backfill_lock = threading.Lock()
        self._content_backfill_thread: threading.Thread | None = None
        self._content_backfill_state: Literal["idle", "running", "ready", "failed"] = "idle"
        self._content_backfill_failure: str | None = None
        # Observable progress for reviewers: how many bounded batches have
        # committed, and the monotonic time of the most recent one. A snapshot
        # taken around a concurrent write can then show the backfill was
        # actively advancing, not merely "running".
        self._content_backfill_batch_count = 0
        self._content_backfill_last_batch_at: float | None = None
        # The writer is single-owner. A long derived-index rebuild must never
        # hold it while the caller that started the rebuild still has startup
        # writes to make, or startup waits out its budget and fails. The
        # composition root releases this after EvidenceStore initialization.
        self._content_backfill_startup_gate = threading.Event()
        if database_path.exists() and not (
            prepared is not None and prepared.consume(database_path)
        ):
            # Nothing but reading may happen before that proof.
            with self._connection(writing=False) as connection:
                legacy = holds_legacy_rows(connection)
            if legacy:
                raise LibraryUpgradeError(
                    "library_upgrade_not_prepared",
                    reason="this library holds rows the upgrade rewrites and was not backed up",
                    remedy="Open it through prepare_library first, as Core start does.",
                )
        database_path.parent.mkdir(parents=True, exist_ok=True)
        # One transaction for every change a start makes to an existing
        # library, tables included, so a failure part-way leaves it exactly as
        # it was, journal mode too: WAL only once it has committed. Statement
        # by statement: `executescript` commits first.
        with self._connection(keep_journal_mode=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for statement in _statements(GOVERNANCE_SCHEMA):
                connection.execute(statement)
            self._add_missing_columns(connection)
            # Startup creates the shadow structure only. Backfilling one CJK
            # bigram row per memory would make opening an existing large
            # library a multi-second write; the composition root schedules the
            # explicit backfill instead.
            self._ensure_content_shadow(connection, backfill=False)
            self._migrate_legacy_rows(connection)
        with self._connection() as connection:
            enable_write_ahead_log(connection)

    def start_content_candidate_backfill(self, *, hold_until_startup_release: bool = False) -> bool:
        """Build the derived content index off the startup path.

        Returns False when a backfill is already running or has completed.
        The composition root owns the decision to schedule this; reads use
        the exact LIKE fallback until it lands.
        """
        if not hold_until_startup_release:
            self._content_backfill_startup_gate.set()
        with self._content_backfill_lock:
            if self._content_backfill_state in {"running", "ready"}:
                return False
            self._content_backfill_failure = None
            thread = threading.Thread(
                target=self._run_content_candidate_backfill,
                name="governance-content-index",
                daemon=True,
            )
            self._content_backfill_thread = thread
            self._content_backfill_state = "running"
            thread.start()
            return True

    def release_content_backfill_startup_gate(self) -> None:
        """Allow the background rebuild to contend after startup writes finish."""
        self._content_backfill_startup_gate.set()

    def _await_startup_writes(self) -> bool:
        """Return False when startup still owns the write checkout."""
        return not self._content_backfill_startup_gate.is_set()

    def content_candidate_backfill_state(self) -> str:
        with self._content_backfill_lock:
            return self._content_backfill_state

    def content_candidate_backfill_failure(self) -> str | None:
        with self._content_backfill_lock:
            return self._content_backfill_failure

    def content_candidate_backfill_progress(self) -> dict[str, object]:
        with self._content_backfill_lock:
            return {
                "batch_count": self._content_backfill_batch_count,
                "last_batch_at": self._content_backfill_last_batch_at,
            }

    def wait_content_candidate_backfill(self, timeout: float | None = None) -> bool:
        """Wait for a scheduled backfill without making startup depend on it."""
        if timeout is not None and timeout < 0:
            raise ValueError("invalid_content_backfill_timeout")
        with self._content_backfill_lock:
            thread = self._content_backfill_thread
        if thread is None:
            return self._content_backfill_state in {"ready", "failed"}
        thread.join(timeout)
        return not thread.is_alive()

    def _run_content_candidate_backfill(self) -> None:
        try:
            while self._await_startup_writes():
                if self._content_backfill_startup_gate.wait(timeout=0.01):
                    break
            self._rebuild_content_shadow_batched()
        except Exception as error:
            failure = type(error).__name__
            if isinstance(error, sqlite3.OperationalError):
                message = str(error).casefold()
                if "locked" in message or "busy" in message:
                    failure = "database_locked"
            with self._content_backfill_lock:
                self._content_backfill_failure = failure
                self._content_backfill_state = "failed"
            return
        except BaseException:
            with self._content_backfill_lock:
                self._content_backfill_failure = "interrupted"
                self._content_backfill_state = "failed"
            raise
        with self._content_backfill_lock:
            self._content_backfill_failure = None
            self._content_backfill_state = "ready"

    def _rebuild_content_shadow_batched(self) -> None:
        """Rebuild stale derived rows in bounded, committed batches.

        A stable `memory_id` cursor walks the ledger in primary-key order. Every
        batch is one immediate write transaction (`_begin_write` ... `COMMIT`)
        through `_connection()`, so the single writer is released between
        batches and a concurrent writer can get in. Progress needs no extra
        schema: `governed_memories_terms_meta`
        records the content hash each memory was last indexed at, so a restart
        (or a crash mid-run) simply skips rows whose hash already matches and
        re-derives the rest. Re-running is idempotent.
        """
        # One ordered sweep, committed in bounded batches. A write that lands
        # after the cursor is handled by its own write path, which maintains the
        # shadow in the same transaction; a write behind the cursor was already
        # committed before this batch read it. No full-table re-scan: the next
        # batch's `SELECT ... LIMIT` is the only cursor query.
        cursor = ""
        while True:
            with self._connection() as connection:
                self._begin_write(connection)
                rows = connection.execute(
                    """
                    SELECT m.memory_id,m.content,m.status
                    FROM governed_memories m
                    WHERE m.memory_id > ?
                    ORDER BY m.memory_id
                    LIMIT ?
                    """,
                    (cursor, _CONTENT_BACKFILL_BATCH_ROWS),
                ).fetchall()
                if not rows:
                    with self._content_backfill_lock:
                        self._content_backfill_last_batch_at = time.monotonic()
                    return
                batch_chars = 0
                last_processed = cursor
                for memory_id_value, content_value, status_value in rows:
                    memory_id = str(memory_id_value)
                    content = str(content_value)
                    if batch_chars and (batch_chars + len(content) > _CONTENT_BACKFILL_BATCH_CHARS):
                        cursor = last_processed
                        break
                    batch_chars += len(content)
                    if str(status_value) == MemoryStatus.DELETED:
                        MemoryGovernanceStore._content_shadow_delete(connection, memory_id)
                        last_processed = memory_id
                        continue
                    indexed_hash = connection.execute(
                        "SELECT content_hash FROM governed_memories_terms_meta WHERE memory_id=?",
                        (memory_id,),
                    ).fetchone()
                    if indexed_hash is None or str(indexed_hash[0]) != _shadow_content_hash(
                        content
                    ):
                        MemoryGovernanceStore._content_shadow_upsert(connection, memory_id, content)
                    last_processed = memory_id
                else:
                    cursor = last_processed
                with self._content_backfill_lock:
                    self._content_backfill_batch_count += 1
                    self._content_backfill_last_batch_at = time.monotonic()

    @staticmethod
    def _ensure_content_shadow(connection: sqlite3.Connection, *, backfill: bool = True) -> None:
        """Keep a derived CJK bigram / latin-token inverted table with the ledger.

        The shadow stores no authority: every row is joined back to
        `governed_memories` and rechecked by the caller's existing scope,
        lifecycle, status and revision predicates.
        """
        # Schema is owned by migration 32; this method only verifies the
        # migration has run and repairs derived rows. Creating the tables here
        # made this business module a second schema owner and failed the A1-04
        # ratchet.
        for table in ("governed_memories_terms", "governed_memories_terms_meta"):
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                is None
            ):
                raise LibraryUpgradeError(
                    "governance_schema_missing",
                    reason=f"migration 32 did not create {table}",
                    remedy="Open the database through the versioned migration runner.",
                )
        if backfill:
            MemoryGovernanceStore._rebuild_stale_content_shadow_rows(connection)

    @staticmethod
    def _rebuild_stale_content_shadow_rows(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT m.memory_id,m.content,m.status,meta.content_hash
            FROM governed_memories m
            LEFT JOIN governed_memories_terms_meta meta ON meta.memory_id=m.memory_id
            """
        ).fetchall()
        indexed_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT memory_id FROM governed_memories_terms"
            ).fetchall()
        }
        for row in rows:
            memory_id, content, status, indexed_hash = (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                row[3],
            )
            content_hash = _shadow_content_hash(content)
            if status == MemoryStatus.DELETED:
                connection.execute(
                    "DELETE FROM governed_memories_terms WHERE memory_id=?", (memory_id,)
                )
                connection.execute(
                    "DELETE FROM governed_memories_terms_meta WHERE memory_id=?", (memory_id,)
                )
                continue
            if indexed_hash == content_hash and memory_id in indexed_ids:
                continue
            connection.execute(
                "DELETE FROM governed_memories_terms WHERE memory_id=?", (memory_id,)
            )
            connection.executemany(
                "INSERT OR IGNORE INTO governed_memories_terms(term,memory_id) VALUES (?,?)",
                ((term, memory_id) for term in _shadow_terms(content)),
            )
            connection.execute(
                "INSERT INTO governed_memories_terms_meta(memory_id,content_hash) VALUES (?,?) "
                "ON CONFLICT(memory_id) DO UPDATE SET content_hash=excluded.content_hash",
                (memory_id, content_hash),
            )

    @staticmethod
    def _content_shadow_upsert(
        connection: sqlite3.Connection, memory_id: str, content: str
    ) -> None:
        connection.execute("DELETE FROM governed_memories_terms WHERE memory_id=?", (memory_id,))
        connection.executemany(
            "INSERT OR IGNORE INTO governed_memories_terms(term,memory_id) VALUES (?,?)",
            ((term, memory_id) for term in _shadow_terms(content)),
        )
        connection.execute(
            "INSERT INTO governed_memories_terms_meta(memory_id,content_hash) VALUES (?,?) "
            "ON CONFLICT(memory_id) DO UPDATE SET content_hash=excluded.content_hash",
            (memory_id, _shadow_content_hash(content)),
        )

    @staticmethod
    def _content_shadow_delete(connection: sqlite3.Connection, memory_id: str) -> None:
        connection.execute("DELETE FROM governed_memories_terms WHERE memory_id=?", (memory_id,))
        connection.execute(
            "DELETE FROM governed_memories_terms_meta WHERE memory_id=?", (memory_id,)
        )

    @staticmethod
    def _matching_shadow_clause(
        connection: sqlite3.Connection,
        terms: Sequence[str],
        *,
        limit: int = 200,
        narrowing: tuple[str, tuple[str, ...]] | None = None,
    ) -> tuple[str, tuple[str, ...], int, bool] | None:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governed_memories_terms'"
        ).fetchone():
            return None
        # The shadow answers completely only when every current memory has been
        # indexed. A freshly opened existing library, or one whose background
        # backfill is still running, can have a partial shadow: the candidate
        # set then omits memories entirely, so per-term counts cannot reveal the
        # loss and a bounded candidate result would masquerade as a complete one.
        # `governed_memories_terms_meta` holds one row per indexed, non-deleted
        # memory, so equal counts prove coverage and any shortfall forces the
        # exact `LIKE` path instead of a silently incomplete candidate set.
        # (The meta primary key keeps this a cheap scalar count, not a join.)
        covered = connection.execute(
            """
            SELECT (SELECT count(*) FROM governed_memories_terms_meta)
                 = (SELECT count(*) FROM governed_memories WHERE status != ?)
            """,
            (MemoryStatus.DELETED,),
        ).fetchone()[0]
        if not covered:
            return None
        indexed = [term for term in terms if term in _shadow_terms(term)]
        if not indexed:
            return None
        # Defensive: the shadow only indexes CJK bigrams and latin tokens of
        # length >= 2, while this method's contract is "matches ANY term". A
        # bounded candidate set built from only the indexed terms would drop a
        # row whose sole match is a non-indexed term. Today the only production
        # caller (recall) passes `_terms()` output, which is always indexable, so
        # this is unreachable in production; it guards a future direct caller
        # (API/filter plumbing) that passes a mixed set such as a single-CJK
        # character alongside a bigram. Fall back to the exact LIKE path rather
        # than trade completeness for the fast path.
        if len(indexed) != len(terms):
            return None
        counts = {
            str(term): int(count)
            for term, count in connection.execute(
                "SELECT term,count(*) FROM governed_memories_terms "
                f"WHERE term IN ({','.join('?' * len(indexed))}) GROUP BY term",
                tuple(indexed),
            )
        }
        all_terms_present = all(
            counts.get(term, _MAX_CANDIDATE_TERM_DOCS + 1) <= _MAX_CANDIDATE_TERM_DOCS
            for term in indexed
        )
        indexed = [
            term
            for term in indexed
            if counts.get(term, _MAX_CANDIDATE_TERM_DOCS + 1) <= _MAX_CANDIDATE_TERM_DOCS
        ]
        if not indexed:
            return None
        count_row = connection.execute(
            "SELECT count(DISTINCT memory_id) FROM governed_memories_terms "
            f"WHERE term IN ({','.join('?' * len(indexed))})",
            tuple(indexed),
        ).fetchone()
        candidate_count = 0 if count_row is None else int(count_row[0])
        # Spend the candidate `LIMIT` on rows the caller may read. Applying the
        # scope predicate outside this subquery lets another workspace fill the
        # whole candidate pool first, so the readable match is dropped and the
        # caller reports an honest-looking empty result with `more=False`.
        scope_clause = ""
        scope_values: tuple[str, ...] = ()
        if narrowing is not None:
            scope_clause = (
                "JOIN governed_memories m ON m.memory_id=t.memory_id AND "
                + narrowing[0].replace("governed_memories.", "m.")
                + " "
            )
            scope_values = narrowing[1]
        # A CTE binds the term set once and both tiers read it, so the helper's
        # public return value stays exactly the terms the caller passed (a
        # pinned contract) even though the SQL uses them twice.
        wanted_values = ",".join("(?)" for _ in indexed)
        return (
            # Two tiers so a selective term's only match cannot be crowded out
            # by a non-selective term. Tier 0 reserves one representative per
            # distinct term that has an eligible match (guaranteeing coverage of
            # every term the caller asked about); tier 1 fills the rest with the
            # existing match-count ranking. Both tiers carry the same scope
            # predicate, so a reserved row is always one the caller may read —
            # reserving an ineligible row the outer query would drop does not
            # satisfy coverage. Dedup across tiers keeps the total at `limit`.
            "m.memory_id IN ("
            f"WITH wanted(term) AS (VALUES {wanted_values}) "
            "SELECT memory_id FROM ("
            "SELECT min(t.memory_id) AS memory_id,0 AS tier FROM wanted w "
            "JOIN governed_memories_terms t ON t.term=w.term "
            f"{scope_clause}"
            "GROUP BY t.term "
            "UNION ALL "
            "SELECT memory_id,1 AS tier FROM ("
            "SELECT t.memory_id AS memory_id,count(*) AS matches FROM wanted w "
            "JOIN governed_memories_terms t ON t.term=w.term "
            f"{scope_clause}"
            "GROUP BY t.memory_id "
            f"ORDER BY matches DESC,t.memory_id LIMIT {limit + 1})) "
            f"GROUP BY memory_id ORDER BY min(tier),memory_id LIMIT {limit})",
            (*indexed, *scope_values, *scope_values),
            candidate_count,
            all_terms_present,
        )

    @staticmethod
    def _add_missing_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(governed_memories)")
        }
        if "origin" not in columns:
            # Nullable and left null for existing rows on purpose. Every
            # memory written before this field existed was extracted,
            # because there was no other way in, so the reader supplies
            # that rather than a backfill writing 427 rows to say what
            # their absence already says.
            connection.execute("ALTER TABLE governed_memories ADD COLUMN origin TEXT")
        if "tags_json" not in columns:
            connection.execute("ALTER TABLE governed_memories ADD COLUMN tags_json TEXT")
        if "undo_cursor_revision" not in columns:
            connection.execute(
                "ALTER TABLE governed_memories ADD COLUMN undo_cursor_revision INTEGER"
            )
        mutation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(governed_memory_mutations)")
        }
        if "created_at" not in mutation_columns:
            connection.execute("ALTER TABLE governed_memory_mutations ADD COLUMN created_at TEXT")
            connection.execute(
                """
                UPDATE governed_memory_mutations
                SET created_at = COALESCE(created_at, ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )
        version_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(governed_memory_versions)")
        }
        if "migrated_from" not in version_columns:
            connection.execute("ALTER TABLE governed_memory_versions ADD COLUMN migrated_from TEXT")
        if "by_model" not in version_columns:
            # Existing rows read as the person's. Every one predates any model
            # call now running, so no refusal is ever decided by one.
            connection.execute(
                "ALTER TABLE governed_memory_versions "
                "ADD COLUMN by_model INTEGER NOT NULL DEFAULT 0"
            )
        deletion_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(memory_deletions)")
        }
        if "next_attempt_at" not in deletion_columns:
            # Null means due now, which is what every deletion left over was.
            connection.execute("ALTER TABLE memory_deletions ADD COLUMN next_attempt_at TEXT")

    @staticmethod
    def _migrate_legacy_rows(connection: sqlite3.Connection) -> None:
        """Map what older versions left pending, rejected or confirmed onto today's terms.

        Statuses move, on versions too because undo restores a version's
        status, and a rejected row gains the `deleted_at` every deleted row
        has. Operations move so the receipts they back still read; the version
        keeps the old name in `migrated_from`. Revision, content and evidence
        stay as they were.

        An old rejection never told HY to forget the memory, so a rejected row
        is registered for deletion here, where it becomes deleted, and the
        deletion sync takes it from there.
        """
        has_legacy_rows = (
            connection.execute(
                "SELECT 1 FROM governed_memories WHERE status IN ('candidate','rejected') LIMIT 1"
            ).fetchone()
            is not None
        )
        if not has_legacy_rows:
            return
        connection.execute(
            """
            INSERT INTO memory_deletions (memory_id, status, checkpoint, updated_at)
            SELECT memory_id, ?, 0, ? FROM governed_memories WHERE status = 'rejected'
            ON CONFLICT(memory_id) DO NOTHING
            """,
            (DeletionStatus.PENDING, datetime.now(UTC).isoformat()),
        )
        connection.execute(
            "UPDATE governed_memories SET status = ?, "
            "deleted_at = COALESCE(deleted_at, updated_at) WHERE status = 'rejected'",
            (MemoryStatus.DELETED,),
        )
        connection.execute(
            "UPDATE governed_memories SET status = ? WHERE status = 'candidate'",
            (MemoryStatus.CONFIRMED,),
        )
        for legacy, current in _LEGACY_STATUSES.items():
            connection.execute(
                "UPDATE governed_memory_versions SET status = ? WHERE status = ?",
                (current, legacy),
            )
        for old_operation, operation in _LEGACY_OPERATIONS.items():
            connection.execute(
                "UPDATE governed_memory_versions SET migrated_from = action, action = ? "
                "WHERE action = ?",
                (operation, old_operation),
            )
            connection.execute(
                "UPDATE governed_memory_mutations SET operation = ? WHERE operation = ?",
                (operation, old_operation),
            )
        # Status migration changes which rows are readable (rejected becomes
        # deleted, candidate becomes confirmed). Refresh the derived index in
        # the same startup transaction; otherwise a migrated candidate is
        # visible to LIKE but absent from the candidate plan.
        MemoryGovernanceStore._rebuild_stale_content_shadow_rows(connection)

    def claim_person_writes(self, actor: PersonActor) -> PersonWrites:
        """Hand out the only way to write as the person; once per store.

        Core's API takes it as it starts, so code holding only the store —
        memory processing, matters, anything HY drives — has no way to write
        as the person and is left with `write_from_hy`, which checks.
        """
        if actor not in PERSON_ACTORS:
            raise ValueError(f"unknown person actor: {actor!r}")
        with self._person_claim_lock:
            if self._person_claimed:
                raise PermissionError("person_writes_already_claimed")
            self._person_claimed = True
        return PersonWrites(self, actor, key=self._person_key)

    def hy_derivation(self, evidence_ids: Collection[str] = ()) -> HyDerivation:
        """Fix where the ledger stands, before a model call reads anything."""
        with self._connection(writing=False) as connection:
            # Versions are never removed, so their rowid only grows.
            row = connection.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM governed_memory_versions"
            ).fetchone()
        return HyDerivation(evidence_ids=tuple(dict.fromkeys(evidence_ids)), watermark=row[0])

    def write_from_hy(
        self,
        change: HyChange,
        *,
        user_id: str,
        derivation: HyDerivation,
    ) -> GovernedMemory:
        """The one way a model's result reaches the ledger.

        Each change is checked against `derivation` inside its own write
        transaction and refused with `MemoryEvidenceOverriddenError` if the
        person overruled its evidence while the model was working. Every
        version it writes is marked as the model's, so one change it made
        never counts as the person overruling the next.

        Whatever evidence the change writes is checked too, whether or not the
        caller cited it: a revision carrying its old evidence forward rests on
        that evidence just as much as on the new.
        """
        derivation = derivation.citing((*derivation.evidence_ids, *_written_evidence(change)))
        match change:
            case HyCreate():
                if change.draft.user_id != user_id:
                    raise KeyError(change.memory_id)
                return self._create(
                    change.draft,
                    request_id=change.request_id,
                    memory_id=change.memory_id,
                    derivation=derivation,
                )
            case HyRevise():
                return self._mutate(
                    change.memory_id,
                    user_id=user_id,
                    request_id=change.request_id,
                    expected_revision=change.expected_revision,
                    operation="hy_reconcile",
                    content=change.content,
                    evidence_ids=change.evidence_ids,
                    derivation=derivation,
                )
            case HyLink():
                return self._link_evidence(
                    change.memory_id,
                    user_id=user_id,
                    evidence_id=change.evidence_id,
                    derivation=derivation,
                )
            case HyRetire():
                return self._mutate(
                    change.memory_id,
                    user_id=user_id,
                    request_id=change.request_id,
                    expected_revision=change.expected_revision,
                    operation="delete",
                    status=MemoryStatus.DELETED,
                    deletion=DeletionStatus.COMPLETED,
                    derivation=derivation,
                )
            case HySupersede():
                return self._supersede(change, user_id=user_id, derivation=derivation)

    def _create(
        self,
        draft: MemoryDraft,
        *,
        request_id: str,
        memory_id: str | None,
        derivation: HyDerivation | None,
    ) -> GovernedMemory:
        self._validate_request_id(request_id)
        memory_id = memory_id or str(uuid4())
        if not memory_id or len(memory_id) > 512:
            raise ValueError("memory_id must contain 1-512 characters")
        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            self._begin_write(connection)
            existing = self._mutation(connection, draft.user_id, request_id)
            if existing is not None:
                if existing["operation"] != "create":
                    raise MemoryMutationRequestConflictError(request_id)
                return self._result_at_revision(connection, existing)
            self._refuse_overridden(connection, draft.user_id, derivation)
            placeholders = ",".join("?" for _ in draft.evidence_ids)
            found = connection.execute(
                f"""
                SELECT evidence_id FROM evidence
                WHERE user_id = ? AND evidence_id IN ({placeholders})
                """,
                (draft.user_id, *draft.evidence_ids),
            ).fetchall()
            if {row["evidence_id"] for row in found} != set(draft.evidence_ids):
                raise MemoryEvidenceNotFoundError("memory_evidence_not_found")
            scopes_json = self._scopes_json(draft.scopes)
            connection.execute(
                """
                INSERT INTO governed_memories (
                    memory_id, user_id, kind, status, content, scopes_json,
                    origin, tags_json, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    memory_id,
                    draft.user_id,
                    draft.kind,
                    MemoryStatus.CONFIRMED,
                    draft.content,
                    scopes_json,
                    draft.origin,
                    json.dumps(list(draft.tags), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._content_shadow_upsert(connection, memory_id, draft.content)
            connection.executemany(
                """
                INSERT INTO governed_memory_evidence (memory_id, evidence_id)
                VALUES (?, ?)
                """,
                ((memory_id, evidence_id) for evidence_id in draft.evidence_ids),
            )
            self._insert_version(
                connection, memory_id, "create", now, by_model=derivation is not None
            )
            self._record_mutation(
                connection,
                user_id=draft.user_id,
                request_id=request_id,
                memory_id=memory_id,
                operation="create",
                revision=1,
            )
            return self._require_with_connection(connection, memory_id)

    def _supersede(
        self,
        change: HySupersede,
        *,
        user_id: str,
        derivation: HyDerivation,
    ) -> GovernedMemory:
        """Mirror HY replacing this memory with newer ones.

        Not a deletion: HY keeps the node on its evolution chain, so no HY
        cleanup is queued. The row keeps its content and evidence and only
        its status moves. A memory already superseded only gains the new
        links; it has no current state left for a second replacement to change.
        """
        memory_id, request_id = change.memory_id, change.request_id
        expected_revision = change.expected_revision
        self._validate_request_id(request_id)
        replacements = tuple(dict.fromkeys(change.superseded_by))
        if (
            not replacements
            or memory_id in replacements
            or any(not key or len(key) > 512 for key in replacements)
        ):
            raise ValueError("superseded_by must name 1-512 character ids of other memories")
        with self._connection() as connection:
            self._begin_write(connection)
            previous = self._mutation(connection, user_id, request_id)
            if previous is not None:
                if previous["memory_id"] != memory_id or previous["operation"] != "hy_supersede":
                    raise MemoryMutationRequestConflictError(request_id)
                return self._result_at_revision(connection, previous)
            self._refuse_overridden(connection, user_id, derivation)
            current = self._require_with_connection(connection, memory_id)
            if current.user_id != user_id:
                raise KeyError(memory_id)
            if current.status is MemoryStatus.DELETED:
                raise MemoryMutationConflictError("memory_deleted")
            if current.revision != expected_revision:
                raise MemoryMutationConflictError("memory_revision_conflict")
            if current.status not in {MemoryStatus.CONFIRMED, MemoryStatus.SUPERSEDED}:
                raise MemoryMutationConflictError("memory_not_supersedable")
            placeholders = ",".join("?" for _ in replacements)
            found = connection.execute(
                f"""
                SELECT memory_id FROM governed_memories
                WHERE user_id = ? AND status != ? AND memory_id IN ({placeholders})
                """,
                (user_id, MemoryStatus.DELETED, *replacements),
            ).fetchall()
            if {row["memory_id"] for row in found} != set(replacements):
                raise MemoryMutationConflictError("memory_replacement_not_found")
            now = datetime.now(UTC).isoformat()
            connection.executemany(
                """
                INSERT INTO governed_memory_supersessions (memory_id, superseded_by, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(memory_id, superseded_by) DO NOTHING
                """,
                ((memory_id, key, now) for key in replacements),
            )
            revision = current.revision
            if current.status is not MemoryStatus.SUPERSEDED:
                revision += 1
                changed = connection.execute(
                    """
                    UPDATE governed_memories
                    SET status = ?, revision = ?, updated_at = ?, undo_cursor_revision = NULL
                    WHERE memory_id = ? AND user_id = ? AND revision = ?
                    """,
                    (MemoryStatus.SUPERSEDED, revision, now, memory_id, user_id, expected_revision),
                )
                if changed.rowcount != 1:
                    raise MemoryMutationConflictError("memory_revision_conflict")
                self._insert_version(connection, memory_id, "hy_supersede", now, by_model=True)
            self._record_mutation(
                connection,
                user_id=user_id,
                request_id=request_id,
                memory_id=memory_id,
                operation="hy_supersede",
                revision=revision,
            )
            return self._require_with_connection(connection, memory_id)

    def read_lifecycle_from(self, source: LifecycleSource) -> None:
        """Where this store learns which memories HY holds current.

        Set once by the composition root. Every read that is not handed a
        lifecycle asks `source`, and so does every edit before it checks the
        memory is still current: the ledger keeps no copy of HY's state for
        any of them to fall back on. `source` answers None to leave the
        ledger as it stands, e.g. before HY has opened. Reads of one memory
        pass its id, so HY looks up that node instead of scanning.
        """
        self._lifecycle_source = source

    def current_lifecycle(self, user_id: str) -> HyLifecycle | None:
        """The source's answer, for readers that query the tables themselves."""
        return self._lifecycle(user_id)

    def _lifecycle(
        self,
        user_id: str,
        given: LifecycleArg = FROM_SOURCE,
        ids: tuple[str, ...] | None = None,
    ) -> HyLifecycle | None:
        if not isinstance(given, _FromSource):
            return given
        source = self._lifecycle_source
        if source is None:
            return None
        return source(user_id) if ids is None else source(user_id, ids)

    def get(
        self, memory_id: str, *, user_id: str, lifecycle: LifecycleArg = FROM_SOURCE
    ) -> GovernedMemory | None:
        resolved = self._lifecycle(user_id, lifecycle, (memory_id,))
        with self._connection(writing=False) as connection:
            return self._get_with_connection(
                connection, memory_id, user_id=user_id, lifecycle=resolved
            )

    def _get_with_connection(
        self,
        connection: sqlite3.Connection,
        memory_id: str,
        *,
        user_id: str,
        lifecycle: HyLifecycle | None = None,
    ) -> GovernedMemory | None:
        row = connection.execute(
            """
            SELECT memory_id FROM governed_memories
            WHERE memory_id = ? AND user_id = ?
            """,
            (memory_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return self._require_with_connection(connection, row["memory_id"], lifecycle=lifecycle)

    def list(
        self,
        *,
        user_id: str,
        status: MemoryStatus | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
        scopes: Collection[str] | None = None,
        lifecycle: LifecycleArg = FROM_SOURCE,
    ) -> list[GovernedMemory]:
        """A page of this user's memories, newest first.

        `lifecycle` is HY's answer to which memories are current; see
        `lifecycle_predicate`.

        `scopes` narrows to what the caller may read, in SQL, for the reason
        given on `_scope_narrowing`: the answerer reads this page and then
        discarded unreadable rows afterwards, so a library with more than a
        page of other projects' memories answered as though it held none of
        the caller's. `None` does not narrow; an empty collection returns
        nothing.
        """
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid pagination")
        narrowing = _scope_narrowing(scopes)
        if isinstance(narrowing, _NothingReadable):
            return []
        clauses = ["user_id = ?"]
        values: list[object] = [user_id]
        resolved = self._lifecycle(user_id, lifecycle)
        selected, selected_values = lifecycle_predicate(
            resolved, status, include_deleted=include_deleted
        )
        if selected is not None:
            clauses.append(selected)
            values.extend(selected_values)
        if query:
            clauses.append("content LIKE ? ESCAPE '\\'")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append(f"%{escaped}%")
        if narrowing is not None:
            clauses.append(narrowing[0])
            values.extend(narrowing[1])
        values.extend((limit, offset))
        with self._connection(writing=False) as connection:
            # Two queries for the page, not two per row. This selected ids and
            # then re-read each memory one at a time, which cost 201 queries
            # for a hundred memories; alongside the ingest pipeline writing to
            # the same database it took 5.7s, past the renderer's five second
            # timeout, so a full library read as though it were empty.
            rows = connection.execute(
                f"""
                SELECT * FROM governed_memories
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC, memory_id
                LIMIT ? OFFSET ?
                """,
                values,
            ).fetchall()
            return self.memories_from_rows(connection, rows, lifecycle=resolved)

    def supersession_integrity(self, *, user_id: str) -> SupersessionIntegrity:
        """Read-only structural audit of this user's supersession graph.

        `governed_memory_supersessions` is a directed graph (`memory_id` ->
        `superseded_by`). A well-formed replacement chain is a DAG whose
        components have exactly one current head, so two shapes are always
        wrong and are reported separately, without repairing anything:

        - `cycles`: strongly connected components of size > 1 (or a
          self-loop). A cycle contradicts HY's single-`is_latest` model: it
          leaves the whole component with no current head, so every current read
          hides it. Detected with an SCC algorithm, not by "no confirmed head"
          (a headless component can also come from deletion or status drift).
        - `headless`: components with more than one memory but no `confirmed`
          member, even when acyclic.

        Only `SUPERSEDED`/`CONFIRMED` memories participate; deleted rows are
        skipped so a person's deletion is never reported as corruption. This
        never mutates a row: it is a query an operator or the reconciliation
        path can run to surface the red before deciding repair.
        """
        with self._connection(writing=False) as connection:
            rows = connection.execute(
                """
                SELECT edge.memory_id AS source_id,
                       edge.superseded_by AS target_id,
                       source.status AS source_status,
                       target.status AS target_status
                FROM governed_memory_supersessions edge
                JOIN governed_memories source ON source.memory_id = edge.memory_id
                LEFT JOIN governed_memories target
                  ON target.memory_id = edge.superseded_by AND target.user_id = ?
                WHERE source.user_id = ?
                  AND source.status != ?
                """,
                (user_id, user_id, MemoryStatus.DELETED),
            ).fetchall()
            statuses = {
                str(row["memory_id"]): str(row["status"])
                for row in connection.execute(
                    "SELECT memory_id, status FROM governed_memories WHERE user_id = ?",
                    (user_id,),
                )
            }
        edges: dict[str, set[str]] = {}
        # A memory whose replacement the person deleted has no `confirmed`
        # successor left, but that is the person's action, not corruption. Track
        # it so a shortened chain is not reported as `headless` — a two-link
        # chain happened to stay quiet only because the survivor was a lone
        # node; a three-or-more-link chain used to be falsely flagged.
        explained_by_deletion: set[str] = set()
        for row in rows:
            source_id = str(row["source_id"])
            target_id = str(row["target_id"])
            if str(row["target_status"]) == MemoryStatus.DELETED:
                explained_by_deletion.add(source_id)
                continue
            if row["target_status"] is None:
                # A dangling reference is not a replacement chain member; drop
                # it exactly as before so this stays a deletion-only fix.
                continue
            edges.setdefault(source_id, set()).add(target_id)
        cycles = _strongly_connected_components(edges)
        cycle_members = {node for component in cycles for node in component}
        headless: list[tuple[str, ...]] = []
        for component in _weakly_connected_components(edges):
            if len(component) < 2 or cycle_members & set(component):
                continue
            if any(statuses.get(node) == MemoryStatus.CONFIRMED for node in component):
                continue
            if explained_by_deletion & set(component):
                continue
            headless.append(component)
        return SupersessionIntegrity(
            cycles=tuple(sorted(cycles)),
            headless=tuple(sorted(headless)),
        )

    def find_confirmed_matching_terms(
        self,
        *,
        user_id: str,
        terms: Collection[str],
        limit: int = 200,
        scopes: Collection[str] | None = None,
        lifecycle: LifecycleArg = FROM_SOURCE,
    ) -> tuple[builtins.list[GovernedMemory], bool]:
        """Confirmed memories whose content matches any term — not newest-N.

        Recall used to score only `list(..., limit=500)` ordered by
        `updated_at DESC`. Once the library grew past that page, older
        matches became unreachable while the receipt still said empty and
        `truncated=False`. Filtering in SQL keeps a buried match findable;
        `limit` caps how many matches to score, not how much of the library
        exists. The bool is whether more matches remain beyond `limit`.

        `scopes` narrows to memories the caller may read, and it has to
        happen here rather than after the fact: filtering in Python spends
        the `limit` on rows the caller will then discard, so a few hundred
        newer memories from another project push an older readable one out
        of the pool and recall answers "empty" for something it holds. The
        same mistake also made `more` report a cap that only unreadable
        rows had hit.

        `None` means no scope narrowing and is for the internal wildcard
        alone. An empty collection means nothing is readable and returns
        nothing — it must never be read as "do not filter".
        """
        if not 1 <= limit <= 2_000:
            raise ValueError("invalid candidate limit")
        narrowing = _scope_narrowing(scopes)
        if isinstance(narrowing, _NothingReadable):
            return [], False
        cleaned: builtins.list[str] = []
        dropped_terms = False
        for term in terms:
            text = term.strip()
            if not text or text in cleaned:
                continue
            if len(cleaned) == 32:
                # The candidate query only unions 32 distinct terms. Recall
                # passes every term it extracted, so a long query can hit this
                # bound; dropping the rest silently made recall answer empty
                # with `truncated=False` even when a memory matched only a
                # dropped term. A 33rd distinct term is the first one that is
                # actually dropped, so only then is truncation reported: an
                # exactly-32-term query is a clean, complete answer.
                dropped_terms = True
                break
            cleaned.append(text)
        if not cleaned:
            return [], False
        resolved = self._lifecycle(user_id, lifecycle)
        current, current_values = lifecycle_predicate(resolved, MemoryStatus.CONFIRMED, alias="m")
        self._refresh_content_shadow()
        with self._connection(writing=False) as connection:
            clauses = ["m.user_id = ?", cast(str, current)]
            values: builtins.list[object] = [user_id, *current_values]
            shadow = self._matching_shadow_clause(
                connection,
                cleaned,
                # Ask the candidate subquery for one row past the page so the
                # outer `LIMIT limit+1` can tell that matches remain. Capping
                # the subquery at `limit` made `more` always False on the shadow
                # path even when hundreds of matches were left unreturned,
                # while the LIKE fallback correctly reported truncation.
                limit=limit + 1,
                narrowing=None if narrowing is None else narrowing,
            )
            if shadow is None or not shadow[3]:
                like_parts: builtins.list[str] = []
                for term in cleaned:
                    like_parts.append("m.content LIKE ? ESCAPE '\\'")
                    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    values.append(f"%{escaped}%")
                clauses.append(f"({' OR '.join(like_parts)})")
            else:
                clauses.append(shadow[0])
                values.extend(shadow[1])
            if narrowing is not None:
                clauses.append(narrowing[0].replace("governed_memories.", "m."))
                values.extend(narrowing[1])
            # One extra row tells us the candidate pool itself was capped.
            values.append(limit + 1)
            rows = connection.execute(
                f"""
                SELECT m.* FROM governed_memories m
                WHERE {" AND ".join(clauses)}
                ORDER BY m.updated_at DESC, m.memory_id
                LIMIT ?
                """,
                values,
            ).fetchall()
            more = len(rows) > limit
            return (
                self.memories_from_rows(connection, rows[:limit], lifecycle=resolved),
                more or dropped_terms,
            )

    def _refresh_content_shadow(self) -> None:
        """Never write or backfill on the read path; a missing shadow fails closed.

        This runs on every recall read, so it must stay on a READ connection:
        opening `_connection()` with its default `writing=True` checked out the
        ONE global writer for a check that changes nothing on a migrated
        database, serializing unrelated readers behind the writer. Migration 32
        owns the shadow tables, so a missing table is a schema fault the
        migration runner must repair — `_ensure_content_shadow(backfill=False)`
        raises `governance_schema_missing` without creating anything, which is
        the same fail-closed behavior as before, now without taking the writer.
        """
        with self._connection(writing=False) as connection:
            self._ensure_content_shadow(connection, backfill=False)

    @staticmethod
    def memories_from_rows(
        connection: sqlite3.Connection,
        rows: builtins.list[sqlite3.Row],
        *,
        lifecycle: HyLifecycle | None = None,
    ) -> builtins.list[GovernedMemory]:
        if not rows:
            return []
        links = _memory_links(connection, [row["memory_id"] for row in rows])
        return [
            _memory_from_row(
                row, *(tuple(values) for values in links[row["memory_id"]]), lifecycle=lifecycle
            )
            for row in rows
        ]

    def has_evidence_link(self, evidence_id: str) -> bool:
        with self._connection(writing=False) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM governed_memory_evidence
                WHERE evidence_id = ?
                LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
        return row is not None

    def blocked_source_ids(self, *, user_id: str, evidence_ids: tuple[str, ...]) -> set[str]:
        if not evidence_ids:
            return set()
        with self._connection(writing=False) as connection:
            return self._overridden_evidence(connection, user_id, evidence_ids)

    def sources_behind_memories(
        self,
        *,
        user_id: str,
        memory_ids: Collection[str],
    ) -> dict[str, frozenset[str]]:
        """Which sources each memory's active evidence came from.

        D94's empty-result explanation needs source names, and a memory
        carries none: the evidence table does. This is a read-only join,
        bounded by the candidate ids already scored.
        """
        if not memory_ids:
            return {}
        placeholders = ",".join("?" for _ in memory_ids)
        with self._connection(writing=False) as connection:
            rows = connection.execute(
                f"""
                SELECT link.memory_id, evidence.source_id
                FROM governed_memory_evidence link
                JOIN evidence ON evidence.evidence_id = link.evidence_id
                WHERE link.active = 1
                  AND link.memory_id IN ({placeholders})
                  AND link.evidence_id IN (
                      SELECT evidence_id FROM evidence WHERE user_id = ?
                  )
                """,
                (*memory_ids, user_id),
            ).fetchall()
        found: dict[str, set[str]] = {}
        for row in rows:
            found.setdefault(str(row["memory_id"]), set()).add(str(row["source_id"]))
        return {memory_id: frozenset(sources) for memory_id, sources in found.items()}

    @staticmethod
    def _overridden_evidence(
        connection: sqlite3.Connection, user_id: str, evidence_ids: Collection[str]
    ) -> set[str]:
        """Evidence a memory still rests on that the person deleted or corrected.

        A link the memory has since moved off (`active = 0`) is history, not
        something the memory rests on, and holds nothing back. A memory HY
        retired itself is not the person's deletion either.
        """
        if not evidence_ids:
            return set()
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = connection.execute(
            "SELECT DISTINCT link.evidence_id FROM governed_memory_evidence link "
            "JOIN governed_memories memory USING(memory_id) WHERE memory.user_id=? "
            f"AND link.active = 1 AND link.evidence_id IN ({placeholders}) "
            "AND ((memory.status = 'deleted' "
            "AND EXISTS (SELECT 1 FROM governed_memory_versions version "
            "WHERE version.memory_id=memory.memory_id "
            "AND version.action = 'delete' AND version.by_model = 0)) "
            "OR EXISTS (SELECT 1 FROM governed_memory_versions version "
            "WHERE version.memory_id=memory.memory_id "
            "AND version.action IN ('correct','undo')))",
            (user_id, *evidence_ids),
        ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _refuse_overridden(
        connection: sqlite3.Connection, user_id: str, derivation: HyDerivation | None
    ) -> None:
        """Refuse a model's write if the person overruled its evidence since the call began.

        Checked in the write's own transaction: a check made before the model
        call leaves the whole call for a deletion to land in.
        """
        if derivation is None or not derivation.evidence_ids:
            return
        placeholders = ",".join("?" for _ in derivation.evidence_ids)
        overruled = connection.execute(
            "SELECT 1 FROM governed_memory_versions version "
            "JOIN governed_memory_evidence link "
            "ON link.memory_id = version.memory_id AND link.active = 1 "
            "JOIN governed_memories memory ON memory.memory_id = version.memory_id "
            "WHERE memory.user_id = ? AND version.rowid > ? AND version.by_model = 0 "
            "AND version.action IN ('delete','correct','undo') "
            f"AND link.evidence_id IN ({placeholders}) LIMIT 1",
            (user_id, derivation.watermark, *derivation.evidence_ids),
        ).fetchone()
        if overruled is not None:
            raise MemoryEvidenceOverriddenError("memory_evidence_overridden")

    def _link_evidence(
        self,
        memory_id: str,
        *,
        user_id: str,
        evidence_id: str,
        derivation: HyDerivation,
    ) -> GovernedMemory:
        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            self._begin_write(connection)
            self._refuse_overridden(connection, user_id, derivation)
            memory = connection.execute(
                """
                SELECT memory_id FROM governed_memories
                WHERE memory_id = ? AND user_id = ? AND status != ?
                """,
                (memory_id, user_id, MemoryStatus.DELETED),
            ).fetchone()
            evidence = connection.execute(
                "SELECT evidence_id FROM evidence WHERE evidence_id = ? AND user_id = ?",
                (evidence_id, user_id),
            ).fetchone()
            if memory is None or evidence is None:
                raise KeyError(memory_id if memory is None else evidence_id)
            connection.execute(
                """
                INSERT INTO governed_memory_evidence (memory_id, evidence_id, active)
                VALUES (?, ?, 1)
                ON CONFLICT(memory_id, evidence_id) DO UPDATE SET active = 1
                """,
                (memory_id, evidence_id),
            )
            connection.execute(
                "UPDATE governed_memories SET updated_at = ? WHERE memory_id = ?",
                (now, memory_id),
            )
            return self._require_with_connection(connection, memory_id)

    def history(
        self,
        memory_id: str,
        *,
        user_id: str,
    ) -> builtins.list[MemoryVersion]:
        with self._connection(writing=False) as connection:
            if self._get_with_connection(connection, memory_id, user_id=user_id) is None:
                return []
            rows = connection.execute(
                """
                SELECT * FROM governed_memory_versions
                WHERE memory_id = ? ORDER BY revision DESC
                """,
                (memory_id,),
            ).fetchall()
        return [
            MemoryVersion(
                memory_id=row["memory_id"],
                revision=row["revision"],
                action=row["action"],
                status=row["status"],
                content=row["content"],
                scopes=tuple(json.loads(row["scopes_json"])),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def deletion(self, memory_id: str, *, user_id: str) -> MemoryDeletion | None:
        with self._connection(writing=False) as connection:
            row = connection.execute(
                """
                SELECT d.* FROM memory_deletions d
                JOIN governed_memories m ON m.memory_id = d.memory_id
                WHERE d.memory_id = ? AND m.user_id = ?
                """,
                (memory_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return MemoryDeletion(
            memory_id=row["memory_id"],
            status=row["status"],
            checkpoint=row["checkpoint"],
            attempts=row["attempts"],
            last_error_code=row["last_error_code"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def claim_deletions(
        self,
        *,
        user_id: str,
        limit: int = DELETION_BATCH,
        memory_id: str | None = None,
    ) -> builtins.list[DeletionClaim]:
        """Take the deletions that are due, oldest first, so no other runner works them.

        Due means not yet confirmed, and either waiting out no backoff or held
        by a claim whose lease ran out. Taken in one write transaction, so two
        runners asking at once never both get the same deletion.
        """
        now = self.clock()
        narrowing = "" if memory_id is None else "AND d.memory_id = ? "
        with self._connection() as connection:
            self._begin_write(connection)
            rows = connection.execute(
                f"""
                SELECT d.memory_id FROM memory_deletions d
                JOIN governed_memories m ON m.memory_id = d.memory_id
                WHERE m.user_id = ? AND d.status != ?
                  AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
                  AND d.attempts < ?
                  {narrowing}
                ORDER BY COALESCE(d.next_attempt_at, ''), d.updated_at, d.memory_id
                LIMIT ?
                """,
                (
                    user_id,
                    DeletionStatus.COMPLETED,
                    now.isoformat(),
                    DELETION_MAX_ATTEMPTS,
                    *(() if memory_id is None else (memory_id,)),
                    limit,
                ),
            ).fetchall()
            claims = [DeletionClaim(row["memory_id"], uuid4().hex) for row in rows]
            connection.executemany(
                """
                UPDATE memory_deletions
                SET status = ?, runner_token = ?, next_attempt_at = ?, updated_at = ?
                WHERE memory_id = ?
                """,
                (
                    (
                        DeletionStatus.RUNNING,
                        claim.token,
                        (now + DELETION_LEASE).isoformat(),
                        now.isoformat(),
                        claim.memory_id,
                    )
                    for claim in claims
                ),
            )
        return claims

    def next_deletion_due(self, *, user_id: str) -> datetime | None:
        """When the earliest unconfirmed deletion is next due; None if there is none."""
        with self._connection(writing=False) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS waiting,
                       MIN(COALESCE(d.next_attempt_at, '')) AS earliest
                FROM memory_deletions d
                JOIN governed_memories m ON m.memory_id = d.memory_id
                WHERE m.user_id = ? AND d.status != ? AND d.attempts < ?
                """,
                (user_id, DeletionStatus.COMPLETED, DELETION_MAX_ATTEMPTS),
            ).fetchone()
        if not row["waiting"]:
            return None
        if not row["earliest"]:
            return self.clock()
        return datetime.fromisoformat(row["earliest"])

    def deletion_retry_at(self, memory_id: str, *, user_id: str) -> datetime | None:
        with self._connection(writing=False) as connection:
            row = connection.execute(
                """
                SELECT d.next_attempt_at FROM memory_deletions d
                JOIN governed_memories m ON m.memory_id = d.memory_id
                WHERE d.memory_id = ? AND m.user_id = ?
                """,
                (memory_id, user_id),
            ).fetchone()
        if row is None or row["next_attempt_at"] is None:
            return None
        return datetime.fromisoformat(row["next_attempt_at"])

    def settle_deletion(
        self,
        claim: DeletionClaim,
        *,
        user_id: str,
        error_code: str | None,
    ) -> MemoryDeletion | None:
        """Record how a claimed deletion went; None if the claim was taken over meanwhile.

        A failure waits `DELETION_RETRY_FIRST`, twice as long after each
        further one, up to the ceiling. Only the claim's holder writes, so a
        runner that outlived its lease cannot overwrite what the next one did.
        """
        now = self.clock()
        with self._connection() as connection:
            self._begin_write(connection)
            row = connection.execute(
                """
                SELECT d.attempts FROM memory_deletions d
                JOIN governed_memories m ON m.memory_id = d.memory_id
                WHERE d.memory_id = ? AND m.user_id = ? AND d.runner_token = ?
                """,
                (claim.memory_id, user_id, claim.token),
            ).fetchone()
            if row is None:
                return None
            exhausted = error_code is not None and row["attempts"] + 1 >= DELETION_MAX_ATTEMPTS
            retry_at = (
                None
                if error_code is None or exhausted
                else now
                + min(DELETION_RETRY_FIRST * 2 ** min(row["attempts"], 20), DELETION_RETRY_CEILING)
            )
            if exhausted:
                logging.getLogger(__name__).warning(
                    "memory_deletion_needs_attention: %s after %d attempts",
                    error_code,
                    row["attempts"] + 1,
                )
                error_code = DELETION_NEEDS_ATTENTION
            connection.execute(
                """
                UPDATE memory_deletions
                SET status = ?, checkpoint = ?, attempts = attempts + 1, runner_token = NULL,
                    last_error_code = ?, updated_at = ?, next_attempt_at = ?
                WHERE memory_id = ? AND runner_token = ?
                """,
                (
                    DeletionStatus.COMPLETED if error_code is None else DeletionStatus.FAILED,
                    1 if error_code is None else 0,
                    error_code,
                    now.isoformat(),
                    None if retry_at is None else retry_at.isoformat(),
                    claim.memory_id,
                    claim.token,
                ),
            )
        return self.deletion(claim.memory_id, user_id=user_id)

    def index_sync(self, memory_id: str, *, user_id: str) -> MemoryIndexSync | None:
        with self._connection(writing=False) as connection:
            row = connection.execute(
                """
                SELECT s.* FROM memory_index_syncs s
                JOIN governed_memories m ON m.memory_id = s.memory_id
                WHERE s.memory_id = ? AND m.user_id = ?
                """,
                (memory_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return _index_sync_from_row(row)

    def pending_index_syncs(
        self,
        *,
        user_id: str,
        memory_ids: Collection[str],
    ) -> builtins.list[MemoryIndexSync]:
        """Which of these memories HY has not caught up with.

        Always bounded by the ids asked about — the caller is recall, which
        knows the handful it just returned — so this stays one indexed lookup
        however far behind the index has fallen.
        """
        ids = tuple(dict.fromkeys(memory_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connection(writing=False) as connection:
            rows = connection.execute(
                f"""
                SELECT s.* FROM memory_index_syncs s
                JOIN governed_memories m ON m.memory_id = s.memory_id
                WHERE m.user_id = ? AND s.status = ?
                  AND s.memory_id IN ({placeholders})
                ORDER BY s.updated_at, s.memory_id
                """,
                (user_id, IndexSyncStatus.PENDING, *ids),
            ).fetchall()
        return [_index_sync_from_row(row) for row in rows]

    def complete_index_sync(self, memory_id: str, *, user_id: str) -> MemoryIndexSync:
        return self._finish_index_sync(memory_id, user_id=user_id, error_code=None)

    def fail_index_sync(
        self,
        memory_id: str,
        *,
        user_id: str,
        error_code: str,
    ) -> MemoryIndexSync:
        return self._finish_index_sync(memory_id, user_id=user_id, error_code=error_code)

    def _finish_index_sync(
        self,
        memory_id: str,
        *,
        user_id: str,
        error_code: str | None,
    ) -> MemoryIndexSync:
        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            exists = connection.execute(
                """
                SELECT 1 FROM governed_memories
                WHERE memory_id = ? AND user_id = ?
                """,
                (memory_id, user_id),
            ).fetchone()
            if exists is None:
                raise KeyError(memory_id)
            status = IndexSyncStatus.SYNCED if error_code is None else IndexSyncStatus.PENDING
            changed = connection.execute(
                """
                UPDATE memory_index_syncs
                SET status = ?, attempts = attempts + 1,
                    last_error_code = ?, updated_at = ?
                WHERE memory_id = ?
                """,
                (status, error_code, now, memory_id),
            )
            if changed.rowcount != 1:
                raise KeyError(memory_id)
        record = self.index_sync(memory_id, user_id=user_id)
        if record is None:
            raise KeyError(memory_id)
        return record

    def _mutate(
        self,
        memory_id: str,
        *,
        user_id: str,
        request_id: str,
        expected_revision: int,
        operation: str,
        status: MemoryStatus | None = None,
        content: str | None = None,
        scopes: tuple[str, ...] | None = None,
        deletion: DeletionStatus | None = None,
        restore_previous: bool = False,
        evidence_ids: tuple[str, ...] | None = None,
        derivation: HyDerivation | None = None,
    ) -> GovernedMemory:
        self._validate_request_id(request_id)
        # Asked before the write is opened: the source reads other tables of
        # this library, which a held write checkout would block.
        # A local tombstone must be writable even while HY is unavailable; the
        # deletion outbox will keep retrying the physical delete. Other edits
        # fail closed when current HY state cannot be established.
        lifecycle = None if operation == "delete" else self._lifecycle(user_id, ids=(memory_id,))
        with self._connection() as connection:
            self._begin_write(connection)
            previous = self._mutation(connection, user_id, request_id)
            if previous is not None:
                if previous["memory_id"] != memory_id or previous["operation"] != operation:
                    raise MemoryMutationRequestConflictError(request_id)
                if operation == "set_scopes" and scopes is not None:
                    result = self._result_at_revision(connection, previous)
                    if self._scopes_json(result.scopes) != self._scopes_json(scopes):
                        raise MemoryMutationRequestConflictError(request_id)
                return self._result_at_revision(connection, previous)
            self._refuse_overridden(connection, user_id, derivation)
            current = self._require_with_connection(connection, memory_id, lifecycle=lifecycle)
            if current.user_id != user_id:
                raise KeyError(memory_id)
            if current.status is MemoryStatus.DELETED:
                raise MemoryMutationConflictError("memory_deleted")
            if current.revision != expected_revision:
                raise MemoryMutationConflictError("memory_revision_conflict")
            # Only its replacement is current; editing the old one would fork the chain.
            if current.status is MemoryStatus.SUPERSEDED and operation != "delete":
                raise MemoryMutationConflictError("memory_superseded")
            if (
                operation in {"correct", "undo", "hy_reconcile", "set_scopes"}
                and current.status is not MemoryStatus.CONFIRMED
            ):
                raise MemoryMutationConflictError("memory_not_correctable")
            if content is not None and (not content.strip() or len(content) > 1_000_000):
                raise ValueError("content must contain 1-1000000 characters")
            next_revision = current.revision + 1
            now = datetime.now(UTC).isoformat()
            next_status = status or current.status
            next_content = content or current.content
            next_scopes = scopes or current.scopes
            undo_cursor_revision: int | None = None
            if restore_previous:
                undo_state = connection.execute(
                    """
                    SELECT undo_cursor_revision FROM governed_memories
                    WHERE memory_id = ?
                    """,
                    (memory_id,),
                ).fetchone()
                cursor = (
                    undo_state["undo_cursor_revision"]
                    if undo_state is not None and undo_state["undo_cursor_revision"] is not None
                    else current.revision
                )
                target_revision = cursor - 1
                target = connection.execute(
                    """
                    SELECT status, content, scopes_json
                    FROM governed_memory_versions
                    WHERE memory_id = ? AND revision = ?
                    """,
                    (memory_id, target_revision),
                ).fetchone()
                if target is None:
                    raise MemoryMutationConflictError("memory_nothing_to_undo")
                next_status = MemoryStatus(target["status"])
                next_content = target["content"]
                next_scopes = tuple(json.loads(target["scopes_json"]))
                undo_cursor_revision = target_revision
            changed = connection.execute(
                """
                UPDATE governed_memories
                SET status = ?, content = ?, scopes_json = ?, revision = ?,
                    updated_at = ?, undo_cursor_revision = ?,
                    deleted_at = CASE WHEN ? = 'deleted' THEN ? ELSE deleted_at END
                WHERE memory_id = ? AND user_id = ? AND revision = ?
                """,
                (
                    next_status,
                    next_content,
                    self._scopes_json(next_scopes),
                    next_revision,
                    now,
                    undo_cursor_revision,
                    next_status,
                    now,
                    memory_id,
                    user_id,
                    expected_revision,
                ),
            )
            if next_status is MemoryStatus.DELETED:
                self._content_shadow_delete(connection, memory_id)
            else:
                self._content_shadow_upsert(connection, memory_id, next_content)
            if changed.rowcount != 1:
                raise MemoryMutationConflictError("memory_revision_conflict")
            if evidence_ids is not None:
                placeholders = ",".join("?" for _ in evidence_ids)
                found = connection.execute(
                    "SELECT evidence_id FROM evidence WHERE user_id=? "
                    f"AND evidence_id IN ({placeholders})",
                    (user_id, *evidence_ids),
                ).fetchall()
                if not evidence_ids or {row[0] for row in found} != set(evidence_ids):
                    raise MemoryEvidenceNotFoundError("memory_evidence_not_found")
                connection.execute(
                    "UPDATE governed_memory_evidence SET active=0 WHERE memory_id=?", (memory_id,)
                )
                connection.executemany(
                    "INSERT INTO governed_memory_evidence (memory_id,evidence_id,active) "
                    "VALUES (?,?,1) "
                    "ON CONFLICT(memory_id,evidence_id) DO UPDATE SET active=1",
                    ((memory_id, key) for key in evidence_ids),
                )
            self._insert_version(
                connection, memory_id, operation, now, by_model=derivation is not None
            )
            self._record_mutation(
                connection,
                user_id=user_id,
                request_id=request_id,
                memory_id=memory_id,
                operation=operation,
                revision=next_revision,
            )
            if next_content != current.content and operation != "hy_reconcile":
                # Keyed on the text having moved, not on which operation moved
                # it: a correction and an undo leave HY's index equally stale,
                # while a scope change or a deletion leaves it untouched. Marked in
                # the same transaction as the write, so there is no instant in
                # which the new text is stored and nothing records that the
                # index is behind. `attempts` survives a conflict — a memory
                # corrected twice while HY was down has tried twice.
                connection.execute(
                    """
                    INSERT INTO memory_index_syncs (
                        memory_id, status, attempts, updated_at
                    ) VALUES (?, ?, 0, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (memory_id, IndexSyncStatus.PENDING, now),
                )
            if deletion is not None:
                connection.execute(
                    """
                    INSERT INTO memory_deletions (
                        memory_id, status, checkpoint, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO NOTHING
                    """,
                    (memory_id, deletion, int(deletion is DeletionStatus.COMPLETED), now),
                )
            return self._require_with_connection(connection, memory_id)

    def _require_with_connection(
        self,
        connection: sqlite3.Connection,
        memory_id: str,
        *,
        lifecycle: HyLifecycle | None = None,
    ) -> GovernedMemory:
        row = connection.execute(
            "SELECT * FROM governed_memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        links = _memory_links(connection, [memory_id])
        return _memory_from_row(
            row, *(tuple(values) for values in links[memory_id]), lifecycle=lifecycle
        )

    def _result_at_revision(
        self,
        connection: sqlite3.Connection,
        mutation: sqlite3.Row,
    ) -> GovernedMemory:
        current = self._require_with_connection(connection, mutation["memory_id"])
        version = connection.execute(
            """
            SELECT * FROM governed_memory_versions
            WHERE memory_id = ? AND revision = ?
            """,
            (mutation["memory_id"], mutation["result_revision"]),
        ).fetchone()
        if version is None:
            raise RuntimeError("memory mutation result is missing")
        return current.model_copy(
            update={
                "status": MemoryStatus(version["status"]),
                "content": version["content"],
                "scopes": tuple(json.loads(version["scopes_json"])),
                "revision": version["revision"],
                "updated_at": datetime.fromisoformat(version["created_at"]),
            }
        )

    @staticmethod
    def _mutation(
        connection: sqlite3.Connection,
        user_id: str,
        request_id: str,
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """
            SELECT * FROM governed_memory_mutations
            WHERE user_id = ? AND request_id = ?
            """,
                (user_id, request_id),
            ).fetchone(),
        )

    @staticmethod
    def _record_mutation(
        connection: sqlite3.Connection,
        *,
        user_id: str,
        request_id: str,
        memory_id: str,
        operation: str,
        revision: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO governed_memory_mutations (
                user_id, request_id, memory_id, operation, result_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                request_id,
                memory_id,
                operation,
                revision,
                datetime.now(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _insert_version(
        connection: sqlite3.Connection,
        memory_id: str,
        action: str,
        created_at: str,
        *,
        by_model: bool = False,
    ) -> None:
        connection.execute(
            """
            INSERT INTO governed_memory_versions (
                memory_id, revision, action, status, content, scopes_json, created_at, by_model
            )
            SELECT memory_id, revision, ?, status, content, scopes_json, ?, ?
            FROM governed_memories WHERE memory_id = ?
            """,
            (action, created_at, int(by_model), memory_id),
        )

    @staticmethod
    def _scopes_json(scopes: tuple[str, ...]) -> str:
        cleaned = tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))
        if not cleaned or len(cleaned) > 128 or any(len(scope) > 256 for scope in cleaned):
            raise ValueError("scopes must contain 1-128 non-empty values")
        return json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if not request_id or len(request_id) > 256:
            raise ValueError("request_id must contain 1-256 characters")

    @contextmanager
    def _connect(
        self, *, writing: bool = True, keep_journal_mode: bool = False
    ) -> Iterator[sqlite3.Connection]:
        database = main_database(self.database_path)
        with (
            database.write(
                autocommit=True,
                foreign_keys=True,
                row_factory=sqlite3.Row,
                keep_journal_mode=keep_journal_mode,
            )
            if writing
            else database.read(foreign_keys=True, row_factory=sqlite3.Row)
        ) as connection:
            yield connection

    @contextmanager
    def guard_writes(self, check: Callable[[], object]) -> Iterator[None]:
        token = self._write_guard.set(check)
        try:
            yield
        finally:
            self._write_guard.reset(token)

    def _begin_write(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        check = self._write_guard.get()
        if check is not None:
            check()

    @contextmanager
    def _connection(
        self, *, writing: bool = True, keep_journal_mode: bool = False
    ) -> Iterator[sqlite3.Connection]:
        with self._connect(writing=writing, keep_journal_mode=keep_journal_mode) as connection:
            try:
                yield connection
                if connection.in_transaction:
                    check = self._write_guard.get()
                    if check is not None:
                        check()
                    connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise


class PersonWrites:
    """What the person does to their memories; the one write that skips HY's checks.

    Only `MemoryGovernanceStore.claim_person_writes` makes one, once per
    store. Built any other way, it refuses.
    """

    def __init__(self, store: MemoryGovernanceStore, actor: PersonActor, *, key: object) -> None:
        if key is not store._person_key:
            raise PermissionError("person_writes_not_issued")
        self._store = store
        self.actor = actor

    def create(
        self,
        draft: MemoryDraft,
        *,
        request_id: str,
        memory_id: str | None = None,
    ) -> GovernedMemory:
        return self._store._create(
            draft, request_id=request_id, memory_id=memory_id, derivation=None
        )

    def correct(
        self,
        memory_id: str,
        *,
        user_id: str,
        request_id: str,
        expected_revision: int,
        content: str,
        scopes: tuple[str, ...] | None = None,
    ) -> GovernedMemory:
        if not content.strip():
            raise ValueError("content must not be blank")
        return self._store._mutate(
            memory_id,
            user_id=user_id,
            request_id=request_id,
            expected_revision=expected_revision,
            operation="correct",
            content=content,
            scopes=scopes,
        )

    def set_scopes(
        self,
        memory_id: str,
        *,
        user_id: str,
        request_id: str,
        expected_revision: int,
        scopes: tuple[str, ...],
    ) -> GovernedMemory:
        self._store._scopes_json(scopes)
        return self._store._mutate(
            memory_id,
            user_id=user_id,
            request_id=request_id,
            expected_revision=expected_revision,
            operation="set_scopes",
            scopes=scopes,
        )

    def undo(
        self,
        memory_id: str,
        *,
        user_id: str,
        request_id: str,
        expected_revision: int,
    ) -> GovernedMemory:
        return self._store._mutate(
            memory_id,
            user_id=user_id,
            request_id=request_id,
            expected_revision=expected_revision,
            operation="undo",
            restore_previous=True,
        )

    def delete(
        self,
        memory_id: str,
        *,
        user_id: str,
        request_id: str,
        expected_revision: int,
    ) -> GovernedMemory:
        return self._store._mutate(
            memory_id,
            user_id=user_id,
            request_id=request_id,
            expected_revision=expected_revision,
            operation="delete",
            status=MemoryStatus.DELETED,
            deletion=DeletionStatus.PENDING,
        )
