import hashlib
import sqlite3
import time
from contextlib import closing

import pytest
from pydantic import ValidationError

import one_memory.memory_library as memory_library
from one_memory.database_access import close_main_databases, main_database
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.storage import SCHEMA as EVIDENCE_SCHEMA
from one_memory.memory_governance import (
    GOVERNANCE_SCHEMA,
    GovernedMemory,
    MemoryGovernanceStore,
    MemoryOrigin,
    MemoryStatus,
)
from one_memory.memory_library import MemoryLibraryPage, read_memory_library
from one_memory.source_read_access import SourceReadAccess

NOW = "2026-09-19T01:00:00+00:00"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "library # local.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(EVIDENCE_SCHEMA + GOVERNANCE_SCHEMA + GRANT_SCHEMA)
        for key, source, user in (
            ("e1", "cursor", "local"),
            ("e2", "workbuddy", "local"),
            ("e3", "cursor", "local"),
            ("other-e", "cursor", "other"),
        ):
            db.execute(
                """INSERT INTO evidence
                (evidence_id,idempotency_key,user_id,source_id,conversation_id,role,
                 written_by,verbatim,content_hash,revision,imported_at,updated_at)
                VALUES (?,?,?,?,?,'user','person','Synthetic evidence','hash',1,?,?)""",
                (key, key, user, source, "synthetic-conversation", NOW, NOW),
            )
    return path


def put(
    db,
    key,
    *,
    status="confirmed",
    user="local",
    content="Memory",
    time=NOW,
    links=("e1",),
    deleted=None,
    origin="extracted",
    tags='["topic"]',
):
    db.execute(
        """INSERT INTO governed_memories
        (memory_id,user_id,kind,status,content,scopes_json,origin,tags_json,revision,
         created_at,updated_at,deleted_at)
        VALUES (?,?,'working',?,?,'["project:one", "all-apps"]',?,?,7,?,?,?)""",
        (key, user, status, content, origin, tags, NOW, time, deleted),
    )
    db.executemany(
        "INSERT INTO governed_memory_evidence(memory_id,evidence_id) VALUES (?,?)",
        ((key, evidence_id) for evidence_id in links),
    )


def grant(db, source, *, user="local", revoked=NOW):
    db.execute(
        """INSERT OR REPLACE INTO source_grants
        (user_id,source_id,root_path,requested_path,root_identity,candidate_kind,
         granted_at,revoked_at,revision)
        VALUES (?,?, '/synthetic', '/synthetic', 'synthetic', 'default', ?, ?, 1)""",
        (user, source, NOW, revoked),
    )


def ids(page):
    return [item.memory_id for item in page.items]


@pytest.mark.parametrize("count", [501, 1001, 1607])
def test_full_library_is_reachable_in_stable_pages(database, count):
    with closing(sqlite3.connect(database)) as db, db:
        for index in reversed(range(count)):
            put(db, f"m-{index:05d}")
        put(db, "newest", time="2026-09-19T02:00:00+00:00")
    expected = ["newest", *(f"m-{index:05d}" for index in range(count))]
    actual = []
    for offset in range(0, count + 1, 500):
        page = read_memory_library(database, offset=offset, limit=500)
        assert (page.total, page.offset, page.limit) == (count + 1, offset, 500)
        assert ids(page) == expected[offset : offset + 500]
        assert page == read_memory_library(database, offset=offset, limit=500)
        actual.extend(ids(page))
    assert actual == expected
    assert len(set(actual)) == count + 1
    beyond = read_memory_library(database, offset=count + 20, limit=500)
    assert beyond.total == count + 1
    assert beyond.items == []


