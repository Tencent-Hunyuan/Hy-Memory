"""The one way into the main database: one write connection, a few readers.

Each store used to open the file itself, set its own pragmas and close it
again — or not. Nothing could say how many connections the Core held; a leak
was found only when the process ran out of file handles, and two threads that
read before writing met SQLite's refusal instead of waiting their turn.

Here writes are made one at a time on a single connection, and reads share a
small pool of read-only ones. Every checkout starts from the same state, so
what one caller changed on a connection does not reach the next.

Waiting for either is a queue, not a scramble: callers are admitted in the
order they arrived (see `_InTurn`), so what one waits for is bounded by those
ahead of it.
"""

import asyncio
import logging
import os
import sqlite3
import stat
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from one_memory.database import (
    WRITE_LOCK_WAIT_MS,
    enable_write_ahead_log,
    take_the_write_lock_first,
)
from one_memory.service_levels import current_service_level

# Reads are short and WAL lets them run beside the writer, so a handful covers
# the interface's polling alongside the background work; a caller beyond it
# waits for one to come back rather than opening another.
#
# Tunable, because production logs measured `readers_waiting` up to 20 and
# `reader_wait_ms_max` at 18 seconds with a pool of 4 — reads were queueing
# behind each other, not behind the writer. Capacity has to be adjustable to
# tune that without a code change.
#
# Read at use time rather than at import. A module-level constant forced tests
# to `importlib.reload` to change it, and reloading swaps the module object —
# which silently detached `monkeypatch` patches in unrelated tests. Reading per
# call keeps the module identity stable; it is a function so callers cannot
# capture a stale number.
DEFAULT_READER_POOL_SIZE = 12


def reader_pool_size() -> int:
    raw = os.environ.get("ONE_MEMORY_READER_POOL_SIZE", str(DEFAULT_READER_POOL_SIZE))
    size = int(raw)
    if size < 1:
        raise RuntimeError("ONE_MEMORY_READER_POOL_SIZE must be at least 1")
    return size


# Only one main database exists in a running Core. Tests open one per case;
# past this many, the least recently used give their idle connections back.
_KEPT_OPEN = 8

# Connection settings a caller can change and `_reset` does not put back, and
# whether anything was ever created in TEMP. Read as plain pragmas (about 12µs):
# the `pragma_*` table form takes a lock on the file and would wait out a
# writer. A connection handed back different from SQLite's defaults is closed
# instead of pooled.
_SETTINGS = (
    "synchronous",
    "cache_size",
    "temp_store",
    "recursive_triggers",
    "automatic_index",
    "cell_size_check",
    "ignore_check_constraints",
    "reverse_unordered_selects",
    "trusted_schema",
    "secure_delete",
    "writable_schema",
    "legacy_alter_table",
    "temp.schema_version",
)


def _settings(connection: sqlite3.Connection) -> tuple[Any, ...]:
    return tuple(connection.execute(f"PRAGMA {name}").fetchone()[0] for name in _SETTINGS)


def _default_settings() -> tuple[Any, ...]:
    # Read once, before anyone uses the database: no file is opened, and a
    # statement run at each open would be one more a caller's hooks see.
    with closing(sqlite3.connect(":memory:")) as pristine:
        return _settings(pristine)


_DEFAULTS = _default_settings()

# Often enough to see a climb within a session, rare enough to stay out of the way.
MAIN_DATABASE_REPORT_SECONDS = 60.0

# A checkpoint is cheap when the WAL has no new frames, but it still takes the
# serialized writer. Do it often enough to keep the WAL bounded under a hot
# import without turning it into a per-transaction cost.
MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL = 64
MAIN_DATABASE_CHECKPOINT_SECONDS = 30.0

# Advisory only: PASSIVE cannot force a frame past a long reader. Crossing this
# threshold is a signal that the WAL is not converging and that read snapshots
# or write volume need investigation.
MAIN_DATABASE_WAL_WARN_BYTES = 512 * 1024 * 1024


def checkpoint_write_interval() -> int:
    raw = os.environ.get(
        "ONE_MEMORY_MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL",
        str(MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL),
    )
    value = int(raw)
    if value < 1:
        raise RuntimeError("ONE_MEMORY_MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL must be positive")
    return value


logger = logging.getLogger("one_memory.database_access")

RowFactory = Callable[[sqlite3.Cursor, tuple[Any, ...]], Any] | type[sqlite3.Row] | None


