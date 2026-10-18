"""One append-only diagnostic log, written off whoever produced the event.

Writing a line used to cost a directory scan plus a full disk flush on the
calling thread. Most callers are on the event loop, so on a busy disk a single
diagnostic event stopped the whole process from answering: the real log has
three `event loop blocked for 2.0-2.8s` reports whose main-thread stack ends
inside this module, two of them merely waiting for the lock another thread was
holding across its own fsync.

So the hot path here is deliberately only two things: turn the event into a
line, and hand it to a queue. One writer thread owns the file and everything
that touches the filesystem — the directory checks, the pruning, the rotation,
the writing. Durability is bought once at shutdown rather than per event,
because this is telemetry: it has to be complete when we exit cleanly, and
losing the last few lines to a power cut costs nothing anyone can use.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from collections import Counter, deque
from collections.abc import Mapping
from contextlib import suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module.
    resource = None  # type: ignore[assignment]

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_AGE = timedelta(days=14)
# Deep enough to swallow any burst this process actually produces — the busiest
# real hour logged 2082 events — and small enough that a wedged disk costs a
# bounded amount of memory instead of an unbounded one.
MAX_QUEUED_EVENTS = 4096
# The directory scan exists to enforce the age and total-size budgets. Those are
# measured in days and megabytes, so running it once a minute is as correct as
# running it per event, and roughly a thousand times cheaper.
PRUNE_INTERVAL_SECONDS = 60.0
HIGH_FREQUENCY_SUMMARY_SECONDS = 300.0
_HIGH_FREQUENCY_EVENTS = frozenset(
    {"import.phase_changed", "import.parse.started", "context_stage.completed"}
)
SHUTDOWN_DRAIN_SECONDS = 5.0
"""How long a clean shutdown waits for the writer to finish."""

MAX_WRITE_ATTEMPTS = 3
"""Retries before a batch is given up on and counted as dropped."""

WRITE_RETRY_SECONDS = 0.05
"""Back-off between retries, so a wedged disk cannot become a busy loop."""
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class _Refused(OSError):
    """The path is not one we are willing to write to, and never will be."""


DiagnosticLevel = Literal["info", "warn", "error"]
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_EVENT = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$")
_DETAIL_KEYS = frozenset(
    {
        "action",
        "attempt",
        "count",
        "method",
        "mode",
        "phase",
        "platform",
        "reason",
        "result_count",
        "source_id",
        "spawned",
        "status",
        "version",
        "parent_session_id",
        "invalid_lines",
        "truncated",
        "open_file_count",
        "memory_bytes",
        "thread_count",
        "database_error_count",
        "route_path",
        "sqlite_errorcode",
        "sqlite_errorname",
        "file_count",
        "sqlite_file_count",
        "byte_file_count",
        "total_bytes",
        "copied_bytes",
        "bytes_per_second",
        "stored_count",
        "revised_count",
        "skipped_count",
        "error_count",
        "pending_count",
        "prepared_count",
        "queue_depth",
        "in_flight_count",
        "call_count",
        "created_count",
        "updated_count",
        "superseded_count",
        "deleted_count",
        "operation_kind",
        "index_complete",
        "lexical_hits",
        "semantic_hits",
        "generation",
        "timed_out",
        "failure_site",
        "error_class",
        "file_kind",
        "file_size",
        "file_mtime_ns",
        "file_inode",
        "cursor_new",
        "cursor_old",
        "cursor_read_to_end",
        "cursor_rewind_branch",
        "summed_ms",
        "model_ms",
        "accounted_ms",
        "unaccounted_ms",
        "conversation_hash",
        # The sedimentation switch: a bounded boolean, the same shape as
        # `spawned` and `index_complete`. Left out of this list it did not
        # drop a log line, it turned the switch's own request into a 500.
        "enabled",
    }
)
_trace_id: ContextVar[str | None] = ContextVar("one_memory_trace_id", default=None)
_active: DiagnosticLog | None = None
_database_errors: Counter[tuple[str, str]] = Counter()
_database_errors_lock = threading.Lock()


@dataclass(frozen=True)
class DiagnosticReceipt:
    session_id: str
    path: Path


class DiagnosticLog:
    def __init__(
        self,
        data_dir: Path,
        *,
        service: Literal["core"] = "core",
        session_id: str | None = None,
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.service = service
        self.session_id = _uuid(session_id) or str(uuid4())
        # Held only around the queue, never across a syscall. That is the whole
        # difference from the version that stalled the loop: a caller can always
        # get this lock, because nobody holds it while waiting on a disk.
        self._ready = threading.Condition(threading.Lock())
        self._queue: deque[str] = deque()
        self._writer: threading.Thread | None = None
        self._closed = False
        self._dropped = 0
        self._attempts = 0
        self._degraded = False
        # When the current run of drops began, so the record can say which
        # stretch of time is missing rather than only how much.
        self._dropped_since: float | None = None
        self._drained = threading.Event()
        self._drained.set()
        # Size is tracked here so the common write needs no stat(); it is
        # invalidated whenever something else may have changed the file.
        self._size: int | None = None
        self._pruned_at: float | None = None
        self._sample_lock = threading.Lock()
        self._sampled: dict[tuple[object, ...], tuple[float, int, int]] = {}

    @property
    def path(self) -> Path:
        return self.data_dir / "logs" / f"diagnostic-{self.service}.jsonl"

    def write(
        self,
        *,
        level: DiagnosticLevel,
        component: str,
        event: str,
        outcome: str,
        trace_id: str | None = None,
        incident_id: str | None = None,
        instance_id: str | None = None,
        operation_id: str | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
        task_id: str | None = None,
        build_id: str | None = None,
        package_id: str | None = None,
        round: str | None = None,
        duration_ms: int | None = None,
        state_before: str | None = None,
        state_after: str | None = None,
        error_code: str | None = None,
        error_type: str | None = None,
        safe_stack: tuple[str, ...] | None = None,
        details: Mapping[str, str | int | float | bool | None] | None = None,
    ) -> None:
        if level == "info" and event in _HIGH_FREQUENCY_EVENTS:
            detail_values = dict(details or {})
            sample_key = (
                component,
                event,
                outcome,
                state_before,
                state_after,
                detail_values.get("source_id"),
                detail_values.get("operation_kind"),
                detail_values.get("phase"),
            )
            now = monotonic()
            with self._sample_lock:
                previous = self._sampled.get(sample_key)
                if previous is not None and now - previous[0] < HIGH_FREQUENCY_SUMMARY_SECONDS:
                    self._sampled[sample_key] = (
                        previous[0],
                        previous[1] + 1,
                        previous[2] + (duration_ms or 0),
                    )
                    return
                if previous is not None:
                    detail_values["count"] = previous[1] + 1
                    detail_values["summed_ms"] = previous[2] + (duration_ms or 0)
                self._sampled[sample_key] = (now, 0, 0)
            details = detail_values
        value = self._header(level=level, component=component, event=event, outcome=outcome)
        identifiers = {
            "trace_id": trace_id or current_trace_id(),
            "incident_id": incident_id,
            "instance_id": instance_id,
            "operation_id": operation_id,
            "request_id": request_id,
            "job_id": job_id,
            "task_id": task_id,
            # Package identity comes from the shared spawn point's environment;
            # a source run leaves it unset and writes nothing.
            "build_id": build_id or os.environ.get("ONE_MEMORY_BUILD_ID"),
            "package_id": package_id or os.environ.get("ONE_MEMORY_PACKAGE_ID"),
            "round": round or os.environ.get("ONE_MEMORY_ROUND"),
        }
        value.update({key: _identifier(item) for key, item in identifiers.items() if item})
        if duration_ms is not None:
            if not isinstance(duration_ms, int) or duration_ms < 0:
                raise ValueError("diagnostic_duration_invalid")
            value["duration_ms"] = duration_ms
        for field_name, item in {
            "state_before": state_before,
            "state_after": state_after,
            "error_code": error_code,
            "error_type": error_type,
        }.items():
            if item is not None:
                value[field_name] = _identifier(item)
        if safe_stack:
            value["safe_stack"] = [_safe_stack_line(line) for line in safe_stack[:256]]
        if details:
            value["details"] = _details(details)
        self._submit(_serialize(value))

    def _header(
        self,
        *,
        level: DiagnosticLevel,
        component: str,
        event: str,
        outcome: str,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": level,
            "service": self.service,
            "component": _event_name(component),
            "event": _event_name(event),
            "outcome": _identifier(outcome),
            "session_id": self.session_id,
        }

    # --- the hot path: no syscalls past this point ------------------------

    def _submit(self, line: str) -> None:
        with self._ready:
            if self._closed:
                return
            if len(self._queue) >= MAX_QUEUED_EVENTS:
                # Dropping is the only honest option left. Blocking here would
                # reintroduce exactly the stall this queue exists to remove,
                # and the count and window are written out so the gap is
                # visible rather than silent.
                if self._dropped_since is None:
                    self._dropped_since = monotonic()
                self._dropped += 1
                return
            self._queue.append(line)
            self._drained.clear()
            try:
                self._start_writer()
            except RuntimeError:
                # No writer means nobody will ever pick this up. Count it as a
                # drop rather than let a telemetry call raise at its caller.
                self._queue.pop()
                if self._dropped_since is None:
                    self._dropped_since = monotonic()
                self._dropped += 1
                return
            self._ready.notify()

    def _start_writer(self) -> None:
        """Called with the lock held, so the thread is started exactly once."""
        if self._writer is not None:
            return
        writer = threading.Thread(
            target=self._drain_forever,
            name=f"diagnostic-log-{self.service}",
            daemon=True,
        )
        try:
            writer.start()
        except RuntimeError:
            # Out of threads, or the interpreter is already tearing down.
            # Leaving `_writer` set would mean never trying again for the rest
            # of the process's life.
            self._writer = None
            raise
        self._writer = writer

    def flush(self, timeout: float = SHUTDOWN_DRAIN_SECONDS) -> bool:
        """Block until everything queued so far is on disk.

        Returns False if the writer gave up on a batch. Draining and
        persisting are not the same thing, and only the second one is worth
        answering True to.
        """
        with self._ready:
            self._ready.notify()
        drained = self._drained.wait(timeout)
        with self._ready:
            return drained and not self._degraded

    def close(self, timeout: float = SHUTDOWN_DRAIN_SECONDS) -> bool:
        """Drain and stop. A clean exit must not lose events.

        Calling this twice does not make it true. The first call can time out
        with the writer still holding events; answering `True` to the second
        one would let a daemon thread be killed mid-batch while the caller
        believed the log was closed.
        """
        with self._ready:
            self._closed = True
            writer = self._writer
            self._ready.notify_all()
        if writer is None:
            return True
        if writer is threading.current_thread():
            # The writer asking itself to stop: joining would raise. Marking it
            # closed is enough — the drain loop returns on its next pass.
            return False
        writer.join(timeout)
        with self._ready:
            return not writer.is_alive() and not self._degraded

    # --- the writer thread: everything below runs only there --------------

    def _drain_forever(self) -> None:
        while True:
            with self._ready:
                while not self._queue and not self._closed:
                    self._drained.set()
                    self._ready.wait(PRUNE_INTERVAL_SECONDS)
                batch = list(self._queue)
                self._queue.clear()
                dropped = self._dropped
                dropped_since = self._dropped_since
                self._dropped = 0
                self._dropped_since = None
                closing = self._closed
            if dropped:
                # After the batch, where the gap actually is. Written with both
                # the count and the window it spans: someone reading this file
                # later has to be able to tell "nothing happened here" from
                # "this stretch was thrown away".
                batch.append(self._dropped_line(dropped, dropped_since))
            if batch:
                # Nothing below may treat an unwritten batch as persisted. A
                # dead writer thread would silence diagnostics for the rest of
                # the process's life, so the thread survives — but it says so.
                outcome = self._try_write(batch)
                if outcome == "retry":
                    continue
                if outcome == "lost":
                    if closing:
                        # The disk is broken and we are on the way out. Trying
                        # forever would hang shutdown; `close()` reports the
                        # loss through the degraded flag instead.
                        return
                    continue
            with self._ready:
                if not self._queue and not self._dropped:
                    if closing:
                        # The one flush we buy, as its own step rather than a
                        # side effect of having had a batch to write: normal
                        # shutdown drains first and arrives here empty-handed.
                        self._final_sync()
                        self._drained.set()
                        return
                    self._drained.set()

    def _try_write(self, batch: list[str]) -> Literal["written", "retry", "lost"]:
        """Write a batch, or put it back. Never silently swallow it.

        A full disk is usually a moment, not a verdict, so the batch goes back
        to the front of the queue and is tried again. After `MAX_WRITE_ATTEMPTS`
        it is given up on — but counted, so the gap shows up in the file the
        same way an overflow does.
        """
        try:
            self._write_batch(batch)
        except Exception as error:
            # A symlinked path is a verdict, not a moment: retrying it just
            # delays telling the truth about the loss.
            permanent = isinstance(error, _Refused)
            with self._ready:
                self._attempts += 1
                retrying = not permanent and self._attempts < MAX_WRITE_ATTEMPTS
                if retrying:
                    # Back to the front, so the order events were emitted in
                    # survives the retry.
                    self._queue.extendleft(reversed(batch))
                    self._drained.clear()
                else:
                    self._attempts = 0
                    self._degraded = True
                    self._dropped += len(batch)
                    if self._dropped_since is None:
                        self._dropped_since = monotonic()
            # Back off so a wedged disk cannot turn into a busy loop.
            time.sleep(WRITE_RETRY_SECONDS)
            return "retry" if retrying else "lost"
        with self._ready:
            self._attempts = 0
            self._degraded = False
        return "written"

    def _final_sync(self) -> None:
        """Flush what is on the page cache to the platter, once, on the way out."""
        with suppress(OSError):
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | _O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _dropped_line(self, dropped: int, since: float | None) -> str:
        """The gap, stated as a count and a window.

        `timestamp` is when the run of drops ended and `duration_ms` how long
        it lasted, so the missing stretch is [timestamp - duration_ms,
        timestamp]. Both are fields the schema already has, so nothing on disk
        gains a key.
        """
        value = self._header(
            level="warn",
            component="core.diagnostics",
            event="diagnostic.events_dropped",
            outcome="dropped",
        )
        value["duration_ms"] = 0 if since is None else elapsed_ms(since)
        value["details"] = {"count": dropped}
        return _serialize(value)

    def _write_batch(self, lines: list[str]) -> None:
        parent = self.path.parent
        if parent.is_symlink():
            raise _Refused("diagnostic_log_parent_is_symlink")
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or not parent.is_dir():
            raise _Refused("diagnostic_log_parent_not_a_directory")
        os.chmod(parent, 0o700)
        self._maybe_prune()
        if self.path.is_symlink():
            raise _Refused("diagnostic_log_path_is_symlink")
        pending = [line.encode("utf-8") for line in lines]
        while pending:
            size = self._tracked_size()
            if size and size + len(pending[0]) > MAX_FILE_BYTES:
                self._rotate()
                size = 0
            chunk: list[bytes] = []
            used = 0
            while pending and size + used + len(pending[0]) <= MAX_FILE_BYTES:
                encoded = pending.pop(0)
                chunk.append(encoded)
                used += len(encoded)
            if not chunk:
                # One line larger than the whole budget. Keep it rather than
                # spin: the log is append-only evidence, not a cache.
                chunk.append(pending.pop(0))
            self._append(b"".join(chunk))

    def _append(self, chunk: bytes) -> None:
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_NOFOLLOW,
            0o600,
        )
        written = 0
        try:
            # os.write is allowed to accept fewer bytes than it was given. The
            # first version ignored the count, so a short write left half a
            # JSON object on a line nobody could parse.
            while written < len(chunk):
                sent = os.write(descriptor, chunk[written:])
                if sent <= 0:
                    raise OSError("diagnostic_write_stalled")
                written += sent
            # Permissions on the descriptor we just wrote, not on a path that
            # could have been swapped underneath us since.
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
            self._size = (self._size or 0) + written

    def _tracked_size(self) -> int:
        if self._size is None:
            try:
                self._size = self.path.stat().st_size
            except OSError:
                self._size = 0
        return self._size

    def _maybe_prune(self) -> None:
        now = monotonic()
        if self._pruned_at is not None and now - self._pruned_at < PRUNE_INTERVAL_SECONDS:
            return
        self._pruned_at = now
        self._prune()
        # Pruning may have removed the file being written to.
        self._size = None

    def _rotate(self) -> None:
        self.path.with_suffix(self.path.suffix + ".3").unlink(missing_ok=True)
        for index in (2, 1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                source.replace(self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        if self.path.exists():
            self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
        self._size = 0

    def _prune(self) -> None:
        now = datetime.now(UTC).timestamp()
        files: list[tuple[Path, os.stat_result]] = []
        for candidate in self.path.parent.glob("diagnostic-*.jsonl*"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            info = candidate.stat()
            if now - info.st_mtime > MAX_AGE.total_seconds():
                candidate.unlink(missing_ok=True)
            else:
                files.append((candidate, info))
        total = sum(info.st_size for _, info in files)
        for candidate, info in sorted(files, key=lambda item: item[1].st_mtime):
            if total <= MAX_TOTAL_BYTES:
                break
            candidate.unlink(missing_ok=True)
            total -= info.st_size


def diagnostics_requested() -> bool:
    """Whether the pipeline log was asked for.

    Off unless asked. The pipeline log is an instrument for working out what
    happened during a debugging session, not a product asset: left on it grows
    without bound — measured at roughly 24 MB across ten files in ordinary
    use — and every event is a write the product does not need.

    `ONE_MEMORY_DIAGNOSTICS=1` turns it on.
    """
    raw = (os.environ.get("ONE_MEMORY_DIAGNOSTICS") or "").strip().lower()
    return raw in ("1", "true", "yes", "on", "debug")


def configure_diagnostics(data_dir: Path, *, session_id: str | None = None) -> DiagnosticReceipt:
    # Decided once, at startup: switching it mid-run would leave a session
    # half-recorded, which is worse than either extreme.
    if not diagnostics_requested():
        reset_diagnostics()
        # Points at the directory rather than a file that was never made:
        # callers only ever report this path, and naming something that does
        # not exist sends anyone looking for the log on a dead end.
        return DiagnosticReceipt(
            session_id=_uuid(session_id) or str(uuid4()),
            path=data_dir,
        )
    global _active
    previous = _active
    _active = DiagnosticLog(data_dir, session_id=session_id)
    if previous is not None:
        previous.close()
    return DiagnosticReceipt(session_id=_active.session_id, path=_active.path)


def reset_diagnostics() -> None:
    """Stop diagnostics, and do not return until what was queued is on disk."""
    global _active
    previous = _active
    _active = None
    if previous is not None:
        previous.close()


def flush_diagnostics(timeout: float = SHUTDOWN_DRAIN_SECONDS) -> bool:
    active = _active
    return True if active is None else active.flush(timeout)


@atexit.register
def _drain_diagnostics_at_exit() -> None:
    """The writer thread is a daemon, so a clean exit has to ask it to finish.

    Registered rather than relied upon: normal shutdown goes through the app
    lifespan. This is only for the paths that do not.
    """
    active = _active
    if active is not None:
        active.close()


def diagnostic_event(**values: object) -> None:
    active = _active
    if active is None:
        return
    if values.get("level") == "debug" and not logging.getLogger().isEnabledFor(
        logging.DEBUG
    ):
        return
    try:
        active.write(**values)  # type: ignore[arg-type]
    except OSError:
        # Diagnostics must not become a new product outage when a disk is full
        # or the log directory is temporarily unavailable.
        return
    except ValueError:
        # Same promise, same reason, for a rejected payload instead of a failed
        # write. An unlisted `details` key once turned every external recall
        # into a 500 and then killed the process, and the sedimentation switch
        # into a permanent failure. The payload itself is still wrong and is
        # still caught in CI by tests/test_diagnostic_payload_keys.py; what
        # must not happen is the caller paying for it.
        return


def current_session_id() -> str | None:
    return _active.session_id if _active else None


def diagnostics_enabled() -> bool:
    return _active is not None


def read_trace_events(
    data_dir: Path,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    limit: int = 500,
) -> tuple[dict[str, object], ...]:
    """Read a bounded, content-free projection of one diagnostic trace."""
    if (request_id is None) == (trace_id is None):
        raise ValueError("exactly_one_trace_identity_required")
    key, expected = ("request_id", request_id) if request_id is not None else ("trace_id", trace_id)
    if expected is None or not _IDENTIFIER.fullmatch(expected):
        raise ValueError("invalid_trace_identity")
    selected: list[dict[str, object]] = []
    allowed_details = {"phase", "mode", "status", "reason", "failure_site", "count"}
    files = sorted(
        (data_dir / "logs").glob("diagnostic-*.jsonl*"),
        key=lambda path: path.stat().st_mtime,
    )
    for path in files:
        if path.is_symlink() or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict) or event.get(key) != expected:
                continue
            details = event.get("details")
            projected = {
                name: event[name]
                for name in (
                    "timestamp",
                    "component",
                    "event",
                    "outcome",
                    "duration_ms",
                    "error_code",
                    "error_type",
                )
                if name in event
            }
            if isinstance(details, dict):
                projected["details"] = {
                    name: value for name, value in details.items() if name in allowed_details
                }
            selected.append(projected)
    return tuple(selected[-max(1, min(limit, 500)) :])


def current_trace_id() -> str | None:
    return _trace_id.get()


def bind_trace_id(value: str | None) -> tuple[str, Token[str | None]]:
    trace_id = _uuid(value) or str(uuid4())
    return trace_id, _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


def elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def resource_snapshot() -> dict[str, int]:
    """Return process counters without exposing descriptor targets."""
    open_files = 0
    descriptor_root = Path("/dev/fd")
    if descriptor_root.is_dir():
        try:
            descriptors = tuple(descriptor_root.iterdir())
        except OSError:
            descriptors = ()
        open_files = len(descriptors)
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else 0
    memory_bytes = int(usage if sys.platform == "darwin" else usage * 1024)
    with _database_errors_lock:
        database_error_count = sum(_database_errors.values())
    return {
        "open_file_count": open_files,
        "memory_bytes": max(0, memory_bytes),
        "thread_count": threading.active_count(),
        "database_error_count": database_error_count,
    }


def database_error_code(error: BaseException) -> str:
    text = str(error).lower()
    if "locked" in text:
        return "database_locked"
    if "busy" in text:
        return "database_busy"
    if "unable to open" in text or "cannot open" in text:
        return "database_cannot_open"
    if "readonly" in text or "read-only" in text:
        return "database_readonly"
    if "disk i/o" in text:
        return "database_disk_io"
    if "malformed" in text or "corrupt" in text:
        return "database_corrupt"
    return "database_other"


def record_database_error(
    component: str,
    error: BaseException,
    *,
    route_path: str | None = None,
) -> str:
    code = database_error_code(error)
    safe_component = _event_name(component)
    with _database_errors_lock:
        _database_errors[(safe_component, code)] += 1
        count = _database_errors[(safe_component, code)]
    sqlite_code = getattr(error, "sqlite_errorcode", None)
    sqlite_name = getattr(error, "sqlite_errorname", None)
    details: dict[str, object] = {"phase": safe_component, "count": count}
    if route_path is not None and route_path.startswith("/api/v1/"):
        details["route_path"] = route_path
    if isinstance(sqlite_code, int):
        details["sqlite_errorcode"] = sqlite_code
    if isinstance(sqlite_name, str) and _IDENTIFIER.fullmatch(sqlite_name):
        details["sqlite_errorname"] = sqlite_name
    diagnostic_event(
        level="error",
        component="core.database",
        event="database.error",
        outcome="failed",
        error_code=code,
        error_type=type(error).__name__,
        details=details,
    )
    return code


def diagnostic_exception(error: BaseException) -> dict[str, object]:
    frames: list[str] = []
    for frame in traceback.extract_tb(error.__traceback__):
        parts = Path(frame.filename).parts
        try:
            root = parts.index("one_memory")
        except ValueError:
            continue
        relative = "/".join(parts[root:])
        name = frame.name if _IDENTIFIER.fullmatch(frame.name) else "call"
        frames.append(f"{relative}:{frame.lineno}:{name}")
    error_type = type(error).__name__
    return {
        "error_type": error_type if _IDENTIFIER.fullmatch(error_type) else "Error",
        **({"safe_stack": tuple(frames[-16:])} if frames else {}),
    }


def _serialize(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n"


def _uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return str(parsed) if str(parsed) == value else None


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("diagnostic_identifier_invalid")
    return value


def _event_name(value: str) -> str:
    if not _EVENT.fullmatch(value):
        raise ValueError("diagnostic_event_invalid")
    return value


def _safe_stack_line(value: str) -> str:
    if not value or len(value) > 256 or "\n" in value or "\r" in value:
        raise ValueError("diagnostic_stack_invalid")
    if Path(value.split(":", 1)[0]).is_absolute() or "file://" in value:
        raise ValueError("diagnostic_stack_invalid")
    return value


def _details(
    values: Mapping[str, str | int | float | bool | None],
) -> dict[str, str | int | float | bool | None]:
    result: dict[str, str | int | float | bool | None] = {}
    for key, value in values.items():
        if key not in _DETAIL_KEYS:
            raise ValueError("diagnostic_detail_key_invalid")
        if isinstance(value, str):
            if not value or len(value) > 256 or "\n" in value or "\r" in value:
                raise ValueError("diagnostic_detail_value_invalid")
        elif value is not None and not isinstance(value, (int, float, bool)):
            raise ValueError("diagnostic_detail_value_invalid")
        result[key] = value
    return result
