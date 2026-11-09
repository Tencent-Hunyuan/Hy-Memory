"""The first open of the ultra graph store, as an explicit, guarded startup step.

The Kuzu graph is derived data: `MemoryGraphBackfill` rebuilds it per user from
the SDK's memories. A bloated or damaged graph file can still take Core down
while it opens -- in the field `kuzu.Database()` held the GIL, grew RSS to 13 GB
in two seconds and the OS killed the process. When that open ran after
readiness, `/health` stopped answering, the process died, the Runtime
relaunched it, and the next Core opened the same file again, forever.

So the first open now runs before Core reports ready, under two durable
records in the data directory:

- `core-startup.json` declares the startup phase (`graph_open`) with its
  budget, so the Runtime supervisor treats a slow open as "starting", not as a
  hung Core, until the budget runs out.
- `graph-open-in-progress.json` is written before the open and removed once it
  succeeds. A start that finds it left behind knows the previous start died or
  was killed while opening the graph. After `QUARANTINE_AFTER_FAILED_OPENS`
  such starts in a row the graph is renamed aside (never deleted), Core starts
  without it, and the normal per-user rebuild is scheduled.

A staging graph of an embedding switch is never touched: only the live space's
graph is opened here, and while a switch is open nothing is quarantined (the
live graph is the switch's source).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from one_memory.diagnostic_log import diagnostic_event

logger = logging.getLogger(__name__)

MARKER_NAME = "graph-open-in-progress.json"
STARTUP_RECEIPT_NAME = "core-startup.json"
QUARANTINE_LEDGER_NAME = "graph-quarantine.json"
#: Starts that died (or were killed) while opening the graph before it is
#: quarantined. One death can be a coincidence (a quit, a power cut); two in a
#: row on the same file is the file.
QUARANTINE_AFTER_FAILED_OPENS = 2
#: Quarantined copies kept for diagnosis; older ones are removed.
KEEP_QUARANTINED = 2
#: How long the Runtime should wait for the graph open before treating Core as
#: hung. Generous, because a healthy large graph can take a while on a cold
#: disk, and bounded, because a wedged open must still end.
GRAPH_OPEN_BUDGET_MS = 180_000
#: Kuzu's own files next to the database file.
_GRAPH_SIDECAR_SUFFIXES = (".wal", ".shadow", ".tmp", ".lock")
_QUARANTINE_INFIX = ".quarantined-"

GraphStoreStateName = Literal[
    "not_used", "opening", "ready", "rebuilding_after_quarantine", "unavailable"
]
GraphOpenOutcome = Literal["absent", "opened", "quarantined", "failed", "skipped"]


class GraphStoreStatus(BaseModel):
    """`pipeline.graph_store` in `/memory-processing` and `memhub status --json`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: GraphStoreStateName = "not_used"
    #: Every quarantine this home has seen (the copies kept are fewer).
    quarantine_count: int = 0
    last_quarantined_at: datetime | None = None
    #: Starts that died while opening the graph and have not been resolved yet.
    failed_open_attempts: int = 0


@dataclass(frozen=True)
class _Marker:
    attempts: int
    graph_path: str
    started_at: str


def default_open_graph(path: Path) -> None:
    from one_memory.hy_graph_admin import open_graph_store_once

    open_graph_store_once(path)


