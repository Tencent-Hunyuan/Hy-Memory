from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from one_memory.database_access import close_main_databases
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.sdk_raw_dispatch import (
    SdkRawDispatchConflict,
    SdkRawDispatchStore,
)


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(database), MIGRATIONS))
    close_main_databases()
    return SdkRawDispatchStore(database)


def stage(
    store: SdkRawDispatchStore,
    raw_id: str,
    order: int,
    *,
    priority: str = "normal",
    session: str = "session-1",
    request_id: str | None = None,
) -> None:
    store.stage(
        user_id="local",
        source_id="cursor",
        file_key="file-1",
        internal_session_id=session,
        workspace="/repo",
        agent_id="one-memory",
        raw_id=raw_id,
        source_order=order,
        priority=priority,  # type: ignore[arg-type]
        event_at=datetime(2026, 10, 21, 12, order, tzinfo=UTC),
        import_request_id=request_id or f"import-{raw_id}",
    )


def test_raw_dispatch_receipts_store_ids_and_order_but_never_content(store):
    stage(store, "raw-2", 2)
    stage(store, "raw-1", 1, priority="live")

    assert store.pending_files() == (("local", "cursor", "file-1"),)
    dispatch = store.take_file(
        user_id="local",
        source_id="cursor",
        file_key="file-1",
    )
    assert len(dispatch) == 1
    assert dispatch[0].raw_ids == ("raw-1", "raw-2")
    assert dispatch[0].priority == "live"
    assert dispatch[0].event_at == datetime(2026, 10, 21, 12, 2, tzinfo=UTC)

    with closing(sqlite3.connect(store.database_path)) as database:
        columns = {
            str(row[1])
            for row in database.execute("PRAGMA table_info(sdk_raw_pending_dispatch)")
        }
    assert "content" not in columns
    assert "verbatim" not in columns


def test_stage_is_idempotent_escalates_priority_and_rejects_rebinding(store):
    stage(store, "raw-1", 1)
    stage(store, "raw-1", 1, priority="live")
    assert store.take_file(
        user_id="local",
        source_id="cursor",
        file_key="file-1",
    )[0].priority == "live"

    with pytest.raises(SdkRawDispatchConflict):
        stage(store, "raw-1", 2)


def test_consume_happens_only_after_durable_queue_submission(store):
    stage(store, "raw-1", 1)
    assert store.consume_file(
        user_id="local",
        source_id="cursor",
        file_key="file-1",
    ) == 1
    assert store.pending_files() == ()
