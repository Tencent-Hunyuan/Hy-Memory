import hashlib
import json
import sqlite3
import time
from contextlib import closing

import pytest

from one_memory import context_memory
from one_memory.context_memory import (
    compose_memory_retrieval_path,
    read_memory_context,
    revalidate_memory_context,
)
from one_memory.context_models import ContextSearchRequest
from one_memory.database_access import close_main_databases, main_database
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.storage import SCHEMA as EVIDENCE_SCHEMA
from one_memory.memory_governance import GOVERNANCE_SCHEMA, MemoryGovernanceStore

NOW = "2026-09-19T01:00:00+00:00"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "context # local.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(EVIDENCE_SCHEMA + GOVERNANCE_SCHEMA + GRANT_SCHEMA)
        for key, source, user in (
            ("e1", "cursor", "local"),
            ("e2", "workbuddy", "local"),
            ("other-e", "cursor", "other"),
        ):
            evidence(db, key, source=source, user=user)
    return path


def evidence(db, key, *, source="cursor", user="local"):
    db.execute(
        """INSERT INTO evidence
        (evidence_id,idempotency_key,user_id,source_id,conversation_id,role,
         written_by,verbatim,content_hash,revision,imported_at,updated_at)
        VALUES (?,?,?,?,?,'user','person','PRIVATE RAW MUST NOT BE READ','hash',1,?,?)""",
        (key, key, user, source, "synthetic", NOW, NOW),
    )


