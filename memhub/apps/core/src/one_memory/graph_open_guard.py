"""The first open of the ultra graph store, as a bounded probe before readiness.

The Kuzu graph is derived data: `MemoryGraphBackfill` rebuilds it per user from
the SDK's memories. A bloated or damaged graph file can still take down the
process that opens it -- in the field `kuzu.Database()` held the GIL, grew RSS
to 5-13 GB within seconds and the OS killed the process after 26-33 s. When
that open ran inside Core, Core froze, died, was relaunched, and opened the
same file again, forever. An open inside Core cannot protect Core.

So before Core reports ready, the live graph is opened once by a short-lived
child process (`GraphOpenProbe`), watched from here:

- a wall-clock budget (`probe_timeout_s`, 60 s plus a little per MB, at most
  120 s; `ONE_MEMORY_GRAPH_PROBE_TIMEOUT_S` overrides) and an RSS cap
  (`ONE_MEMORY_GRAPH_PROBE_MAX_RSS_MB`, default 2048), polled from the parent
  with `/proc` or `ps`; the child is SIGKILLed past either; it opens the
  graph with the SDK's buffer pool and thread bounds
  (`MEMORY_GRAPH_BUFFER_POOL_MB`, `MEMORY_GRAPH_MAX_THREADS`);
- a clean exit means the graph opens (and its WAL replay / checkpoint already
  happened in the child); the SDK then opens it in Core as before;
- a kill, a crash or a non-zero exit is a failed open: the graph is renamed to
  `graph.quarantined-<UTC>` at once (never deleted), Core starts without it,
  and the normal per-user rebuild runs after readiness.

Two durable records still cover Core itself dying mid-probe (a quit, the OS
killing Core under memory pressure):

- `core-startup.json` declares the startup phase (`graph_open`) with its
  budget, so the Runtime supervisor treats the probe as "starting".
- `graph-open-in-progress.json` is written before the probe and removed after
  it. A start that finds it left behind counts the previous start as failed;
  after `QUARANTINE_AFTER_FAILED_STARTS` such starts the graph is quarantined
  without probing it again.

A staging graph of an embedding switch is never touched: only the live space's
graph is probed, and while a switch is open nothing is quarantined (the live
graph is the switch's source).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from one_memory.diagnostic_log import diagnostic_event

logger = logging.getLogger(__name__)

MARKER_NAME = "graph-open-in-progress.json"
STARTUP_RECEIPT_NAME = "core-startup.json"
QUARANTINE_LEDGER_NAME = "graph-quarantine.json"
#: Starts in which Core itself died during the probe before the graph is
#: quarantined without another probe. One death can be a coincidence (a quit,
#: a power cut); two in a row is not.
QUARANTINE_AFTER_FAILED_STARTS = 2
#: Quarantined copies kept for diagnosis; older ones are removed.
KEEP_QUARANTINED = 2
#: The probe's wall clock: a healthy graph opens in well under a second, a
#: cold disk and a large file get more, a wedged open must still end.
PROBE_BASE_TIMEOUT_S = 60.0
PROBE_TIMEOUT_PER_MB_S = 0.5
PROBE_MAX_TIMEOUT_S = 120.0
#: A healthy open stays far below this; the field file passed 5 GB.
PROBE_MAX_RSS_BYTES = 2048 * 1024 * 1024
PROBE_POLL_INTERVAL_S = 0.1
#: What the Runtime is told on top of the probe budget: spawn, the ledger
#: writes and the rest of startup.
STARTUP_PHASE_MARGIN_MS = 30_000
#: The probe child's exit status when it cannot probe at all (no Kuzu). Not a
#: verdict on the graph, so it never quarantines.
PROBE_UNAVAILABLE_EXIT = 3
#: Kuzu's own files next to the database file.
_GRAPH_SIDECAR_SUFFIXES = (".wal", ".shadow", ".tmp", ".lock")
_QUARANTINE_INFIX = ".quarantined-"

GraphStoreStateName = Literal[
    "not_used", "opening", "ready", "rebuilding_after_quarantine", "unavailable"
]
GraphOpenOutcome = Literal["absent", "opened", "quarantined", "failed", "skipped"]
ProbeOutcome = Literal["ok", "timeout", "rss_cap", "crashed", "exit_nonzero", "unavailable"]


class GraphStoreStatus(BaseModel):
    """`pipeline.graph_store` in `/memory-processing` and `memhub status --json`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: GraphStoreStateName = "not_used"
    #: Every quarantine this home has seen (the copies kept are fewer).
    quarantine_count: int = 0
    last_quarantined_at: datetime | None = None
    #: Why the last graph was quarantined: a probe outcome (`timeout`,
    #: `rss_cap`, `crashed`, `exit_nonzero`) or `core_died_during_probe`.
    last_quarantine_reason: str | None = None
    #: Starts in which Core died during the probe, not resolved yet.
    failed_open_attempts: int = 0


@dataclass(frozen=True)
class _Marker:
    attempts: int
    graph_path: str
    started_at: str


