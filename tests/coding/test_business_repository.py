"""The same business-storage contract runs against SQLite and real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from hy_memory.config import BusinessStoreConfig, MemoryConfig
from hy_memory.coding.chain_repository import create_chain_repository
from hy_memory.coding.chain_store import HyCodeMem
from hy_memory.coding.repository import create_memory_repository, memory_repository_exists
from hy_memory.coding.types import CodingMemory, WorkingFact



def memory(key, **kwargs):
    return CodingMemory(
        experience_id=key, user_id="u1", task="发布 ? 100% 'quoted'", solution="方案",
        search_keys=["搜索", "?"], confidence=0.987654321,
        created_at=datetime(2041, 1, 1), updated_at=datetime(2041, 1, 2),
        memory_at=datetime(2041, 1, 1), **kwargs,
    )


def test_experiences_scope_sources_order_and_roundtrip(repositories):
    _, create, _ = repositories
    repo = create()
    repo.upsert_experience(memory("one", workspace_id="repo", branch="main", source="cli"))
    repo.upsert_experience(memory("two", workspace_id="repo", branch=None, source="web"))
    repo.upsert_experience(memory("three", workspace_id="elsewhere", branch="main"))
    got = repo.get_experience("one", "u1")
    assert got.to_dict() == memory("one", workspace_id="repo", branch="main", source="cli").to_dict()
    assert repo.get_experience("one", "another-user") is None
    assert [m.experience_id for m in repo.get_experiences(["two", "missing", "one"], "u1", None, None)] == ["two", "one"]
    assert repo.get_experiences([], "u1", None, None) == []
    assert [m.experience_id for m in repo.list_by_workspace("u1", "repo", "main", 1, 1)] == ["two"]
    assert [m.experience_id for m in repo.list_by_workspace("u1", "repo", "main", 10, 0, ["cli", "web"], ["web"])] == ["one"]
    assert repo.stats("u1", "repo", "main")["count"] == 2
    assert repo.stats("u1", None, None)["count"] == 3
    assert repo.scoped_experience_ids("u1", None, None, "repo", "main") == ({"one", "two"}, 3)
    assert repo.scoped_experience_ids("u1", ["other"], None, None, None) == (set(), 3)
    assert repo.scoped_experience_ids("u1", None, ["other"], None, None) == (set(), 3)
    assert len(repo.list_user_tasks("u1", 2)) == 2
    got.solution = "changed"
    repo.upsert_experience(got)
    assert repo.get_experience("one", "u1").solution == "changed"
    repo.delete_experience("one", "another-user")
    assert repo.get_experience("one", "u1") is not None
    repo.delete_experience("one", "u1")
    assert repo.get_experience("one", "u1") is None


def test_facts_links_supersede_and_ancestors(repositories):
    _, create, _ = repositories
    repo = create()
    repo.upsert_experience(memory("one"))
    for key in ("old", "new"):
        repo.upsert_fact(WorkingFact(
            fact_id=key, user_id="u1", statement="事实 ? %", subject="主体",
            details="详情", workspace_id="repo", source="cli",
            memory_at=datetime(2041, 1, 1), created_at=datetime(2041, 1, 1),
        ))
    repo.set_fact_derived_experiences("old", ["one"])
    repo.set_experience_source_facts("one", ["old"])
    assert repo.get_facts(["old"], "u1")[0].derived_experience_ids == ["one"]
    assert repo.get_experience("one", "u1").source_fact_ids == ["old"]
    assert repo.get_facts(["old"], "another-user") == []
    assert repo.get_facts([], "u1") == []
    assert repo.count_active_facts("u1", None, None, "repo", None) == (2, 2)
    assert repo.count_active_facts("u1", ["other"], None, None, None) == (0, 2)
    assert repo.supersede_fact("old", "new") is True
    assert repo.supersede_fact("old", "new") is False
    assert [f.fact_id for f in repo.get_fact_ancestors("new", "u1", 8)] == ["old"]
    assert repo.get_fact_ancestors("new", "another-user", 8) == []
    assert repo.stats("u1", "repo", None, ["cli"], None)["facts"] == 1
    fact = repo.get_facts(["new"], "u1")[0]
    fact.statement = "更新"
    repo.upsert_fact(fact)
    assert repo.get_facts(["new"], "u1")[0].statement == "更新"
    repo.delete_fact("new", "another-user")
    assert repo.get_facts(["new"], "u1")
    repo.delete_fact("new", "u1")
    assert repo.get_facts(["new"], "u1") == []


def test_variant_isolation_shared_trace_idempotency_and_retention(repositories):
    _, create, _ = repositories
    working, coding = create(), create("coding")
    working.upsert_experience(memory("same"))
    coding.upsert_experience(memory("same", source="coding"))
    assert working.get_experience("same", "u1").source == "default"
    assert coding.get_experience("same", "u1").source == "coding"
    payload = [{"role": "user", "content": "共享 ? 100%"}]
    for key in range(105):
        working.append_trace(str(key), "u1", "a1", None, None, None, "cli", payload)
    coding.append_trace("104", "u1", "a1", None, None, None, "cli", [{"content": "duplicate"}])
    assert coding.recent_trace_payloads("u1", "a1", "cli", 200) == [payload] * 100
    assert coding.recent_trace_payloads("u1", "a2", "cli", 200) == []
    assert coding.recent_trace_payloads("u2", "a1", "cli", 200) == []
    assert coding.recent_trace_payloads("u1", "a1", "web", 200) == []
    working.close()
    working.close()
    reopened = create()
    assert reopened.get_experience("same", "u1") is not None


def test_chain_transactions_rollback_owner_isolation_and_render(repositories, tmp_path):
    cfg, _, opened = repositories
    def chain(owner):
        repository = create_chain_repository(cfg, str(tmp_path / "chain"), owner, 20)
        opened.append(repository)
        return HyCodeMem(str(tmp_path / "chain"), owner=owner, repository=repository)
    first, other = chain("u1|repo"), chain("u2|repo")
    with pytest.raises(RuntimeError, match="abort"):
        with first._repository.transaction():
            tid = first._upsert_thread("feat/test", "a.py", "f", 1, 3)
            first._add_round(tid, "failed", "+return 1")
            raise RuntimeError("abort")
    assert first.stats()["threads"] == 0
    for said, value in (("第一版", 1), ("第二版", 2)):
        text = f"user: {said}\nassistant:\ndiff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@ def f\n-return 0\n+return {value}\n"
        assert first.observe(text, {"branch": "feat/test"}) == 1
    assert first.stats()["rounds"] == 2
    assert other.stats()["threads"] == 0
    tid = first._repository.threads_by_branch("feat/test")[0][0]
    assert other._repository.rounds_for_thread(tid) == []
    assert other._repository.round_texts(tid) == []
    with pytest.raises(ValueError, match="this owner"):
        with other._repository.transaction():
            other._repository.add_round(tid, "foreign write", "+return 3")
    facts = [{"statement": "feat/test f", "files": ["a.py"], "branch": "feat/test"}]
    block, used = first.decorate(facts)
    assert "第一版" in block and "第二版" in block and used == [0]
    assert other.decorate(facts) == ("", [])


def test_postgres_schema_probe_concurrent_initialization_and_no_sqlite(repositories, tmp_path):
    cfg, create, _ = repositories
    if cfg.business_store.backend != "postgres":
        pytest.skip("PostgreSQL initialization contract")
    assert not memory_repository_exists(cfg, str(tmp_path / "missing.db"), "working")
    # Construct in the main thread so the fixture's resource list is deterministic.
    repos = [create(initialize=False) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda repo: repo.initialize(), repos))
    assert memory_repository_exists(cfg, "unused.db", "working")
    assert not memory_repository_exists(cfg, "unused.db", "coding")
    repos[0].upsert_experience(memory("one"))
    assert repos[1].get_experience("one", "u1") is not None
    assert not list(tmp_path.rglob("*.db"))


def test_postgres_concurrent_chain_append(repositories, tmp_path):
    cfg, _, opened = repositories
    if cfg.business_store.backend != "postgres":
        pytest.skip("PostgreSQL multi-instance chain writes")
    repos = [create_chain_repository(cfg, str(tmp_path / "chain"), "owner", 20) for _ in range(4)]
    opened.extend(repos)
    def append(pair):
        index, repo = pair
        with repo.transaction():
            tid = repo.upsert_thread("feat/test", "a.py", "f", 1, 3)
            repo.add_round(tid, f"round {index}", f"+return {index}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, enumerate(repos)))
    assert repos[0].stats()["threads"] == 1
    assert repos[0].stats()["rounds"] == 4
    tid = repos[0].threads_by_branch("feat/test")[0][0]
    assert [row[0] for row in repos[0].rounds_for_thread(tid)] == [1, 2, 3, 4]


def test_business_config_validation_and_credentials_not_reported(monkeypatch):
    monkeypatch.delenv("MEMORY_BUSINESS_STORE", raising=False)
    assert BusinessStoreConfig().backend == "sqlite"
    dsn = "postgresql://user:secret@localhost/memory"
    cfg = MemoryConfig.from_dict({"business_store": {"backend": "postgres", "postgres_dsn": dsn}})
    assert cfg.business_store.postgres_dsn == dsn
    assert "secret" not in repr(cfg)
    assert "secret" not in str(cfg.to_dict())
    with pytest.raises(ValueError, match="sqlite / postgres"):
        MemoryConfig.from_dict({"business_store": {"backend": "unknown"}})
    with pytest.raises(ValueError, match="identifier"):
        BusinessStoreConfig(postgres_schema='public; DROP TABLE anything')
    with pytest.raises(ValueError, match="positive integer"):
        BusinessStoreConfig(connect_timeout=0)


def test_postgres_missing_dsn_never_falls_back_to_sqlite(tmp_path):
    cfg = MemoryConfig()
    cfg.business_store = BusinessStoreConfig(backend="postgres", postgres_dsn="")
    repo = create_memory_repository(cfg, str(tmp_path / "memory.db"), str(tmp_path / "trace.db"), "working")
    with pytest.raises(ValueError, match="MEMORY_POSTGRES_DSN"):
        repo.initialize()
    assert repo._conn is None and repo._trace_conn is None
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_repository_with_existing_vector_search(repositories, coding_config, mock_embed):
    from hy_memory.coding.store import CodingMemoryStore

    cfg, _, _ = repositories
    coding_config.business_store = cfg.business_store
    stores = [CodingMemoryStore(coding_config, mock_embed, variant=variant)
              for variant in ("working", "coding")]
    try:
        for store in stores:
            await store.initialize()
        await stores[0].insert(memory("one", workspace_id="repo"))
        await stores[1].insert(memory("one", workspace_id="other"))
        embedding = (await mock_embed.embed_batch(["发布 ? 100% 'quoted'"]))[0]
        hits = await stores[0].search_by_query_embedding(embedding, "u1", top=5, workspace_id="repo")
        assert [hit["experience_id"] for hit in hits] == ["one"]
        assert await stores[1].search_by_query_embedding(embedding, "u1", top=5, workspace_id="repo") == []
        await stores[0].insert_fact(WorkingFact(fact_id="fact", user_id="u1", statement="事实", workspace_id="repo"))
        assert (await stores[0].get_fact("fact", "u1")).statement == "事实"
        assert await stores[1].get_fact("fact", "u1") is None
    finally:
        for store in stores:
            await store.close()


@pytest.mark.asyncio
async def test_custom_repository_injection_and_failed_vector_initialization(coding_config, mock_embed, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from hy_memory.coding.store import CodingMemoryStore

    repository = Mock()
    vector = Mock(initialize=AsyncMock(side_effect=RuntimeError("vector unavailable")), close=AsyncMock())
    monkeypatch.setattr("hy_memory.coding.store.create_vector_store", lambda cfg: vector)
    store = CodingMemoryStore(coding_config, mock_embed, repository=repository)
    with pytest.raises(RuntimeError, match="vector unavailable"):
        await store.initialize()
    repository.initialize.assert_called_once()
    repository.close.assert_called_once()
    vector.close.assert_awaited_once()
    assert store._initialized is False


def test_legacy_chain_schema_upgrades_without_losing_rounds(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "hy_code.db") as connection:
        connection.executescript("""
            CREATE TABLE thread(id INTEGER PRIMARY KEY, branch TEXT, file TEXT, symbol TEXT,
                lo INTEGER DEFAULT 0, hi INTEGER DEFAULT 0, n_rounds INTEGER DEFAULT 0,
                updated_at REAL DEFAULT 0);
            INSERT INTO thread VALUES(1,'feat/test','a.py','f',1,3,1,1);
        """)
    chain = HyCodeMem(str(tmp_path))
    try:
        assert chain.stats()["threads"] == 1
        assert chain._repository.body_for_thread(1) == ("",)
        assert chain._repository.upsert_thread("feat/test", "a.py", "f", 1, 3) == 1
    finally:
        chain.close()


def test_postgres_partial_initialization_closes_connections(repositories, monkeypatch):
    cfg, create, _ = repositories
    if cfg.business_store.backend != "postgres":
        pytest.skip("PostgreSQL connection lifecycle")
    from hy_memory.coding import repository_postgres

    original = repository_postgres.connect_postgres
    connections = []
    def connect(settings, **kwargs):
        if connections:
            raise RuntimeError("trace connection unavailable")
        connection = original(settings, **kwargs)
        connections.append(connection)
        return connection
    repo = create(initialize=False)
    with monkeypatch.context() as patch:
        patch.setattr(repository_postgres, "connect_postgres", connect)
        with pytest.raises(RuntimeError, match="trace connection unavailable"):
            repo.initialize()
    assert connections[0].closed
    assert repo._conn is None and repo._trace_conn is None


def test_postgres_rejects_future_schema_without_downgrade(repositories):
    cfg, create, _ = repositories
    if cfg.business_store.backend != "postgres":
        pytest.skip("PostgreSQL schema versions")
    repo = create()
    repo._conn.execute("UPDATE business_schema_version SET version=999 WHERE id=1")
    repo.close()
    with pytest.raises(RuntimeError, match="newer than this SDK"):
        repo.initialize()
    assert repo._conn is None and repo._trace_conn is None


def test_postgres_connection_failure_does_not_create_sqlite(tmp_path):
    pytest.importorskip("psycopg")
    cfg = MemoryConfig()
    cfg.business_store = BusinessStoreConfig(
        backend="postgres", postgres_dsn="postgresql://127.0.0.1:1/postgres", connect_timeout=1,
    )
    repo = create_memory_repository(cfg, str(tmp_path / "memory.db"), str(tmp_path / "trace.db"), "working")
    import psycopg
    with pytest.raises(psycopg.OperationalError):
        repo.initialize()
    assert not list(tmp_path.iterdir())


def test_sqlite_needs_no_postgres_driver_and_missing_extra_is_actionable(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "psycopg", None)
    cfg = MemoryConfig()
    cfg.business_store = BusinessStoreConfig(backend="sqlite")
    repo = create_memory_repository(cfg, str(tmp_path / "memory.db"), str(tmp_path / "trace.db"), "working")
    try:
        repo.initialize()
        repo.upsert_experience(memory("one"))
        assert repo.get_experience("one", "u1") is not None
    finally:
        repo.close()
    cfg.business_store = BusinessStoreConfig(backend="postgres", postgres_dsn="postgresql://localhost/test")
    repo = create_memory_repository(cfg, "unused.db", "unused-trace.db", "working")
    with pytest.raises(ImportError, match=r"hy-memory\[postgres\]"):
        repo.initialize()


def test_pinned_offline_sqlite_migration_hook(tmp_path):
    from hy_memory.coding.store import CodingMemoryStore

    store = CodingMemoryStore.__new__(CodingMemoryStore)
    store._db_path = str(tmp_path / "memory.db")
    store._conn = None
    try:
        store._init_sqlite_sync()
        assert store._conn.execute("SELECT COUNT(*) FROM coding_memory_meta").fetchone()[0] == 0
    finally:
        store._conn.close()