def test_permission_filter_precedes_count_search_source_and_page(database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(1100):
            put(db, f"blocked-{index:05d}", content="needle", links=("e1", "e2"))
        for index in range(507):
            put(db, f"readable-{index:05d}", content="needle")
        grant(db, "workbuddy")
    first = read_memory_library(database, query="needle", source="cursor", limit=500)
    last = read_memory_library(database, query="needle", source="cursor", offset=500)
    assert first.total == last.total == 507
    assert ids(first) == [f"readable-{index:05d}" for index in range(500)]
    assert ids(last) == [f"readable-{index:05d}" for index in range(500, 507)]
    assert read_memory_library(database, source="workbuddy").total == 0


def test_query_filters_before_expensive_freshness(database, monkeypatch) -> None:
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(1_000):
            put(db, f"bulk-{index:04d}", content=f"unrelated {index}")
        put(db, "needle", content="unique gate marker")

    calls: list[str] = []

    def register(db):
        def current(memory_id, _revision, _user_id):
            calls.append(memory_id)
            return 1

        db.create_function("test_library_current", 3, current)
        return "test_library_current(m.memory_id,m.revision,m.user_id)"

    monkeypatch.setattr(memory_library, "register_memory_freshness", register)

    started = time.perf_counter()
    page = read_memory_library(database, query="unique gate marker", limit=10)

    assert ids(page) == ["needle"]
    assert calls == ["needle"]
    assert time.perf_counter() - started < 1


@pytest.mark.parametrize("query", ["%", "_", "\\", "50%_\\done", "' OR 1=1 --", "记忆"])
def test_query_is_literal_substring_not_sql_or_like_pattern(database, query):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(1001):
            put(db, f"decoy-{index:05d}", content="other 500Xdone")
        put(db, "target", content=f"prefix {query} suffix")
    page = read_memory_library(database, query=query, limit=1)
    assert page.total == 1
    assert ids(page) == ["target"]


def test_optional_query_is_not_trimmed_and_ascii_search_is_case_insensitive(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a", content="pre NEEDLE post")
        put(db, "b", content="needle")
    assert read_memory_library(database, query=None).total == 2
    assert read_memory_library(database, query="").total == 2
    assert read_memory_library(database, query="needle").total == 2
    assert ids(read_memory_library(database, query=" NEEDLE ")) == ["a"]


def test_source_uses_active_evidence_exact_match_and_deduplicates_memories(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "mixed", links=("e3", "e2", "e1"))
        put(db, "cursor-only")
        put(db, "workbuddy-only", links=("e2",))
        put(db, "inactive", links=("e2", "e1"))
        db.execute(
            "UPDATE governed_memory_evidence SET active=0 "
            "WHERE memory_id='inactive' AND evidence_id='e2'"
        )
    page = read_memory_library(database, source="workbuddy")
    assert ids(page) == ["mixed", "workbuddy-only"]
    assert page.total == 2
    assert page.items[0].evidence_ids == ("e1", "e2", "e3")
    assert read_memory_library(database, source="cursor").total == 3
    assert read_memory_library(database, source="cur").total == 0
    assert read_memory_library(database, source="%").total == 0
    assert read_memory_library(database, source="cursor' OR 1=1 --").total == 0


def test_source_filter_uses_the_reverse_active_evidence_index(database):
    with closing(sqlite3.connect(database)) as db:
        plan = "\n".join(
            row[3]
            for row in db.execute(
                """EXPLAIN QUERY PLAN
                SELECT m.memory_id FROM governed_memories m
                WHERE m.user_id=? AND m.memory_id IN (
                    SELECT link.memory_id
                    FROM governed_memory_evidence link
                    JOIN evidence e ON e.evidence_id=link.evidence_id
                    WHERE link.active=1 AND e.user_id=? AND e.source_id=?
                )""",
                ("local", "local", "cursor"),
            )
        )
    assert "CORRELATED" not in plan
    assert "governed_memory_evidence_active_evidence" in plan


@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.serial
def test_source_filter_returns_within_300ms_at_500k_evidence(database):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("PRAGMA synchronous=OFF")
        db.execute(
            """WITH RECURSIVE seq(value) AS (
                VALUES(0) UNION ALL SELECT value + 1 FROM seq WHERE value < 499999
            )
            INSERT INTO evidence (
                evidence_id,idempotency_key,user_id,source_id,conversation_id,role,
                written_by,verbatim,content_hash,revision,imported_at,updated_at
            )
            SELECT printf('bulk-%06d',value), printf('bulk-key-%06d',value),
                'local','cursor',printf('conversation-%06d',value),'user','person',
                'Synthetic evidence','hash',1,?,? FROM seq""",
            (NOW, NOW),
        )
        for index in range(500):
            put(db, f"scale-{index:03d}", links=(f"bulk-{index:06d}",))

    started = time.perf_counter()
    page = read_memory_library(database, source="cursor", limit=20)
    elapsed = time.perf_counter() - started

    assert page.total == 500
    assert len(page.items) == 20
    assert elapsed < 0.3, f"source-filtered library took {elapsed:.3f}s"


def test_page_sources_use_all_active_citations_not_selected_filter_or_graph(database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(101):
            put(db, f"a-{index:03d}")
        put(db, "target", links=("e1", "e2", "e3"))
        put(db, "z-inactive", links=("e1", "e2"))
        put(db, "z-manual", links=())
        db.execute(
            "UPDATE governed_memory_evidence SET active=0 "
            "WHERE memory_id='z-inactive' AND evidence_id='e2'"
        )
    page = read_memory_library(database, active_only=True, offset=101, limit=2)
    assert page.source_ids_by_memory == {
        "target": ["cursor", "workbuddy"],
        "z-inactive": ["cursor"],
    }
    filtered = read_memory_library(database, source="cursor", query="Memory", offset=101)
    assert filtered.source_ids_by_memory["target"] == ["cursor", "workbuddy"]
    assert read_memory_library(database, offset=103).source_ids_by_memory == {"z-manual": []}
    assert set(page.source_ids_by_memory) == set(ids(page))


def test_source_projection_excludes_revoked_mixed_and_invalid_citations(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "mixed", links=("e1", "e2"))
        put(db, "safe", links=("e1", "e3"))
        put(db, "cross-user", links=("other-e",))
        put(db, "dangling", links=("missing",))
        grant(db, "workbuddy")
    page = read_memory_library(database, source="cursor", active_only=True)
    assert ids(page) == ["safe"]
    assert page.source_ids_by_memory == {"safe": ["cursor"]}


def test_revocation_matches_source_read_access_and_refreshes_without_cache(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "mixed", links=("e1", "e2"))
        put(db, "inactive", links=("e1", "e2"))
        put(db, "cursor-only")
        put(db, "no-active-evidence", links=())
        db.execute(
            "UPDATE governed_memory_evidence SET active=0 "
            "WHERE memory_id='inactive' AND evidence_id='e2'"
        )
        grant(db, "cursor", user="other")
        grant(db, "workbuddy", revoked=None)
    before = read_memory_library(database)
    assert before.total == 4  # No cursor grant is not an explicit revocation.
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "workbuddy")
    after = read_memory_library(database)
    assert after.items == SourceReadAccess(database).memories(before.items)
    assert ids(after) == ["cursor-only", "inactive", "no-active-evidence"]
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "workbuddy", revoked=None)
    assert read_memory_library(database) == before


def test_other_users_and_malformed_cross_user_links_never_leak(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "local")
        put(db, "other", user="other", links=("other-e",))
        put(db, "cross-user", links=("e1", "other-e"))
    assert ids(read_memory_library(database)) == ["local"]
    assert ids(read_memory_library(database, user_id="other")) == ["other"]
    assert ids(read_memory_library(database, source="cursor")) == ["local"]
    assert read_memory_library(database, user_id="unknown").total == 0


def test_dangling_active_link_is_hidden_without_repairing_data(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "dangling", links=("missing-evidence",))
        put(db, "good")
    assert ids(read_memory_library(database)) == ["good"]


def test_deleted_requires_explicit_status_and_status_filters_compose(database):
    with closing(sqlite3.connect(database)) as db, db:
        for status in MemoryStatus:
            put(db, status.value, status=status, deleted=NOW if status == "deleted" else None)
        put(db, "inconsistent-tombstone", deleted=NOW)
        put(db, "revoked-deleted", status="deleted", deleted=NOW, links=("e2",))
        grant(db, "workbuddy")
    assert ids(read_memory_library(database)) == ["confirmed"]
    for status in MemoryStatus:
        page = read_memory_library(database, status=status, source="cursor", query="Memory")
        assert ids(page) == [status.value]
        assert page.items[0].status is status
    assert ids(read_memory_library(database, status="deleted")) == ["deleted"]


def test_active_page_counts_live_memories_in_one_ordered_query(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a", content="target")
        put(db, "b", content="target")
        put(db, "c", status="superseded", content="target")
        put(db, "d", status="deleted", deleted=NOW, content="target")
        put(db, "e", content="target", links=("e2",))
        grant(db, "workbuddy")
    first = read_memory_library(
        database, active_only=True, source="cursor", query="target", limit=1
    )
    second = read_memory_library(
        database, active_only=True, source="cursor", query="target", limit=1, offset=1
    )
    assert first.total == second.total == 2
    assert ids(first) == ["a"] and ids(second) == ["b"]
    with pytest.raises(ValueError):
        read_memory_library(database, active_only=True, status="confirmed")


def test_projection_reuses_governance_parser_and_preserves_complete_fields(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "full", content="x" * 2500, links=("e3", "e1"), origin="agent")
        put(db, "legacy", origin=None, tags=None)
    original = MemoryGovernanceStore.memories_from_rows
    calls = []

    def parse(db, rows, **kwargs):
        calls.append((db.in_transaction, len(rows)))
        return original(db, rows, **kwargs)

    def forbidden_init(*args, **kwargs):
        pytest.fail("Read projection must not instantiate a schema-writing Store")

    monkeypatch.setattr(MemoryGovernanceStore, "__init__", forbidden_init)
    monkeypatch.setattr(MemoryGovernanceStore, "memories_from_rows", staticmethod(parse))
    page = read_memory_library(database)
    assert calls == [(True, 2)]
    assert all(isinstance(item, GovernedMemory) for item in page.items)
    full, legacy = page.items
    assert (full.content, full.evidence_ids) == ("x" * 2500, ("e1", "e3"))
    assert full.scopes == ("project:one", "all-apps")
    assert full.tags == ("topic",)
    assert full.origin is MemoryOrigin.AGENT
    assert full.revision == 7
    assert full.created_at.isoformat() == full.updated_at.isoformat() == NOW
    assert full.deleted_at is None
    assert legacy.origin is MemoryOrigin.EXTRACTED
    assert legacy.tags == ()
    assert MemoryLibraryPage.model_validate_json(page.model_dump_json()) == page


def test_count_page_permissions_and_citations_share_one_snapshot(database, monkeypatch):
    connect = sqlite3.connect
    with closing(connect(database)) as writer, writer:
        writer.execute("PRAGMA journal_mode=WAL")
        put(writer, "a", links=("e1",))
        put(writer, "b", links=("e2",))
    changes = []
    reads = []

    class ConcurrentConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.lstrip().upper().startswith("SELECT"):
                reads.append(self.in_transaction)
            if "COUNT(*)" in sql and not changes:
                with closing(connect(database)) as writer, writer:
                    grant(writer, "cursor")
                    writer.execute(
                        "UPDATE governed_memory_evidence SET active=0 WHERE memory_id='b'"
                    )
                changes.append(True)
            return cursor

    def concurrent_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=ConcurrentConnection)

    monkeypatch.setattr(sqlite3, "connect", concurrent_connect)
    snapshot = read_memory_library(database)
    assert snapshot.total == 2
    assert ids(snapshot) == ["a", "b"]
    assert [item.evidence_ids for item in snapshot.items] == [("e1",), ("e2",)]
    assert snapshot.source_ids_by_memory == {"a": ["cursor"], "b": ["workbuddy"]}
    assert len(reads) == 5 and all(reads)
    assert changes == [True]
    refreshed = read_memory_library(database)
    assert refreshed.total == 1
    assert ids(refreshed) == ["b"]
    assert refreshed.items[0].evidence_ids == ()
    assert refreshed.source_ids_by_memory == {"b": []}


def test_read_only_open_no_schema_or_data_writes_and_closed_connection(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "present")
    connect = sqlite3.connect
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    opened = []
    statements = []
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,
    }

    def read_only_connect(database_uri, **kwargs):
        assert database_uri == database.resolve().as_uri() + "?mode=ro"
        assert kwargs["uri"] is True
        db = connect(database_uri, **kwargs)
        # Positive write target, so a failed UPDATE proves mode=ro, not a missing table.
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute(
                "UPDATE governed_memories SET content='unexpected' WHERE memory_id='present'"
            )
        db.rollback()
        db.set_authorizer(
            # The access layer makes its readers query_only as well.
            lambda action, name, *_: (
                sqlite3.SQLITE_OK
                if action in allowed or (action == sqlite3.SQLITE_PRAGMA and name == "query_only")
                else sqlite3.SQLITE_DENY
            )
        )
        db.set_trace_callback(statements.append)
        opened.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", read_only_connect)
    page = read_memory_library(database)
    assert page.total == 1 and page.items[0].content == "Memory"
    assert len(opened) == 1
    assert len([sql for sql in statements if sql.lstrip().startswith("SELECT")]) == 5
    assert all(
        sql.lstrip().startswith(("SELECT", "BEGIN", "PRAGMA query_only = ON")) for sql in statements
    )
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert main_database(database).stats().readers_in_use == 0
    close_main_databases()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_read_budget_exhaustion_returns_stale_page_instead_of_error(database, monkeypatch):
    """2026-10-12: under a concurrent import the page query blew the read
    budget and the caller turned that into 503 `memory_library_unavailable`,
    so the memory page read as broken for the whole import. Past the budget
    the read must return a `stale` partial page instead of an error.
    """
    # Enough rows that the COUNT exceeds one progress-handler step, so the
    # (deliberately exhausted) deadline actually interrupts the statement.
    with closing(sqlite3.connect(database)) as db, db:
        db.executemany(
            """INSERT INTO governed_memories
            (memory_id,user_id,kind,status,content,scopes_json,origin,tags_json,revision,
             created_at,updated_at,deleted_at)
            VALUES (?,'local','working','confirmed',?,'[]','extracted','[]',7,?,?,NULL)""",
            ((f"bulk-{n}", f"content {n}", NOW, NOW) for n in range(4000)),
        )
    real_monotonic = time.monotonic
    monkeypatch.setattr(memory_library.time, "monotonic", real_monotonic)
    monkeypatch.setattr(memory_library, "READ_BUDGET_SECONDS", 0)
    page = read_memory_library(database, limit=10)
    assert page.stale is True
    assert page.items == []
    assert page.limit == 10