@dataclass(frozen=True)
class ProbeResult:
    outcome: ProbeOutcome
    duration_ms: int = 0
    peak_rss_bytes: int | None = None
    exit_code: int | None = None
    #: The Memory embedding width the child read, when it printed one.
    width: int | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"

    @property
    def condemns_graph(self) -> bool:
        """Whether this says the graph cannot be opened (vs. could not probe)."""
        return self.outcome not in {"ok", "unavailable"}


def probe_timeout_s(graph_path: Path) -> float:
    raw = os.environ.get("ONE_MEMORY_GRAPH_PROBE_TIMEOUT_S", "").strip()
    with suppress(ValueError):
        if raw and float(raw) > 0:
            return float(raw)
    size_mb = _graph_bytes(graph_path) / (1024 * 1024)
    return min(PROBE_MAX_TIMEOUT_S, PROBE_BASE_TIMEOUT_S + size_mb * PROBE_TIMEOUT_PER_MB_S)


def probe_max_rss_bytes() -> int:
    raw = os.environ.get("ONE_MEMORY_GRAPH_PROBE_MAX_RSS_MB", "").strip()
    with suppress(ValueError):
        if raw and int(raw) > 0:
            return int(raw) * 1024 * 1024
    return PROBE_MAX_RSS_BYTES


def default_probe_command(graph_path: Path, alarm_s: int, *, read_only: bool = False) -> list[str]:
    """The child that opens the graph: the frozen binary's hidden subcommand,
    or this interpreter in isolated mode from source. Read-write by default,
    like the SDK's own open, so the WAL replay happens in the child."""
    mode = ["ro"] if read_only else []
    if getattr(sys, "frozen", False):
        return [sys.executable, "--graph-open-probe", str(graph_path), str(alarm_s), *mode]
    from one_memory.hy_graph_admin import GRAPH_OPEN_PROBE_SOURCE

    return [
        sys.executable,
        "-I",
        "-c",
        GRAPH_OPEN_PROBE_SOURCE,
        str(graph_path),
        str(alarm_s),
        *mode,
    ]


class GraphProbe(Protocol):
    def budget_s(self, graph_path: Path) -> float: ...

    def __call__(self, graph_path: Path) -> ProbeResult: ...


class GraphOpenProbe:
    """Open the graph in a child process, bounded by time and RSS."""

    def __init__(
        self,
        *,
        command: Callable[[Path, int], Sequence[str]] = default_probe_command,
        timeout_s: float | None = None,
        max_rss_bytes: int | None = None,
        poll_interval_s: float = PROBE_POLL_INTERVAL_S,
        rss_of: Callable[[int], int | None] | None = None,
    ) -> None:
        self.command = command
        self.timeout_s = timeout_s
        self.max_rss_bytes = max_rss_bytes
        self.poll_interval_s = poll_interval_s
        self.rss_of = rss_of or process_rss_bytes

    def budget_s(self, graph_path: Path) -> float:
        return self.timeout_s if self.timeout_s is not None else probe_timeout_s(graph_path)

    def __call__(self, graph_path: Path) -> ProbeResult:
        timeout = self.budget_s(graph_path)
        cap = self.max_rss_bytes if self.max_rss_bytes is not None else probe_max_rss_bytes()
        # The child also arms its own alarm, so it ends even if Core is killed
        # while it runs and nothing is left to watch it.
        argv = list(self.command(graph_path, int(timeout) + 5))
        started = time.monotonic()
        try:
            child = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                # One short line (`width=`); read after the child exits.
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            return ProbeResult("unavailable")
        peak: int | None = None

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        def stop(outcome: ProbeOutcome) -> ProbeResult:
            with suppress(OSError):
                child.kill()
            with suppress(subprocess.TimeoutExpired):
                child.wait(timeout=10)
            if child.stdout is not None:
                child.stdout.close()
            return ProbeResult(outcome, elapsed_ms(), peak, child.returncode)

        while True:
            code = child.poll()
            if code is not None:
                break
            rss = self.rss_of(child.pid)
            if rss is not None:
                peak = rss if peak is None else max(peak, rss)
                if rss > cap:
                    return stop("rss_cap")
            if time.monotonic() - started > timeout:
                return stop("timeout")
            time.sleep(self.poll_interval_s)
        output = ""
        if child.stdout is not None:
            with suppress(OSError, ValueError):
                output = child.stdout.read(4096).decode("utf-8", "replace")
            child.stdout.close()
        if code == 0:
            return ProbeResult("ok", elapsed_ms(), peak, 0, _printed_width(output))
        if code == PROBE_UNAVAILABLE_EXIT:
            return ProbeResult("unavailable", elapsed_ms(), peak, code)
        return ProbeResult("crashed" if code < 0 else "exit_nonzero", elapsed_ms(), peak, code)


def _printed_width(output: str) -> int | None:
    for line in reversed(output.splitlines()):
        if line.startswith("width="):
            value = line.removeprefix("width=").strip()
            return int(value) if value.isdecimal() else None
    return None