@dataclass(frozen=True)
class MainDatabaseStats:
    open_connections: int
    peak_open_connections: int
    writer_in_use: bool
    readers_in_use: int
    writer_checkouts: int
    writer_waits: int
    writer_wait_ms_total: float
    writer_wait_ms_max: float
    reader_checkouts: int
    reader_waits: int
    reader_wait_ms_total: float
    reader_wait_ms_max: float
    writers_waiting: int
    readers_waiting: int
    nested_refusals: int
    reader_held_ms_max: float


class NestedCheckoutError(RuntimeError):
    """A thread asked for the main database while already holding it.

    The caller should use the connection it is inside. A second write
    connection could only wait out SQLite's one writer and fail, or commit
    apart from the transaction around it; a second reader goes past the pool.
    """


class _Waits:
    def __init__(self) -> None:
        self.checkouts = 0
        self.waits = 0
        self.total_ms = 0.0
        self.max_ms = 0.0

    def record(self, waited_ms: float, *, waited: bool) -> None:
        self.checkouts += 1
        if waited:
            self.waits += 1
            self.total_ms += waited_ms
            self.max_ms = max(self.max_ms, waited_ms)

    def record_refusal(self, waited_ms: float) -> None:
        """A caller that waited its whole budget and got nothing.

        Counted apart from a checkout, which it never became. Left out, the
        log reported the longest wait as whatever the slowest *successful*
        caller saw and could never reach the budget — so the waits that
        actually cost a user their import were the ones it did not show.
        """
        self.waits += 1
        self.total_ms += waited_ms
        self.max_ms = max(self.max_ms, waited_ms)


class _InTurn:
    """Admission in the order callers arrived, rather than a scramble.

    `Condition.wait_for` on its own does not queue. Every release wakes
    everyone waiting and whichever thread the interpreter happens to run next
    takes the place, so a caller can be passed over by arrivals again and
    again — for as long as its budget lasts, at which point this layer raises
    SQLite's "database is locked" at it although nothing was ever locked. A
    catch-up over the user's own corpus died that way after 54 minutes while
    callers that came later were being served hundreds of times.

    Holding a place puts a ceiling on the wait: whoever is k-th waits for the
    k-1 ahead of it to have their turn, and no longer.
    """

    def __init__(self) -> None:
        self._line: list[tuple[int, int]] = []
        self._next_place = 0

    def __len__(self) -> int:
        return len(self._line)

    def join(self) -> tuple[int, int]:
        place = self._next_place
        self._next_place += 1
        ticket = (int(current_service_level()), place)
        self._line.append(ticket)
        return ticket

    def at_the_front(self, place: tuple[int, int]) -> bool:
        return bool(self._line) and min(self._line) == place

    def leave(self, place: tuple[int, int]) -> None:
        # At the front when admitted; anywhere in it when the budget ran out.
        with suppress(ValueError):
            self._line.remove(place)


def _locked() -> sqlite3.OperationalError:
    # Callers tell a busy database from a broken one by these, as they would
    # for SQLite's own refusal.
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    error.sqlite_errorname = "SQLITE_BUSY"
    return error


def _missing() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("unable to open database file")