def test_empty_success_is_distinct_from_missing_invalid_or_uninitialized_database(
    database, tmp_path
):
    empty = read_memory_library(database, offset=40, limit=10)
    assert empty.model_dump() == {
        "items": [],
        "total": 0,
        "offset": 40,
        "limit": 10,
        "source_ids_by_memory": {},
        "source_time_by_memory": {},
        "stale": False,
    }
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        read_memory_library(missing)
    assert not missing.exists()
    invalid = tmp_path / "invalid.sqlite3"
    invalid.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        read_memory_library(invalid)
    blank = tmp_path / "blank.sqlite3"
    with closing(sqlite3.connect(blank)):
        pass
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        read_memory_library(blank)
    assert blank.stat().st_size == 0


def test_corrupt_projection_fails_instead_of_returning_partial_or_empty(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "bad", tags="not json")
        put(db, "good")
    with pytest.raises(ValueError):
        read_memory_library(database)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 501},
        {"limit": True},
        {"limit": 1.5},
        {"offset": -1},
        {"offset": True},
        {"offset": 1.5},
        {"user_id": ""},
        {"status": "not-a-status"},
        {"source": ""},
    ],
)
def test_invalid_filters_and_pagination_are_rejected(database, kwargs):
    with pytest.raises((ValueError, ValidationError)):
        read_memory_library(database, **kwargs)


