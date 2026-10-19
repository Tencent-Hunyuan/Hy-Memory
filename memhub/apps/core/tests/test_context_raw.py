import hashlib
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from one_memory import context_raw
from one_memory.context_models import ContextSearchRequest
from one_memory.context_raw import read_raw_context, revalidate_raw_context
from one_memory.database_access import close_main_databases, main_database
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.storage import SCHEMA, SEARCH_SCHEMA, EvidenceStore


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "raw # context.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA + GRANT_SCHEMA + SEARCH_SCHEMA)
    return path


def put(
    db,
    key,
    text="needle",
    *,
    source="cursor",
    user="local",
    conversation=None,
    role="user",
    author="person",
    created="2026-09-19T01:00:00+00:00",
    revision=1,
):
    db.execute(
        """INSERT INTO evidence
        (evidence_id,idempotency_key,user_id,source_id,conversation_id,role,written_by,
         created_at,verbatim,content_hash,revision,imported_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,'synthetic',?,'2026-09-19','2026-09-19')""",
        (key, key, user, source, conversation or key, role, author, created, text, revision),
    )


def revoke(db, source, *, user="local"):
    db.execute(
        "INSERT INTO source_grants "
        "(user_id,source_id,root_path,requested_path,root_identity,candidate_kind,"
        "granted_at,revoked_at,revision) "
        "VALUES (?,?,'/synthetic','/synthetic','synthetic','default',"
        "'2026-09-19','revoked',1)",
        (user, source),
    )


def read(path, query="needle", sources=None, **kwargs):
    return read_raw_context(
        path,
        ContextSearchRequest(query=query, **kwargs),
        source_ids=sources,
        deadline=time.monotonic() + 5,
    )


def revalidate(path, items, sources=None):
    return revalidate_raw_context(path, items, source_ids=sources, deadline=time.monotonic() + 5)