def _remove_stale_sidecar(path: Path) -> None:
    """Remove a sidecar after the main file's commit point.

    Once `os.replace` publishes the verified restore, stale `-wal`/`-shm`
    sidecars are cleanup, not the restore's result. A filesystem that still
    holds one open must not turn a successful publish into a reported failure.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("main_database_sidecar_cleanup_failed path=%s", path, exc_info=True)


class MainDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._state = threading.Condition()
        self._writer: sqlite3.Connection | None = None
        # False while the writer still has the file's own journal mode: opened
        # for a migration that switches to WAL only once it has committed.
        self._writer_in_wal = False
        self._writer_owner: int | None = None
        self._idle_readers: list[sqlite3.Connection] = []
        self._readers_open = 0
        self._reader_holders: dict[int, int] = {}
        self._nested_refusals = 0
        self._peak = 0
        self._generation = 0
        self._born: dict[int, int] = {}
        self._identity: tuple[int, int] | None = None
        self._writer_waits = _Waits()
        self._reader_waits = _Waits()
        self._write_line = _InTurn()
        self._read_line = _InTurn()
        self._writes_since_checkpoint = 0
        self._last_checkpoint_at = time.monotonic()
        self._wal_warned_at = 0.0
        self._reader_started_at: dict[int, float] = {}

    @contextmanager
    def write(
        self,
        *,
        autocommit: bool | int = sqlite3.LEGACY_TRANSACTION_CONTROL,
        foreign_keys: bool = False,
        row_factory: RowFactory = None,
        create: bool = True,
        busy_timeout_ms: int = WRITE_LOCK_WAIT_MS,
        keep_journal_mode: bool = False,
        maintenance: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        """The write connection, held by this caller alone until it leaves.

        `create=False` refuses a missing file instead of creating an empty
        one. A transaction left open on leaving is rolled back, as closing a
        connection did. Every checkout switches the file to WAL first, except
        with `keep_journal_mode`: a migration that must leave a library it
        could not finish exactly as it found it.
        """
        me = threading.get_ident()
        started = time.monotonic()
        with self._state:
            if self._writer_owner == me:
                raise self._refuse_nested("write")
            place = self._write_line.join()
            free = self._writer_owner is None and self._write_line.at_the_front(place)
            try:
                if not self._state.wait_for(
                    lambda: self._writer_owner is None and self._write_line.at_the_front(place),
                    timeout=busy_timeout_ms / 1_000,
                ):
                    self._writer_waits.record_refusal((time.monotonic() - started) * 1_000)
                    self._report_lock_refusal("write", busy_timeout_ms, started)
                    raise _locked()
            finally:
                # Admitted or not, the place is given up here; whoever is next
                # has to be told, or a line with no holder would sit still.
                self._write_line.leave(place)
                self._state.notify_all()
            self._writer_owner = me
            self._writer_waits.record((time.monotonic() - started) * 1_000, waited=not free)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._checked_writer(create, busy_timeout_ms, wal=not keep_journal_mode)
            _prepare(connection, autocommit, foreign_keys, row_factory, busy_timeout_ms)
            yield connection
        finally:
            with self._state:
                if connection is not None and (
                    not _reset(connection, reading=False)
                    or self._born.get(id(connection)) != self._generation
                ):
                    self._drop_writer()
                self._writer_owner = None
                self._state.notify_all()
            if connection is not None and not keep_journal_mode and not maintenance:
                self._maybe_checkpoint_after_write()

    @contextmanager
    def read(
        self,
        *,
        foreign_keys: bool = False,
        row_factory: RowFactory = None,
        busy_timeout_ms: int = WRITE_LOCK_WAIT_MS,
    ) -> Iterator[sqlite3.Connection]:
        """A read-only connection from the pool; writing through it fails."""
        me = threading.get_ident()
        with self._state:
            if self._reader_holders.get(me, 0) > 0:
                raise self._refuse_nested("read")
        connection = self._take_reader(me, busy_timeout_ms)
        started_at = time.monotonic()
        with self._state:
            self._reader_started_at[me] = started_at
        try:
            _prepare(
                connection,
                sqlite3.LEGACY_TRANSACTION_CONTROL,
                foreign_keys,
                row_factory,
                busy_timeout_ms,
            )
            yield connection
        finally:
            healthy = _reset(connection, reading=True)
            with self._state:
                self._reader_started_at.pop(me, None)
                self._reader_holders[me] -= 1
                if not self._reader_holders[me]:
                    del self._reader_holders[me]
                if healthy and self._born.get(id(connection)) == self._generation:
                    self._idle_readers.append(connection)
                else:
                    self._forget(connection)
                    _close(connection)
                    self._readers_open -= 1
                self._state.notify_all()

    def back_up(self, target: Path) -> None:
        """Copy the committed database to `target`, readable by its owner only.

        `target` is first reserved as an empty owner-only file, which fails if
        the name is taken, so an earlier backup is never replaced. The copy is
        made under a temporary name, also owner-only, and then moved over the
        reservation — the one file that is certainly this call's own. Neither
        step needs hard links, which exFAT, FAT and some network shares refuse.
        Killed part-way, what is left under `target` is at most empty, never a
        half-written copy. The directory must be a real one, not a link to
        somewhere else.
        """
        directory = target.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise NotADirectoryError(f"backups directory is not a plain directory: {directory}")
        directory.chmod(0o700)
        _create_owner_only(target)
        partial = directory / f".{target.name}.partial"
        published = False
        try:
            _create_owner_only(partial)
            with self.read() as source, closing(sqlite3.connect(partial)) as copy:
                source.backup(copy)
                # "A backup is only a backup if it can be opened." Verify the
                # copy before publishing it: a truncated or corrupt copy that
                # replaced `target` would look like safety while being none.
                if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise sqlite3.DatabaseError("backup failed SQLite quick_check")
            # fsync the file (and the directory below) so a crash right after
            # this returns cannot leave the published name pointing at data the
            # filesystem has not written out.
            descriptor = os.open(partial, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(partial, target)
            published = True
            directory_descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except BaseException:
            # Before `os.replace`, `target` is only this call's reservation.
            # After it, `target` is the verified rollback copy: a directory
            # fsync failure is reported, but erasing the copy would leave the
            # upgrade with no way back.
            if not published:
                target.unlink(missing_ok=True)
            raise
        finally:
            partial.unlink(missing_ok=True)

    def restore_from(
        self,
        source: Path,
        *,
        required_tables: frozenset[str] = frozenset(),
    ) -> None:
        """Atomically replace this database from a verified SQLite backup.

        The Runtime must already hold the One Memory Home process lock. This
        method additionally refuses active in-process checkouts, validates the
        source before touching the live file, restores through a private file,
        and validates that copy before the atomic replace.

        `required_tables` names the tables the source must contain, so a caller
        at the product boundary can prove the file is really a One Memory
        database rather than some other readable SQLite file. It defaults to
        empty because this access layer is engine/product-neutral; a
        product-level caller (the restore command) passes `{"evidence"}` so a
        foreign database cannot silently replace the user's memory.
        """
        if source.is_symlink():
            raise ValueError("backup source must not be a symbolic link")
        try:
            source_status = source.stat()
        except FileNotFoundError as error:
            raise ValueError("backup source does not exist") from error
        if not stat.S_ISREG(source_status.st_mode):
            raise ValueError("backup source must be a regular file")

        resolved_source = source.resolve()
        if resolved_source == self.path:
            raise ValueError("backup source must differ from the live database")

        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise NotADirectoryError(f"database directory is not a plain directory: {directory}")
        partial = directory / f".{self.path.name}.restore-{uuid4().hex}.partial"

        with self._state:
            if self._writer_owner is not None or self._reader_holders:
                raise RuntimeError("database restore requires all checkouts to be released")
            try:
                with closing(
                    sqlite3.connect(
                        f"{resolved_source.as_uri()}?mode=ro",
                        uri=True,
                    )
                ) as backup:
                    if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise sqlite3.DatabaseError("backup failed SQLite quick_check")
                    if required_tables:
                        present = {
                            str(row[0])
                            for row in backup.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                        missing = required_tables - present
                        if missing:
                            raise sqlite3.DatabaseError(
                                f"backup is not a One Memory database; missing {sorted(missing)}"
                            )
                    # Only now is the source proven usable. A failed
                    # validation must leave the live pool exactly as it was;
                    # closing idle readers before this point made a rejected
                    # restore churn connections for no reason.
                    self._generation += 1
                    self._identity = None
                    self._close_idle()
                    _create_owner_only(partial)
                    with closing(sqlite3.connect(partial)) as restored:
                        backup.backup(restored)
                        if restored.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                            raise sqlite3.DatabaseError("restored copy failed SQLite quick_check")
                partial.chmod(0o600)
                descriptor = os.open(partial, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(partial, self.path)
                # The main file is now the verified restored copy. Until the
                # replace succeeds, the old WAL can still hold committed rows:
                # deleting it first turned a failed replace into data loss.
                _remove_stale_sidecar(Path(f"{self.path}-wal"))
                _remove_stale_sidecar(Path(f"{self.path}-shm"))
                directory_descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
            finally:
                partial.unlink(missing_ok=True)
                self._identity = None
                self._state.notify_all()

    def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        """Checkpoint this database's WAL through the serialized writer.

        PASSIVE is safe while readers are active and reports how far it got;
        stronger modes are available to controlled shutdown/maintenance paths.
        The fixed allowlist keeps a caller-controlled mode out of SQL text.
        """
        return self._checkpoint(mode, maintenance=False)

    def _checkpoint_from_maintenance(self, mode: str) -> tuple[int, int, int]:
        return self._checkpoint(mode, maintenance=True)

    def _checkpoint(self, mode: str, *, maintenance: bool) -> tuple[int, int, int]:
        normalized = mode.strip().upper()
        if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError("unsupported WAL checkpoint mode")
        with self.write(autocommit=True, maintenance=maintenance) as connection:
            row = connection.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
        if row is None or len(row) != 3:
            raise sqlite3.DatabaseError("SQLite returned an invalid checkpoint result")
        return int(row[0]), int(row[1]), int(row[2])

    def _maybe_checkpoint_after_write(self) -> None:
        """Checkpoint on a write cadence without opening another connection.

        SQLite's default autocheckpoint only runs when the writer itself
        crosses the page threshold. Production hit a state where the WAL grew
        to 13GB, so the main database needs its own bounded, observable
        maintenance path. This runs after the caller's checkout has been reset
        and only re-enters the same serialized writer queue.
        """
        now = time.monotonic()
        with self._state:
            self._writes_since_checkpoint += 1
            due = (
                self._writes_since_checkpoint >= checkpoint_write_interval()
                or now - self._last_checkpoint_at >= MAIN_DATABASE_CHECKPOINT_SECONDS
            )
            if due:
                self._writes_since_checkpoint = 0
                self._last_checkpoint_at = now
        if not due:
            return
        try:
            busy, log_frames, checkpointed = self._checkpoint_from_maintenance("PASSIVE")
        except sqlite3.Error:
            logger.warning("main_database_checkpoint_failed file=%s", self.path.name, exc_info=True)
            return
        wal_bytes = self._wal_bytes()
        if wal_bytes >= MAIN_DATABASE_WAL_WARN_BYTES:
            with self._state:
                should_warn = now - self._wal_warned_at >= MAIN_DATABASE_REPORT_SECONDS
                if should_warn:
                    self._wal_warned_at = now
            if should_warn:
                logger.warning(
                    "main_database_wal_high file=%s wal_bytes=%d busy=%d "
                    "log_frames=%d checkpointed=%d readers_in_use=%d",
                    self.path.name,
                    wal_bytes,
                    busy,
                    log_frames,
                    checkpointed,
                    sum(self._reader_holders.values()),
                )

    def _wal_bytes(self) -> int:
        try:
            return Path(f"{self.path}-wal").stat().st_size
        except FileNotFoundError:
            return 0

    def stats(self) -> MainDatabaseStats:
        with self._state:
            return MainDatabaseStats(
                open_connections=self._open_count(),
                peak_open_connections=self._peak,
                writer_in_use=self._writer_owner is not None,
                readers_in_use=sum(self._reader_holders.values()),
                writer_checkouts=self._writer_waits.checkouts,
                writer_waits=self._writer_waits.waits,
                writer_wait_ms_total=self._writer_waits.total_ms,
                writer_wait_ms_max=self._writer_waits.max_ms,
                reader_checkouts=self._reader_waits.checkouts,
                reader_waits=self._reader_waits.waits,
                reader_wait_ms_total=self._reader_waits.total_ms,
                reader_wait_ms_max=self._reader_waits.max_ms,
                writers_waiting=len(self._write_line),
                readers_waiting=len(self._read_line),
                nested_refusals=self._nested_refusals,
                reader_held_ms_max=self._reader_held_ms_max(),
            )

    def _reader_held_ms_max(self) -> float:
        """How long the oldest read checkout still in use has been held.

        A read snapshot held across a long stretch of work is what stops a
        PASSIVE checkpoint from advancing. Naming the worst current hold turns
        that from an inference into one number in the periodic log.
        """
        now = time.monotonic()
        holders = (
            now - started
            for thread, started in self._reader_started_at.items()
            if self._reader_holders.get(thread, 0) > 0
        )
        return round(max(holders, default=0.0) * 1_000, 1)

    def close(self) -> None:
        """Close every idle connection now; ones in use close when handed back."""
        with self._state:
            self._generation += 1
            self._identity = None
            self._close_idle()

    def _report_lock_refusal(self, kind: str, busy_timeout_ms: int, started: float) -> None:
        """Make a `database is locked` refusal visible at the moment it happens.

        The periodic line reports counters, which shows that waits exist but
        not what held the line. A refusal during an import peak is the
        user-visible symptom, so it is logged immediately with the lock
        holder, how deep the queue was and how long this caller waited —
        never a bare average.
        """
        waited_ms = round((time.monotonic() - started) * 1_000, 1)
        with self._state:
            if kind == "write":
                holder = self._writer_owner
                waiting = len(self._write_line)
                max_wait = self._writer_waits.max_ms
            else:
                holder = None
                waiting = len(self._read_line)
                max_wait = self._reader_waits.max_ms
            reader_holders = sum(self._reader_holders.values())
            idle_readers = len(self._idle_readers)
            open_readers = self._readers_open
        logger.error(
            "main_database_lock_refused kind=%s waited_ms=%.1f budget_ms=%d "
            "holder_thread=%s waiting_now=%d reader_holders=%d idle_readers=%d "
            "open_readers=%d wait_ms_max_recent=%.1f file=%s",
            kind,
            waited_ms,
            busy_timeout_ms,
            holder,
            waiting,
            reader_holders,
            idle_readers,
            open_readers,
            max_wait,
            self.path.name,
        )

    def _refuse_nested(self, kind: str) -> NestedCheckoutError:
        global _nested_refusals_total
        self._nested_refusals += 1
        with _databases_lock:
            _nested_refusals_total += 1
        logger.error("main_database_nested_checkout kind=%s", kind, stack_info=True)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return NestedCheckoutError(f"nested main-database {kind} on one thread")
        # Waiting here would block the loop, and with it the holder.
        return NestedCheckoutError(
            f"main-database {kind} already held on the event loop's thread: "
            "a coroutine keeps a checkout across an await"
        )

    def _room_to_read(self) -> bool:
        return bool(self._idle_readers) or self._readers_open < reader_pool_size()

    def _open_count(self) -> int:
        return (self._writer is not None) + self._readers_open

    def _note_opened(self) -> None:
        self._peak = max(self._peak, self._open_count())

    def _close_idle(self) -> None:
        for connection in self._idle_readers:
            self._forget(connection)
            _close(connection)
        self._readers_open -= len(self._idle_readers)
        self._idle_readers.clear()
        if self._writer is not None and self._writer_owner is None:
            self._drop_writer()

    def _drop_writer(self) -> None:
        if self._writer is not None:
            self._forget(self._writer)
            _close(self._writer)
            self._writer = None
            self._writer_in_wal = False

    def _forget(self, connection: sqlite3.Connection) -> None:
        self._born.pop(id(connection), None)
        _forget_registered(connection)

    def _follow_the_file(self, *, create: bool) -> None:
        """Start afresh if the file was replaced or removed under us."""
        try:
            status = os.stat(self.path)
            identity: tuple[int, int] | None = (status.st_dev, status.st_ino)
        except FileNotFoundError:
            identity = None
        with self._state:
            if self._identity is not None and identity != self._identity:
                self._generation += 1
                self._close_idle()
            self._identity = identity
        if identity is None and not create:
            raise _missing()

    def _checked_writer(
        self, create: bool, busy_timeout_ms: int, *, wal: bool
    ) -> sqlite3.Connection:
        self._follow_the_file(create=create)
        with self._state:
            if self._writer is not None and self._born.get(id(self._writer)) != self._generation:
                self._drop_writer()
            writer = self._writer
            generation = self._generation
        if writer is not None:
            # Only the caller holding the writer gets here, so no one else
            # can be switching it at the same time.
            if wal and not self._writer_in_wal:
                # Each attempt waits the connection's own timeout; `_prepare`
                # sets the caller's only after this.
                writer.execute(f"PRAGMA busy_timeout = {max(0, int(busy_timeout_ms))}")
                enable_write_ahead_log(writer, wait_ms=busy_timeout_ms)
                self._writer_in_wal = True
            return writer
        connection = self._open_writer(create, busy_timeout_ms, wal=wal)
        with self._state:
            self._writer = connection
            self._writer_in_wal = wal
            self._born[id(connection)] = generation
            self._note_opened()
            if self._identity is None:
                status = os.stat(self.path)
                self._identity = (status.st_dev, status.st_ino)
        return connection

    def _open_writer(
        self, create: bool, busy_timeout_ms: int, *, wal: bool = True
    ) -> sqlite3.Connection:
        if not create and not self.path.exists():
            raise _missing()
        connection = sqlite3.connect(
            self.path, timeout=busy_timeout_ms / 1_000, check_same_thread=False
        )
        try:
            if wal:
                enable_write_ahead_log(connection, wait_ms=busy_timeout_ms)
            _seal(connection)
            if busy_timeout_ms != WRITE_LOCK_WAIT_MS:
                # Opened at the caller's wait; every checkout starts at the default.
                connection.execute(f"PRAGMA busy_timeout = {WRITE_LOCK_WAIT_MS}")
            _tighten_owner(self.path)
        except BaseException:
            connection.close()
            raise
        return connection

    def _open_reader(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=ro",
            uri=True,
            timeout=WRITE_LOCK_WAIT_MS / 1_000,
            check_same_thread=False,
        )
        try:
            _seal(connection)
            connection.execute("PRAGMA query_only = ON")
        except BaseException:
            connection.close()
            raise
        return connection

    def _take_reader(self, me: int, busy_timeout_ms: int) -> sqlite3.Connection:
        self._follow_the_file(create=False)
        started = time.monotonic()
        with self._state:
            place = self._read_line.join()
            free = self._room_to_read() and self._read_line.at_the_front(place)
            try:
                if not self._state.wait_for(
                    lambda: self._room_to_read() and self._read_line.at_the_front(place),
                    timeout=busy_timeout_ms / 1_000,
                ):
                    self._reader_waits.record_refusal((time.monotonic() - started) * 1_000)
                    self._report_lock_refusal("read", busy_timeout_ms, started)
                    raise _locked()
            finally:
                self._read_line.leave(place)
                self._state.notify_all()
            self._reader_waits.record((time.monotonic() - started) * 1_000, waited=not free)
            self._reader_holders[me] = self._reader_holders.get(me, 0) + 1
            if self._idle_readers:
                return self._idle_readers.pop()
            self._readers_open += 1
            generation = self._generation
        try:
            connection = self._open_reader()
        except BaseException:
            with self._state:
                self._readers_open -= 1
                self._reader_holders[me] -= 1
                if not self._reader_holders[me]:
                    del self._reader_holders[me]
                self._state.notify_all()
            raise
        with self._state:
            self._born[id(connection)] = generation
            self._note_opened()
        return connection


def _create_owner_only(path: Path) -> None:
    # O_EXCL alone refuses an existing name, a symlink included; O_NOFOLLOW is
    # not there to add on Windows.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    os.close(os.open(path, flags, 0o600))


def _tighten_owner(path: Path) -> None:
    """Keep a store readable by its owner alone.

    `sqlite3.connect` creates at the process umask, which is usually 0644, so
    every database someone trusted us with was world readable until it was
    reopened here. The write-ahead sidecars are created the same way and carry
    committed entries, so they are tightened too. A name that is not there yet
    is not an error: the sidecars come and go with the checkpoint.
    """
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            os.chmod(candidate, 0o600)
        except OSError:
            # Not created yet, or not ours to change. Neither should break a
            # checkout of a database the caller can already read and write.
            continue


def _prepare(
    connection: sqlite3.Connection,
    autocommit: bool | int,
    foreign_keys: bool,
    row_factory: RowFactory,
    busy_timeout_ms: int,
) -> None:
    connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
    # What `sqlite3.connect` starts with; typeshed does not list it.
    connection.isolation_level = ""  # type: ignore[assignment]
    connection.row_factory = row_factory
    # Every connection is handed back at the defaults (see `_reset`), so only
    # a departure from them costs a statement.
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys = ON")
    if busy_timeout_ms != WRITE_LOCK_WAIT_MS:
        connection.execute(f"PRAGMA busy_timeout = {max(0, int(busy_timeout_ms))}")
    connection.autocommit = autocommit


def _seal(connection: sqlite3.Connection) -> None:
    # `mode=ro` covers only the main file: an attached one would be writable,
    # and the writer's lock would not serialize it. Nothing here attaches.
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)


def _reset(connection: sqlite3.Connection, *, reading: bool) -> bool:
    """Hand a connection back clean; False if it can no longer be used."""
    try:
        connection.set_trace_callback(None)
        connection.set_authorizer(None)
        connection.set_progress_handler(None, 0)
        # Leaving autocommit=True would commit what is pending; leaving False
        # would open the next transaction. Legacy does neither.
        connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        _unregister(connection)
        if connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0) != 0:
            return False
        # Without waiting: one that never loaded the schema must lock the file
        # to read even these, and is not worth a writer's wait to keep.
        connection.execute("PRAGMA busy_timeout = 0")
        clean = _settings(connection) == _DEFAULTS
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(f"PRAGMA busy_timeout = {WRITE_LOCK_WAIT_MS}")
        connection.execute(f"PRAGMA query_only = {'ON' if reading else 'OFF'}")
        connection.row_factory = None
        return clean
    except sqlite3.Error:
        return False


_registered: dict[int, set[tuple[str, int]]] = {}
_registered_lock = threading.Lock()
_COLLATION = -2


def register_function(
    connection: sqlite3.Connection,
    name: str,
    narg: int,
    func: Callable[..., Any],
    *,
    deterministic: bool = False,
) -> None:
    """An SQL function for this checkout; the connection goes back without it."""
    connection.create_function(name, narg, func, deterministic=deterministic)
    with _registered_lock:
        _registered.setdefault(id(connection), set()).add((name, narg))


def register_collation(
    connection: sqlite3.Connection, name: str, func: Callable[[str, str], int]
) -> None:
    """A collation for this checkout; the connection goes back without it."""
    connection.create_collation(name, func)
    with _registered_lock:
        _registered.setdefault(id(connection), set()).add((name, _COLLATION))


def _unregister(connection: sqlite3.Connection) -> None:
    with _registered_lock:
        registered = _registered.pop(id(connection), set())
    for name, narg in registered:
        if narg == _COLLATION:
            connection.create_collation(name, None)
        else:
            connection.create_function(name, narg, None)


def _forget_registered(connection: sqlite3.Connection) -> None:
    with _registered_lock:
        _registered.pop(id(connection), None)


def _close(connection: sqlite3.Connection) -> None:
    try:
        connection.close()
    except sqlite3.Error:
        logger.debug("main_database_close_failed", exc_info=True)


# The most recently used, kept whether or not anyone holds them; and every one
# still referenced anywhere, so a caller holding an access point — or inside
# one of its checkouts — never meets a second one for the same file.
_databases: "OrderedDict[Path, MainDatabase]" = OrderedDict()
_alive: "weakref.WeakValueDictionary[Path, MainDatabase]" = weakref.WeakValueDictionary()
_databases_lock = threading.Lock()
_nested_refusals_total = 0


def main_database(path: Path | str) -> MainDatabase:
    """The access point for this database file, the same one every time."""
    key = Path(path).resolve()
    with _databases_lock:
        database = _databases.get(key) or _alive.get(key)
        if database is None:
            database = _alive[key] = MainDatabase(key)
        _databases[key] = database
        _databases.move_to_end(key)
        retired = []
        while len(_databases) > _KEPT_OPEN:
            retired.append(_databases.popitem(last=False)[1])
    for old in retired:
        old.close()
    return database


@contextmanager
def main_database_transaction(
    path: Path | str, *, writing: bool = False, initialize: bool = False
) -> Iterator[sqlite3.Connection]:
    """One transaction, committed on leaving and rolled back on an error.

    Writing takes the write lock before anything is read (see
    `take_the_write_lock_first`); initializing runs schema statements on the
    write connection; otherwise every read inside sees the same snapshot.
    Rows come back as `sqlite3.Row`.
    """
    database = main_database(path)
    checkout = (
        database.write(foreign_keys=True, row_factory=sqlite3.Row)
        if writing or initialize
        else database.read(row_factory=sqlite3.Row)
    )
    with checkout as connection, connection:
        if writing:
            take_the_write_lock_first(connection)
        else:
            connection.autocommit = False
        yield connection


def close_main_databases() -> None:
    """Close every connection this process holds; used when the Core stops."""
    with _databases_lock:
        databases = list(_alive.values())
        _databases.clear()
    for database in databases:
        database.close()


def log_main_database_stats(moment: str) -> None:
    """One line in core.log: how many connections are open and who waited."""
    stats = main_database_stats()
    logger.debug(
        "main_database_connections moment=%s %s",
        moment,
        " ".join(f"{key}={value}" for key, value in stats.items()),
    )


async def report_main_database(seconds: float) -> None:
    while True:
        await asyncio.sleep(seconds)
        log_main_database_stats("periodic")


def main_database_stats() -> dict[str, float | int]:
    """Totals across every database this process still holds, for the log."""
    with _databases_lock:
        databases = list(_alive.values())
    stats = [database.stats() for database in databases]
    return {
        "open_connections": sum(s.open_connections for s in stats),
        "peak_open_connections": max((s.peak_open_connections for s in stats), default=0),
        # Checkout totals, not just waits. A sample whose load runs in another
        # process shows zero here, which is what proves a soak is measuring its
        # own writes rather than a quiet sampler's empty counters.
        "writer_checkouts": sum(s.writer_checkouts for s in stats),
        "reader_checkouts": sum(s.reader_checkouts for s in stats),
        "writer_waits": sum(s.writer_waits for s in stats),
        "writer_wait_ms_total": round(sum(s.writer_wait_ms_total for s in stats), 1),
        "writer_wait_ms_max": round(max((s.writer_wait_ms_max for s in stats), default=0.0), 1),
        "reader_waits": sum(s.reader_waits for s in stats),
        "reader_wait_ms_total": round(sum(s.reader_wait_ms_total for s in stats), 1),
        "reader_wait_ms_max": round(max((s.reader_wait_ms_max for s in stats), default=0.0), 1),
        "writers_waiting": sum(s.writers_waiting for s in stats),
        "readers_waiting": sum(s.readers_waiting for s in stats),
        # Process-wide: a database no longer held takes its own count with it.
        "nested_refusals": _nested_refusals_total,
        "reader_held_ms_max": round(max((s.reader_held_ms_max for s in stats), default=0.0), 1),
    }
