"""Switching the embedding model without losing a memory.

A change of embedding identity (model or width) never takes effect by itself.
It becomes a *switch job*: model writes pause (intake keeps queueing), every
vector of every user is re-embedded from the stored text into a new space,
the copy is verified, and one SQLite transaction makes the new space live,
retires the old one and activates the target profile. Reads keep serving the
old space until that commit.

The same machinery runs a *merge*. Every Core start scans the vector store;
a non-empty space that is neither live nor known to the registry (left by an
earlier build that wrote one collection and read another) is re-embedded into
the live space with the live embedder. The live space's records win: a merge
only adds the ids it lacks. A merged stray is retired like a switched-away
space, kept 14 days, then dropped.

Jobs live in the main Core database beside the profiles and the registry,
with a heartbeat and an owner pid. A Core that stops mid-job -- quit, crash,
an App update installing a new build -- resumes it on its next start: every
step is idempotent (a re-embed skips ids the target already holds; the commit
is one transaction that either happened or did not).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from one_memory.database import take_the_write_lock_first
from one_memory.database_access import main_database
from one_memory.embedding_backend import (
    EmbeddingTarget,
    EmbeddingWidthError,
    PhysicalSpace,
    ReembedBackend,
    ReembedCancelledError,
    ReembedStep,
    ReembedUnavailableError,
    ScannedSpace,
)
from one_memory.embedding_spaces import (
    DEFAULT_GRAPH_DIR,
    EmbeddingSpace,
    EmbeddingSpaceError,
    EmbeddingSpaceStore,
    derived_space,
    embedding_identity,
    graph_dir_for,
    insert_space_in,
    live_space_in,
    next_v2_base,
    set_state_in,
)
from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE
from one_memory.memory_server.migrations import embedding_switch_jobs_ddl
from one_memory.model_profiles import (
    ModelProfileSnapshot,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import SecretStore, SecretStoreUnavailableError

logger = logging.getLogger(__name__)

JobKind = Literal["switch", "merge"]
JobState = Literal[
    "preparing",
    "reembedding",
    "verifying",
    "committing",
    "done",
    "failed",
    "rolled_back",
]
#: States in which model writes stay paused. `failed` is the rollback in
#: progress: writes resume only once the old space is certainly intact.
OPEN_STATES: tuple[JobState, ...] = (
    "preparing",
    "reembedding",
    "verifying",
    "committing",
    "failed",
)
TERMINAL_STATES: tuple[JobState, ...] = ("done", "rolled_back")
CANCELLABLE_STATES: tuple[JobState, ...] = ("preparing", "reembedding", "verifying")

HEARTBEAT_SECONDS = 5.0
#: A job whose owner stopped beating this long ago belongs to nobody.
STALE_HEARTBEAT = timedelta(seconds=60)
PROGRESS_WRITE_SECONDS = 2.0
DEFAULT_DRAIN_TIMEOUT_SECONDS = 600.0
#: Rounds of a re-embed that failed on the embedding service; each resumes
#: where the last stopped, so a flaky endpoint costs at most one batch.
REEMBED_ATTEMPTS = 3
REEMBED_RETRY_SECONDS = (5.0, 15.0)
#: Rough throughput for the estimate shown before a switch (vectors/second).
_LOCAL_RATE = 60.0
_REMOTE_RATE = 200.0
_BYTES_PER_VECTOR_OVERHEAD = 2_500
_DISK_RESERVE = 200 * 1024 * 1024

JOBS_SCHEMA = embedding_switch_jobs_ddl()


class EmbeddingSwitchError(RuntimeError):
    """A refusal with a stable code; `plan` rides along when one was computed."""

    def __init__(self, code: str, *, plan: EmbeddingSwitchPlan | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.plan = plan


class SwitchProgress(BaseModel):
    """What the App's banner and loading screen draw."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["prepare", "vectors", "graph", "verify", "commit", "rollback", "done"] = (
        "prepare"
    )
    done: int = 0
    total: int = 0
    percent: float = 0.0
    embedded: int = 0
    reused: int = 0
    kept: int = 0
    role: str | None = None
    #: Which source space is being folded in (1-based) out of how many.
    source_index: int = 0
    sources: int = 0
    rate_per_s: float | None = None
    eta_s: int | None = None
    updated_at: str | None = None


class SpaceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str | None
    model: str | None
    dims: int | None
    collection_base: str
    vectors: int | None = None


class EmbeddingSwitchJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    kind: JobKind
    state: JobState
    source: SpaceSummary
    target: SpaceSummary
    stray_space_ids: list[str]
    target_profile_id: str | None
    target_revision: int | None
    requested_by: str
    progress: SwitchProgress
    cancel_requested: bool
    error_code: str | None
    attempt: int
    created_at: str
    updated_at: str
    finished_at: str | None


class PlannedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str | None
    collection_base: str
    dims: int | None
    vectors: int
    #: Ids a stray holds that the live space lacks; None for the live space.
    unmerged: int | None = None


class EmbeddingSwitchPlan(BaseModel):
    """Everything the confirmation dialog shows; no side effects."""

    model_config = ConfigDict(extra="forbid")

    needed: bool
    reason: Literal["embedding_identity_changed", "same_embedding", "empty_home"]
    plan_token: str
    source: SpaceSummary
    target: SpaceSummary
    target_profile_id: str
    target_revision: int
    target_provider_kind: str
    target_local: bool
    sources: list[PlannedSource] = Field(default_factory=list)
    total_vectors: int = 0
    users: int | None = None
    graph_vectors: int | None = None
    estimate_s: int | None = None
    requests: int | None = None
    disk_bytes_needed: int = 0
    disk_bytes_free: int | None = None
    retention_days: int = 14
    blockers: list[str] = Field(default_factory=list)


@dataclass
class _Live:
    """Progress kept in memory and written at most every few seconds."""

    progress: SwitchProgress
    cancel: bool = False
    last_write: float = 0.0
    started: float = field(default_factory=time.monotonic)
    base_done: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


