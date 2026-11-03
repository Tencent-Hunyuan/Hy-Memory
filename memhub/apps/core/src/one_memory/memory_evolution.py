"""System 2 for ultra: evolve runs, their receipts, the nightly schedule, graph backfill.

hy-memory 2.0.9's `client.evolve(user_id, agent_id, max_batches)` is ultra
only, holds no lock, and has no scheduler: MemHub owns all three.

- A run is asynchronous: `start` answers a receipt at once and the run goes
  on in the background (evolve makes model calls across the user's whole
  library and can take minutes, longer than any HTTP timeout). One run per
  user at a time: asking again while one runs returns that run. A run holds
  the write coordinator's per-user exclusivity, so it never overlaps that
  user's write batch, and loops while the SDK answers `should_continue`, up
  to `EVOLVE_MAX_ROUNDS`.
- The schedule runs every user that has memories once a day at the
  configured local time, and catches up after the machine slept through it.
- Switching to ultra backfills each user's existing facts into the graph
  once (`build_memory_graph`).

What is kept on disk is counts and stable codes only, never memory text:
`<data_dir>/evolve-state.json`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from one_memory.diagnostic_log import diagnostic_event
from one_memory.memory_server.config_state import EvolveSchedule
from one_memory.memory_server.durable_queue import safe_error_code
from one_memory.sdk_write_processor import PRODUCT_AGENT_ID

logger = logging.getLogger(__name__)

#: Rounds one run makes while the SDK says there is more to do.
EVOLVE_MAX_ROUNDS = 20
#: How often the schedule looks at the clock; also bounds catch-up after wake.
SCHEDULE_CHECK_SECONDS = 300.0
#: Receipts kept in memory for `GET /api/v2/evolve/{run_id}`.
_KEPT_RUNS = 64
_STABLE_CODE = re.compile(r"[a-z][a-z0-9_]{0,95}")
_COUNTS = (
    "remaining_clusters",
    "remaining_actionable_clusters",
    "remaining_fresh_facts",
)

RunState = Literal["running", "succeeded", "failed"]
Trigger = Literal["manual", "scheduled"]


class EvolveModeError(RuntimeError):
    """Evolve asked for outside ultra."""

    def __init__(self) -> None:
        super().__init__("memory_mode_not_ultra")


class EvolveRunReceipt(BaseModel):
    """One evolve run. Counts and codes; `results` only while in memory."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    user_id: str
    agent_id: str
    max_batches: int | None = None
    trigger: Trigger = "manual"
    state: RunState = "running"
    started_at: datetime
    finished_at: datetime | None = None
    rounds: int = 0
    tasks_processed: int = 0
    batch_failures: int = 0
    cycle_status: str | None = None
    should_continue: bool | None = None
    continuation_reason: str | None = None
    remaining_clusters: int | None = None
    remaining_actionable_clusters: int | None = None
    remaining_fresh_facts: int | None = None
    #: True when the round cap stopped a run the SDK would have continued.
    capped: bool = False
    elapsed_ms: float = 0.0
    error_code: str | None = None
    failure_kind: str | None = None
    #: The last round's SDK `request_id`, for matching SDK logs.
    request_id: str | None = None
    #: The last round's SDK `results` and `cross_domain_sweeper`. Kept only
    #: in this receipt, never written to disk or diagnostics.
    results: Any = None
    cross_domain_sweeper: Any = None

    def summary(self) -> dict[str, object]:
        """What is persisted and shown in status: no SDK result bodies."""
        return self.model_dump(mode="json", exclude={"results", "cross_domain_sweeper"})


class UserEvolveState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_run_at: datetime | None = None
    last_result: dict[str, object] | None = None
    last_error: str | None = None


class GraphBuildUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["pending", "running", "succeeded", "failed"] = "pending"
    memories_scanned: int | None = None
    nodes: int | None = None
    error_code: str | None = None