def test_page_shows_when_the_cited_conversation_happened_not_when_it_was_generated(database):
    """D96: a fresh import makes every memory "today"; the list must show the dialogue time."""
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE evidence SET created_at='2026-10-02T02:54:00+00:00' WHERE evidence_id='e1'"
        )
        db.execute(
            "UPDATE evidence SET created_at='2026-10-01T09:00:00+00:00' WHERE evidence_id='e3'"
        )
        put(db, "conversation", links=("e1", "e3"), time="2026-10-09T05:32:00+00:00")
        put(db, "imported-only", links=("e2",), time="2026-10-09T05:32:00+00:00")
        put(db, "manual", links=())
        put(db, "inactive-newer", links=("e1", "e2"), time="2026-10-09T05:32:00+00:00")
        db.execute(
            "UPDATE governed_memory_evidence SET active=0 "
            "WHERE memory_id='inactive-newer' AND evidence_id='e1'"
        )
    page = read_memory_library(database, active_only=True)
    times = {key: (value.at, value.basis) for key, value in page.source_time_by_memory.items()}
    # D104: the cited round has two person-side messages; the time is when the
    # person spoke, so the earliest one wins rather than the latest (MAX used
    # to land on the reply and read as a later, contradictory time).
    assert times["conversation"] == ("2026-10-01T09:00:00+00:00", "conversation")
    # No conversation time on any cited message: fall back to import time, marked so.
    assert times["imported-only"] == (NOW, "import")
    # An inactive citation is not this memory's source any more.
    assert times["inactive-newer"] == (NOW, "import")
    # Nothing cited: no time, the UI keeps the generation date.
    assert "manual" not in times
    # Only the page that is shown is looked up.
    one = read_memory_library(database, active_only=True, limit=1)
    assert set(one.source_time_by_memory) <= set(ids(one))