def process_rss_bytes(pid: int) -> int | None:
    """Resident memory of `pid`, or None when it cannot be read."""
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        with suppress(OSError, ValueError):
            for line in status.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        return None
    if os.name != "posix":
        return None
    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    with suppress(ValueError):
        return int(result.stdout.strip()) * 1024
    return None


class GraphOpenGuard:
    """Owns the marker, the quarantine ledger and the startup-phase receipt."""

    def __init__(
        self,
        data_dir: Path,
        *,
        probe: GraphProbe | None = None,
        instance_id: str | None = None,
        pid: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.data_dir = data_dir
        self.probe: GraphProbe = probe if probe is not None else GraphOpenProbe()
        self.instance_id = instance_id
        self.pid = pid if pid is not None else os.getpid()
        self._now = now
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
            last_quarantine_reason=(
                reason if isinstance(reason := ledger.get("last_reason"), str) else None
            ),
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
        """Probe the live graph once; quarantine it if it cannot be opened.

        Blocking (it waits for the child): run it off the event loop.
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
        if failed >= QUARANTINE_AFTER_FAILED_STARTS:
            if switch_in_progress:
                return self._defer(failed)
            return self._quarantined(graph_path, failed, "core_died_during_probe")
        self._state = "opening"
        budget_s = self.probe.budget_s(graph_path)
        self._write_startup_receipt("graph_open", int(budget_s * 1000) + STARTUP_PHASE_MARGIN_MS)
        # Written before the probe: a start that dies during it leaves this
        # behind, and that is how the next start knows.
        self._write_marker(_Marker(failed + 1, str(graph_path), self._now().isoformat()))
        try:
            result = self.probe(graph_path)
        finally:
            self.clear_startup_receipt()
        details: dict[str, str | int | float | bool | None] = {"reason": result.outcome}
        if result.peak_rss_bytes is not None:
            details["memory_bytes"] = result.peak_rss_bytes
        if result.exit_code is not None:
            details["exit_code"] = result.exit_code
        if result.ok:
            from one_memory.hy_graph_admin import remember_graph_width

            # The width check before every SDK client reads this instead of
            # opening the graph in Core.
            remember_graph_width(graph_path, result.width)
        if result.ok or not result.condemns_graph:
            # `unavailable` (no Kuzu in the child, no way to spawn it) says
            # nothing about the graph: start as before and let the SDK open it.
            self._clear_marker()
            self._failed_attempts = 0
            self._state = "ready"
            diagnostic_event(
                level="info" if result.ok else "warn",
                component="core.graph_store",
                event="graph_store.opened" if result.ok else "graph_store.probe_unavailable",
                outcome="succeeded" if result.ok else "skipped",
                duration_ms=result.duration_ms,
                details=details,
            )
            return "opened"
        diagnostic_event(
            level="warn",
            component="core.graph_store",
            event="graph_store.probe_failed",
            outcome="failed",
            duration_ms=result.duration_ms,
            details=details,
        )
        if switch_in_progress:
            # Not renamed under the switch; the marker stays so the first
            # start after it tries again.
            self._state = "unavailable"
            return "failed"
        return self._quarantined(graph_path, failed + 1, result.outcome)

    def _defer(self, failed: int) -> GraphOpenOutcome:
        # The live graph is the switch's source; renaming it would break the
        # re-embed. Leave it, keep the marker, and do not probe it again. The
        # first start after the switch ends quarantines it.
        diagnostic_event(
            level="warn",
            component="core.graph_store",
            event="graph_store.quarantine_deferred",
            outcome="skipped",
            details={"reason": "embedding_switch_in_progress", "count": failed},
        )
        self._state = "unavailable"
        return "skipped"

    def _quarantined(self, graph_path: Path, failed: int, reason: str) -> GraphOpenOutcome:
        self.quarantine(graph_path, failed_attempts=failed, reason=reason)
        self._clear_marker()
        self._failed_attempts = 0
        self._state = "rebuilding_after_quarantine"
        return "quarantined"

    def quarantine(
        self, graph_path: Path, *, failed_attempts: int, reason: str = "unknown"
    ) -> Path:
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
        self._write_ledger(
            {
                "count": count,
                "last_quarantined_at": now,
                "last_reason": reason,
                "rebuild_pending": True,
            }
        )
        logger.warning(
            "graph_store_quarantined reason=%s failed_opens=%d files=%d target=%s",
            reason,
            failed_attempts,
            moved,
            target.name,
        )
        diagnostic_event(
            level="warn",
            component="core.graph_store",
            event="graph_store.quarantined",
            outcome="recovered",
            details={
                "count": count,
                "reason": reason,
                "failed_opens": failed_attempts,
                "files": moved,
            },
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

    def _write_startup_receipt(self, phase: str, budget_ms: int) -> None:
        _write_json(
            self.startup_receipt_path,
            {
                "schema_version": 1,
                "pid": self.pid,
                "instance_id": self.instance_id,
                "phase": phase,
                "started_at": self._now().isoformat(),
                "budget_ms": budget_ms,
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


def _graph_bytes(path: Path) -> int:
    with suppress(OSError):
        if path.is_dir():
            return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())
        return path.stat().st_size
    return 0


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