def test_empty_authority_and_unrequested_path_do_not_open_database(database, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("denied lane opened SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert read(database, sources=frozenset()).status == "not_authorized"
    assert read(database, kinds=("memory",)).status == "not_requested"
    assert revalidate(database, (), sources=frozenset()) == ()


def test_permissions_precede_candidate_limit_and_no_model_needed(database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(60):
            put(db, f"blocked-{index:03}", source="revoked")
            put(db, f"foreign-{index:03}", user="other")
            put(db, f"denied-{index:03}", source="outside")
        put(db, "readable", role="tool", author="unknown", created=None, revision=4)
        revoke(db, "revoked")
        revoke(db, "cursor", user="other")
    result = read(database, sources=frozenset({"cursor", "revoked"}), limit=1)
    assert result.status == "ok"
    assert [item.anchor_evidence_id for item in result.items] == ["readable"]
    message = result.items[0].messages[0]
    assert (message.role, message.written_by, message.created_at, message.revision) == (
        "tool",
        "unknown",
        None,
        4,
    )


def test_explicit_source_filter_never_broadens_authority(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a", source="cursor")
        put(db, "b", source="workbuddy")
    assert [item.source_id for item in read(database, raw_source_ids=("workbuddy",)).items] == [
        "workbuddy"
    ]
    result = read(database, sources=frozenset({"cursor"}), raw_source_ids=("workbuddy",))
    assert not result.items and result.status == "not_authorized"
    assert len(read(database).items) == 2


@pytest.mark.parametrize(
    "query, body",
    [
        ("beta alpha", "alpha then beta"),
        ("记忆 搜索", "这里有搜索也有记忆"),
        ("café привет", "привет café"),
        ("%_\\", "literal %_\\"),
        ('" OR *', 'literal " OR *'),
        ("打包", "今天打包"),
    ],
)
def test_lexical_terms_multilingual_and_literal_syntax(database, query, body):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "hit", body)
        put(db, "miss", "unrelated xyz")
    result = read(database, query)
    assert result.status == "ok"
    assert [item.anchor_evidence_id for item in result.items] == ["hit"]


def test_query_2000_is_not_truncated_to_512(database):
    query = "filler " * 284 + "tailword"
    assert 512 < len(query) <= 2000
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "tail", "tailword")
    assert [item.anchor_evidence_id for item in read(database, query).items] == ["tail"]


def test_large_body_returns_bounded_actual_anchor_and_true_metadata(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "long", "x" * 1_000_000 + " needle actual ending", role="assistant", author="agent")
    result = read(database, character_budget=200)
    item = result.items[0]
    anchor = next(
        message for message in item.messages if message.evidence_id == item.anchor_evidence_id
    )
    assert "needle" in anchor.text
    assert len(anchor.text) <= 200
    assert anchor.truncated and item.truncated
    assert "character_budget" in result.truncated_by
    assert (anchor.role, anchor.written_by) == ("assistant", "agent")
    assert anchor.created_at.isoformat() == "2026-09-19T01:00:00+00:00"


def test_neighbors_are_same_authorized_conversation_and_maximum_three(database):
    with closing(sqlite3.connect(database)) as db, db:
        for key in ("a", "b", "d", "e"):
            put(db, key, "adjacent", conversation="thread")
        put(db, "c", "needle", conversation="thread")
        put(db, "bb", "foreign", user="other", conversation="thread")
        put(db, "cc", "other source", source="workbuddy", conversation="thread")
    result = read(database, sources=frozenset({"cursor"}))
    assert len(result.items) == 1
    item = result.items[0]
    assert item.anchor_evidence_id == "c"
    assert [message.evidence_id for message in item.messages] == ["c", "b", "d"]
    assert item.messages[0].evidence_id == item.anchor_evidence_id


def test_anchor_body_precedes_adjacent_context_in_the_emitted_window(database):
    """The lexical anchor is the answer, not whatever message happens to be older."""
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "older", "上午 10:00", conversation="thread", created="2026-09-19T00:00:00+00:00")
        put(
            db,
            "fact",
            "C5回流验收标记：周会时间改为每周四上午九点，旧值作废。",
            conversation="thread",
            created=None,
        )
        db.execute(
            "UPDATE evidence SET imported_at=? WHERE evidence_id='older'",
            ("2026-09-19T00:00:00+00:00",),
        )
        db.execute(
            "UPDATE evidence SET imported_at=? WHERE evidence_id='fact'",
            ("2026-09-19T01:00:00+00:00",),
        )
    result = read(database, query="周会时间改为每周四上午九点")
    item = result.items[0]
    assert item.anchor_evidence_id == "fact"
    assert [message.evidence_id for message in item.messages] == ["fact", "older"]
    assert item.messages[0].evidence_id == item.anchor_evidence_id
    assert "周四上午九点" in item.messages[0].text


def test_continuation_query_prefers_a_declared_fact_over_repeated_question_text(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(
            db,
            "question",
            "继续上次的周会时间任务，只回答时间",
            conversation="thread-question",
            created=None,
        )
        put(
            db,
            "fact",
            "C5回流验收标记：周会时间改为每周四上午九点，旧值作废。",
            conversation="thread-fact",
            created=None,
        )
    result = read(database, query="继续上次的周会时间任务，只回答时间", limit=1)
    assert result.items[0].anchor_evidence_id == "fact"
    assert "周四上午九点" in result.items[0].messages[0].text


def test_continuation_query_prefers_person_fact_over_tool_output(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(
            db,
            "tool",
            "周会时间记录：继续上次的周会时间任务",
            conversation="thread-tool",
            role="tool",
            author="tool",
            created="2026-09-29T00:00:00+00:00",
        )
        put(
            db,
            "fact",
            "C5回流验收标记：周会时间改为每周四上午九点，旧值作废。",
            conversation="thread-fact",
            created=None,
        )
    result = read(database, query="继续上次的周会时间任务，只回答时间", limit=1)
    ids = [item.anchor_evidence_id for item in result.items]
    assert ids.index("fact") < ids.index("tool")
    assert result.items[0].anchor_evidence_id == "fact"
    assert "周四上午九点" in result.items[0].messages[0].text


def test_direct_fact_query_prefers_person_fact_over_repeated_question(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(
            db,
            "question",
            "周会时间改为每周四上午九点",
            conversation="thread-question",
            created="2026-09-29T00:00:00+00:00",
        )
        put(
            db,
            "fact",
            "C5回流验收标记：周会时间改为每周四上午九点，旧值作废。",
            conversation="thread-fact",
            created=None,
        )
    result = read(database, query="周会时间改为每周四上午九点", limit=1)
    assert result.items[0].anchor_evidence_id == "fact"
    assert "周四上午九点" in result.items[0].messages[0].text


@pytest.mark.parametrize("anchor_index", range(4))
@pytest.mark.parametrize(
    "ordering",
    ["same-time-reverse-ids", "missing-created-import-order", "null-ordinal", "text-ordinal"],
)
def test_neighbors_match_canonical_conversation_order(
    database, monkeypatch, ordering, anchor_index
):
    # This full-list call is only a test oracle, never the adapter implementation.
    store = EvidenceStore(database)
    timestamp = "2026-09-19T01:00:00+00:00"
    if ordering == "same-time-reverse-ids":
        ordered = [
            (key, timestamp, timestamp, str(index))
            for index, key in enumerate(("z", "y", "b", "a"))
        ]
    elif ordering == "missing-created-import-order":
        ordered = [
            (key, None, f"2026-09-19T0{index}:00:00+00:00", None)
            for index, key in enumerate(("z", "y", "b", "a"))
        ]
        # A present created_at wins over a much later imported_at.
        ordered[1] = ("y", "2026-09-19T01:00:00+00:00", "2026-09-20T00:00:00+00:00", None)
    elif ordering == "null-ordinal":
        ordered = [
            (key, timestamp, timestamp, ordinal)
            for key, ordinal in (("y", None), ("z", None), ("b", "10"), ("a", "2"))
        ]
    else:
        ordered = [
            (key, timestamp, timestamp, ordinal)
            for key, ordinal in (("z", "001"), ("y", "10"), ("b", "2"), ("a", "part:3"))
        ]
    anchor_id = ordered[anchor_index][0]
    with closing(sqlite3.connect(database)) as db, db:
        for key, created, imported, ordinal in reversed(ordered):
            put(
                db,
                key,
                "needle" if key == anchor_id else "adjacent",
                conversation="ordered",
                created=created,
            )
            db.execute(
                "UPDATE evidence SET imported_at=?,source_ordinal=? WHERE evidence_id=?",
                (imported, ordinal, key),
            )
        # Same conversation ID cannot bridge to another user or source.
        put(db, "other-user", "adjacent", conversation="ordered", user="other")
        put(db, "other-source", "adjacent", conversation="ordered", source="workbuddy")
    canonical = [record.evidence_id for record in store.list_conversation(anchor_id)]
    assert canonical == [row[0] for row in ordered]

    def forbidden(*args, **kwargs):
        pytest.fail("raw adapter must not hydrate the whole conversation")

    monkeypatch.setattr(EvidenceStore, "list_conversation", forbidden)
    connect = sqlite3.connect
    neighbor_reads = []

    class BoundedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "substr(e.verbatim,1,?)" in sql:
                neighbor_reads.append(sql)
                assert "LIMIT 1" in sql
            return super().execute(sql, parameters)

    # Pooled connections were opened before the patch; start the pool afresh.
    close_main_databases()
    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **k: connect(*a, **k, factory=BoundedConnection)
    )
    result = read(database, sources=frozenset({"cursor"}))
    assert result.status == "ok" and len(result.items) == 1
    item = result.items[0]
    assert item.anchor_evidence_id == anchor_id
    canonical_window = canonical[max(0, anchor_index - 1) : anchor_index + 2]
    # The selected window is canonical, but the answer-bearing anchor is
    # emitted first so a host rendering messages[0] cannot show a neighbor.
    assert [message.evidence_id for message in item.messages] == [
        anchor_id,
        *(identity for identity in canonical_window if identity != anchor_id),
    ]
    assert len(neighbor_reads) == 2
    assert "source_ordinal" not in item.model_dump_json()
    assert "imported_at" not in item.model_dump_json()


@pytest.mark.parametrize(
    "query,body",
    [
        ("我们之前讨论过的上下文检索方案是什么", "方案记录：采用上下文检索，保留来源。"),
        ("检索方案和权限检查如何实现", "先做权限检查，然后设计检索方案。"),
        ("alpha,beta?", "beta comes before alpha"),
        ("权限，检索？", "检索结果必须核对权限"),
    ],
)
def test_natural_lexical_query_is_not_a_whole_sentence_substring(database, query, body):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "matched", body)
        put(db, "unrelated", "unrelated content")
    result = read(database, query)
    assert result.status == "ok"
    assert [item.anchor_evidence_id for item in result.items] == ["matched"]


@pytest.mark.parametrize(
    "query, older, newer",
    [
        ("alpha,beta", "beta and alpha", "alpha only"),
        ("alpha beta", "beta and alpha", "alpha only"),
        ("权限 检索", "检索之后核对权限", "只记录权限"),
    ],
)
def test_coverage_ranking_precedes_limit_not_newest_candidate_pool(database, query, older, newer):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(60):
            put(db, f"new-{index:03}", newer, created="2026-09-20T00:00:00+00:00")
        put(db, "old-relevant", older, created="2026-09-18T00:00:00+00:00")
        put(db, "foreign", query, user="other")
        put(db, "revoked", query, source="revoked")
        revoke(db, "revoked")
    result = read(database, query, limit=1)
    assert result.items[0].anchor_evidence_id == "old-relevant"
    assert len(result.items) == 2 and "count_budget" in result.truncated_by
    assert result == read(database, query, limit=1)


def test_exact_phrase_breaks_equal_coverage_before_time(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "new-reversed", "beta then alpha", created="2026-09-20T00:00:00+00:00")
        put(db, "old-exact", "stored alpha beta phrase", created="2026-09-18T00:00:00+00:00")
    result = read(database, "alpha beta", limit=1)
    assert result.items[0].anchor_evidence_id == "old-exact"


def test_lexical_terms_stay_bounded_and_cover_whole_long_chinese_query():
    terms = context_raw._terms("开头词" + "请检查这些已保存的内容" * 100 + "结尾词")
    assert 0 < len(terms) <= 16
    assert "开头词" in terms and "结尾词" in terms
    assert all(len(term) == 3 for term in terms)
    assert context_raw._terms("权限") == ("权限",)
    assert context_raw._terms("alpha,beta;gamma") == ("alpha", "beta", "gamma")


def test_count_and_metadata_are_bounded_and_skips_are_not_silent(database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(30):
            put(db, f"m-{index:03}")
    result = read(database, limit=20)
    assert len(result.items) <= 21
    assert "count_budget" in result.truncated_by
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE evidence SET conversation_id=? WHERE evidence_id='m-000'", ("x" * 10000,)
        )
    result = read(database)
    assert all(len(item.conversation_id) <= 512 for item in result.items)
    assert "metadata_budget" in result.truncated_by


def test_empty_and_index_or_database_failure_are_distinct(database, tmp_path):
    assert read(database).status == "empty"
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP TABLE evidence_search")
    for query in ("needle", "短"):
        result = read(database, query)
        assert result.status == "unavailable" and result.reason == "read_unavailable"
    missing = tmp_path / "not-created.sqlite3"
    assert read(missing).status == "unavailable"
    assert not missing.exists()


def test_expired_deadline_raises_before_open(database, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("opened after expiry"))
    with pytest.raises(TimeoutError):
        read_raw_context(
            database,
            ContextSearchRequest(query="needle"),
            source_ids=None,
            deadline=time.monotonic() - 1,
        )


def test_lock_wait_obeys_deadline_and_releases_connection(database):
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            read_raw_context(
                database,
                ContextSearchRequest(query="needle"),
                source_ids=None,
                deadline=start + 0.06,
            )
        assert time.monotonic() - start < 0.6
        writer.rollback()
    assert read(database).status == "empty"


@pytest.mark.parametrize(
    "change", ["revision", "revoked", "delete", "user", "source", "conversation"]
)
def test_final_revalidation_drops_changed_items_without_replacement(database, change):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a", "needle original")
        put(db, "b", "needle retained")
    initial = read(database).items
    assert len(initial) == 2 and revalidate(database, initial) == initial
    with closing(sqlite3.connect(database)) as db, db:
        if change == "revision":
            db.execute(
                "UPDATE evidence SET revision=2,verbatim='replacement' WHERE evidence_id='a'"
            )
        elif change == "revoked":
            revoke(db, "cursor")
        elif change == "delete":
            db.execute("DELETE FROM evidence WHERE evidence_id='a'")
        else:
            column = {"user": "user_id", "source": "source_id", "conversation": "conversation_id"}[
                change
            ]
            db.execute(f"UPDATE evidence SET {column}='changed' WHERE evidence_id='a'")
    final = revalidate(database, initial)
    assert [item.anchor_evidence_id for item in final] == ([] if change == "revoked" else ["b"])
    assert all(item in initial for item in final)


def test_generic_finalizer_rechecks_each_result_against_current_positive_grant(database):
    with closing(sqlite3.connect(database)) as db, db:
        for source in ("cursor", "workbuddy", "missing", "revoked"):
            put(db, source, f"needle {source}", source=source)
        put(db, "foreign", "needle foreign", source="foreign", user="other")
        db.executemany(
            "INSERT INTO source_grants "
            "(user_id,source_id,root_path,requested_path,root_identity,candidate_kind,"
            "granted_at,revision) "
            "VALUES (?,?,'/synthetic','/synthetic','synthetic','default','2026-09-19',1)",
            (("local", "cursor"), ("local", "workbuddy"), ("other", "foreign")),
        )
        revoke(db, "revoked")
    initial = read(database).items
    assert {item.source_id for item in initial} == {"cursor", "workbuddy", "missing"}

    def positive():
        return revalidate_raw_context(
            database,
            initial,
            source_ids=frozenset({"cursor", "workbuddy", "missing", "foreign", "revoked"}),
            deadline=time.monotonic() + 5,
            require_active_grant=True,
        )

    assert {item.source_id for item in positive()} == {"cursor", "workbuddy"}
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("DELETE FROM source_grants WHERE user_id='local' AND source_id='cursor'")
    assert {item.source_id for item in positive()} == {"workbuddy"}
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE source_grants SET revoked_at='2026-09-20' "
            "WHERE user_id='local' AND source_id='workbuddy'"
        )
    assert positive() == ()
    # Trusted internal reads retain their existing missing-grant semantics.
    assert {item.source_id for item in revalidate(database, initial)} == {"cursor", "missing"}


def test_neighbor_revision_change_drops_group_and_empty_authority_reads_nothing(
    database, monkeypatch
):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a", "neighbor", conversation="thread")
        put(db, "b", "needle", conversation="thread")
    initial = read(database).items
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE evidence SET revision=2 WHERE evidence_id='a'")
    assert revalidate(database, initial) == ()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("empty authority read"))
    assert revalidate(database, initial, frozenset()) == ()