class GraphBuildState(BaseModel):
    """The one-time backfill of existing facts into the graph after → ultra."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["idle", "running", "succeeded", "failed"] = "idle"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    users: dict[str, GraphBuildUser] = Field(default_factory=dict)


class EvolveStateDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    users: dict[str, UserEvolveState] = Field(default_factory=dict)
    #: When the schedule first ran in ultra: the "last run" of a user that
    #: never ran, so enabling ultra at noon does not evolve everyone at once.
    schedule_anchor: datetime | None = None
    graph_build: GraphBuildState = Field(default_factory=GraphBuildState)


class EvolveStateStore:
    """The only writer of `<data_dir>/evolve-state.json`."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "evolve-state.json"
        self._lock = threading.RLock()
        self._document = self._read()

    def document(self) -> EvolveStateDocument:
        with self._lock:
            return self._document.model_copy(deep=True)

    def user(self, user_id: str) -> UserEvolveState:
        with self._lock:
            return self._document.users.get(user_id, UserEvolveState()).model_copy()

    def record_run(self, receipt: EvolveRunReceipt) -> None:
        with self._lock:
            users = dict(self._document.users)
            users[receipt.user_id] = UserEvolveState(
                last_run_at=receipt.started_at,
                last_result=receipt.summary(),
                last_error=receipt.error_code,
            )
            self._save(self._document.model_copy(update={"users": users}))

    def anchor(self, now: datetime) -> datetime:
        """The schedule anchor, set to `now` the first time it is asked."""
        with self._lock:
            if self._document.schedule_anchor is None:
                self._save(self._document.model_copy(update={"schedule_anchor": now}))
            assert self._document.schedule_anchor is not None
            return self._document.schedule_anchor

    def graph_build(self) -> GraphBuildState:
        with self._lock:
            return self._document.graph_build.model_copy(deep=True)

    def set_graph_build(self, state: GraphBuildState) -> None:
        with self._lock:
            self._save(self._document.model_copy(update={"graph_build": state}))

    def _save(self, document: EvolveStateDocument) -> None:
        payload = json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".evolve-state.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            # Status is best-effort: a run must not fail because its record did.
            logger.warning("evolve_state_write_failed")
        self._document = document

    def _read(self) -> EvolveStateDocument:
        try:
            return EvolveStateDocument.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return EvolveStateDocument()
        except (OSError, ValueError):
            # A damaged status file only loses history; start a fresh one.
            logger.warning("evolve_state_unreadable")
            return EvolveStateDocument()


class EvolveCoordinator(Protocol):
    """The write coordinator surface evolve and the graph backfill use."""

    def exclusive_for_evolve(self, user_id: str) -> Any: ...

    def use_client(self) -> Any: ...