def test_conversation_time_is_when_the_person_spoke_not_when_the_turn_ended(database):
    """D104: a round's time is the person's question, not the assistant reply.

    One memory cites the whole round: the question at 17:15, tool traffic, and
    the reply at 17:18. The page used to take MAX() and show 17:18 next to a
    source pointing at 17:15, which read as a contradiction. Only person-side
    evidence may set this time, and the earliest of it wins.
    """
    with closing(sqlite3.connect(database)) as db, db:
        for key, role, written_by, created_at in (
            ("round-question", "user", "person", "2026-10-09T17:15:02+00:00"),
            ("round-tool", "tool", "unknown", "2026-10-09T17:16:00+00:00"),
            ("round-reply", "assistant", "agent", "2026-10-09T17:18:52+00:00"),
        ):
            db.execute(
                """INSERT INTO evidence
                (evidence_id,idempotency_key,user_id,source_id,conversation_id,role,
                 written_by,verbatim,content_hash,revision,imported_at,updated_at,
                 created_at)
                VALUES (?,?,?,?,?,'user','person','Synthetic evidence','hash',1,?,?,?)""",
                (
                    key,
                    key,
                    "local",
                    "cursor",
                    "one-round",
                    NOW,
                    NOW,
                    created_at,
                ),
            )
            db.execute(
                "UPDATE evidence SET role=?, written_by=? WHERE evidence_id=?",
                (role, written_by, key),
            )
        put(
            db,
            "one-round-memory",
            links=("round-question", "round-tool", "round-reply"),
            time="2026-10-10T09:00:00+00:00",
        )

    page = read_memory_library(database, active_only=True)
    shown = page.source_time_by_memory["one-round-memory"]
    assert (shown.at, shown.basis) == ("2026-10-09T17:15:02+00:00", "conversation")


