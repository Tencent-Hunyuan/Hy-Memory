"""Shared admission policy for interactive, realtime, and background work."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from one_memory.diagnostic_log import diagnostic_event

_QUIET_ADMISSION_SUMMARY_SECONDS = 300.0


class ServiceLevel(IntEnum):
    INTERACTIVE = 0
    REALTIME = 1
    BACKGROUND = 2


class ServiceResource(StrEnum):
    IMPORT = "import"
    MODEL = "model"
    INDEX = "index"
    INTERACTIVE_READ = "interactive_read"


_CURRENT_LEVEL: ContextVar[ServiceLevel] = ContextVar(
    "one_memory_service_level",
    default=ServiceLevel.REALTIME,
)


def current_service_level() -> ServiceLevel:
    return _CURRENT_LEVEL.get()


@contextmanager
def service_level_context(level: ServiceLevel) -> Iterator[None]:
    token = _CURRENT_LEVEL.set(level)
    try:
        yield
    finally:
        _CURRENT_LEVEL.reset(token)


@dataclass(frozen=True, slots=True)
class ResourceLimit:
    capacity: int
    background_capacity: int

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("service_level_capacity_must_be_positive")
        if not 0 <= self.background_capacity <= self.capacity:
            raise ValueError("service_level_background_capacity_invalid")


@dataclass(frozen=True, slots=True)
class ServiceLevelSnapshot:
    active: dict[str, dict[str, int]]
    waiting: dict[str, dict[str, int]]
    interactive_cooldown: bool


@dataclass(frozen=True, slots=True)
class _Waiter:
    sequence: int
    resource: ServiceResource
    level: ServiceLevel
    work_id: str
    scope_key: str | None
    queued_at: float


class ServiceLevelScheduler:
    """Priority admission shared by every expensive runtime subsystem.

    P0 is interactive work, P1 is a newly arrived turn, and P2 is catch-up.
    A P2 capacity is an unconditional ceiling, so background work cannot fill
    every slot and make a later P0/P1 request wait for a full task lifetime.
    Running work is cooperatively preempted at its next bounded checkpoint.
    """

    def __init__(
        self,
        limits: dict[ServiceResource, ResourceLimit] | None = None,
        *,
        interactive_cooldown_seconds: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interactive_cooldown_seconds < 0:
            raise ValueError("service_level_cooldown_invalid")
        self._limits = limits or {
            ServiceResource.IMPORT: ResourceLimit(capacity=2, background_capacity=1),
            ServiceResource.MODEL: ResourceLimit(capacity=32, background_capacity=31),
            ServiceResource.INDEX: ResourceLimit(capacity=1, background_capacity=1),
            ServiceResource.INTERACTIVE_READ: ResourceLimit(capacity=32, background_capacity=0),
        }
        self._cooldown_seconds = interactive_cooldown_seconds
        self._clock = clock
        self._condition = asyncio.Condition()
        self._sequence = 0
        self._waiters: list[_Waiter] = []
        self._active: dict[ServiceResource, list[_Waiter]] = {
            resource: [] for resource in self._limits
        }
        self._interactive_until = 0.0
        self._import_realtime_streak = 0
        self._quiet_admission_counts: dict[tuple[ServiceResource, ServiceLevel], int] = {}
        self._quiet_admission_queue_depths: dict[tuple[ServiceResource, ServiceLevel], int] = {}
        self._quiet_admission_logged_at: dict[tuple[ServiceResource, ServiceLevel], float] = {}

    @asynccontextmanager
    async def admit(
        self,
        resource: ServiceResource,
        level: ServiceLevel,
        *,
        work_id: str,
        scope_key: str | None = None,
    ) -> AsyncIterator[None]:
        waiter = await self._acquire(resource, level, work_id=work_id, scope_key=scope_key)
        token = _CURRENT_LEVEL.set(level)
        try:
            yield
        finally:
            try:
                _CURRENT_LEVEL.reset(token)
            finally:
                await self._release(waiter)

    async def note_interactive(self, *, work_id: str, for_seconds: float = 0) -> None:
        async with (
            self.admit(
                ServiceResource.INTERACTIVE_READ,
                ServiceLevel.INTERACTIVE,
                work_id=work_id,
            ),
            self._condition,
        ):
            self._interactive_until = max(
                self._interactive_until,
                self._clock() + max(0, for_seconds),
            )
            self._condition.notify_all()

    def should_yield(self, resource: ServiceResource, level: ServiceLevel) -> bool:
        if level is not ServiceLevel.BACKGROUND:
            return False
        if self._clock() < self._interactive_until:
            return True
        return self._realtime_active() or any(
            waiter.resource is resource and waiter.level < level for waiter in self._waiters
        )

    def snapshot(self) -> ServiceLevelSnapshot:
        levels = tuple(ServiceLevel)
        resources = tuple(self._limits)
        return ServiceLevelSnapshot(
            active={
                resource.value: {
                    level.name.lower(): sum(item.level is level for item in self._active[resource])
                    for level in levels
                }
                for resource in resources
            },
            waiting={
                resource.value: {
                    level.name.lower(): sum(
                        item.resource is resource and item.level is level for item in self._waiters
                    )
                    for level in levels
                }
                for resource in resources
            },
            interactive_cooldown=self._clock() < self._interactive_until,
        )

    async def _acquire(
        self,
        resource: ServiceResource,
        level: ServiceLevel,
        *,
        work_id: str,
        scope_key: str | None,
    ) -> _Waiter:
        if resource not in self._limits:
            raise ValueError("service_level_resource_unknown")
        started = self._clock()
        async with self._condition:
            self._sequence += 1
            waiter = _Waiter(
                self._sequence,
                resource,
                level,
                work_id,
                scope_key,
                started,
            )
            self._waiters.append(waiter)
            if level is ServiceLevel.INTERACTIVE:
                self._interactive_until = max(
                    self._interactive_until,
                    started + self._cooldown_seconds,
                )
            try:
                while not self._can_admit(waiter):
                    timeout = self._cooldown_wait(waiter)
                    try:
                        if timeout is None:
                            await self._condition.wait()
                        else:
                            await asyncio.wait_for(self._condition.wait(), timeout=timeout)
                    except TimeoutError:
                        pass
            except BaseException:
                with suppress(ValueError):
                    self._waiters.remove(waiter)
                self._condition.notify_all()
                raise
            self._waiters.remove(waiter)
            self._active[resource].append(waiter)
            if resource is ServiceResource.IMPORT:
                if level is ServiceLevel.REALTIME:
                    self._import_realtime_streak += 1
                elif level is ServiceLevel.BACKGROUND:
                    self._import_realtime_streak = 0
        waited_ms = max(0, round((self._clock() - started) * 1000))
        self._record_admission(waiter, waited_ms=waited_ms, queue_depth=len(self._waiters))
        return waiter

    def _record_admission(
        self,
        waiter: _Waiter,
        *,
        waited_ms: int,
        queue_depth: int,
    ) -> None:
        details = {
            "mode": waiter.level.name.lower(),
            "phase": waiter.resource.value,
            "queue_depth": queue_depth,
        }
        key = (waiter.resource, waiter.level)
        now = self._clock()
        if waited_ms == 0:
            last = self._quiet_admission_logged_at.get(key)
            if last is not None and now - last < _QUIET_ADMISSION_SUMMARY_SECONDS:
                self._quiet_admission_counts[key] = self._quiet_admission_counts.get(key, 0) + 1
                self._quiet_admission_queue_depths[key] = max(
                    queue_depth,
                    self._quiet_admission_queue_depths.get(key, 0),
                )
                return
            suppressed = self._quiet_admission_counts.pop(key, 0)
            max_queue_depth = max(
                queue_depth,
                self._quiet_admission_queue_depths.pop(key, 0),
            )
            self._quiet_admission_logged_at[key] = now
            if last is not None:
                diagnostic_event(
                    level="debug",
                    component="service_level.scheduler",
                    event="service_level.summary",
                    outcome="succeeded",
                    details={
                        **details,
                        "queue_depth": max_queue_depth,
                        "count": suppressed + 1,
                    },
                )
                return
        diagnostic_event(
            level="debug",
            component="service_level.scheduler",
            event="service_level.admitted",
            outcome="succeeded",
            task_id=waiter.work_id,
            duration_ms=waited_ms,
            details={
                "mode": waiter.level.name.lower(),
                "phase": waiter.resource.value,
                "queue_depth": queue_depth,
            },
        )

    async def _release(self, waiter: _Waiter) -> None:
        async with self._condition:
            with suppress(ValueError):
                self._active[waiter.resource].remove(waiter)
            if waiter.level is ServiceLevel.INTERACTIVE:
                self._interactive_until = max(
                    self._interactive_until,
                    self._clock() + self._cooldown_seconds,
                )
            self._condition.notify_all()

    def _can_admit(self, waiter: _Waiter) -> bool:
        active = self._active[waiter.resource]
        limit = self._limits[waiter.resource]
        if len(active) >= limit.capacity:
            return False
        if waiter.scope_key is not None and any(
            item.scope_key == waiter.scope_key for item in active
        ):
            return False
        if waiter.level is ServiceLevel.BACKGROUND:
            active_background = sum(item.level is ServiceLevel.BACKGROUND for item in active)
            if active_background >= limit.background_capacity:
                return False
            if self._clock() < self._interactive_until:
                return False
            if self._realtime_active():
                return False
        waiting = [item for item in self._waiters if item.resource is waiter.resource]
        force_background = (
            waiter.resource is ServiceResource.IMPORT
            and self._import_realtime_streak >= 9
            and any(item.level is ServiceLevel.BACKGROUND for item in waiting)
        )
        eligible = sorted(
            waiting,
            key=lambda item: (
                0 if force_background and item.level is ServiceLevel.BACKGROUND else 1,
                item.level,
                item.sequence,
            ),
        )
        for candidate in eligible:
            if candidate.scope_key is not None and any(
                item.scope_key == candidate.scope_key for item in active
            ):
                continue
            return candidate is waiter
        return False

    def _realtime_active(self) -> bool:
        return any(
            item.level is ServiceLevel.REALTIME
            for active in self._active.values()
            for item in active
        )

    def _cooldown_wait(self, waiter: _Waiter) -> float | None:
        if waiter.level is not ServiceLevel.BACKGROUND:
            return None
        remaining = self._interactive_until - self._clock()
        return max(0.001, remaining) if remaining > 0 else None