def test_queries_are_readonly_bounded_and_connections_closed(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "one")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    connect = sqlite3.connect
    connections, sql = [], []

    def observed(path, **kwargs):
        assert path.endswith("?mode=ro") and kwargs["uri"] is True
        assert 0 < kwargs["timeout"] <= 5
        db = connect(path, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE evidence SET revision=2 WHERE evidence_id='one'")
        db.rollback()
        db.set_trace_callback(sql.append)
        connections.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", observed)
    initial = read(database).items
    assert revalidate(database, initial) == initial
    assert any("substr(e.verbatim" in statement for statement in sql)
    assert not any(
        "SELECT e.*" in statement or "SELECT * FROM evidence " in statement for statement in sql
    )
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    # Pooled: handed back rather than held, and closed with the pool.
    assert main_database(database).stats().readers_in_use == 0
    close_main_databases()
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")


def test_progress_handler_interrupts_executing_scan_and_closes(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(2000):
            put(db, str(index), "unrelated")
    clock = [1000.0]
    callbacks, opened = [], []
    connect = sqlite3.connect
    monkeypatch.setattr(context_raw, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class InterruptedConnection(sqlite3.Connection):
        armed = False

        def execute(self, sql, parameters=()):
            if "FROM evidence e WHERE" in sql:
                self.armed = True
            return super().execute(sql, parameters)

        def set_progress_handler(self, callback, steps):
            if callback is None:
                return super().set_progress_handler(None, 0)

            def expire():
                if self.armed:
                    clock[0] = 1002.0
                    callbacks.append(True)
                return callback()

            return super().set_progress_handler(expire, steps)

    def monitored(*args, **kwargs):
        db = connect(*args, **kwargs, factory=InterruptedConnection)
        opened.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", monitored)
    with pytest.raises(TimeoutError):
        read_raw_context(
            database, ContextSearchRequest(query="zz"), source_ids=None, deadline=1001.0
        )
    assert callbacks
    # Pooled: handed back rather than held, and closed with the pool.
    assert main_database(database).stats().readers_in_use == 0
    close_main_databases()
    for db in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")


@pytest.mark.parametrize(
    "column, value",
    [
        ("evidence_id", "x" * 513),
        ("source_id", "x" * 129),
        ("role", "x" * 33),
        ("written_by", "x" * 33),
        ("created_at", "not-a-time"),
        ("revision", 0),
    ],
)
def test_invalid_metadata_is_not_fabricated_or_aliased(database, column, value):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "invalid")
        db.execute(f"UPDATE evidence SET {column}=? WHERE evidence_id='invalid'", (value,))
    result = read(database)
    assert result.status == "degraded"
    assert result.items == ()
    assert "metadata_budget" in result.truncated_by


def test_invalid_neighbor_keeps_real_anchor_and_marks_partial_group(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a", "neighbor", conversation="thread", author="x" * 33)
        put(db, "b", "needle", conversation="thread")
    result = read(database)
    assert result.status == "degraded"
    assert [message.evidence_id for message in result.items[0].messages] == ["b"]
    assert result.items[0].anchor_evidence_id == "b" and result.items[0].truncated


@pytest.mark.parametrize(
    "column, value",
    [
        ("role", "assistant"),
        ("written_by", "agent"),
        ("created_at", "2026-09-20T00:00:00+00:00"),
    ],
)
def test_revalidation_drops_changed_authorship_or_timestamp(database, column, value):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a")
    initial = read(database).items
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(f"UPDATE evidence SET {column}=? WHERE evidence_id='a'", (value,))
    assert revalidate(database, initial) == ()


def test_revalidation_failure_and_timeout_do_not_return_stale_items(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a")
    initial = read(database).items
    with pytest.raises(TimeoutError):
        revalidate_raw_context(database, initial, source_ids=None, deadline=time.monotonic() - 1)
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP TABLE source_grants")
    with pytest.raises(sqlite3.Error):
        revalidate(database, initial)


def test_revalidation_uses_single_read_snapshot(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        put(db, "a")
        put(db, "b")
    initial = read(database).items
    close_main_databases()
    connect = sqlite3.connect
    changed, transactions = [], []

    class RacingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if "FROM evidence e" in sql:
                transactions.append(self.in_transaction)
                if not changed:
                    with closing(connect(database)) as writer, writer:
                        writer.execute("UPDATE evidence SET revision=2 WHERE evidence_id='b'")
                    changed.append(True)
            return cursor

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **k: connect(*a, **k, factory=RacingConnection)
    )
    assert revalidate(database, initial) == initial
    assert changed and transactions == [True, True]
    close_main_databases()
    monkeypatch.setattr(sqlite3, "connect", connect)
    assert [item.anchor_evidence_id for item in revalidate(database, initial)] == ["a"]


def test_lexical_sampling_includes_tail_even_with_more_than_sixteen_unique_terms(database):
    query = " ".join(f"term{index:02}" for index in range(40)) + " tailword"
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "tail", "tailword")
    assert [item.anchor_evidence_id for item in read(database, query).items] == ["tail"]


def test_no_network_keychain_or_store_initialization(database, monkeypatch):
    import socket

    import keyring

    from one_memory.imports.storage import EvidenceStore

    with closing(sqlite3.connect(database)) as db, db:
        put(db, "one")

    def forbidden(*args, **kwargs):
        pytest.fail("raw adapter entered a network, secret or initializing store path")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(keyring, "get_password", forbidden)
    monkeypatch.setattr(EvidenceStore, "__init__", forbidden)
    initial = read(database).items
    assert initial and revalidate(database, initial) == initial


def test_revalidation_narrower_authority_and_missing_anchor_fail_closed(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "one")
    initial = read(database).items
    assert revalidate(database, initial, frozenset({"workbuddy"})) == ()
    malformed = initial[0].model_copy(update={"anchor_evidence_id": "not-in-messages"})
    assert revalidate(database, (malformed,)) == ()


@pytest.mark.parametrize(
    "query",
    [
        "alpha,beta;gamma",
        "权限，检索？",
        " ".join(f"term{index:02}" for index in range(16)),
        "alpha " * 40,
    ],
)
def test_unsampled_lexical_empty_is_complete(database, query):
    result = read(database, query)
    assert result.status == "empty"
    assert result.items == ()
    assert result.truncated_by == ()


@pytest.mark.parametrize("hit", ["omitted", "sampled", "none"])
def test_actual_term_sampling_discloses_incomplete_search_even_without_hits(database, hit):
    full_terms = [f"term{index:02}" for index in range(17)]
    query = " ".join(full_terms)
    sampled = context_raw._terms(query)
    assert sampled == tuple(full_terms[index * 16 // 15] for index in range(16))
    omitted = next(term for term in full_terms if term not in sampled)
    body = {"omitted": omitted, "sampled": sampled[0], "none": "unrelated"}[hit]
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "one", body)
    if hit == "omitted":
        assert read(database, omitted).status == "ok"  # The omitted match really exists.
    result = read(database, query)
    assert result.status == "degraded"
    assert result.truncated_by == ("metadata_budget",)
    assert bool(result.items) is (hit == "sampled")


def test_candidate_probe_is_bounded_and_saturation_is_disclosed(database, monkeypatch):
    # The FTS probe is an OR over trigrams, so a large corpus can legally match
    # nearly every row. Materializing all of them to sort is what pushed 430k
    # reads past the budget; the fix bounds the probe, so a saturated probe has
    # to say so rather than presenting itself as whole-corpus coverage.
    monkeypatch.setattr(context_raw, "_candidate_probe_cap", lambda: 4)
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(12):
            put(
                db,
                f"row-{index:02}",
                f"alpha beta gamma {index}",
                created=f"2026-09-{index + 1:02d}T00:00:00+00:00",
            )
        db.commit()
    saturated = read(database, "alpha", limit=2)
    assert saturated.status == "degraded"
    assert "metadata_budget" in saturated.truncated_by

    monkeypatch.setattr(context_raw, "_candidate_probe_cap", lambda: 4096)
    complete = read(database, "alpha", limit=2)
    assert complete.status == "ok"
    # count_budget is the returned-item limit, not probe saturation.
    assert complete.truncated_by == ("count_budget",)


def test_saturated_probe_on_a_large_corpus_is_disclosed_not_silent(database, monkeypatch):
    """A corpus larger than the probe must not read as whole-corpus coverage.

    This is the shape that made 430k reads exceed the budget: the trigram OR
    matches nearly every row, so an unbounded probe materializes and sorts the
    whole table. With a bounded probe the query has to stay fast *and* admit
    that rows beyond the probe were never ranked.
    """
    monkeypatch.setattr(context_raw, "_candidate_probe_cap", lambda: 64)
    rows = 600
    with closing(sqlite3.connect(database)) as db:
        db.executescript("BEGIN")
        for index in range(rows):
            put(
                db,
                f"row-{index:04}",
                f"alpha shared filler {index}",
                created=f"2026-09-{index % 28 + 1:02d}T00:00:00+00:00",
            )
        db.commit()
    result = read(database, "alpha", limit=3)
    assert result.status == "degraded"
    assert "metadata_budget" in result.truncated_by
    assert result.items  # Still answers, rather than failing closed.


def test_coverage_ranking_survives_a_bounded_probe(database, monkeypatch):
    # A bounded probe must not degrade relevance: the row carrying the whole
    # question still outranks rows that match only one of its terms.
    # A realistically sized probe, not one so small it cannot hold the answer.
    monkeypatch.setattr(context_raw, "_candidate_probe_cap", lambda: 9)
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(8):
            put(
                db,
                f"partial-{index:02}",
                f"alpha filler {index}",
                created=f"2026-09-{index + 1:02d}T00:00:00+00:00",
            )
        put(db, "whole", "alpha beta gamma delta whole phrase", created="2026-09-01T00:00:00+00:00")
        db.commit()
    result = read(database, "alpha beta gamma delta", limit=3)
    assert result.items[0].anchor_evidence_id == "whole"