def test_conversation_time_falls_back_to_the_earliest_evidence_without_a_person(database):
    """D104: with no person-authored row, use the earliest evidence, not the latest."""
    with closing(sqlite3.connect(database)) as db, db:
        for key, created_at in (
            ("first-tool", "2026-10-09T11:00:00+00:00"),
            ("last-tool", "2026-10-09T13:00:00+00:00"),
        ):
            db.execute(
                """INSERT INTO evidence
                (evidence_id,idempotency_key,user_id,source_id,conversation_id,role,
                 written_by,verbatim,content_hash,revision,imported_at,updated_at,
                 created_at)
                VALUES (?,?,'local','cursor','tool-only','tool','unknown','v','h',1,?,?,?)""",
                (key, key, NOW, NOW, created_at),
            )
        put(db, "tool-only-memory", links=("first-tool", "last-tool"))

    page = read_memory_library(database, active_only=True)
    shown = page.source_time_by_memory["tool-only-memory"]
    assert (shown.at, shown.basis) == ("2026-10-09T11:00:00+00:00", "conversation")


def test_library_reads_as_a_conversation_timeline(database, monkeypatch):
    """D99: the list follows the conversation, not the day the memory was made.

    A memory generated today from a two-week-old dialogue belongs next to that
    week. Memories whose conversations share a timestamp keep the generated
    order among themselves rather than an arbitrary one.
    """
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE evidence SET created_at=? WHERE evidence_id='e1'",
            ("2026-10-08T10:00:00+00:00",),
        )
        db.execute(
            "UPDATE evidence SET created_at=? WHERE evidence_id='e2'",
            ("2026-09-01T10:00:00+00:00",),
        )
        # Made later, but from the older conversation.
        put(db, "older-chat", links=("e2",), time="2026-10-09T09:00:00+00:00")
        # Made earlier, from the newer conversation.
        put(db, "newer-chat", links=("e1",), time="2026-10-09T08:00:00+00:00")
        # No conversation time at all: keeps its generated place.
        put(db, "manual", links=(), time="2026-10-09T10:00:00+00:00")
    assert ids(read_memory_library(database)) == [
        "manual",
        "newer-chat",
        "older-chat",
    ]
    # ONE_MEMORY_LIBRARY_SORT=updated restores the generated order, no rebuild.
    monkeypatch.setenv("ONE_MEMORY_LIBRARY_SORT", "updated")
    assert ids(read_memory_library(database)) == [
        "manual",
        "older-chat",
        "newer-chat",
    ]