class EmbeddingSwitchStore:
    """The `embedding_switch_jobs` table of the main Core database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        with self._connect(writing=True) as connection:
            connection.executescript(JOBS_SCHEMA)

    @contextmanager
    def _connect(self, *, writing: bool = False) -> Iterator[sqlite3.Connection]:
        database = main_database(self.database_path)
        checkout = (
            database.write(row_factory=sqlite3.Row)
            if writing
            else database.read(row_factory=sqlite3.Row)
        )
        with checkout as connection, connection:
            if writing:
                take_the_write_lock_first(connection)
            yield connection

    def open_job(self) -> sqlite3.Row | None:
        with self._connect() as connection:
            return open_job_in(connection)

    def get(self, job_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return cast(
                sqlite3.Row | None,
                connection.execute(
                    "SELECT * FROM embedding_switch_jobs WHERE job_id=?", (job_id,)
                ).fetchone(),
            )

    def last_finished(self) -> sqlite3.Row | None:
        with self._connect() as connection:
            return cast(
                sqlite3.Row | None,
                connection.execute(
                    "SELECT * FROM embedding_switch_jobs WHERE state IN ('done','rolled_back') "
                    "ORDER BY finished_at DESC LIMIT 1"
                ).fetchone(),
            )

    def update(self, job_id: str, **values: object) -> None:
        values["updated_at"] = _now().isoformat()
        assignments = ", ".join(f"{column}=?" for column in values)
        with self._connect(writing=True) as connection:
            connection.execute(
                f"UPDATE embedding_switch_jobs SET {assignments} WHERE job_id=?",
                (*values.values(), job_id),
            )

    def claim(self, job_id: str, *, pid: int) -> None:
        self.update(job_id, owner_pid=pid, heartbeat_at=_now().isoformat())

    def beat(self, job_id: str, *, pid: int) -> None:
        with self._connect(writing=True) as connection:
            connection.execute(
                "UPDATE embedding_switch_jobs SET heartbeat_at=? WHERE job_id=? AND owner_pid=?",
                (_now().isoformat(), job_id, pid),
            )


def open_job_in(connection: sqlite3.Connection) -> sqlite3.Row | None:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding_switch_jobs'"
        ).fetchone()
        is None
    ):
        return None
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM embedding_switch_jobs WHERE state NOT IN ('done','rolled_back')"
            ).fetchone(),
        )
    finally:
        connection.row_factory = previous


def read_open_job(database_path: Path) -> dict[str, object] | None:
    """The unfinished job, read only: for `sdk-migration-status` beside Core."""
    if not database_path.is_file():
        return None
    with main_database(database_path).read(row_factory=sqlite3.Row) as connection:
        row = open_job_in(connection)
    if row is None:
        return None
    return {"job_id": row["job_id"], "kind": row["kind"], "state": row["state"]}


class EmbeddingSwitchService:
    """Plans, starts, runs, resumes and cancels switches and merges for one home."""

    def __init__(
        self,
        *,
        database_path: Path,
        data_dir: Path,
        profiles: ModelProfileStore,
        spaces: EmbeddingSpaceStore,
        secrets: SecretStore,
        backend: ReembedBackend,
        drain_idle: Callable[[], Awaitable[bool]] | None = None,
        on_commit: Callable[[EmbeddingSwitchJob], Awaitable[None]] | None = None,
        quiesce_clients: Callable[[], Awaitable[Callable[[], None]]] | None = None,
        graph_enabled: Callable[[], bool] = lambda: False,
        worker_enabled: bool = True,
        drain_timeout_s: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        self.database_path = database_path
        self.data_dir = data_dir
        self.root = data_dir.resolve() / "hy-memory" / HY_MEMORY_NAMESPACE
        self.profiles = profiles
        self.spaces = spaces
        self.secrets = secrets
        self.backend = backend
        self.jobs = EmbeddingSwitchStore(database_path)
        self.drain_idle = drain_idle or _always_idle
        self.on_commit = on_commit
        self.quiesce_clients = quiesce_clients
        self.graph_enabled = graph_enabled
        self.worker_enabled = worker_enabled
        self.drain_timeout_s = drain_timeout_s
        self.pid = os.getpid()
        #: "pending" until the startup scan decided, then "done"/"failed"/"unavailable".
        self.scan_state: Literal["pending", "scanning", "done", "failed", "unavailable"] = "pending"
        self.scan_error: str | None = None
        self._scan_hold = True
        self._live: dict[str, _Live] = {}
        self._task: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._open_cache: bool = self.jobs.open_job() is not None
        self._lock = asyncio.Lock()
        self._retention: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # The write gate
    # ------------------------------------------------------------------

    def writes_open(self) -> bool:
        """False while a job runs or the startup scan has not decided yet."""
        return not self._open_cache and not self._scan_hold

    def pause_reason(self) -> str | None:
        if self._open_cache:
            return "embedding_switch_in_progress"
        if self._scan_hold:
            return "embedding_scan_pending"
        return None

    def release_scan_hold(self) -> None:
        self._scan_hold = False

    # ------------------------------------------------------------------
    # Reading state
    # ------------------------------------------------------------------

    def current(self) -> dict[str, object]:
        row = self.jobs.open_job()
        job = self._job(row) if row is not None else None
        last = self.jobs.last_finished()
        phase = (
            "running"
            if job is not None
            else ("scanning" if self.scan_state in {"pending", "scanning"} else "idle")
        )
        return {
            "schema_version": 1,
            "phase": phase,
            "writes_paused": not self.writes_open(),
            "scan_state": self.scan_state,
            "scan_error": self.scan_error,
            "job": job.model_dump() if job is not None else None,
            "last_finished": self._job(last).model_dump() if last is not None else None,
        }

    def get_job(self, job_id: str) -> EmbeddingSwitchJob | None:
        row = self.jobs.get(job_id)
        return self._job(row) if row is not None else None

    def summary(self) -> dict[str, object] | None:
        """One line for `/memory-processing` and `/health`."""
        row = self.jobs.open_job()
        if row is None:
            if self.scan_state in {"pending", "scanning"}:
                return {"kind": "merge", "state": "scanning", "percent": 0.0, "eta_s": None}
            return None
        job = self._job(row)
        return {
            "job_id": job.job_id,
            "kind": job.kind,
            "state": job.state,
            "percent": job.progress.percent,
            "eta_s": job.progress.eta_s,
        }

    def _job(self, row: sqlite3.Row) -> EmbeddingSwitchJob:
        live = self._live.get(row["job_id"])
        progress = (
            live.progress
            if live is not None
            else SwitchProgress.model_validate(json.loads(row["progress_json"] or "{}"))
        )
        source = self.spaces.get(row["source_space_id"])
        target = self.spaces.get(row["target_space_id"])
        return EmbeddingSwitchJob(
            job_id=row["job_id"],
            kind=row["kind"],
            state=row["state"],
            source=_summary(source, row["source_space_id"]),
            target=_summary(target, row["target_space_id"]),
            stray_space_ids=list(json.loads(row["stray_space_ids"] or "[]")),
            target_profile_id=row["target_profile_id"],
            target_revision=row["target_revision"],
            requested_by=row["requested_by"],
            progress=progress,
            cancel_requested=bool(row["cancel_requested"]) or bool(live and live.cancel),
            error_code=row["error_code"],
            attempt=int(row["attempt"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
        )

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    async def plan(
        self, profile_id: str, *, expected_revision: int | None = None
    ) -> EmbeddingSwitchPlan:
        """What switching to `profile_id` would do. Reads only."""
        target = await asyncio.to_thread(self._target_snapshot, profile_id, expected_revision)
        live = await asyncio.to_thread(self.spaces.require_live)
        try:
            identity = embedding_identity(target)
        except EmbeddingSpaceError:
            raise EmbeddingSwitchError("embedding_model_required") from None
        provider = target.embedding_provider_kind or target.provider_kind
        local = provider is ProviderKind.LOCAL_ONNX
        blockers: list[str] = []
        if await asyncio.to_thread(self.jobs.open_job) is not None:
            blockers.append("embedding_switch_running")
        source_summary = _summary(live, live.space_id)
        target_summary = SpaceSummary(
            space_id=None,
            model=identity.model,
            dims=identity.dims,
            collection_base="",
        )
        base: dict[str, Any] = {
            "source": source_summary,
            "target": target_summary,
            "target_profile_id": target.profile_id,
            "target_revision": target.revision,
            "target_provider_kind": provider.value,
            "target_local": local,
        }
        if live.identity is not None and identity.matches(live.identity):
            return EmbeddingSwitchPlan(
                needed=False,
                reason="same_embedding",
                plan_token=_token({"same": live.space_id, **_identity_key(target)}),
                blockers=blockers,
                **base,
            )
        if await self._home_is_empty():
            return EmbeddingSwitchPlan(
                needed=False,
                reason="empty_home",
                plan_token=_token({"empty": live.space_id, **_identity_key(target)}),
                blockers=blockers,
                **base,
            )
        unavailable = self.backend.unavailable_reason()
        if unavailable is not None:
            blockers.append(unavailable)
        if identity.dims is None:
            blockers.append("embedding_dimensions_unknown")
        if not await asyncio.to_thread(self._probed, target):
            blockers.append("embedding_probe_required")
        if await asyncio.to_thread(self._legacy_vectors_waiting, live):
            blockers.append("embedding_switch_requires_migration")
        sources: list[PlannedSource] = []
        users: int | None = None
        if unavailable is None:
            scanned = await self.backend.scan(per_user=True)
            live_scan = _find(scanned, live)
            sources.append(
                PlannedSource(
                    space_id=live.space_id,
                    collection_base=live.collection_base,
                    dims=live.dims,
                    vectors=live_scan.vectors if live_scan else 0,
                )
            )
            for scan, space in await self._strays(scanned, live):
                sources.append(
                    PlannedSource(
                        space_id=space.space_id if space else None,
                        collection_base=scan.collection_base,
                        dims=scan.width,
                        vectors=scan.vectors,
                        unmerged=scan.vectors,
                    )
                )
            folded = {item.collection_base for item in sources}
            users = len(
                {
                    user
                    for scan in scanned
                    if scan.collection_base in folded
                    for user, count in scan.per_user.items()
                    if count
                }
            )
        total = sum(item.vectors for item in sources)
        graph_vectors: int | None = None
        graph = self._graph_path(live)
        if graph.exists() and unavailable is None:
            try:
                graph_vectors = await self.backend.graph_vectors(graph)
            except Exception:  # noqa: BLE001 - a client may hold it open: null, not a refusal
                logger.info("embedding_switch_plan_graph_unreadable")
        dims = identity.dims or 1024
        needed_bytes = (
            int((total + (graph_vectors or 0)) * (dims * 4 + _BYTES_PER_VECTOR_OVERHEAD) * 1.5)
            + _DISK_RESERVE
        )
        free = _free_bytes(self.root)
        if free is not None and free < needed_bytes:
            blockers.append("insufficient_disk_space")
        work = total + (graph_vectors or 0)
        rate = _LOCAL_RATE if local else _REMOTE_RATE
        token = _token(
            {
                "source": live.space_id,
                "target": _identity_key(target),
                "sources": [(item.collection_base, item.vectors) for item in sources],
            }
        )
        return EmbeddingSwitchPlan(
            needed=True,
            reason="embedding_identity_changed",
            plan_token=token,
            sources=sources,
            total_vectors=total,
            users=users,
            graph_vectors=graph_vectors,
            estimate_s=int(work / rate) + 1 if work else 0,
            requests=None if local else -(-work // 64),
            disk_bytes_needed=needed_bytes,
            disk_bytes_free=free,
            blockers=blockers,
            **base,
        )

    def _target_snapshot(
        self, profile_id: str, expected_revision: int | None
    ) -> ModelProfileSnapshot:
        profile = self.profiles.get(profile_id, self.secrets)
        if profile is None:
            raise EmbeddingSwitchError("model_profile_not_found")
        if expected_revision is not None and profile.revision != expected_revision:
            raise EmbeddingSwitchError("model_profile_conflict")
        return self.profiles.snapshot(profile_id, profile.revision)

    def _probed(self, target: ModelProfileSnapshot) -> bool:
        profile = self.profiles.get(target.profile_id, self.secrets)
        return bool(
            profile is not None
            and profile.revision == target.revision
            and profile.capabilities.embedding_available
            and profile.capabilities.endpoint_reachable
            and profile.capabilities.model_available
        )

    def _legacy_vectors_waiting(self, live: EmbeddingSpace) -> bool:
        from one_memory.sdk_vector_reattach import legacy_vector_collections

        profile = self.embedding_profile(live)[0]
        try:
            return bool(legacy_vector_collections(self.data_dir, profile))
        except Exception:  # noqa: BLE001 - an unreadable store is not a blocker
            return False

    async def _home_is_empty(self) -> bool:
        """No vector anywhere, no graph, no write in flight: nothing to carry over."""
        if self.backend.unavailable_reason() is None:
            if any(scan.vectors for scan in await self.backend.scan()):
                return False
        else:
            from one_memory.sdk_vector_reattach import filled_collection_names

            if await asyncio.to_thread(filled_collection_names, self.data_dir):
                return False
        return await asyncio.to_thread(self._nothing_else_held)

    def _nothing_else_held(self) -> bool:
        live = self.spaces.live()
        if live is not None and _graph_has_files(self._graph_path(live)):
            return False
        with main_database(self.database_path).read() as connection:
            try:
                running = connection.execute(
                    "SELECT COUNT(*) FROM memory_write_jobs WHERE state='running'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                running = 0
        return not running

    async def _strays(
        self, scanned: tuple[ScannedSpace, ...], live: EmbeddingSpace
    ) -> list[tuple[ScannedSpace, EmbeddingSpace | None]]:
        """Non-empty spaces neither live nor retired: what a merge folds in.

        A retired space is excluded on purpose: its records are already in the
        live space, and folding it in again would bring back what was deleted
        since. Legacy `one_memory_*` collections belong to `sdk-migrate`.
        """
        known = {
            (space.collection_base, space.name_dims or 0): space
            for space in await asyncio.to_thread(self.spaces.list_spaces)
        }
        out: list[tuple[ScannedSpace, EmbeddingSpace | None]] = []
        for scan in scanned:
            if not scan.vectors or scan.key == (live.collection_base, live.name_dims or 0):
                continue
            if not scan.collection_base.startswith("memhub_"):
                continue
            space = known.get(scan.key)
            if space is not None and space.state not in {"stray"}:
                continue
            out.append((scan, space))
        return out

    # ------------------------------------------------------------------
    # Starting a switch
    # ------------------------------------------------------------------

    async def start_switch(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        plan_token: str,
        requested_by: str,
    ) -> EmbeddingSwitchJob:
        async with self._lock:
            plan = await self.plan(profile_id, expected_revision=expected_revision)
            if "embedding_switch_running" in plan.blockers:
                raise EmbeddingSwitchError("embedding_switch_running", plan=plan)
            if plan.plan_token != plan_token:
                raise EmbeddingSwitchError("embedding_switch_plan_stale", plan=plan)
            if not plan.needed:
                raise EmbeddingSwitchError("embedding_switch_not_needed", plan=plan)
            if plan.blockers:
                raise EmbeddingSwitchError(plan.blockers[0], plan=plan)
            target = await asyncio.to_thread(self._target_snapshot, profile_id, expected_revision)
            scanned = await self.backend.scan()
            live = await asyncio.to_thread(self.spaces.require_live)
            strays = [
                await asyncio.to_thread(self._register_stray, scan, space)
                for scan, space in await self._strays(scanned, live)
            ]
            stored = frozenset(name for scan in scanned for name in scan.collections.values())
            job_id = await asyncio.to_thread(
                self._insert_switch, plan, target, live, strays, stored, requested_by
            )
        self._open_cache = True
        self._spawn(job_id)
        job = self.get_job(job_id)
        assert job is not None
        return job

    def _register_stray(self, scan: ScannedSpace, space: EmbeddingSpace | None) -> EmbeddingSpace:
        if space is not None:
            return space
        return self.spaces.register_stray(
            collection_base=scan.collection_base,
            name_dims=scan.name_dims,
            dims=scan.width,
            vectors=scan.vectors,
        )

    def _insert_switch(
        self,
        plan: EmbeddingSwitchPlan,
        target: ModelProfileSnapshot,
        live: EmbeddingSpace,
        strays: list[EmbeddingSpace],
        stored: frozenset[str],
        requested_by: str,
    ) -> str:
        identity = embedding_identity(target)
        provider = target.embedding_provider_kind or target.provider_kind
        now = _now().isoformat()
        job_id = str(uuid4())
        space_id = str(uuid4())
        with self.spaces.transaction() as connection:
            if open_job_in(connection) is not None:
                raise EmbeddingSwitchError("embedding_switch_running")
            current_live = live_space_in(connection)
            if current_live is None or current_live.space_id != live.space_id:
                raise EmbeddingSwitchError("embedding_switch_plan_stale")
            space = EmbeddingSpace(
                space_id=space_id,
                state="building",
                model=identity.model,
                dims=identity.dims,
                name_dims=identity.dims,
                collection_base=next_v2_base(connection, identity, stored_names=stored),
                naming="v2",
                graph_dir=graph_dir_for(space_id),
                provider_kind=provider.value,
                profile_id=target.profile_id,
                profile_revision=target.revision,
                created_at=now,
            )
            insert_space_in(connection, space)
            active = connection.execute(
                "SELECT profile_id FROM model_profiles WHERE is_active=1"
            ).fetchone()
            connection.execute(
                "INSERT INTO embedding_switch_jobs (job_id, kind, state, source_space_id, "
                "target_space_id, stray_space_ids, target_profile_id, target_revision, "
                "prior_profile_id, requested_by, plan_json, progress_json, created_at, "
                "updated_at, owner_pid, heartbeat_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    "switch",
                    "preparing",
                    live.space_id,
                    space_id,
                    json.dumps([stray.space_id for stray in strays]),
                    target.profile_id,
                    target.revision,
                    active[0] if active is not None else None,
                    requested_by,
                    plan.model_dump_json(),
                    SwitchProgress(sources=1 + len(strays)).model_dump_json(),
                    now,
                    now,
                    self.pid,
                    now,
                ),
            )
        self.spaces.invalidate()
        return job_id

    def _insert_merge(self, live: EmbeddingSpace, strays: list[EmbeddingSpace]) -> str:
        now = _now().isoformat()
        job_id = str(uuid4())
        with self.spaces.transaction() as connection:
            if open_job_in(connection) is not None:
                raise EmbeddingSwitchError("embedding_switch_running")
            connection.execute(
                "INSERT INTO embedding_switch_jobs (job_id, kind, state, source_space_id, "
                "target_space_id, stray_space_ids, requested_by, plan_json, progress_json, "
                "created_at, updated_at, owner_pid, heartbeat_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    "merge",
                    "preparing",
                    live.space_id,
                    live.space_id,
                    json.dumps([stray.space_id for stray in strays]),
                    "startup",
                    json.dumps({"strays": [stray.space_id for stray in strays]}),
                    SwitchProgress(sources=len(strays)).model_dump_json(),
                    now,
                    now,
                    self.pid,
                    now,
                ),
            )
        return job_id

    def activate_empty_home(self, profile_id: str) -> EmbeddingSpace:
        """Activate a profile with another embedding on a home holding no vector.

        Nothing needs re-embedding, so there is no job: one transaction makes
        the profile's space live and activates the profile. The space keeps
        the name every earlier build would give it, so an onboarding that
        picks a custom embedding stays readable after a downgrade.
        """
        profile = self.profiles.get(profile_id, self.secrets)
        if profile is None:
            raise EmbeddingSwitchError("model_profile_not_found")
        snapshot = self.profiles.snapshot(profile_id, profile.revision)
        derived = derived_space(snapshot)
        now = _now()
        with self.spaces.transaction() as connection:
            if open_job_in(connection) is not None:
                raise EmbeddingSwitchError("embedding_switch_running")
            secret_ref = connection.execute(
                "SELECT secret_ref FROM model_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            current = live_space_in(connection)
            if current is not None:
                set_state_in(
                    connection, current.space_id, state="dropped", expected=("live",), now=now
                )
            connection.execute(
                "UPDATE embedding_spaces SET state='dropped', dropped_at=? "
                "WHERE collection_base=? AND COALESCE(name_dims,0)=? AND state != 'dropped'",
                (now.isoformat(), derived.collection_base, derived.name_dims or 0),
            )
            space = derived.model_copy(
                update={
                    "space_id": str(uuid4()),
                    "created_at": now.isoformat(),
                    "live_since": now.isoformat(),
                }
            )
            insert_space_in(connection, space)
            ModelProfileStore.activate_in_connection(
                connection,
                snapshot,
                expected_secret_ref=secret_ref[0] if secret_ref is not None else None,
            )
        self.spaces.invalidate()
        return space

    # ------------------------------------------------------------------
    # Startup: resume, or scan and merge
    # ------------------------------------------------------------------

    async def recover(self) -> None:
        """Resume an unfinished job, or scan for strays and start a merge.

        Called before the write drain starts; until it decides, the gate
        holds every model write back (`embedding_scan_pending`).
        """
        try:
            row = await asyncio.to_thread(self.jobs.open_job)
            if row is not None:
                self._open_cache = True
                if self.worker_enabled and _owned_elsewhere(row, self.pid):
                    # Another live Core still beats for it: never two workers.
                    logger.warning(
                        "embedding_switch_owned_elsewhere job_id=%s owner_pid=%s",
                        row["job_id"],
                        row["owner_pid"],
                    )
                elif self.worker_enabled:
                    logger.info(
                        "embedding_switch_resuming job_id=%s kind=%s state=%s",
                        row["job_id"],
                        row["kind"],
                        row["state"],
                    )
                    self._spawn(row["job_id"])
                self.scan_state = "done"
                return
            await self.scan_and_merge()
        finally:
            self.release_scan_hold()

    async def scan_and_merge(self) -> str | None:
        """The startup scan. Returns the merge job it started, if any."""
        reason = self.backend.unavailable_reason()
        if reason is not None:
            self.scan_state, self.scan_error = "unavailable", reason
            return None
        if not self.worker_enabled:
            self.scan_state = "done"
            return None
        self.scan_state = "scanning"
        try:
            live = await asyncio.to_thread(self.spaces.require_live)
            scanned = await self.backend.scan()
            live_scan = _find(scanned, live)
            counts = {live.space_id: live_scan.vectors if live_scan else 0}
            if live_scan is not None and live.dims is None and live_scan.width is not None:
                # A relabel: an "auto" space now knows its width. Names stay.
                live = await asyncio.to_thread(
                    self.spaces.record_width, live.space_id, live_scan.width
                )
            unmerged: list[EmbeddingSpace] = []
            for scan, space in await self._strays(scanned, live):
                stray = await asyncio.to_thread(self._register_stray, scan, space)
                counts[stray.space_id] = scan.vectors
                missing = sum(
                    (await self.backend.diff(scan.physical(), self._physical(live))).values()
                )
                if missing:
                    unmerged.append(stray)
                    logger.info(
                        "embedding_stray_space_found base=%s vectors=%d unmerged=%d",
                        scan.collection_base,
                        scan.vectors,
                        missing,
                    )
                else:
                    # Nothing it holds is missing: retire it without a job.
                    await asyncio.to_thread(self._retire_strays, [stray.space_id])
            await asyncio.to_thread(self.spaces.record_vectors, counts)
            self.scan_state = "done"
            if not unmerged:
                return None
            job_id = await asyncio.to_thread(self._insert_merge, live, unmerged)
        except Exception as error:
            self.scan_state = "failed"
            self.scan_error = getattr(error, "code", None) or type(error).__name__
            logger.warning(
                "embedding_space_scan_failed error_type=%s", type(error).__name__, exc_info=True
            )
            return None
        self._open_cache = True
        self._spawn(job_id)
        return job_id

    def _retire_strays(self, space_ids: list[str]) -> None:
        with self.spaces.transaction() as connection:
            for space_id in space_ids:
                set_state_in(connection, space_id, state="retired", expected=("stray",))
        self.spaces.invalidate()

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel(self, job_id: str) -> EmbeddingSwitchJob:
        row = self.jobs.get(job_id)
        if row is None:
            raise EmbeddingSwitchError("embedding_switch_not_found")
        if row["state"] not in CANCELLABLE_STATES:
            raise EmbeddingSwitchError("embedding_switch_not_cancellable")
        self.jobs.update(job_id, cancel_requested=1)
        live = self._live.get(job_id)
        if live is not None:
            live.cancel = True
        job = self.get_job(job_id)
        assert job is not None
        return job

    # ------------------------------------------------------------------
    # The worker
    # ------------------------------------------------------------------

    def _spawn(self, job_id: str) -> None:
        if not self.worker_enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(
            self.run(job_id), name="one-memory-embedding-switch"
        )

    async def wait(self) -> None:
        """Until the running worker (if any) stops. For tests and shutdown."""
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        for task in (self._task, self._heartbeat, self._retention):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._task, self._heartbeat, self._retention):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    async def run(self, job_id: str) -> None:
        """Drive one job to `done` or `rolled_back`, from whatever state it is in."""
        await asyncio.to_thread(self.jobs.claim, job_id, pid=self.pid)
        row = await asyncio.to_thread(self.jobs.get, job_id)
        if row is None:
            return
        live = _Live(
            progress=SwitchProgress.model_validate(json.loads(row["progress_json"] or "{}")),
            cancel=bool(row["cancel_requested"]),
        )
        self._live[job_id] = live
        self._heartbeat = asyncio.get_running_loop().create_task(self._beat(job_id))
        try:
            await self._drive(job_id)
        finally:
            self._heartbeat.cancel()
            await asyncio.gather(self._heartbeat, return_exceptions=True)
            self._live.pop(job_id, None)
            self._open_cache = await asyncio.to_thread(self.jobs.open_job) is not None

    async def _beat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await asyncio.to_thread(self.jobs.beat, job_id, pid=self.pid)
            except Exception:  # noqa: BLE001 - a missed beat is not a failure
                logger.debug("embedding_switch_heartbeat_failed", exc_info=True)

    async def _drive(self, job_id: str) -> None:
        while True:
            row = await asyncio.to_thread(self.jobs.get, job_id)
            if row is None or row["state"] in TERMINAL_STATES:
                return
            state = row["state"]
            if state == "failed":
                await self._rollback(row)
                continue
            try:
                if self._cancelled(job_id) and state in CANCELLABLE_STATES:
                    raise ReembedCancelledError("embedding_switch_cancelled")
                if state == "preparing":
                    await self._prepare(row)
                    next_state: JobState = "reembedding"
                elif state == "reembedding":
                    await self._reembed(row)
                    next_state = "verifying"
                elif state == "verifying":
                    await self._verify(row)
                    next_state = "committing"
                elif state == "committing":
                    await self._commit(row)
                    continue
                else:  # pragma: no cover - CHECK constraint
                    raise EmbeddingSwitchError("embedding_switch_state_unknown")
            except asyncio.CancelledError:
                # Core is stopping: the job resumes from this state next start.
                await asyncio.to_thread(self._flush, job_id)
                raise
            except Exception as error:
                code = _failure_code(error)
                logger.warning(
                    "embedding_switch_failed job_id=%s state=%s code=%s error_type=%s",
                    job_id,
                    state,
                    code,
                    type(error).__name__,
                )
                await asyncio.to_thread(
                    self.jobs.update,
                    job_id,
                    state="failed",
                    error_code=code,
                    error_detail=type(error).__name__,
                )
                continue
            await asyncio.to_thread(self._flush, job_id)
            await asyncio.to_thread(self.jobs.update, job_id, state=next_state)

    def _cancelled(self, job_id: str) -> bool:
        live = self._live.get(job_id)
        if live is not None and live.cancel:
            return True
        row = self.jobs.get(job_id)
        cancelled = bool(row is not None and row["cancel_requested"])
        if cancelled and live is not None:
            live.cancel = True
        return cancelled

    # -- preparing -------------------------------------------------------

    async def _prepare(self, row: sqlite3.Row) -> None:
        self._set_progress(row["job_id"], phase="prepare")
        if row["kind"] == "switch":
            await asyncio.to_thread(self._check_target, row)
        deadline = time.monotonic() + self.drain_timeout_s
        while not await self.drain_idle():
            if self._cancelled(row["job_id"]):
                raise ReembedCancelledError("embedding_switch_cancelled")
            if time.monotonic() >= deadline:
                raise EmbeddingSwitchError("writes_did_not_drain")
            await asyncio.sleep(0.5)

    def _check_target(self, row: sqlite3.Row) -> None:
        profile = self.profiles.get(row["target_profile_id"], self.secrets)
        if profile is None or profile.revision != row["target_revision"]:
            raise EmbeddingSwitchError("target_profile_changed")

    # -- reembedding -----------------------------------------------------

    async def _reembed(self, row: sqlite3.Row) -> None:
        job_id = row["job_id"]
        source, target, strays = await asyncio.to_thread(self._job_spaces, row)
        embedding = await asyncio.to_thread(self._embedding_target, target, row)
        target_physical = self._physical(target)
        if target_physical.dims is None:
            raise EmbeddingSwitchError("embedding_dimensions_unknown")
        sources: list[PhysicalSpace] = []
        if row["kind"] == "switch":
            # The live space first, so its records win over any stray's.
            sources.append(self._physical(source))
        sources.extend(self._physical(stray) for stray in strays)
        scanned = await self.backend.scan()
        totals = []
        for item in sources:
            found = next(
                (
                    scan
                    for scan in scanned
                    if scan.key == (item.collection_base, item.name_dims or 0)
                ),
                None,
            )
            totals.append(found.vectors if found else 0)
        live = self._live[job_id]
        grand_total = sum(totals)
        live.base_done = 0
        live.started = time.monotonic()
        for index, item in enumerate(sources):
            self._set_progress(
                job_id,
                phase="vectors",
                source_index=index + 1,
                sources=len(sources),
                total=grand_total,
                done=live.base_done,
            )
            await self._reembed_one(job_id, item, target_physical, embedding, grand_total)
            live.base_done += totals[index]
        if row["kind"] == "switch":
            await self._reembed_graph(job_id, source, target, embedding)
        self._set_progress(job_id, phase="verify", done=grand_total, total=grand_total)

    async def _reembed_one(
        self,
        job_id: str,
        source: PhysicalSpace,
        target: PhysicalSpace,
        embedding: EmbeddingTarget,
        grand_total: int,
    ) -> None:
        live = self._live[job_id]
        role_done: dict[str, int] = {}
        counters: dict[str, tuple[int, int, int]] = {}

        def on_step(step: ReembedStep) -> None:
            role_done[step.role] = step.done
            counters[step.role] = (step.embedded, step.reused, step.kept)
            done = live.base_done + sum(role_done.values())
            embedded = sum(value[0] for value in counters.values())
            elapsed = max(time.monotonic() - live.started, 1e-3)
            rate = embedded / elapsed if embedded else None
            remaining = max(grand_total - done, 0)
            self._set_progress(
                job_id,
                phase="vectors",
                role=step.role,
                done=done,
                total=grand_total,
                embedded=embedded,
                reused=sum(value[1] for value in counters.values()),
                kept=sum(value[2] for value in counters.values()),
                rate_per_s=round(rate, 1) if rate else None,
                eta_s=int(remaining / rate) if rate else None,
            )

        for attempt in range(REEMBED_ATTEMPTS):
            try:
                await self.backend.reembed(
                    source,
                    target,
                    embedding=embedding,
                    cancel=lambda: self._cancel_flag(job_id),
                    on_step=on_step,
                )
                return
            except (ReembedCancelledError, EmbeddingWidthError, ReembedUnavailableError):
                raise
            except Exception:
                if attempt + 1 >= REEMBED_ATTEMPTS:
                    raise
                delay = REEMBED_RETRY_SECONDS[min(attempt, len(REEMBED_RETRY_SECONDS) - 1)]
                logger.warning(
                    "embedding_switch_reembed_retry job_id=%s attempt=%d delay_s=%.0f",
                    job_id,
                    attempt + 1,
                    delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

    def _cancel_flag(self, job_id: str) -> bool:
        live = self._live.get(job_id)
        return bool(live and live.cancel)

    async def _reembed_graph(
        self,
        job_id: str,
        source: EmbeddingSpace,
        target: EmbeddingSpace,
        embedding: EmbeddingTarget,
    ) -> None:
        # Whatever the mode: a pro home can hold the graph of its ultra days,
        # and it would otherwise be dropped with the old space.
        source_graph = self._graph_path(source)
        if not _graph_has_files(source_graph):
            return
        target_dims = target.dims or target.name_dims
        if target_dims is None:
            raise EmbeddingSwitchError("embedding_dimensions_unknown")
        self._set_progress(job_id, phase="graph", role=None)
        release = await self._quiesce()
        try:
            await self.backend.reembed_graph(
                source_graph,
                self._graph_path(target),
                embedding=embedding,
                target_dims=target_dims,
                cancel=lambda: self._cancel_flag(job_id),
                on_step=lambda step: self._set_progress(job_id, phase="graph", role=step.role),
            )
        finally:
            release()

    async def _quiesce(self) -> Callable[[], None]:
        """Close every SDK client so the graph can be read; reads wait meanwhile.

        Only ultra clients hold the graph, so other modes keep serving reads.
        """
        if self.quiesce_clients is None or not self.graph_enabled():
            return lambda: None
        return await self.quiesce_clients()

    # -- verifying -------------------------------------------------------

    async def _verify(self, row: sqlite3.Row) -> None:
        job_id = row["job_id"]
        self._set_progress(job_id, phase="verify")
        source, target, strays = await asyncio.to_thread(self._job_spaces, row)
        target_physical = self._physical(target)
        problems: list[str] = []
        report: dict[str, Any] = {"strays": {}}
        for stray in strays:
            missing = await self.backend.diff(self._physical(stray), target_physical)
            report["strays"][stray.space_id] = missing
            if sum(missing.values()):
                problems.append(f"stray {stray.collection_base}: {sum(missing.values())} missing")
        if row["kind"] == "switch":
            embedding = await asyncio.to_thread(self._embedding_target, target, row)
            source_physical = self._physical(source)
            check = await self.backend.verify(source_physical, target_physical, embedding=embedding)
            report.update(
                {
                    "per_role": {key: dict(value) for key, value in check.per_role.items()},
                    "per_user": dict(check.per_user),
                    "warnings": list(check.warnings),
                    "self_recall": [check.self_recall_hits, check.self_recall_samples],
                }
            )
            if not strays:
                problems.extend(check.problems)
            else:
                # Strays add ids the source does not have; only a lost,
                # changed or mis-sized record is a failure.
                for role, values in check.per_role.items():
                    for label in ("missing", "mismatched", "bad_dims"):
                        if values.get(label):
                            problems.append(f"{role}: {values[label]} {label}")
                scanned = await self.backend.scan(per_user=True)
                live_scan = _find(scanned, source)
                for user, count in (live_scan.per_user if live_scan else {}).items():
                    if check.per_user.get(user, 0) < count:
                        problems.append(f"user {user!r}: target holds fewer records")
            if _graph_has_files(self._graph_path(source)):
                dims = target.dims or target.name_dims or 0
                # Reads are served between the copy and here: a client may
                # hold the graph again, and the SDK refuses to open it then.
                release = await self._quiesce()
                try:
                    graph = await self.backend.verify_graph(
                        self._graph_path(source), self._graph_path(target), target_dims=dims
                    )
                finally:
                    release()
                report["graph"] = {"ok": graph.ok, "problems": list(graph.problems)}
                problems.extend(f"graph: {item}" for item in graph.problems)
        report["problems"] = problems
        await asyncio.to_thread(
            self.jobs.update, job_id, verification_json=json.dumps(report, sort_keys=True)
        )
        if problems:
            raise EmbeddingSwitchError("embedding_verification_failed")

    # -- committing ------------------------------------------------------

    async def _commit(self, row: sqlite3.Row) -> None:
        job_id = row["job_id"]
        self._set_progress(job_id, phase="commit")
        await asyncio.to_thread(self._flush, job_id)
        await asyncio.to_thread(self.jobs.update, job_id, state="committing")
        try:
            await asyncio.to_thread(self._commit_transaction, job_id)
        except Exception as error:
            await asyncio.to_thread(
                self.jobs.update,
                job_id,
                state="failed",
                error_code=_failure_code(error),
                error_detail=type(error).__name__,
            )
            return
        self._open_cache = False
        job = self.get_job(job_id)
        logger.info("embedding_switch_committed job_id=%s kind=%s", job_id, row["kind"])
        if job is not None and self.on_commit is not None:
            try:
                await self.on_commit(job)
            except Exception:  # noqa: BLE001 - the commit stands; the hooks are follow-ups
                logger.warning("embedding_switch_post_commit_failed", exc_info=True)

    def _commit_transaction(self, job_id: str) -> None:
        """One transaction: the new space live, the old one retired, the profile active.

        A failure anywhere rolls back all of it; nothing outside SQLite is
        touched, because no collection is renamed at commit.
        """
        now = _now()
        before = self.jobs.get(job_id)
        profile = (
            self.profiles.snapshot(before["target_profile_id"], before["target_revision"])
            if before is not None and before["kind"] == "switch"
            else None
        )
        with self.spaces.transaction() as connection:
            previous = connection.row_factory
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    "SELECT * FROM embedding_switch_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            finally:
                connection.row_factory = previous
            if row is None or row["state"] != "committing":
                raise EmbeddingSwitchError("embedding_switch_state_changed")
            if not row["verification_json"]:
                raise EmbeddingSwitchError("embedding_verification_missing")
            for stray_id in json.loads(row["stray_space_ids"] or "[]"):
                set_state_in(connection, stray_id, state="retired", expected=("stray",), now=now)
            if row["kind"] == "switch":
                if profile is None:
                    raise EmbeddingSwitchError("embedding_switch_state_changed")
                current = connection.execute(
                    "SELECT revision, secret_ref FROM model_profiles WHERE profile_id=?",
                    (row["target_profile_id"],),
                ).fetchone()
                if current is None or current[0] != row["target_revision"]:
                    raise EmbeddingSwitchError("target_profile_changed")
                set_state_in(
                    connection, row["source_space_id"], state="retired", expected=("live",), now=now
                )
                set_state_in(
                    connection,
                    row["target_space_id"],
                    state="live",
                    expected=("building",),
                    now=now,
                )
                ModelProfileStore.activate_in_connection(
                    connection, profile, expected_secret_ref=current[1]
                )
            connection.execute(
                "UPDATE embedding_switch_jobs SET state='done', finished_at=?, updated_at=?, "
                "progress_json=? WHERE job_id=?",
                (
                    now.isoformat(),
                    now.isoformat(),
                    self._final_progress(job_id).model_dump_json(),
                    job_id,
                ),
            )
        self.spaces.invalidate()

    def _final_progress(self, job_id: str) -> SwitchProgress:
        live = self._live.get(job_id)
        progress = live.progress if live is not None else SwitchProgress()
        return progress.model_copy(
            update={"phase": "done", "percent": 100.0, "eta_s": 0, "updated_at": _now().isoformat()}
        )

    # -- rollback --------------------------------------------------------

    async def _rollback(self, row: sqlite3.Row) -> None:
        """Drop what the job built. The source was never written; it stays live."""
        job_id = row["job_id"]
        self._set_progress(job_id, phase="rollback")
        if row["kind"] == "switch":
            target = await asyncio.to_thread(self.spaces.get, row["target_space_id"])
            if target is not None and target.state == "building":
                try:
                    await self.backend.drop(self._physical(target))
                    if _graph_has_files(self._graph_path(target)):
                        await self.backend.drop_graph(self._graph_path(target))
                except ReembedUnavailableError:
                    pass
                except Exception:  # noqa: BLE001 - the next start's scan merges and retires it
                    logger.warning("embedding_switch_rollback_drop_failed", exc_info=True)
                await asyncio.to_thread(self.spaces.mark_dropped, target.space_id)
        now = _now().isoformat()
        await asyncio.to_thread(self.jobs.update, job_id, state="rolled_back", finished_at=now)
        self._open_cache = False
        logger.info("embedding_switch_rolled_back job_id=%s code=%s", job_id, row["error_code"])

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def start_retention(self, *, interval_s: float = 24 * 60 * 60) -> None:
        if self._retention is None or self._retention.done():
            self._retention = asyncio.get_running_loop().create_task(
                self._retention_loop(interval_s), name="one-memory-embedding-retention"
            )

    async def _retention_loop(self, interval_s: float) -> None:
        while True:
            try:
                dropped = await self.sweep_retired()
                if dropped:
                    logger.info("embedding_retired_spaces_dropped count=%d", len(dropped))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - tried again tomorrow
                logger.warning("embedding_retention_sweep_failed", exc_info=True)
            await asyncio.sleep(interval_s)

    async def sweep_retired(self, *, now: datetime | None = None) -> list[str]:
        """Drop every retired space past its keep-until date. Never the live one."""
        if self.backend.unavailable_reason() is not None:
            return []
        dropped: list[str] = []
        for space in await asyncio.to_thread(self.spaces.expired, now=now):
            await self.drop_space(space.space_id)
            dropped.append(space.space_id)
        return dropped

    async def drop_space(self, space_id: str) -> EmbeddingSpace:
        """Delete a retired space's collections and graph now (the user's cleanup)."""
        space = await asyncio.to_thread(self.spaces.get, space_id)
        if space is None or space.state == "dropped":
            raise EmbeddingSwitchError("embedding_space_not_found")
        if space.state != "retired":
            raise EmbeddingSwitchError("embedding_space_not_retired")
        await self.backend.drop(self._physical(space))
        live = await asyncio.to_thread(self.spaces.live)
        if space.graph_dir and (live is None or live.graph_dir != space.graph_dir):
            graph = self._graph_path(space)
            if _graph_has_files(graph):
                await self.backend.drop_graph(graph)
        await asyncio.to_thread(self.spaces.mark_dropped, space_id)
        dropped = await asyncio.to_thread(self.spaces.get, space_id)
        assert dropped is not None
        return dropped

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _job_spaces(
        self, row: sqlite3.Row
    ) -> tuple[EmbeddingSpace, EmbeddingSpace, list[EmbeddingSpace]]:
        source = self.spaces.get(row["source_space_id"])
        target = self.spaces.get(row["target_space_id"])
        if source is None or target is None:
            raise EmbeddingSwitchError("embedding_space_not_found")
        strays = []
        for space_id in json.loads(row["stray_space_ids"] or "[]"):
            stray = self.spaces.get(space_id)
            if stray is not None and stray.state == "stray":
                strays.append(stray)
        return source, target, strays

    def _physical(self, space: EmbeddingSpace) -> PhysicalSpace:
        return PhysicalSpace.of(space)

    def _graph_path(self, space: EmbeddingSpace) -> Path:
        return self.root / (space.graph_dir or DEFAULT_GRAPH_DIR)

    def embedding_profile(self, space: EmbeddingSpace) -> tuple[ModelProfileSnapshot, bool]:
        """The profile version whose embedding fills `space`, and whether it is active."""
        from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

        identity = self.profiles.active_identity()
        if identity is not None and space.identity is not None:
            active = self.profiles.snapshot(*identity)
            if embedding_identity(active).matches(space.identity):
                return active, True
        if space.profile_id is not None and space.profile_revision is not None:
            try:
                return self.profiles.snapshot(space.profile_id, space.profile_revision), False
            except KeyError:
                pass
        return builtin_retrieval_snapshot(), False

    def _embedding_target(self, space: EmbeddingSpace, row: sqlite3.Row) -> EmbeddingTarget:
        if row["kind"] == "switch":
            profile = self.profiles.snapshot(row["target_profile_id"], row["target_revision"])
        else:
            profile = self.embedding_profile(space)[0]
        key: str | None = None
        provider = profile.embedding_provider_kind or profile.provider_kind
        if provider is ProviderKind.OPENAI_COMPATIBLE:
            try:
                key = self.profiles.resolve_snapshot_secret(
                    profile.profile_id, profile.revision, self.secrets
                )
            except SecretStoreUnavailableError:
                raise EmbeddingSwitchError("secure_storage_unavailable") from None
            if key is None:
                raise EmbeddingSwitchError("model_secret_unavailable")
        if space.dims is None and space.name_dims is None:
            identity = embedding_identity(profile)
            if identity.dims is not None:
                space = space.model_copy(update={"dims": identity.dims})
        return EmbeddingTarget(profile=profile, space=space, api_key=key)

    def _set_progress(self, job_id: str, **values: Any) -> None:
        live = self._live.get(job_id)
        if live is None:
            return
        update = {key: value for key, value in values.items() if key in SwitchProgress.model_fields}
        progress = live.progress.model_copy(update=update)
        if progress.total:
            progress = progress.model_copy(
                update={"percent": round(min(progress.done / progress.total, 1.0) * 100, 1)}
            )
        live.progress = progress.model_copy(update={"updated_at": _now().isoformat()})
        if time.monotonic() - live.last_write >= PROGRESS_WRITE_SECONDS:
            live.last_write = time.monotonic()
            try:
                self.jobs.update(job_id, progress_json=live.progress.model_dump_json())
            except Exception:  # noqa: BLE001 - progress is advisory
                logger.debug("embedding_switch_progress_write_failed", exc_info=True)

    def _flush(self, job_id: str) -> None:
        live = self._live.get(job_id)
        if live is not None:
            self.jobs.update(job_id, progress_json=live.progress.model_dump_json())


async def _always_idle() -> bool:
    return True


def _find(scanned: tuple[ScannedSpace, ...], space: EmbeddingSpace) -> ScannedSpace | None:
    key = (space.collection_base, space.name_dims or 0)
    return next((scan for scan in scanned if scan.key == key), None)


def _summary(space: EmbeddingSpace | None, space_id: str) -> SpaceSummary:
    if space is None:
        return SpaceSummary(space_id=space_id, model=None, dims=None, collection_base="")
    return SpaceSummary(
        space_id=space.space_id,
        model=space.model,
        dims=space.dims if space.dims is not None else space.name_dims,
        collection_base=space.collection_base,
        vectors=space.vectors,
    )


def _identity_key(profile: ModelProfileSnapshot) -> dict[str, object]:
    identity = embedding_identity(profile)
    return {
        "profile_id": profile.profile_id,
        "revision": profile.revision,
        "model": identity.model,
        "dims": identity.dims,
    }


def _token(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _free_bytes(path: Path) -> int | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _graph_has_files(path: Path) -> bool:
    return path.exists()


def _failure_code(error: BaseException) -> str:
    if isinstance(error, ReembedCancelledError):
        return "embedding_switch_cancelled"
    if isinstance(error, EmbeddingWidthError):
        return "embedding_dimensions_mismatch"
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(error, EmbeddingSpaceError):
        return error.code
    return "embedding_service_failed"


def default_reembed_backend(data_dir: Path) -> ReembedBackend:
    """The SDK-backed re-embed functions for this home (tests replace this)."""
    from one_memory.hy_sdk_migration import SdkReembedBackend

    return SdkReembedBackend(data_dir)


def _owned_elsewhere(row: sqlite3.Row, pid: int) -> bool:
    """Whether another process that is alive beat for this job within a minute."""
    owner = row["owner_pid"]
    if owner is None or int(owner) == pid or not row["heartbeat_at"]:
        return False
    beat = datetime.fromisoformat(row["heartbeat_at"])
    if _now() - beat > STALE_HEARTBEAT:
        return False
    try:
        os.kill(int(owner), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