def put(
    db,
    key,
    *,
    content="alpha beta",
    scopes=("all-apps",),
    links=("e1",),
    status="confirmed",
    user="local",
    deleted=None,
    revision=7,
    kind="working",
    at=NOW,
):
    db.execute(
        """INSERT INTO governed_memories
        (memory_id,user_id,kind,status,content,scopes_json,revision,created_at,updated_at,
         deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (key, user, kind, status, content, json.dumps(scopes), revision, NOW, at, deleted),
    )
    db.executemany(
        "INSERT INTO governed_memory_evidence(memory_id,evidence_id) VALUES (?,?)",
        ((key, eid) for eid in links),
    )


def revoke(db, source, *, user="local"):
    db.execute(
        """INSERT OR REPLACE INTO source_grants
        (user_id,source_id,root_path,requested_path,root_identity,candidate_kind,
         granted_at,revoked_at,revision)
        VALUES (?,?,'/synthetic','/synthetic','fixture','default',?,?,1)""",
        (user, source, NOW, NOW),
    )


def read(database, query="alpha beta", *, scopes=None, budget_seconds=3, **kwargs):
    return read_memory_context(
        database,
        ContextSearchRequest(query=query, **kwargs),
        scopes=scopes,
        deadline=time.monotonic() + budget_seconds,
    )


def ids(result):
    return [item.memory_id for item in result.items]


@pytest.mark.parametrize("count", [201, 501, 1001])
def test_permissions_precede_candidate_limit_and_do_not_leak_counts(database, count):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(count):
            put(db, f"blocked-{index:04d}", scopes=("workspace:secret",))
            put(db, f"revoked-{index:04d}", links=("e1", "e2"))
        put(db, "readable")
        revoke(db, "workbuddy")
    # This is a permission-ordering test over 2,003 rows, not a latency gate.
    result = read(database, scopes=frozenset({"all-apps"}), limit=1, budget_seconds=10)
    assert result.status == "ok" and result.reason == "lexical_only"
    assert ids(result) == ["readable"]
    assert not result.truncated_by


def test_scope_user_status_and_active_source_isolation(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "global")
        put(db, "workspace", scopes=("workspace:one",))
        put(db, "prefix", scopes=("workspace:one-other",))
        put(db, "source-scope", scopes=("source:cursor",))
        put(db, "other-user", user="other", links=("other-e",))
        for status in ("superseded", "deleted"):
            put(db, status, status=status)
        put(db, "tombstone", deleted=NOW)
        put(db, "dangling", links=("missing",))
        put(db, "cross-user", links=("e1", "other-e"))
        put(db, "revoked", links=("e1", "e2"))
        put(db, "inactive", links=("e1", "e2"))
        put(db, "no-evidence", links=())
        db.execute(
            "UPDATE governed_memory_evidence SET active=0 WHERE memory_id='inactive'"
            " AND evidence_id='e2'"
        )
        revoke(db, "workbuddy")
        revoke(db, "cursor", user="other")
    assert ids(read(database, scopes=frozenset({"workspace:one"}))) == ["workspace"]
    assert ids(read(database, scopes=frozenset({"all-apps"}))) == [
        "global",
        "inactive",
        "no-evidence",
    ]
    assert "workspace" in ids(read(database))
    assert read(database, scopes=frozenset({"workspace:on"})).status == "empty"
    assert read(database, scopes=frozenset({"*"})).status == "not_authorized"
    assert read(database, scopes=frozenset({"source:cursor"})).status == "not_authorized"


@pytest.mark.parametrize("scopes", [frozenset(), frozenset({"source:cursor"})])
def test_denied_scope_does_not_open_database(database, scopes, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized lane must not open SQLite")

    monkeypatch.setattr(context_memory.sqlite3, "connect", forbidden)
    assert read(database, scopes=scopes).status == "not_authorized"
    assert (
        revalidate_memory_context(database, (), scopes=scopes, deadline=time.monotonic() + 1) == ()
    )


def test_unrequested_lane_does_not_open_database(database, monkeypatch):
    monkeypatch.setattr(
        context_memory.sqlite3, "connect", lambda *a, **k: pytest.fail("Unrequested lane")
    )
    result = read(database, kinds=("raw",))
    assert (result.status, result.reason) == ("not_requested", "not_requested")


@pytest.mark.parametrize("query", ["beta alpha", "BETA ALPHA", "alpha ! beta"])
def test_lexical_terms_ignore_order_without_semantic_calls(database, query):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "match", content="the alpha decision precedes beta")
        put(db, "not-match", content="irrelevant content")
    assert ids(read(database, query)) == ["match"]


@pytest.mark.parametrize(
    "query",
    [
        "!!!",
        "%_\\",
        "a",
        *tuple("我你他她它的了是在和与或这那也都很没有就还而但却因且把被从给到又著着过啊呢吗吧"),
    ],
)
def test_empty_terms_are_empty_not_match_all_or_model_fallback(database, query, monkeypatch):
    monkeypatch.setattr(
        context_memory.sqlite3,
        "connect",
        lambda *a, **k: pytest.fail("No searchable lexical terms"),
    )
    result = read(database, query)
    assert (result.status, result.reason, result.items) == ("empty", "lexical_only", ())


def test_chinese_terms_and_exact_like_characters(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "zh", content="需要记录项目的发布范围")
        put(db, "literal", content="alpha_beta")
        put(db, "not-wildcard", content="alphaXbeta")
    assert ids(read(database, "发布范围")) == ["zh"]
    assert ids(read(database, "alpha_beta")) == ["literal"]


def test_single_chinese_character_query_finds_the_memory(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "zh-name", content="张 的偏好")
        put(db, "unrelated", content="项目发布范围")
    assert ids(read(database, "张")) == ["zh-name"]


def test_count_sentinel_is_bounded_and_only_counts_authorized_matches(database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(50):
            put(db, f"m-{index:03d}")
    result = read(database, limit=20)
    assert len(result.items) == 21
    assert "count_budget" in result.truncated_by
    assert ids(result) == [f"m-{index:03d}" for index in range(21)]
    small = read(database, limit=2)
    assert len(small.items) == 3
    assert "count_budget" in small.truncated_by


def test_actual_identity_revision_kind_time_bounded_body_and_refs(database):
    with closing(sqlite3.connect(database)) as db, db:
        refs = tuple(f"ref-{i:03d}" for i in range(140))
        for ref in refs:
            evidence(db, ref)
        put(db, "long", content="alpha beta " + "x" * 100000, links=refs, kind="coding")
    result = read(database)
    item = result.items[0]
    assert item.memory_id == "long" and item.revision == 7 and item.memory_kind == "coding"
    assert item.updated_at.isoformat() == NOW
    assert len(item.content) == 20000 and item.truncated
    assert item.evidence_ids == refs[:128]
    assert set(result.truncated_by) == {"character_budget", "metadata_budget"}


def test_raw_source_filter_does_not_narrow_memory_lane(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "memory", links=("e2",))
    assert ids(read(database, raw_source_ids=("cursor",))) == ["memory"]


def test_read_only_snapshot_no_evidence_body_no_store_init_closed(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "memory")
    connect = sqlite3.connect
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    opened, statements, reads = [], [], []
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_RECURSIVE,
    }

    def authorize(action, table, column, *unused):
        if action == sqlite3.SQLITE_READ:
            reads.append((table, column))
            assert not (table == "evidence" and column == "verbatim")
        # The access layer's own: the read's lock budget, and query_only.
        if action == sqlite3.SQLITE_PRAGMA and table in {"busy_timeout", "query_only"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    def tracked(database_uri, **kwargs):
        assert database_uri == database.resolve().as_uri() + "?mode=ro" and kwargs["uri"]
        db = connect(database_uri, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE governed_memories SET content='bad' WHERE memory_id='memory'")
        db.rollback()
        db.set_authorizer(authorize)
        db.set_trace_callback(statements.append)
        opened.append(db)
        return db

    monkeypatch.setattr(context_memory.sqlite3, "connect", tracked)
    monkeypatch.setattr(
        MemoryGovernanceStore,
        "__init__",
        lambda *a, **k: pytest.fail("No schema/store initialization"),
    )
    result = read(database)
    assert ids(result) == ["memory"] and reads
    assert len(opened) == 1
    # The lock wait is the read's own budget, set on the pooled connection.
    budgets = [int(s.split("=")[1]) for s in statements if s.startswith("PRAGMA busy_timeout")]
    assert budgets and all(0 <= budget <= 3000 for budget in budgets)
    assert all(
        sql.lstrip().startswith(
            ("SELECT", "WITH", "BEGIN", "PRAGMA busy_timeout", "PRAGMA query_only = ON")
        )
        for sql in statements
    )
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    # Pooled: handed back rather than held, and closed with the pool.
    assert main_database(database).stats().readers_in_use == 0
    close_main_databases()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_database_errors_are_unavailable_not_empty(database, tmp_path):
    assert read(database).status == "empty"
    missing = tmp_path / "missing.sqlite3"
    assert (read(missing).status, read(missing).reason) == ("unavailable", "read_unavailable")
    assert not missing.exists()
    invalid = tmp_path / "invalid.sqlite3"
    invalid.write_bytes(b"not sqlite")
    assert read(invalid).status == "unavailable"
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "invalid-time", at="not-a-date")
    assert read(database).status == "unavailable"


def test_expired_deadline_no_open(database, monkeypatch):
    monkeypatch.setattr(
        context_memory.sqlite3, "connect", lambda *a, **k: pytest.fail("Expired deadline opened DB")
    )
    result = read_memory_context(
        database, ContextSearchRequest(query="alpha"), scopes=None, deadline=time.monotonic() - 1
    )
    assert (result.status, result.reason) == ("unavailable", "timeout_budget")


def test_sqlite_progress_handler_interrupts_and_closes(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        for i in range(1000):
            put(db, f"m-{i:04d}")
    connect = sqlite3.connect
    opened, handlers = [], []
    expired = False

    class Tracked(sqlite3.Connection):
        def set_progress_handler(self, callback, instructions):
            if callback is not None:
                handlers.append(instructions)
            super().set_progress_handler(callback, instructions)

        def execute(self, sql, parameters=()):
            nonlocal expired
            if sql.lstrip().startswith(("SELECT", "WITH")):
                expired = True
            return super().execute(sql, parameters)

    def tracked(*args, **kwargs):
        db = connect(*args, **kwargs, factory=Tracked)
        opened.append(db)
        return db

    monkeypatch.setattr(context_memory.sqlite3, "connect", tracked)
    monkeypatch.setattr(context_memory.time, "monotonic", lambda: 2 if expired else 0)
    result = read_memory_context(
        database, ContextSearchRequest(query="alpha"), scopes=None, deadline=1
    )
    assert result.reason == "timeout_budget" and result.status == "unavailable"
    assert handlers and max(handlers) <= 1000
    # Pooled: handed back rather than held, and closed with the pool.
    assert main_database(database).stats().readers_in_use == 0
    close_main_databases()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_lock_wait_is_bounded_by_shared_deadline(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "memory")
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        start = time.monotonic()
        result = read_memory_context(
            database, ContextSearchRequest(query="alpha"), scopes=None, deadline=start + 0.06
        )
        assert result.status == "unavailable" and result.reason == "timeout_budget"
        assert time.monotonic() - start < 0.5
        writer.rollback()


@pytest.mark.parametrize("change", ["revoke", "revision", "scope", "delete", "bad-link", "user"])
def test_final_revalidation_drops_changed_items_without_new_text(database, change):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "change", links=("e2",))
        put(db, "keep")
    original = read(database, scopes=frozenset({"all-apps"})).items
    assert len(original) == 2
    with closing(sqlite3.connect(database)) as db, db:
        if change == "revoke":
            revoke(db, "workbuddy")
        elif change == "bad-link":
            db.execute("DELETE FROM evidence WHERE evidence_id='e2'")
        else:
            assignment = {
                "revision": "revision=8,content='new text never return'",
                "scope": "scopes_json='[\"workspace:secret\"]'",
                "delete": "status='deleted'",
                "user": "user_id='other'",
            }[change]
            db.execute(f"UPDATE governed_memories SET {assignment} WHERE memory_id='change'")
    validated = revalidate_memory_context(
        database, original, scopes=frozenset({"all-apps"}), deadline=time.monotonic() + 2
    )
    assert [item.memory_id for item in validated] == ["keep"]
    assert validated[0] is original[1]


def test_final_revalidation_failure_propagates_for_aggregator_not_false_empty(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "memory")
    items = read(database).items
    with pytest.raises(TimeoutError):
        revalidate_memory_context(database, items, scopes=None, deadline=time.monotonic() - 1)
    database.unlink()
    with pytest.raises(sqlite3.Error):
        revalidate_memory_context(database, items, scopes=None, deadline=time.monotonic() + 1)


def test_one_snapshot_then_final_revoke_check(database, monkeypatch):
    connect = sqlite3.connect
    with closing(connect(database)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        put(db, "change", links=("e2",))
        put(db, "keep")
    changes, reads = [], []

    class Concurrent(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.lstrip().startswith("SELECT"):
                reads.append(self.in_transaction)
            if "AS score" in sql and not changes:
                with closing(connect(database)) as writer, writer:
                    revoke(writer, "workbuddy")
                changes.append(True)
            return cursor

    monkeypatch.setattr(
        context_memory.sqlite3, "connect", lambda *a, **k: connect(*a, **k, factory=Concurrent)
    )
    result = read(database)
    assert ids(result) == ["change", "keep"]
    assert changes == [True] and reads and all(reads)
    final = revalidate_memory_context(
        database, result.items, scopes=None, deadline=time.monotonic() + 2
    )
    assert [item.memory_id for item in final] == ["keep"]


def test_final_revalidation_never_reads_memory_or_evidence_body(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "keep")
    items = read(database).items
    close_main_databases()
    connect = sqlite3.connect
    opened, seen = [], []

    def authorize(action, table, column, *unused):
        if action == sqlite3.SQLITE_READ:
            seen.append((table, column))
            if (table, column) in {("evidence", "verbatim"), ("governed_memories", "content")}:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def tracked(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_authorizer(authorize)
        opened.append(db)
        return db

    monkeypatch.setattr(context_memory.sqlite3, "connect", tracked)
    assert (
        revalidate_memory_context(database, items, scopes=None, deadline=time.monotonic() + 2)
        == items
    )
    assert seen
    # Pooled: handed back rather than held, and closed with the pool.
    assert main_database(database).stats().readers_in_use == 0
    close_main_databases()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_unreadable_bodies_do_not_reach_lexical_scorer(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "secret", content="alpha beta SECRET", scopes=("workspace:other",))
        put(db, "revoked", content="alpha beta REVOKED", links=("e2",))
        put(db, "allowed")
        revoke(db, "workbuddy")
    original = context_memory._terms
    calls = []

    def terms(text):
        calls.append(text)
        assert "SECRET" not in text and "REVOKED" not in text
        assert len(text) <= 20000
        return original(text)

    monkeypatch.setattr(context_memory, "_terms", terms)
    assert ids(read(database, scopes=frozenset({"all-apps"}))) == ["allowed"]
    assert calls == ["alpha beta"]  # Bodies never enter the Python tokenizer.


def test_no_hy_model_semantic_or_reindex_calls(database, monkeypatch):
    from one_memory.recall import GovernedRecallTarget

    def forbidden(*a, **k):
        pytest.fail("Unified read must not use existing recall/model/write paths")

    monkeypatch.setattr(GovernedRecallTarget, "__init__", forbidden)
    monkeypatch.setattr(GovernedRecallTarget, "recall_async", forbidden)
    monkeypatch.setattr(MemoryGovernanceStore, "__init__", forbidden)
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "memory")
    assert ids(read(database)) == ["memory"]


def test_permission_checks_beyond_reference_budget(database):
    with closing(sqlite3.connect(database)) as db, db:
        refs = tuple(f"a-ref-{i:03d}" for i in range(129))
        for ref in refs:
            evidence(db, ref)
        evidence(db, "z-revoked", source="workbuddy")
        put(db, "blocked", links=(*refs, "z-revoked"))
        put(db, "readable")
        revoke(db, "workbuddy")
    assert ids(read(database)) == ["readable"]


def test_old_memory_query_after_20000_characters_is_found(database):
    with closing(sqlite3.connect(database)) as db, db:
        for i in range(501):
            put(db, f"newer-{i:04d}", content="unrelated recent memory")
        put(db, "tail-only", content="x" * 25000 + " alpha beta", at="2020-01-01T00:00:00+00:00")
    result = read(database)
    assert result.status == "ok" and result.reason == "lexical_only"
    assert ids(result) == ["tail-only"]
    assert "alpha beta" in result.items[0].content
    assert len(result.items[0].content) <= 20000 and result.items[0].truncated
    assert "character_budget" in result.truncated_by


@pytest.mark.parametrize("short_match", [True, False])
def test_unrelated_long_memory_does_not_make_every_query_partial(database, short_match):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "long-unrelated", content="unrelated " * 10000)
        if short_match:
            put(db, "short")
    result = read(database)
    assert result.status == ("ok" if short_match else "empty")
    assert ids(result) == (["short"] if short_match else [])
    assert not result.truncated_by


def test_full_content_coverage_score_beats_newer_partial_match(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "partial", content="alpha")
        put(db, "full-old", content="x" * 25000 + " beta alpha", at="2020-01-01T00:00:00+00:00")
    result = read(database, limit=1)
    assert ids(result) == ["full-old", "partial"]
    assert "beta alpha" in result.items[0].content


@pytest.mark.parametrize("count", [0, 1, 20, 21, 22])
def test_sentinel_has_positive_and_negative_controls(database, count):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(count):
            put(db, f"m-{index:03d}")
    result = read(database, limit=20)
    assert len(result.items) == min(21, count)
    assert ("count_budget" in result.truncated_by) == (count > 20)


@pytest.mark.parametrize("field", ["memory_id", "evidence_id", "kind"])
def test_oversized_identity_metadata_fails_without_inventing_truncated_ids(database, field):
    with closing(sqlite3.connect(database)) as db, db:
        if field == "evidence_id":
            evidence(db, "e" * 513)
            put(db, "bad", links=("e" * 513,))
        else:
            put(
                db,
                "m" * 513 if field == "memory_id" else "bad",
                kind="k" * 65 if field == "kind" else "working",
            )
    result = read(database)
    assert (result.status, result.reason, result.items) == ("unavailable", "read_unavailable", ())
    assert result.failure_site == "memory_reader"
    assert result.error_class == ("ValueError" if field == "evidence_id" else "ValidationError")


def test_empty_scopes_revalidation_does_not_open_even_with_items(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "keep")
    items = read(database).items
    close_main_databases()
    monkeypatch.setattr(
        context_memory.sqlite3,
        "connect",
        lambda *a, **k: pytest.fail("Zero-authority revalidation opened DB"),
    )
    assert (
        revalidate_memory_context(
            database, items, scopes=frozenset(), deadline=time.monotonic() + 1
        )
        == ()
    )


def test_final_revalidation_drops_reference_set_changes_without_revision_change(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "changed")
    items = read(database).items
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE governed_memory_evidence SET active=0 WHERE memory_id='changed'")
    assert (
        revalidate_memory_context(database, items, scopes=None, deadline=time.monotonic() + 1) == ()
    )


def test_lifecycle_hidden_ids_are_filtered_before_limit_and_again_before_release(database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(25):
            put(
                db,
                f"hidden-{index:02d}",
                content="alpha beta",
                at=f"2026-09-19T02:{index:02d}:00Z",
            )
        put(db, "visible", content="alpha beta", at="2020-01-01T00:00:00Z")
    hidden = frozenset(f"hidden-{index:02d}" for index in range(25))
    result = read_memory_context(
        database,
        ContextSearchRequest(query="alpha beta", kinds=("memory",), limit=1),
        scopes=None,
        deadline=time.monotonic() + 5,
        hidden_memory_ids=hidden,
    )
    assert ids(result) == ["visible"]
    assert (
        revalidate_memory_context(
            database,
            result.items,
            scopes=None,
            deadline=time.monotonic() + 1,
            hidden_memory_ids=frozenset({"visible"}),
        )
        == ()
    )


def test_sql_completion_after_deadline_cannot_release_candidates(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "late", content="alpha beta content")
    expired = False
    connect = sqlite3.connect

    class LateCursor(sqlite3.Cursor):
        def fetchall(self):
            nonlocal expired
            result = super().fetchall()
            expired = True
            return result

    class LateConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "AS score" in sql:
                return self.cursor(factory=LateCursor).execute(sql, parameters)
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        context_memory.sqlite3, "connect", lambda *a, **k: connect(*a, **k, factory=LateConnection)
    )
    monkeypatch.setattr(context_memory.time, "monotonic", lambda: 2 if expired else 0)
    result = read_memory_context(
        database, ContextSearchRequest(query="alpha beta"), scopes=None, deadline=1
    )
    assert expired
    assert (result.status, result.reason, result.items) == ("unavailable", "timeout_budget", ())


def test_sql_like_runs_only_on_authorized_bodies(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "scope-denied", content="SECRET alpha", scopes=("workspace:other",))
        put(db, "revoked", content="SECRET alpha", links=("e2",))
        put(db, "good", content="alpha beta")
        revoke(db, "workbuddy")
    connect = sqlite3.connect
    inspected = []

    # Test-only LIKE spy uses a separate SQLite connection for native semantics.
    # Production registers no Python body-processing function.
    with closing(connect(":memory:")) as native:

        def like(pattern, content, escape):
            inspected.append(content)
            assert "SECRET" not in content
            return native.execute(
                "SELECT ? LIKE ? ESCAPE ?", (content, pattern, escape)
            ).fetchone()[0]

        def tracked(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.create_function("like", 3, like)
            return db

        monkeypatch.setattr(context_memory.sqlite3, "connect", tracked)
        assert ids(read(database, scopes=frozenset({"all-apps"}))) == ["good"]
    assert inspected and set(inspected) == {"alpha beta"}


def test_like_substring_semantics_match_existing_governance_candidate_query(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "substring", content="preALPHABETAsuffix")
        put(db, "literal", content="alpha_beta")
        put(db, "decoy", content="alphaXbeta")
    assert "substring" in ids(read(database, "alpha beta"))
    assert ids(read(database, "alpha_beta")) == ["literal"]


def test_term_budget_is_bounded_and_order_independent(database):
    terms = [f"term{i:02d}" for i in range(40)]
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "match", content=" ".join(terms))
    first = read(database, " ".join(terms))
    assert ids(first) == ["match"] and first.truncated_by == ("metadata_budget",)
    assert first == read(database, " ".join(reversed(terms)))


def test_content_candidate_optional_contract_is_fallback_safe(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "good", content="alpha beta")
        put(db, "other", content="alpha beta too")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 0)
    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_UNION_VERSION", 0)
    monkeypatch.setattr(context_memory, "content_candidate_predicate", None)
    assert set(ids(read(database))) == {"good", "other"}

    def no_candidate(_db, terms, *, alias):
        calls.append(tuple(terms))
        return None, ()

    monkeypatch.setattr(context_memory, "content_candidate_predicate", no_candidate)
    assert set(ids(read(database))) == {"good", "other"}
    assert calls == []
    assert set(ids(read(database, "alpha"))) == {"good", "other"}
    assert calls[-1] == ("alpha",)

    def one_candidate(_db, terms, *, alias):
        calls.append(tuple(terms))
        return "m.memory_id = ?", ("good",)

    monkeypatch.setattr(context_memory, "content_candidate_predicate", one_candidate)
    assert ids(read(database, "alpha")) == ["good"]
    assert calls[-1] == ("alpha",)


def test_multi_term_candidate_is_not_consumed_without_complete_signal(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "alpha-only", content="alpha")
        put(db, "beta-only", content="beta")

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_UNION_VERSION", 0)

    def union_candidate(_db, terms, *, alias):
        calls.append(tuple(terms))
        target = "alpha-only" if terms == ("alpha",) else "beta-only"
        return "m.memory_id = ?", (target,)

    monkeypatch.setattr(context_memory, "content_candidate_predicate", union_candidate)
    assert set(ids(read(database, "alpha beta"))) == {"alpha-only", "beta-only"}
    assert calls == []

    def partial_candidate(_db, terms, *, alias):
        calls.append(tuple(terms))
        if terms == ("beta",):
            return None, ()
        return "m.memory_id = ?", ("alpha-only",)

    calls.clear()
    monkeypatch.setattr(context_memory, "content_candidate_predicate", partial_candidate)
    assert set(ids(read(database, "alpha beta"))) == {"alpha-only", "beta-only"}
    assert calls == []


def test_content_candidate_v1_only_uses_single_term_queries(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "alpha-only", content="alpha")
        put(db, "beta-only", content="beta")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 0)
    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_UNION_VERSION", 1)

    def union_candidate(_db, terms, *, alias):
        calls.append(tuple(terms))
        if terms == ("alpha",):
            return "m.memory_id = ?", ("alpha-only",)
        raise AssertionError("multi-term query must not consume unproven C3 candidates")

    monkeypatch.setattr(context_memory, "content_candidate_predicate", union_candidate)
    # A selectivity-filtered multi-term union has no all-terms-covered signal,
    # so C4 keeps the complete OR path.
    assert set(ids(read(database, "alpha beta"))) == {"alpha-only", "beta-only"}
    assert calls == []
    assert ids(read(database, "alpha")) == ["alpha-only"]
    assert calls == [("alpha",)]


def test_bounded_candidate_without_truncation_signal_falls_back(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "alpha-only", content="alpha")
        put(db, "beta-only", content="beta")
    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_UNION_VERSION", 1)

    def bounded(_db, _terms, *, alias):
        return (
            "m.memory_id IN ("
            "SELECT memory_id FROM governed_memories_terms "
            "GROUP BY memory_id ORDER BY count(*) DESC LIMIT 200)",
            (),
        )

    monkeypatch.setattr(context_memory, "content_candidate_predicate", bounded)
    # No truncation/coverage signal is available, so the safe answer is the
    # complete LIKE path rather than an apparently complete bounded subset.
    assert ids(read(database, "alpha")) == ["alpha-only"]


def test_coverage_aware_candidate_plan_is_used_only_when_complete(database, monkeypatch):
    from types import SimpleNamespace

    with closing(sqlite3.connect(database)) as db, db:
        put(db, "alpha-only", content="alpha")
        put(db, "beta-only", content="beta")

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 1)

    def complete_plan(_db, terms, *, alias):
        calls.append(tuple(terms))
        values = ("alpha-only",) if terms == ("alpha",) else ("alpha-only", "beta-only")
        return SimpleNamespace(
            predicate=(
                "m.memory_id = ?"
                if len(values) == 1
                else f"m.memory_id IN ({','.join('?' * len(values))})"
            ),
            values=values,
            complete=True,
        )

    monkeypatch.setattr(context_memory, "content_candidate_plan", complete_plan)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_predicate",
        lambda *_args, **_kwargs: pytest.fail("legacy helper must not be used"),
    )
    assert ids(read(database, "alpha")) == ["alpha-only"]
    assert calls == [("alpha",)]
    # A complete plan proves all-term coverage, so it is safe for multi-term
    # OR queries too.
    assert set(ids(read(database, "alpha beta"))) == {"alpha-only", "beta-only"}
    assert calls[-1] == ("alpha", "beta")

    def incomplete_plan(_db, terms, *, alias):
        calls.append(tuple(terms))
        return SimpleNamespace(
            predicate="m.memory_id = ?",
            values=("missing",),
            complete=False,
        )

    calls.clear()
    monkeypatch.setattr(context_memory, "content_candidate_plan", incomplete_plan)
    assert ids(read(database, "alpha")) == ["alpha-only"]
    assert calls == [("alpha",)]
    # Incomplete coverage must fall back instead of hiding an OR match.
    assert set(ids(read(database, "alpha beta"))) == {"alpha-only", "beta-only"}
    assert calls == [("alpha",), ("alpha", "beta")]


def test_incomplete_candidate_plan_uses_all_term_shadow_union(database, monkeypatch):
    from types import SimpleNamespace

    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS governed_memories_terms (term TEXT, memory_id TEXT, "
            "PRIMARY KEY(term, memory_id))"
        )
        put(db, "alpha-beta", content="alpha beta")
        put(db, "alpha-only", content="alpha")
        db.executemany(
            "INSERT INTO governed_memories_terms(term,memory_id) VALUES (?,?)",
            (("alpha", "alpha-beta"), ("beta", "alpha-beta"), ("alpha", "alpha-only")),
        )

    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 1)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: SimpleNamespace(
            predicate="m.memory_id = ?",
            values=("dropped-by-selectivity",),
            complete=False,
        ),
    )
    # The incomplete C3 plan would lose `beta`; C4's union keeps both terms,
    # then the original LIKE score still decides which rows are returns.
    result = read(database, "alpha beta")
    assert set(ids(result)) == {"alpha-beta", "alpha-only"}
    assert "coverage_budget" not in result.truncated_by

    calls: list[tuple[str, ...]] = []
    original = context_memory._all_term_candidate_union

    def observed(db, terms, *, threshold):
        calls.append((terms, threshold))
        return original(db, terms, threshold=threshold)

    monkeypatch.setattr(context_memory, "_all_term_candidate_union", observed)
    assert set(ids(read(database, "alpha beta"))) == {"alpha-beta", "alpha-only"}
    assert calls == [(("alpha", "beta"), 1)]


def test_candidate_query_is_materialized_before_the_primary_table_join(database, monkeypatch):
    from types import SimpleNamespace

    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS governed_memories_terms (term TEXT, memory_id TEXT, "
            "PRIMARY KEY(term, memory_id))"
        )
        put(db, "alpha-only", content="alpha")
        db.execute(
            "INSERT INTO governed_memories_terms(term,memory_id) VALUES ('alpha','alpha-only')"
        )

    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 1)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: SimpleNamespace(
            predicate=(
                "m.memory_id IN (SELECT memory_id FROM governed_memories_terms WHERE term='alpha')"
            ),
            values=("alpha-only",),
            complete=True,
        ),
    )
    plans: list[str] = []
    original_connect = context_memory.sqlite3.connect

    class Explained(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "AS score" in sql:
                plans.extend(
                    row[3] for row in super().execute("EXPLAIN QUERY PLAN " + sql, parameters)
                )
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        context_memory.sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=Explained),
    )
    assert ids(read(database, "alpha")) == ["alpha-only"]
    assert any("MATERIALIZE candidate" in plan for plan in plans), plans
    assert any("SEARCH m USING INDEX" in plan for plan in plans), plans


def test_empty_candidate_plan_still_uses_the_maintained_shadow_union(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS governed_memories_terms (term TEXT, memory_id TEXT, "
            "PRIMARY KEY(term, memory_id))"
        )
        put(db, "alpha-only", content="alpha")
        db.execute(
            "INSERT INTO governed_memories_terms(term,memory_id) VALUES ('alpha','alpha-only')"
        )

    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 1)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: None,
    )
    plans: list[str] = []
    original_connect = context_memory.sqlite3.connect

    class Explained(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "AS score" in sql:
                plans.extend(
                    row[3] for row in super().execute("EXPLAIN QUERY PLAN " + sql, parameters)
                )
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        context_memory.sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=Explained),
    )
    assert ids(read(database, "alpha")) == ["alpha-only"]
    assert any("MATERIALIZE candidate" in plan for plan in plans), plans
    assert any("SEARCH m USING INDEX" in plan for plan in plans), plans


def test_unbackfilled_shadow_does_not_black_out_lexical_recall(database):
    """Migration 32 creates the shadow eagerly, so an empty table means
    'not backfilled yet', not 'no memory contains this term'. Treating an
    absent term as selective would narrow every query to the empty candidate
    set and silently return nothing; recall must fall back to the LIKE path.
    """
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "zh", content="需要记录项目的发布范围")
        assert db.execute("SELECT count(*) FROM governed_memories_terms").fetchone() == (0,)

    assert ids(read(database, "发布范围")) == ["zh"]
    assert ids(read(database, "alpha beta")) == []


def test_selective_shadow_terms_narrow_without_hiding_a_valid_or_match(database, monkeypatch):
    from types import SimpleNamespace

    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS governed_memories_terms (term TEXT, memory_id TEXT, "
            "PRIMARY KEY(term, memory_id))"
        )
        put(db, "selective-match", content="rare common")
        put(db, "common-only", content="common")
        db.executemany(
            "INSERT INTO governed_memories_terms(term,memory_id) VALUES (?,?)",
            (("rare", "selective-match"), ("common", "selective-match"), ("common", "common-only")),
        )

    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 1)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: SimpleNamespace(
            predicate="m.memory_id = ?",
            values=("incomplete",),
            complete=False,
        ),
    )
    # `rarest` is selective and `common` is not enough to let a non-selective
    # term qualify alone; the common-only row is still a valid OR match and
    # must survive because the full LIKE score runs after the narrowing.
    result = read(database, "rare common")
    assert set(ids(result)) == {"selective-match", "common-only"}


def test_empty_freshness_store_does_not_evaluate_the_udf_per_candidate(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript(
            "CREATE TABLE memory_task_outputs (task_id TEXT,memory_id TEXT,body TEXT);"
        )
        for index in range(3):
            put(db, f"candidate-{index}", content="alpha")

    calls = []
    monkeypatch.setattr(
        context_memory,
        "register_memory_freshness",
        lambda _db: calls.append("registered") or "1",
    )
    assert ids(read(database, "alpha")) == [
        "candidate-0",
        "candidate-1",
        "candidate-2",
    ]
    # The table exists but has no proof rows. The UDF would return 1 for every
    # row anyway; avoid the per-row call while preserving that semantics.
    assert calls == []


def test_freshness_is_evaluated_only_after_candidate_ranking(database, monkeypatch):
    from types import SimpleNamespace

    with closing(sqlite3.connect(database)) as db, db:
        db.executescript(
            "CREATE TABLE memory_task_outputs (task_id TEXT,memory_id TEXT,body TEXT);"
        )
        db.execute("INSERT INTO memory_task_outputs VALUES ('proof','proof','{}')")
        for index in range(2_280):
            put(
                db,
                f"candidate-{index:04d}",
                content="alpha" if index < 9 else f"other-{index:04d}",
            )
        db.executemany(
            "INSERT INTO governed_memories_terms(term,memory_id) VALUES ('alpha',?)",
            ((f"candidate-{index:04d}",) for index in range(9)),
        )

    calls: list[str] = []

    def register(db):
        def current(memory_id, _revision, _user_id):
            calls.append(memory_id)
            return 1

        db.create_function("test_memory_current", 3, current)
        return "test_memory_current(m.memory_id,m.revision,m.user_id)"

    monkeypatch.setattr(context_memory, "register_memory_freshness", register)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: SimpleNamespace(
            predicate=(
                "m.memory_id IN (SELECT memory_id FROM governed_memories_terms WHERE term = ?)"
            ),
            values=("alpha",),
            complete=True,
        ),
    )

    read(database, "alpha")
    calls.clear()
    started = time.perf_counter()
    result = read(database, "alpha")
    elapsed = time.perf_counter() - started

    assert len(result.items) == 9
    assert len(calls) <= 9
    assert elapsed < 0.1


def test_stale_top_candidates_fetch_the_next_ranked_batch(database, monkeypatch):
    from types import SimpleNamespace

    with closing(sqlite3.connect(database)) as db, db:
        db.executescript(
            "CREATE TABLE memory_task_outputs (task_id TEXT,memory_id TEXT,body TEXT);"
        )
        db.execute("INSERT INTO memory_task_outputs VALUES ('proof','proof','{}')")
        for index in range(18):
            key = f"candidate-{index:02d}"
            put(db, key, content="alpha")
            db.execute(
                "INSERT INTO governed_memories_terms(term,memory_id) VALUES ('alpha',?)", (key,)
            )

    def register(db):
        db.create_function(
            "test_memory_current",
            3,
            lambda memory_id, _revision, _user_id: int(memory_id >= "candidate-09"),
        )
        return "test_memory_current(m.memory_id,m.revision,m.user_id)"

    monkeypatch.setattr(context_memory, "register_memory_freshness", register)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: SimpleNamespace(
            predicate=(
                "m.memory_id IN (SELECT memory_id FROM governed_memories_terms WHERE term = ?)"
            ),
            values=("alpha",),
            complete=True,
        ),
    )

    assert ids(read(database, "alpha")) == [f"candidate-{index:02d}" for index in range(9, 18)]


def test_complete_candidate_plan_cannot_bypass_memory_authority(database, monkeypatch):
    from types import SimpleNamespace

    with closing(sqlite3.connect(database)) as db, db:
        put(db, "alpha-only", content="alpha", links=("e1",))
        put(db, "revoked-beta", content="beta", links=("e2",))
        revoke(db, "workbuddy")

    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 1)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: SimpleNamespace(
            predicate="m.memory_id IN (?, ?)",
            values=("alpha-only", "revoked-beta"),
            complete=True,
        ),
    )
    # The candidate plan may contain the revoked row, but the production
    # authority predicate still has to remove it before the result is built.
    assert ids(read(database, "alpha beta")) == ["alpha-only"]


def test_complete_candidate_plan_cannot_bypass_time_window(database, monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from one_memory.retrieval.models import TimeRange

    with closing(sqlite3.connect(database)) as db, db:
        put(db, "alpha-only", content="alpha", at=NOW)
        put(db, "old-beta", content="beta", at="2026-01-01T00:00:00+00:00")

    monkeypatch.setattr(context_memory, "CONTENT_CANDIDATE_PLAN_VERSION", 1)
    monkeypatch.setattr(
        context_memory,
        "content_candidate_plan",
        lambda _db, _terms, *, alias: SimpleNamespace(
            predicate="m.memory_id IN (?, ?)",
            values=("alpha-only", "old-beta"),
            complete=True,
        ),
    )
    result = read_memory_context(
        database,
        ContextSearchRequest(query="alpha beta"),
        scopes=None,
        deadline=time.monotonic() + 3,
        time_range=TimeRange(
            start=datetime(2026, 9, 1, tzinfo=UTC),
            end=datetime(2026, 10, 1, tzinfo=UTC),
        ),
    )
    assert ids(result) == ["alpha-only"]


@pytest.mark.parametrize("tail", [" ALPHA_BETA", " 发布范围"])
def test_tail_excerpt_is_literal_unicode_contiguous_and_revalidates(database, tail):
    content = "前" * 30000 + tail + "后" * 30000
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "memory", content=content)
    result = read(database, tail)
    assert ids(result) == ["memory"]
    item = result.items[0]
    assert tail.strip() in item.content
    assert len(item.content) == 20000 and item.content in content and item.truncated
    assert (
        revalidate_memory_context(
            database, result.items, scopes=None, deadline=time.monotonic() + 1
        )
        == result.items
    )


def test_semantic_ids_join_when_lexical_does_not_overlap(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "strong", content="用户决定 One Memory 采用 MIT 协议发布。")
        put(db, "weak", content="这个开源项目的许可方案还在评审中，尚未定稿。")
    result = read_memory_context(
        database,
        ContextSearchRequest(query="开源许可证怎么选", kinds=("memory",)),
        scopes=None,
        deadline=time.monotonic() + 5,
        semantic_ids=("strong",),
    )
    assert ids(result)[0] == "strong"
    assert "strong" in ids(result)
    assert "MIT" in result.items[0].content


def test_unique_keyword_match_is_not_displaced_by_full_semantic_top_k(database):
    marker = "n30-exact-unique-marker"
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "lexical-target", content=f"用户使用 {marker} 作为唯一验收代号。")
        semantic_ids = []
        for index in range(10):
            memory_id = f"semantic-unrelated-{index}"
            semantic_ids.append(memory_id)
            put(db, memory_id, content=f"语义候选 {index} 与唯一验收代号无关。")

    request = ContextSearchRequest(query=marker, kinds=("memory",), limit=8)
    result = read_memory_context(
        database,
        request,
        scopes=None,
        deadline=time.monotonic() + 5,
        semantic_ids=tuple(semantic_ids),
        semantic_outcome="answered",
    )

    visible = result.items[: request.limit]
    target = next(item for item in visible if item.memory_id == "lexical-target")
    assert target.matched_by == ("lexical",)
    assert any("governed_semantic" in item.matched_by for item in visible)


def test_semantic_ids_are_still_scope_and_status_bound(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "hidden", content="secret decision", scopes=("workspace:v1:other",))
        put(db, "draft", content="draft decision", status="superseded")
    result = read_memory_context(
        database,
        ContextSearchRequest(query="decision", kinds=("memory",)),
        scopes=frozenset({"all-apps"}),
        deadline=time.monotonic() + 5,
        semantic_ids=("hidden", "draft"),
    )
    assert ids(result) == []


def test_current_query_follows_explicit_supersession_not_date_or_title(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "old", content="项目发布用个人证书。", status="superseded")
        put(db, "head", content="项目发布现在改用团队证书。")
        put(db, "newer-same-title", content="另一个项目发布现在仍用个人证书。")
        db.execute(
            "INSERT INTO governed_memory_supersessions(memory_id,superseded_by,created_at) "
            "VALUES('old','head',?)",
            (NOW,),
        )
    result = read_memory_context(
        database,
        ContextSearchRequest(query="现在该用哪张凭据", kinds=("memory",)),
        scopes=None,
        deadline=time.monotonic() + 5,
        semantic_ids=("old",),
        semantic_outcome="answered",
    )
    assert ids(result) == ["head"]
    assert "团队证书" in result.items[0].content


def test_historical_query_does_not_rewrite_a_superseded_id_to_current(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "old", content="当时发布用个人证书。", status="superseded")
        put(db, "head", content="后来改用团队证书。")
        db.execute(
            "INSERT INTO governed_memory_supersessions(memory_id,superseded_by,created_at) "
            "VALUES('old','head',?)",
            (NOW,),
        )
    result = read_memory_context(
        database,
        ContextSearchRequest(query="当时怎么决定的", kinds=("memory",)),
        scopes=None,
        deadline=time.monotonic() + 5,
        semantic_ids=("old",),
        semantic_outcome="answered",
    )
    assert ids(result) == []


def test_correction_head_must_still_pass_scope_and_source_authority(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "old", content="旧决定", status="superseded")
        put(db, "hidden-head", content="现在的私密决定", scopes=("workspace:secret",))
        put(db, "revoked-head", content="现在的撤权决定", links=("e2",))
        db.executemany(
            "INSERT INTO governed_memory_supersessions(memory_id,superseded_by,created_at) "
            "VALUES('old',?,?)",
            (("hidden-head", NOW), ("revoked-head", NOW)),
        )
        revoke(db, "workbuddy")
    result = read_memory_context(
        database,
        ContextSearchRequest(query="现在的决定", kinds=("memory",)),
        scopes=frozenset({"all-apps"}),
        deadline=time.monotonic() + 5,
        semantic_ids=("old",),
        semantic_outcome="answered",
    )
    assert ids(result) == []


@pytest.mark.xfail(
    strict=True,
    reason="RCL-11 requires C3 scoped HY search before top-K; unscoped top-12 omits readable #13",
)
def test_semantic_authority_must_precede_hy_topk(database):
    with closing(sqlite3.connect(database)) as db, db:
        hidden = []
        for index in range(12):
            memory_id = f"hidden-{index:02d}"
            hidden.append(memory_id)
            put(db, memory_id, content="语义相关的私密决定", scopes=("workspace:secret",))
        put(db, "readable-13", content="语义相关的合法决定")
    # This is the current unscoped HY contract: its finite top-K contains only
    # inaccessible IDs. The readable candidate exists in HY at rank 13 but the
    # context lane cannot ask HY to apply authority before its limit.
    result = read_memory_context(
        database,
        ContextSearchRequest(query="换一种说法找决定", kinds=("memory",)),
        scopes=frozenset({"all-apps"}),
        deadline=time.monotonic() + 5,
        semantic_ids=tuple(hidden),
        semantic_outcome="answered",
    )
    assert "readable-13" in ids(result)


@pytest.mark.parametrize(
    ("outcome", "expected_reason"),
    [
        ("absent", "index_incomplete"),
        ("failed", "semantic_unavailable"),
        ("timed_out", "timeout_budget"),
    ],
)
def test_memory_v2_path_never_recasts_incomplete_semantics_as_empty(
    database, outcome, expected_reason
):
    result = read_memory_context(
        database,
        ContextSearchRequest(query="no-match", kinds=("memory",)),
        scopes=None,
        deadline=time.monotonic() + 5,
        semantic_outcome=outcome,
    )
    path = compose_memory_retrieval_path(result)
    assert path.status == "degraded"
    assert path.coverage == "partial"
    assert path.reason == expected_reason
    assert "coverage_budget" in path.truncated_by


def test_memory_v2_path_distinguishes_semantic_success_from_complete_coverage(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "semantic", content="different wording")
    result = read_memory_context(
        database,
        ContextSearchRequest(query="no lexical overlap", kinds=("memory",)),
        scopes=None,
        deadline=time.monotonic() + 5,
        semantic_ids=("semantic",),
        semantic_outcome="answered",
    )
    path = compose_memory_retrieval_path(result)
    assert path.returned_count == 1
    assert path.searched_channels == ("lexical", "governed_semantic")
    assert path.coverage == "partial" and path.reason == "index_incomplete"