class GraphOpenGuard:
    """Owns the marker, the quarantine ledger and the startup-phase receipt."""

    def __init__(
        self,
        data_dir: Path,
        *,
        open_graph: Callable[[Path], None] = default_open_graph,
        instance_id: str | None = None,
        pid: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        budget_ms: int = GRAPH_OPEN_BUDGET_MS,
    ) -> None:
        self.data_dir = data_dir
        self.open_graph = open_graph
        self.instance_id = instance_id
        self.pid = pid if pid is not None else os.getpid()
        self._now = now
        self.budget_ms = budget_ms
        self._state: GraphStoreStateName = "not_used"
        self._failed_attempts = 0

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @property
    def marker_path(self) -> Path:
        return self.data_dir / MARKER_NAME

    @property
    def startup_receipt_path(self) -> Path:
        return self.data_dir / STARTUP_RECEIPT_NAME

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / QUARANTINE_LEDGER_NAME

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, *, rebuild_running: bool | None = None) -> GraphStoreStatus:
        """The current state; `rebuild_running` settles a finished rebuild."""
        ledger = self._read_ledger()
        state = self._state
        if ledger.get("rebuild_pending") and state in {"ready", "rebuilding_after_quarantine"}:
            if rebuild_running is False:
                self._write_ledger({**ledger, "rebuild_pending": False})
                ledger["rebuild_pending"] = False
                state = "ready"
            else:
                state = "rebuilding_after_quarantine"
        last = ledger.get("last_quarantined_at")
        return GraphStoreStatus(
            state=state,
            quarantine_count=_count(ledger),
            last_quarantined_at=datetime.fromisoformat(last) if isinstance(last, str) else None,
            failed_open_attempts=self._failed_attempts,
        )

    def mark_not_used(self) -> None:
        self._state = "not_used"

    def rebuild_pending(self) -> bool:
        return bool(self._read_ledger().get("rebuild_pending"))

    # ------------------------------------------------------------------
    # The startup step
    # ------------------------------------------------------------------

    def open_at_startup(self, graph_path: Path, *, switch_in_progress: bool) -> GraphOpenOutcome:
        """Open the live graph once, quarantining it after repeated failed starts.

        Blocking: run it off the event loop. Returns what happened.
        """
        previous = self._read_marker()
        if previous is not None and previous.graph_path != str(graph_path):
            # The live graph moved (an embedding switch committed): the
            # failures were about another file.
            previous = None
        failed = previous.attempts if previous is not None else 0
        self._failed_attempts = failed
        if failed:
            diagnostic_event(
                level="warn",
                component="core.graph_store",
                event="graph_store.open_failed_previously",
                outcome="failed",
                details={"count": failed},
            )
        if not _graph_has_files(graph_path):
            self._clear_marker()
            self._failed_attempts = 0
            self._state = "ready"
            return "absent"
        if failed >= QUARANTINE_AFTER_FAILED_OPENS:
            if switch_in_progress:
                # The live graph is the switch's source; renaming it would
                # break the re-embed. Leave it, keep the marker, and do not
                # open it again here (that is what took the last starts
                # down). The first start after the switch ends quarantines it.
                diagnostic_event(
                    level="warn",
                    component="core.graph_store",
                    event="graph_store.quarantine_deferred",
                    outcome="skipped",
                    details={"reason": "embedding_switch_in_progress", "count": failed},
                )
                self._state = "unavailable"
                return "skipped"
            self.quarantine(graph_path, failed_attempts=failed)
            self._clear_marker()
            self._failed_attempts = 0
            self._state = "rebuilding_after_quarantine"
            return "quarantined"
        self._state = "opening"
        self._write_startup_receipt("graph_open")
        try:
            while True:
                # Written before the open: a start that dies inside it leaves
                # this behind, and that is how the next start knows.
                self._write_marker(_Marker(failed + 1, str(graph_path), self._now().isoformat()))
                started = time.monotonic()
                try:
                    self.open_graph(graph_path)
                except Exception as error:
                    # An open that raises did not take the process with it,
                    # but it failed just the same. Count it and decide now
                    # instead of waiting for a restart that never comes.
                    failed += 1
                    self._failed_attempts = failed
                    diagnostic_event(
                        level="warn",
                        component="core.graph_store",
                        event="graph_store.open_failed",
                        outcome="failed",
                        error_type=type(error).__name__,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        details={"count": failed},
                    )
                    if switch_in_progress:
                        self._state = "unavailable"
                        return "failed"
                    if failed >= QUARANTINE_AFTER_FAILED_OPENS:
                        self.quarantine(graph_path, failed_attempts=failed)
                        self._clear_marker()
                        self._failed_attempts = 0
                        self._state = "rebuilding_after_quarantine"
                        return "quarantined"
                    continue
                break
        finally:
            self.clear_startup_receipt()
        self._clear_marker()
        self._failed_attempts = 0
        self._state = "ready"
        diagnostic_event(
            level="info",
            component="core.graph_store",
            event="graph_store.opened",
            outcome="succeeded",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return "opened"

    def quarantine(self, graph_path: Path, *, failed_attempts: int) -> Path:
        """Rename the graph and its sidecars aside; never delete them."""
        stamp = self._now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = graph_path.with_name(f"{graph_path.name}{_QUARANTINE_INFIX}{stamp}")
        suffix = 1
        while target.exists():
            target = graph_path.with_name(f"{graph_path.name}{_QUARANTINE_INFIX}{stamp}-{suffix}")
            suffix += 1
        moved = 0
        if graph_path.exists():
            graph_path.rename(target)
            moved += 1
        for sidecar in _GRAPH_SIDECAR_SUFFIXES:
            source = graph_path.with_name(f"{graph_path.name}{sidecar}")
            if source.exists():
                source.rename(target.with_name(f"{target.name}{sidecar}"))
                moved += 1
        self._prune_quarantined(graph_path)
        ledger = self._read_ledger()
        count = _count(ledger) + 1
        now = self._now().isoformat()
        self._write_ledger({"count": count, "last_quarantined_at": now, "rebuild_pending": True})
        logger.warning(
            "graph_store_quarantined failed_opens=%d files=%d target=%s",
            failed_attempts,
            moved,
            target.name,
        )
        diagnostic_event(
            level="warn",
            component="core.graph_store",
            event="graph_store.quarantined",
            outcome="recovered",
            details={"count": count, "failed_opens": failed_attempts, "files": moved},
        )
        return target

    def _prune_quarantined(self, graph_path: Path) -> None:
        prefix = f"{graph_path.name}{_QUARANTINE_INFIX}"
        copies = sorted(
            (
                entry
                for entry in graph_path.parent.iterdir()
                if entry.name.startswith(prefix)
                and not any(entry.name.endswith(sidecar) for sidecar in _GRAPH_SIDECAR_SUFFIXES)
            ),
            key=lambda entry: entry.name,
        )
        for stale in copies[:-KEEP_QUARANTINED] if len(copies) > KEEP_QUARANTINED else ():
            for path in (
                stale,
                *(stale.with_name(f"{stale.name}{s}") for s in _GRAPH_SIDECAR_SUFFIXES),
            ):
                with suppress(FileNotFoundError):
                    if path.is_dir():
                        _remove_tree(path)
                    else:
                        path.unlink()

    # ------------------------------------------------------------------
    # Durable records
    # ------------------------------------------------------------------

    def _read_marker(self) -> _Marker | None:
        try:
            raw = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        attempts = raw.get("attempts")
        if not isinstance(attempts, int) or attempts < 0:
            return None
        return _Marker(
            attempts=attempts,
            graph_path=str(raw.get("graph_path", "")),
            started_at=str(raw.get("started_at", "")),
        )

    def _write_marker(self, marker: _Marker) -> None:
        _write_json(
            self.marker_path,
            {
                "schema_version": 1,
                "attempts": marker.attempts,
                "graph_path": marker.graph_path,
                "started_at": marker.started_at,
                "pid": self.pid,
            },
        )

    def _clear_marker(self) -> None:
        self.marker_path.unlink(missing_ok=True)

    def _write_startup_receipt(self, phase: str) -> None:
        _write_json(
            self.startup_receipt_path,
            {
                "schema_version": 1,
                "pid": self.pid,
                "instance_id": self.instance_id,
                "phase": phase,
                "started_at": self._now().isoformat(),
                "budget_ms": self.budget_ms,
            },
        )

    def clear_startup_receipt(self) -> None:
        self.startup_receipt_path.unlink(missing_ok=True)

    def _read_ledger(self) -> dict[str, object]:
        try:
            raw = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_ledger(self, values: dict[str, object]) -> None:
        _write_json(self.ledger_path, {"schema_version": 1, **values})


def _count(ledger: dict[str, object]) -> int:
    value = ledger.get("count")
    return value if isinstance(value, int) and value >= 0 else 0


def _graph_has_files(path: Path) -> bool:
    if path.is_dir():
        return any(path.iterdir())
    return path.exists()


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