class EvolveService:
    """Start, serialize and record evolve runs."""

    def __init__(
        self,
        *,
        coordinator: EvolveCoordinator,
        state: EvolveStateStore,
        memory_mode: Callable[[], str],
        max_rounds: int = EVOLVE_MAX_ROUNDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.coordinator = coordinator
        self.state = state
        self.memory_mode = memory_mode
        self.max_rounds = max_rounds
        self._now = now
        self._runs: dict[str, EvolveRunReceipt] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._active: dict[str, str] = {}

    def start(
        self,
        user_id: str,
        *,
        agent_id: str | None = None,
        max_batches: int | None = None,
        trigger: Trigger = "manual",
    ) -> EvolveRunReceipt:
        """Start a run, or return the one already running for this user."""
        if self.memory_mode() != "ultra":
            raise EvolveModeError()
        running = self._active.get(user_id)
        if running is not None:
            return self._runs[running]
        receipt = EvolveRunReceipt(
            run_id=str(uuid4()),
            user_id=user_id,
            agent_id=agent_id or PRODUCT_AGENT_ID,
            max_batches=max_batches,
            trigger=trigger,
            started_at=self._now(),
        )
        self._remember(receipt)
        self._active[user_id] = receipt.run_id
        self._tasks[receipt.run_id] = asyncio.create_task(
            self._run(receipt.run_id), name="one-memory-evolve"
        )
        return receipt

    def get(self, run_id: str) -> EvolveRunReceipt | None:
        return self._runs.get(run_id)

    def running(self) -> tuple[EvolveRunReceipt, ...]:
        return tuple(self._runs[run_id] for run_id in self._active.values())

    async def wait(self, run_id: str) -> EvolveRunReceipt | None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return self._runs.get(run_id)

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _remember(self, receipt: EvolveRunReceipt) -> None:
        self._runs[receipt.run_id] = receipt
        while len(self._runs) > _KEPT_RUNS:
            oldest = next(iter(self._runs))
            if oldest in self._active.values():
                break
            del self._runs[oldest]

    async def _run(self, run_id: str) -> None:
        receipt = self._runs[run_id]
        diagnostic_event(
            level="info",
            component="memory.evolve",
            event="memory_evolve.started",
            outcome="started",
            details={"trigger": receipt.trigger, "max_batches": receipt.max_batches},
        )
        try:
            async with (
                self.coordinator.exclusive_for_evolve(receipt.user_id),
                self.coordinator.use_client() as client,
            ):
                while receipt.rounds < self.max_rounds:
                    result = await asyncio.to_thread(
                        client.evolve,
                        receipt.user_id,
                        agent_id=receipt.agent_id,
                        max_batches=receipt.max_batches,
                    )
                    receipt = _folded(receipt, result)
                    self._runs[run_id] = receipt
                    if receipt.state == "failed" or not receipt.should_continue:
                        break
                else:
                    receipt = receipt.model_copy(update={"capped": True})
            if receipt.state == "running":
                receipt = receipt.model_copy(update={"state": "succeeded"})
        except asyncio.CancelledError:
            receipt = receipt.model_copy(
                update={"state": "failed", "error_code": "memory_evolve_cancelled"}
            )
            raise
        except Exception as error:  # noqa: BLE001 - recorded as a stable code
            code = safe_error_code(error)
            receipt = receipt.model_copy(
                update={
                    "state": "failed",
                    "error_code": _stable(code, "hy_memory_evolve_failed"),
                    "failure_kind": type(error).__name__,
                }
            )
        finally:
            receipt = receipt.model_copy(update={"finished_at": self._now()})
            self._runs[run_id] = receipt
            self._active.pop(receipt.user_id, None)
            self._tasks.pop(run_id, None)
            await asyncio.to_thread(self.state.record_run, receipt)
            diagnostic_event(
                level="info" if receipt.state == "succeeded" else "warning",
                component="memory.evolve",
                event="memory_evolve.finished",
                outcome="succeeded" if receipt.state == "succeeded" else "failed",
                details={
                    "trigger": receipt.trigger,
                    "rounds": receipt.rounds,
                    "tasks_processed": receipt.tasks_processed,
                    "batch_failures": receipt.batch_failures,
                    "capped": receipt.capped,
                    "elapsed_ms": round(receipt.elapsed_ms, 1),
                    "error_code": receipt.error_code,
                },
            )


def _folded(receipt: EvolveRunReceipt, result: object) -> EvolveRunReceipt:
    """One more evolve round's result added to the run."""
    if not isinstance(result, Mapping):
        return receipt.model_copy(
            update={"state": "failed", "error_code": "hy_memory_evolve_shape_unknown"}
        )
    failures = result.get("batch_failures")
    update: dict[str, object] = {
        "rounds": receipt.rounds + 1,
        "tasks_processed": receipt.tasks_processed + _int(result.get("tasks_processed")),
        "batch_failures": receipt.batch_failures
        + (len(failures) if isinstance(failures, list | tuple) else _int(failures)),
        "elapsed_ms": receipt.elapsed_ms + _float(result.get("elapsed_ms")),
        "should_continue": result.get("should_continue") is True,
        "cycle_status": _text(result.get("cycle_status")),
        "continuation_reason": _text(result.get("continuation_reason")),
        "request_id": _text(result.get("request_id")),
        "results": result.get("results"),
        "cross_domain_sweeper": result.get("cross_domain_sweeper"),
    }
    for name in _COUNTS:
        value = result.get(name)
        update[name] = value if isinstance(value, int) and not isinstance(value, bool) else None
    if result.get("success") is not True:
        code = result.get("error_code")
        kind = result.get("failure_kind")
        update.update(
            state="failed",
            should_continue=False,
            error_code=(
                code
                if isinstance(code, str) and _STABLE_CODE.fullmatch(code)
                else "hy_memory_evolve_rejected"
            ),
            failure_kind=kind if isinstance(kind, str) and _STABLE_CODE.fullmatch(kind) else None,
        )
    return receipt.model_copy(update=update)


def _stable(code: str, fallback: str) -> str:
    return code if _STABLE_CODE.fullmatch(code) else fallback


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _float(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _text(value: object) -> str | None:
    return value[:128] if isinstance(value, str) else None


def evolve_due(
    now: datetime,
    schedule: EvolveSchedule,
    last_run_at: datetime | None,
    anchor: datetime,
) -> bool:
    """Whether a user's nightly evolve is due at `now` (aware, local time).

    Due once the day's slot (`schedule.time`, local) has passed and the last
    run (or, for a user who never ran, the anchor) is older than that slot.
    A last run more than 24 hours old is due at once as well: a machine asleep
    at the slot catches up when it wakes, even before the next day's slot.
    """
    if not schedule.enabled:
        return False
    local = now.astimezone()
    hour, minute = (int(part) for part in schedule.time.split(":"))
    slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    reference = last_run_at or anchor
    if local >= slot and reference < slot:
        return True
    return last_run_at is not None and local - last_run_at > timedelta(hours=24)


class EvolveScheduler:
    """Run evolve nightly for every user that has memories (ultra only)."""

    def __init__(
        self,
        *,
        service: EvolveService,
        schedule: Callable[[], EvolveSchedule],
        users_with_memories: Callable[[], Awaitable[Iterable[str]]],
        lifecycle_lock: asyncio.Lock | None = None,
        lifecycle_open: Callable[[], bool] = lambda: True,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        check_seconds: float = SCHEDULE_CHECK_SECONDS,
    ) -> None:
        self.service = service
        self.schedule = schedule
        self.users_with_memories = users_with_memories
        self.lifecycle_lock = lifecycle_lock
        self.lifecycle_open = lifecycle_open
        self._now = now
        self.check_seconds = check_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="one-memory-evolve-schedule")

    async def tick(self) -> tuple[str, ...]:
        """Run every due user once, one after another; the users run."""
        schedule = self.schedule()
        if not schedule.enabled or self.service.memory_mode() != "ultra":
            return ()
        now = self._now()
        anchor = await asyncio.to_thread(self.service.state.anchor, now)
        ran: list[str] = []
        for user_id in await self.users_with_memories():
            last = self.service.state.user(user_id).last_run_at
            if not evolve_due(now, schedule, last, anchor):
                continue
            try:
                if self.lifecycle_lock is None:
                    if not self.lifecycle_open():
                        break
                    receipt = self.service.start(user_id, trigger="scheduled")
                else:
                    async with self.lifecycle_lock:
                        if not self.lifecycle_open():
                            break
                        receipt = self.service.start(user_id, trigger="scheduled")
            except EvolveModeError:
                break
            await self.service.wait(receipt.run_id)
            ran.append(user_id)
        if ran:
            diagnostic_event(
                level="info",
                component="memory.evolve",
                event="memory_evolve.schedule_ran",
                outcome="succeeded",
                details={"users": len(ran)},
            )
        return tuple(ran)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the loop must survive
                logger.warning("evolve_schedule_tick_failed error_type=%s", type(error).__name__)
            await asyncio.sleep(self.check_seconds)

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None


class MemoryGraphBackfill:
    """After a switch to ultra, project each user's existing facts into the graph once."""

    def __init__(
        self,
        *,
        coordinator: EvolveCoordinator,
        state: EvolveStateStore,
        lifecycle_open: Callable[[], bool] = lambda: True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.coordinator = coordinator
        self.state = state
        self.lifecycle_open = lifecycle_open
        self._now = now
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, user_ids: Iterable[str]) -> GraphBuildState:
        if self.running:
            return self.state.graph_build()
        users = tuple(dict.fromkeys(user_ids))
        state = GraphBuildState(
            state="running",
            started_at=self._now(),
            users={user_id: GraphBuildUser() for user_id in users},
        )
        self.state.set_graph_build(state)
        self._task = asyncio.create_task(self._run(users), name="one-memory-graph-backfill")
        return state

    def recover(self, user_ids: Iterable[str]) -> GraphBuildState:
        """Resume an interrupted backfill without repeating completed users."""
        if self.running:
            return self.state.graph_build()
        saved = self.state.graph_build()
        if saved.state != "running":
            return saved
        users = tuple(dict.fromkeys((*saved.users.keys(), *user_ids)))
        pending = tuple(
            user_id
            for user_id in users
            if saved.users.get(user_id, GraphBuildUser()).state != "succeeded"
        )
        resumed = saved.model_copy(deep=True)
        for user_id in pending:
            resumed.users[user_id] = GraphBuildUser()
        if not pending:
            resumed.state = "succeeded"
            resumed.finished_at = self._now()
            self.state.set_graph_build(resumed)
            return resumed
        resumed.state = "running"
        resumed.finished_at = None
        self.state.set_graph_build(resumed)
        self._task = asyncio.create_task(
            self._run(pending), name="one-memory-graph-backfill-recovery"
        )
        return resumed

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _run(self, users: tuple[str, ...]) -> None:
        state = self.state.graph_build()
        for user_id in users:
            while not self.lifecycle_open():
                await asyncio.sleep(0.2)
            state.users[user_id] = GraphBuildUser(state="running")
            await asyncio.to_thread(self.state.set_graph_build, state.model_copy(deep=True))
            try:
                async with (
                    self.coordinator.exclusive_for_evolve(user_id),
                    self.coordinator.use_client() as client,
                ):
                    result = await asyncio.to_thread(
                        client.build_memory_graph,
                        user_id,
                        agent_id=None,
                        limit=10000,
                        clear_first=False,
                    )
                ok = isinstance(result, Mapping) and result.get("success") is True
                state.users[user_id] = GraphBuildUser(
                    state="succeeded" if ok else "failed",
                    memories_scanned=_int(result.get("memories_scanned"))
                    if isinstance(result, Mapping)
                    else None,
                    nodes=_int(result.get("nodes")) if isinstance(result, Mapping) else None,
                    error_code=None if ok else "hy_memory_graph_build_rejected",
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - recorded as a stable code
                code = safe_error_code(error)
                state.users[user_id] = GraphBuildUser(
                    state="failed",
                    error_code=_stable(code, "hy_memory_graph_build_failed"),
                )
        failed = any(user.state == "failed" for user in state.users.values())
        state.state = "failed" if failed else "succeeded"
        state.finished_at = self._now()
        await asyncio.to_thread(self.state.set_graph_build, state)
        diagnostic_event(
            level="warning" if failed else "info",
            component="memory.evolve",
            event="memory_graph_backfill.finished",
            outcome="failed" if failed else "succeeded",
            details={
                "users": len(state.users),
                "failed_users": sum(user.state == "failed" for user in state.users.values()),
            },
        )

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
