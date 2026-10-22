"""One process, one write connection, a few readers: the only way into the main database.

Before this, 85 places opened the file themselves, each with its own pragmas,
and nothing could say how many connections the Core held. Two writers that
read before writing met SQLite's refusal (`database is locked`) instead of
waiting, and a leak was only found when the process ran out of file handles.
"""

import asyncio
import errno
import gc
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from one_memory import database_access as _access
from one_memory.database_access import (
    NestedCheckoutError,
    close_main_databases,
    main_database,
    main_database_stats,
    reader_pool_size,
)

#: Read at import, matching the pool the access layer will use by default.
LIMIT = 1 + reader_pool_size()


@pytest.fixture(autouse=True)
def _closed_afterwards():
    yield
    close_main_databases()


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "one-memory.sqlite3"
    with main_database(path).write() as db, db:
        db.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")
        db.execute("INSERT INTO counter VALUES (1, 0)")
        db.execute("CREATE TABLE rows (thread INTEGER, n INTEGER)")
    return path


def _open_connections_to(path: Path) -> int:
    """Connections whose main database is `path`, not every live connection.

    A process-wide count is contaminated by raw `sqlite3.connect()` handles
    left by neighboring tests. `PRAGMA database_list` identifies each
    connection's resolved main file, so this keeps the leak assertion scoped to
    the database under test.
    """
    expected = path.resolve()
    count = 0
    for item in gc.get_objects():
        if isinstance(item, sqlite3.Connection):
            try:
                _ = item.total_changes
                rows = item.execute("PRAGMA database_list").fetchall()
            except sqlite3.ProgrammingError:
                continue
            if any(Path(str(row[2])).resolve() == expected for row in rows):
                count += 1
    return count


