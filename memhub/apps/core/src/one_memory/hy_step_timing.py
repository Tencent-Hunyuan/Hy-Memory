"""Wall-clock accounting for one leased HY write, step by step.

A leased write runs on HY's own loop thread and on Core worker threads at
once, so context variables do not carry a timer between them. The timer is
handed explicitly to every wrapper that owns a step.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

from one_memory.diagnostic_log import diagnostic_event, diagnostics_enabled

#: Each step reports under the existing hy.* component that owns it.
STEP_COMPONENTS: dict[str, str] = {
    "model": "hy.model",
    "embedding": "hy.embedding",
    "vector_store_read": "hy.vector_store",
    "vector_store_write": "hy.vector_store",
    "permission_check": "hy.permission_store",
    "permission_store_read": "hy.permission_store",
    "permission_store_write": "hy.permission_store",
    "write_verification": "hy.write_verification",
    "reconcile": "hy.reconcile",
}

_inside: ContextVar[frozenset[str]] = ContextVar("hy_step_inside", default=frozenset())


def hy_step_trace_requested() -> bool:
    """Whether the debug tier asked for per-step HY tracing.

    The master switch (`ONE_MEMORY_DIAGNOSTICS`) answers "is there a
    diagnostic log at all". This is a refinement of it, not a bypass: an
    operator turning diagnostics on to investigate a host install failure is
    not asking for one `hy.step.completed` event per step on every write, and
    those are the highest-frequency events the system produces. They are only
    readable by an Agent working out where a write spent its time.

    `ONE_MEMORY_HY_STEP_TRACE=1` turns them on, on top of the master switch.
    """
    raw = (os.environ.get("ONE_MEMORY_HY_STEP_TRACE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on", "debug")


@dataclass(frozen=True)
class StepMeasurement:
    call_count: int
    item_count: int
    #: Wall-clock time with at least one call of this step running.
    busy_ms: int
    #: Per-call durations added up; exceeds busy_ms when calls overlap.
    summed_ms: int


@dataclass
class _Step:
    calls: int = 0
    items: int = 0
    summed: float = 0.0
    busy: float = 0.0
    active: int = 0
    opened: float = 0.0


class HyStepTimer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._steps: dict[str, _Step] = {}
        self._started = monotonic()
        self._active = 0
        self._opened = 0.0
        self._accounted = 0.0

    @contextmanager
    def step(self, name: str, *, items: int = 1) -> Iterator[None]:
        if name not in STEP_COMPONENTS:
            raise ValueError("hy_step_unknown")
        inside = _inside.get()
        if name in inside:
            # A wrapper calling its own wrapped sibling is still one call.
            yield
            return
        token = _inside.set(inside | {name})
        started = self._enter(name)
        try:
            yield
        finally:
            _inside.reset(token)
            self._leave(name, started, items)

    def _enter(self, name: str) -> float:
        now = monotonic()
        with self._lock:
            step = self._steps.setdefault(name, _Step())
            if step.active == 0:
                step.opened = now
            step.active += 1
            if self._active == 0:
                self._opened = now
            self._active += 1
        return now

    def _leave(self, name: str, started: float, items: int) -> None:
        now = monotonic()
        with self._lock:
            step = self._steps[name]
            step.calls += 1
            step.items += max(0, items)
            step.summed += now - started
            step.active -= 1
            if step.active == 0:
                step.busy += now - step.opened
            self._active -= 1
            if self._active == 0:
                self._accounted += now - self._opened

    def measurements(self) -> dict[str, StepMeasurement]:
        with self._lock:
            return {
                name: StepMeasurement(
                    call_count=step.calls,
                    item_count=step.items,
                    busy_ms=_ms(step.busy),
                    summed_ms=_ms(step.summed),
                )
                for name, step in self._steps.items()
                if step.calls
            }

    def emit(self, *, request_id: str | None, task_id: str | None) -> None:
        """One event per step, then one summary; never content or paths."""
        # Tiered, not unconditional: this is nine events per write (eight steps
        # plus a summary) at `info`, on the hottest path in the system. The
        # timer itself still runs — `measurements()` stays available to callers
        # that want the numbers without the log — so gating here costs nothing
        # but the writes.
        if not (diagnostics_enabled() and hy_step_trace_requested()):
            return
        measured = self.measurements()
        wall = _ms(monotonic() - self._started)
        with self._lock:
            accounted = min(wall, _ms(self._accounted))
        for name in STEP_COMPONENTS:
            value = measured.get(name)
            if value is None:
                continue
            diagnostic_event(
                level="debug",
                component=STEP_COMPONENTS[name],
                event="hy.step.completed",
                outcome="succeeded",
                request_id=request_id,
                task_id=task_id,
                duration_ms=value.busy_ms,
                details={
                    "phase": name,
                    "call_count": value.call_count,
                    "count": value.item_count,
                    "summed_ms": value.summed_ms,
                },
            )
        model = measured.get("model")
        diagnostic_event(
            level="debug",
            component="hy.bridge",
            event="hy.steps.summary",
            outcome="succeeded",
            request_id=request_id,
            task_id=task_id,
            duration_ms=wall,
            details={
                "call_count": sum(value.call_count for value in measured.values()),
                "model_ms": model.busy_ms if model else 0,
                "accounted_ms": accounted,
                "unaccounted_ms": wall - accounted,
            },
        )


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))