def test_concurrent_writers_that_read_first_are_serialized_not_refused(tmp_path):
    # Read, then write, on a deferred transaction: with one connection per
    # writer, a writer whose snapshot went stale is refused at once with
    # `database is locked` rather than made to wait.
    path = _database(tmp_path)
    threads, per_thread = 16, 25
    errors: list[BaseException] = []
    start = threading.Barrier(threads)

    def work(thread: int) -> None:
        start.wait()
        for n in range(per_thread):
            try:
                with main_database(path).write() as db, db:
                    (value,) = db.execute("SELECT value FROM counter WHERE id = 1").fetchone()
                    time.sleep(0.0005)
                    db.execute("UPDATE counter SET value = ? WHERE id = 1", (value + 1,))
                    db.execute("INSERT INTO rows VALUES (?, ?)", (thread, n))
            except BaseException as error:  # noqa: BLE001 - every failure counts
                errors.append(error)

    workers = [threading.Thread(target=work, args=(t,)) for t in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert [str(e) for e in errors] == []
    with main_database(path).read() as db:
        assert db.execute("SELECT value FROM counter").fetchone()[0] == threads * per_thread
        assert db.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == threads * per_thread


def test_open_connections_stay_within_one_writer_and_the_reader_pool(tmp_path):
    path = _database(tmp_path)
    stop = threading.Event()
    seen: list[int] = []
    errors: list[BaseException] = []

    def write() -> None:
        while not stop.is_set():
            try:
                with main_database(path).write() as db, db:
                    db.execute("INSERT INTO rows VALUES (0, 0)")
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

    def read() -> None:
        while not stop.is_set():
            try:
                with main_database(path).read() as db:
                    db.execute("SELECT COUNT(*) FROM rows").fetchone()
                    time.sleep(0.001)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

    workers = [threading.Thread(target=write) for _ in range(4)]
    workers += [threading.Thread(target=read) for _ in range(12)]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        seen.append(main_database(path).stats().open_connections)
        time.sleep(0.01)
    stop.set()
    for worker in workers:
        worker.join()

    assert errors == []
    assert max(seen) > 1, "the sampler must be able to see connections open"
    assert max(seen) <= LIMIT
    assert main_database(path).stats().peak_open_connections <= LIMIT
    assert _open_connections_to(path) <= LIMIT


def test_closing_leaves_no_connection_behind(tmp_path):
    path = _database(tmp_path)
    before = _open_connections_to(path)
    with main_database(path).read() as db:
        db.execute("SELECT 1").fetchone()
    assert main_database(path).stats().open_connections >= 1
    assert _open_connections_to(path) > before - 1

    close_main_databases()

    assert main_database(path).stats().open_connections == 0
    assert main_database_stats()["open_connections"] == 0
    assert _open_connections_to(path) == 0


def test_the_count_moves_with_what_is_actually_open(tmp_path):
    path = tmp_path / "one-memory.sqlite3"
    database = main_database(path)
    assert database.stats().open_connections == 0
    with database.write() as db:
        db.execute("CREATE TABLE t (x)")
        assert database.stats().open_connections == 1
        assert database.stats().writer_in_use
    with database.read() as db:
        db.execute("SELECT * FROM t").fetchall()
    assert database.stats().open_connections == 2
    assert main_database_stats()["open_connections"] >= 2
    database.close()
    assert database.stats().open_connections == 0


def test_the_log_line_reports_the_real_count(tmp_path, caplog):
    from one_memory.database_access import log_main_database_stats

    caplog.set_level("DEBUG", logger="one_memory.database_access")
    path = tmp_path / "one-memory.sqlite3"
    log_main_database_stats("before")
    with main_database(path).write() as db:
        db.execute("CREATE TABLE t (x)")
        log_main_database_stats("during")
    close_main_databases()
    log_main_database_stats("after")
    lines = [r.getMessage() for r in caplog.records if "main_database_connections" in r.message]
    counts = [int(line.split("open_connections=")[1].split()[0]) for line in lines]
    assert counts[1] == counts[0] + 1
    assert counts[2] == 0


def test_backup_can_restore_a_damaged_live_database(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    backup = tmp_path / "backups" / "known-good.sqlite3"
    database.back_up(backup)
    database.close()
    path.write_bytes(b"physically damaged database")

    database.restore_from(backup)

    with database.read() as db:
        assert db.execute("SELECT value FROM counter WHERE id = 1").fetchone() == (0,)
        assert db.execute("PRAGMA quick_check").fetchall() == [("ok",)]


def test_restore_rejects_a_corrupt_backup_without_touching_live_data(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        database.restore_from(corrupt)

    with database.read() as db:
        assert db.execute("SELECT value FROM counter WHERE id = 1").fetchone() == (0,)


def test_rejected_restore_keeps_the_idle_reader_pool(tmp_path):
    """A backup that fails validation must not churn the reader pool.

    Validation happens before the live file is touched. Closing idle readers
    and advancing the generation before that validation makes a stream of bad
    restore attempts reopen connections and discards a healthy pool, even
    though the restore contract says the failure has no effect beyond the
    exception.
    """
    path = _database(tmp_path)
    database = main_database(path)
    with database.read() as db:
        db.execute("SELECT value FROM counter WHERE id = 1").fetchone()
    before = database.stats()
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        database.restore_from(corrupt)

    after = database.stats()
    assert after.open_connections == before.open_connections
    assert after.reader_checkouts == before.reader_checkouts


def test_restore_refuses_while_a_checkout_is_active(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    backup = tmp_path / "backup.sqlite3"
    database.back_up(backup)

    with database.read(), pytest.raises(RuntimeError, match="checkouts"):
        database.restore_from(backup)


def test_restore_publish_failure_keeps_live_database_and_cleans_partial(tmp_path, monkeypatch):
    path = _database(tmp_path)
    database = main_database(path)
    backup = tmp_path / "backup.sqlite3"
    database.back_up(backup)
    with database.write() as db, db:
        db.execute("UPDATE counter SET value = 99 WHERE id = 1")

    def disk_full(_source, _target):
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr(os, "replace", disk_full)
    with pytest.raises(OSError, match="disk full"):
        database.restore_from(backup)

    with database.read() as db:
        assert db.execute("SELECT value FROM counter WHERE id = 1").fetchone() == (99,)
    assert list(tmp_path.glob(".one-memory.sqlite3.restore-*.partial")) == []


def test_passive_checkpoint_keeps_an_active_reader_usable(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    with database.read() as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT value FROM counter WHERE id = 1").fetchone() == (0,)
        with database.write() as writer, writer:
            writer.execute("UPDATE counter SET value = 1 WHERE id = 1")

        busy, log_frames, checkpointed = database.checkpoint()

        assert busy == 0
        assert log_frames >= checkpointed >= 0
        assert reader.execute("SELECT value FROM counter WHERE id = 1").fetchone() == (0,)
    assert database.checkpoint("TRUNCATE")[0] == 0
    with database.read() as reader:
        assert reader.execute("SELECT value FROM counter WHERE id = 1").fetchone() == (1,)


def test_checkpoint_mode_is_allowlisted(tmp_path):
    database = main_database(_database(tmp_path))

    with pytest.raises(ValueError, match="unsupported"):
        database.checkpoint("PASSIVE); DROP TABLE counter; --")


def test_main_database_checkpoints_on_a_write_cadence(tmp_path, monkeypatch):
    path = _database(tmp_path)
    database = main_database(path)
    checkpoints = []
    original = database._checkpoint_from_maintenance

    def observed(mode):
        result = original(mode)
        checkpoints.append((mode, result))
        return result

    monkeypatch.setattr(database, "_checkpoint_from_maintenance", observed)
    monkeypatch.setattr(_access, "MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL", 2)

    with database.write() as db, db:
        db.execute("UPDATE counter SET value = 1 WHERE id = 1")
    with database.write() as db, db:
        db.execute("UPDATE counter SET value = 2 WHERE id = 1")

    assert checkpoints and checkpoints[0][0] == "PASSIVE"


def test_main_database_warns_when_wal_stays_above_the_high_water_mark(
    tmp_path, monkeypatch, caplog
):
    path = _database(tmp_path)
    database = main_database(path)
    monkeypatch.setattr(_access, "MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL", 1)
    monkeypatch.setattr(_access, "MAIN_DATABASE_WAL_WARN_BYTES", 0)
    caplog.set_level("WARNING", logger="one_memory.database_access")

    with database.write() as db, db:
        db.execute("UPDATE counter SET value = 1 WHERE id = 1")

    assert any("main_database_wal_high" in record.message for record in caplog.records)


def test_main_database_maintenance_checkpoint_does_not_break_an_active_reader(
    tmp_path, monkeypatch
):
    path = _database(tmp_path)
    database = main_database(path)
    monkeypatch.setattr(_access, "MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL", 1)

    with database.read() as reader:
        reader.execute("BEGIN")
        before = reader.execute("SELECT value FROM counter WHERE id = 1").fetchone()
        with database.write() as db, db:
            db.execute("UPDATE counter SET value = 1 WHERE id = 1")
        after = reader.execute("SELECT value FROM counter WHERE id = 1").fetchone()

    assert before == after == (0,)
    with database.read() as reader:
        assert reader.execute("SELECT value FROM counter WHERE id = 1").fetchone() == (1,)


def test_checkpoint_write_interval_rejects_a_non_positive_value(monkeypatch):
    monkeypatch.setenv("ONE_MEMORY_MAIN_DATABASE_CHECKPOINT_WRITE_INTERVAL", "0")

    with pytest.raises(RuntimeError, match="must be positive"):
        _access.checkpoint_write_interval()


def test_a_long_held_reader_is_visible_in_the_stats(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with database.read() as reader:
            reader.execute("SELECT value FROM counter WHERE id = 1").fetchone()
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        time.sleep(0.05)
        assert database.stats().reader_held_ms_max >= 40
        assert main_database_stats()["reader_held_ms_max"] >= 40
    finally:
        release.set()
        holder.join(10)

    assert database.stats().reader_held_ms_max == 0


def test_a_contended_writer_is_counted_as_waiting(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with database.write():
            held.set()
            release.wait()

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait()
    threading.Timer(0.2, release.set).start()
    with database.write() as db:
        db.execute("SELECT 1")
    holder.join()

    stats = database.stats()
    assert stats.writer_waits >= 1
    assert stats.writer_wait_ms_total >= 150
    assert stats.writer_wait_ms_max >= 150


def test_there_is_one_write_connection(tmp_path):
    path = _database(tmp_path)
    identities = set()
    lock = threading.Lock()

    def work() -> None:
        for _ in range(20):
            with main_database(path).write() as db, lock:
                identities.add(id(db))

    workers = [threading.Thread(target=work) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(identities) == 1


def test_one_checkout_does_not_leak_into_the_next(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    with database.write(foreign_keys=True, row_factory=sqlite3.Row, autocommit=True) as db:
        db.set_progress_handler(lambda: 0, 10)
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO rows VALUES (9, 9)")
        # left open: leaving must roll it back, as closing a connection did
    with database.write() as db:
        assert db.row_factory is None
        assert db.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
        assert db.isolation_level == ""
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM rows WHERE thread = 9").fetchone()[0] == 0
        assert not db.in_transaction


def test_every_connection_agrees_on_the_pragmas(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    with database.write() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    with database.read() as db:
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT INTO rows VALUES (1, 1)")


def test_a_writer_that_kept_the_old_journal_switches_on_the_next_ordinary_checkout(tmp_path):
    path = tmp_path / "one-memory.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE rows (thread INTEGER, n INTEGER)")
    raw.close()
    database = main_database(path)
    with database.write(keep_journal_mode=True) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    with database.write() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_catching_up_to_wal_waits_only_as_long_as_the_caller_allows(tmp_path):
    """补切 WAL 要文件独占片刻；等多久由调用方的 busy_timeout_ms 定，不是连接的缺省 5 秒。"""
    path = tmp_path / "one-memory.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE rows (thread INTEGER, n INTEGER)")
    raw.execute("INSERT INTO rows VALUES (1, 1)")
    raw.commit()
    raw.close()
    database = main_database(path)
    with database.write(keep_journal_mode=True) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM rows").fetchall()
    try:
        started = time.monotonic()
        # 正向前提：有人读着时确实切不过去。
        with pytest.raises(sqlite3.OperationalError), database.write(busy_timeout_ms=120):
            pass
        assert time.monotonic() - started < 1.5
    finally:
        reader.close()


def test_a_database_that_must_exist_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError), main_database(path).write(create=False):
        pass
    assert not path.exists()


def test_a_lock_refusal_is_visible_at_the_moment_it_happens(tmp_path, caplog):
    """A `database is locked` during an import peak must be seen as it occurs.

    The periodic stats line is a counter: it says waits exist, not what held
    the line. This asserts the refusal is logged immediately, naming the lock
    holder and how deep the queue was — never a bare average.
    """
    path = _database(tmp_path)
    database = main_database(path)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with database.write():
            held.set()
            release.wait()

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(5)

    try:
        with (
            caplog.at_level("ERROR", logger="one_memory.database_access"),
            pytest.raises(sqlite3.OperationalError),
            database.write(busy_timeout_ms=80),
        ):
            pass
    finally:
        release.set()
        holder.join(timeout=5)

    messages = [record.getMessage() for record in caplog.records]
    refusal = next(
        (message for message in messages if "main_database_lock_refused" in message),
        None,
    )
    assert refusal is not None, "a refused checkout logged nothing"
    assert "kind=write" in refusal
    assert "holder_thread=" in refusal and "holder_thread=None" not in refusal
    assert "waiting_now=" in refusal
    assert "file=one-memory.sqlite3" in refusal


def test_the_same_file_is_the_same_access_point(tmp_path):
    path = tmp_path / "one-memory.sqlite3"
    assert main_database(path) is main_database(tmp_path / "." / "one-memory.sqlite3")


def test_a_writer_may_read_from_the_pool_on_its_own_thread(tmp_path):
    path = _database(tmp_path)
    database = main_database(path)
    with database.write() as outer, outer:
        outer.execute("INSERT INTO rows VALUES (7, 7)")
        with database.read() as inner:
            assert inner is not outer
            inner.execute("SELECT COUNT(*) FROM rows").fetchone()
    with database.read() as db:
        assert db.execute("SELECT COUNT(*) FROM rows WHERE thread = 7").fetchone()[0] == 1


@pytest.mark.refuses_nested_checkout
@pytest.mark.parametrize("outer_kind", ["write", "read"])
def test_a_nested_checkout_on_one_thread_opens_nothing_and_fails_at_once(tmp_path, outer_kind):
    # SQLite has one writer: a second write connection on the thread that
    # already holds the lock can only wait out the busy timeout and fail, and
    # one that happens to succeed commits apart from the outer transaction.
    # A nested read takes a connection beyond the pool. Either way the caller
    # should be handed the connection it is already inside.
    from one_memory.database_access import NestedCheckoutError

    path = _database(tmp_path)
    database = main_database(path)
    with getattr(database, outer_kind)() as outer:
        outer.execute("SELECT 1").fetchone()
        opened = database.stats().open_connections
        started = time.monotonic()
        with pytest.raises(NestedCheckoutError), getattr(database, outer_kind)():
            pass
        assert time.monotonic() - started < 0.5
        assert opened >= 1
        assert database.stats().open_connections == opened
        assert outer.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 0
    assert database.stats().nested_refusals == 1
    assert main_database_stats()["nested_refusals"] >= 1


@pytest.mark.refuses_nested_checkout
@pytest.mark.parametrize("kind", ["write", "read"])
def test_a_checkout_held_across_an_await_is_refused_at_once_and_named(tmp_path, kind):
    # Two coroutines share the loop's thread. Were the second made to wait
    # instead, it would block the loop and the first could never give the
    # connection back: both would stall until the timeout.
    path = _database(tmp_path)
    database = main_database(path)
    held = asyncio.Event()

    async def holder() -> None:
        with getattr(database, kind)():
            held.set()
            await asyncio.sleep(0.2)

    async def second() -> float:
        await held.wait()
        started = time.monotonic()
        with pytest.raises(NestedCheckoutError, match="await"), getattr(database, kind)():
            pass
        return time.monotonic() - started

    async def both() -> float:
        _, elapsed = await asyncio.gather(holder(), second())
        return elapsed

    assert asyncio.run(both()) < 0.1


@pytest.mark.refuses_nested_checkout
def test_a_refusal_is_still_counted_once_its_database_is_gone(tmp_path):
    # The per-test guard in conftest reads this count after the test's own
    # teardown has let its databases go.
    before = main_database_stats()["nested_refusals"]
    path = _database(tmp_path)
    with (
        main_database(path).write(),
        pytest.raises(NestedCheckoutError),
        main_database(path).write(),
    ):
        pass
    close_main_databases()
    gc.collect()
    assert all(not str(key).startswith(str(tmp_path)) for key in list(_access._alive))
    assert main_database_stats()["nested_refusals"] == before + 1


@pytest.mark.refuses_nested_checkout
def test_a_refused_nested_write_leaves_the_outer_transaction_whole(tmp_path):
    from one_memory.database_access import NestedCheckoutError

    path = _database(tmp_path)
    database = main_database(path)
    with database.write() as outer, outer:
        outer.execute("INSERT INTO rows VALUES (8, 1)")
        with pytest.raises(NestedCheckoutError), database.write():
            pass
        outer.execute("INSERT INTO rows VALUES (8, 2)")
    with database.read() as db:
        assert db.execute("SELECT COUNT(*) FROM rows WHERE thread = 8").fetchone()[0] == 2
