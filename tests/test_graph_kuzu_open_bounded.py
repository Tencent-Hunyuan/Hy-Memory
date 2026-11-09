"""Kuzu graph open is bounded, checkpointed, and kept off the event loop.

Field case (2.0.15, MemHub ultra): opening a 33 MB graph pushed RSS to 13 GB and
held the GIL until the process was killed. What these tests pin down:

- Every `kuzu.Database(...)` gets an explicit `buffer_pool_size` (Kuzu's default is
  ~80% of RAM) and `max_num_threads`, configurable via `GraphStoreConfig` / env.
- `close()` and `build_memory_graph` CHECKPOINT, so no WAL is left for the next open.
- Schema checks and DDL run in the open thread, not on the loop thread.
- Stores on the same path in one process share one `kuzu.Database`. Kuzu's file
  lock is per process, so separate `Database` objects on one file overwrite each
  other's checkpoints: writes are lost and the primary-key index is corrupted.
"""

import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest

kuzu = pytest.importorskip("kuzu")

from hy_memory.config import GraphStoreConfig, MemoryConfig
from hy_memory.data import graph_store_kuzu as gsk
from hy_memory.data.graph_store_kuzu import KuzuGraphStore, kuzu_database_kwargs
from hy_memory.models.memory import MemoryLayer, MemoryNode

_DIMS = 8


def _cfg(path: str) -> MemoryConfig:
    cfg = MemoryConfig()
    cfg.graph_store.provider = "kuzu"
    cfg.graph_store.db_path = path
    cfg.vector_store.embedding_dims = _DIMS
    return cfg


def _node(i: int, **kw) -> MemoryNode:
    return MemoryNode(
        user_id="u", agent_id="a", session_id=f"s{i % 3}",
        layer=kw.pop("layer", MemoryLayer.L2_FACT),
        content=f"memory {i} about topic {i % 5}",
        tags=[f"tag{i % 4}", f"tag{(i * 3) % 7}"], **kw,
    )


def _files(path: str):
    size = os.path.getsize(path) if os.path.exists(path) else 0
    wal = path + ".wal"
    return size, (os.path.getsize(wal) if os.path.exists(wal) else 0)


class _Recorder:
    """Patch kuzu.Database / Connection to record open kwargs and execute threads."""

    def __init__(self, monkeypatch):
        self.open_kwargs = []
        self.executes = []  # (thread name, query)
        rec = self
        real_db, real_conn = kuzu.Database, kuzu.Connection

        class Database(real_db):
            def __init__(self, path, **kwargs):
                rec.open_kwargs.append(kwargs)
                super().__init__(path, **kwargs)

        class Connection(real_conn):
            def execute(self, query, *args, **kwargs):
                rec.executes.append((threading.current_thread().name, str(query)))
                return super().execute(query, *args, **kwargs)

        monkeypatch.setattr(kuzu, "Database", Database)
        monkeypatch.setattr(kuzu, "Connection", Connection)


class TestOpenKwargs:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("MEMORY_GRAPH_BUFFER_POOL_MB", raising=False)
        monkeypatch.delenv("MEMORY_GRAPH_MAX_THREADS", raising=False)
        cfg = GraphStoreConfig()
        assert cfg.buffer_pool_mb == 512
        assert cfg.max_threads == 4
        assert kuzu_database_kwargs(cfg) == {
            "buffer_pool_size": 512 << 20, "max_num_threads": 4,
        }
        # No config at all (reembed's direct opens) reads the same env/defaults.
        assert kuzu_database_kwargs() == kuzu_database_kwargs(cfg)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MEMORY_GRAPH_BUFFER_POOL_MB", "128")
        monkeypatch.setenv("MEMORY_GRAPH_MAX_THREADS", "1")
        assert kuzu_database_kwargs(GraphStoreConfig()) == {
            "buffer_pool_size": 128 << 20, "max_num_threads": 1,
        }
        assert kuzu_database_kwargs()["buffer_pool_size"] == 128 << 20

    def test_config_override_and_limits(self):
        assert kuzu_database_kwargs(GraphStoreConfig(buffer_pool_mb=1024))[
            "buffer_pool_size"] == 1024 << 20
        # Below 64 MB Kuzu cannot even replay a modest WAL; clamp up.
        assert kuzu_database_kwargs(GraphStoreConfig(buffer_pool_mb=8))[
            "buffer_pool_size"] == 64 << 20
        # 0 = leave Kuzu's own default.
        assert kuzu_database_kwargs(
            GraphStoreConfig(buffer_pool_mb=0, max_threads=0)
        ) == {}

    def test_from_dict(self):
        cfg = MemoryConfig.from_dict({"graph_store": {"buffer_pool_mb": 256}})
        assert cfg.graph_store.buffer_pool_mb == 256


class TestInitialize:
    @pytest.mark.asyncio
    async def test_buffer_pool_passed_and_ddl_off_loop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEMORY_GRAPH_BUFFER_POOL_MB", "96")
        rec = _Recorder(monkeypatch)
        gs = KuzuGraphStore(_cfg(str(tmp_path / "g")))
        await gs.initialize()
        try:
            assert gs._available
            assert rec.open_kwargs == [{"buffer_pool_size": 96 << 20, "max_num_threads": 4}]
            loop_thread = threading.current_thread().name
            init_threads = {name for name, _ in rec.executes}
            assert any("CREATE NODE TABLE" in q for _, q in rec.executes)
            assert any("show_tables" in q for _, q in rec.executes)
            assert loop_thread not in init_threads
            assert all(name.startswith("kuzu-open") for name in init_threads)
        finally:
            await gs.close()

    @pytest.mark.asyncio
    async def test_reopen_existing_schema_runs_no_ddl(self, tmp_path, monkeypatch):
        path = str(tmp_path / "g")
        gs = KuzuGraphStore(_cfg(path))
        await gs.initialize()
        await gs.close()
        rec = _Recorder(monkeypatch)
        gs = KuzuGraphStore(_cfg(path))
        await gs.initialize()
        try:
            assert gs._available
            assert not any("CREATE NODE TABLE" in q or "CREATE REL TABLE" in q
                           for _, q in rec.executes)
        finally:
            await gs.close()

    @pytest.mark.asyncio
    async def test_open_timeout_degrades_and_releases_path(self, tmp_path, monkeypatch):
        gate = threading.Event()
        real_db = kuzu.Database

        class SlowDatabase(real_db):
            def __init__(self, path, **kwargs):
                gate.wait(5)
                super().__init__(path, **kwargs)

        monkeypatch.setattr(kuzu, "Database", SlowDatabase)
        monkeypatch.setattr(gsk, "_KUZU_OPEN_TIMEOUT_S", 0.2)
        path = str(tmp_path / "g")
        gs = KuzuGraphStore(_cfg(path))
        await gs.initialize()
        assert not gs._available
        gate.set()
        real_path = os.path.realpath(path)
        for _ in range(100):
            if real_path not in gsk._SHARED_DBS:
                break
            await asyncio.sleep(0.05)
        # The late open was released, not left holding the file.
        assert real_path not in gsk._SHARED_DBS
        assert gs._db_ref is None
        monkeypatch.setattr(kuzu, "Database", real_db)
        again = KuzuGraphStore(_cfg(path))
        await again.initialize()
        try:
            assert again._available
        finally:
            await again.close()


class TestCheckpoint:
    @pytest.mark.asyncio
    async def test_close_checkpoints(self, tmp_path, monkeypatch):
        rec = _Recorder(monkeypatch)
        path = str(tmp_path / "g")
        gs = KuzuGraphStore(_cfg(path))
        await gs.initialize()
        await gs.upsert_memory_node(_node(0))
        rec.executes.clear()
        await gs.close()
        queries = [q for _, q in rec.executes]
        assert queries and queries[-1].strip() == "CHECKPOINT;"
        assert _files(path)[1] == 0
        assert path not in [os.path.realpath(p) for p in gsk.open_graph_paths()]

    @pytest.mark.asyncio
    async def test_checkpoint_folds_wal_while_open(self, tmp_path):
        path = str(tmp_path / "g")
        gs = KuzuGraphStore(_cfg(path))
        await gs.initialize()
        try:
            for i in range(20):
                await gs.upsert_memory_node(_node(i))
            assert _files(path)[1] > 0
            assert await gs.checkpoint() is True
            assert _files(path)[1] == 0
        finally:
            await gs.close()


class _FakeVectorStore:
    def __init__(self, nodes):
        self.nodes = nodes

    async def list_by_user(self, **_kw):
        return list(self.nodes)


@pytest.mark.asyncio
async def test_build_memory_graph_checkpoints_and_stays_small(tmp_path, monkeypatch):
    """MemHub's ultra write pattern on real Kuzu: backfill, per-memory writes,
    evolve-style DERIVED_FROM / TAGGED_WITH. After the backfill the WAL is already
    folded in; after close there is no WAL and the file stays small.

    Bound: Kuzu gives every table its first storage chunk on its first row
    (~1.1 MB per node table, ~2.1 MB per rel table, PK hash index included), so
    with all tables in use a healthy graph of this size is ~10-30 MB, mostly zero
    pages. 64 MB leaves room for that and still catches runaway growth.
    """
    from hy_memory.client import HyMemoryClient
    from hy_memory.pipelines._graph.projector import project_memories_to_graph
    from hy_memory.pipelines._retrieval import ner

    # NER only adds Entity rows; keep it out of the timing.
    monkeypatch.setattr(ner, "extract_entities", lambda *_a, **_k: [])
    path = str(tmp_path / "graph")
    cfg = _cfg(path)
    gs = KuzuGraphStore(cfg)
    await gs.initialize()
    nodes = [_node(i, embedding=[float((i + k) % 5) for k in range(_DIMS)])
             for i in range(30)]
    fake_self = SimpleNamespace(
        _graph_store=gs,
        _config=cfg,
        _registry=SimpleNamespace(
            _get_shared_vector_store=lambda: _FakeVectorStore(nodes),
            _get_shared_embed_service=lambda: None,
        ),
    )
    build = HyMemoryClient.async_build_memory_graph.__wrapped__
    try:
        result = await build(fake_self, "u")
        assert result["success"] and result["nodes"] == 30
        assert _files(path)[1] == 0, "backfill left its WAL for the next open"

        for i in range(30, 40):
            await project_memories_to_graph(
                graph_store=gs, embed_service=None, nodes=[_node(i)],
            )
        for i in range(5):
            schema = _node(1000 + i)
            await gs.upsert_memory_node(schema)
            for k in range(3):
                ref = nodes[(i + k) % len(nodes)].node_id
                await gs.ensure_vdbref(ref, "l2_fact")
                await gs.add_derived_from(schema.node_id, ref)
            await gs.add_topic_tag(schema.node_id, f"tag{i % 4}", "u::a")
    finally:
        await gs.close()

    size, wal = _files(path)
    assert wal == 0
    assert size < 64 << 20, f"graph file {size} bytes"

    started = time.perf_counter()
    db = kuzu.Database(path, read_only=True, **kuzu_database_kwargs())
    conn = kuzu.Connection(db)
    count = conn.execute("MATCH (m:Memory) RETURN count(m);").get_next()[0]
    conn.close()
    db.close()
    assert count == 45
    assert time.perf_counter() - started < 5


@pytest.mark.asyncio
async def test_stores_on_same_path_share_one_database(tmp_path):
    """MemHub opens one client (one graph store) per lease. Two separate writable
    `kuzu.Database` objects on one path lose writes (measured before the fix: 140
    written, 100 left after reopen) and can corrupt the primary-key index. Stores
    on one path must share a single Database, and every write must survive."""
    path = str(tmp_path / "g")
    first = KuzuGraphStore(_cfg(path))
    await first.initialize()
    second = KuzuGraphStore(_cfg(path))
    await second.initialize()
    try:
        assert first._available and second._available
        assert first._db is second._db
        for i in range(20):
            await first.upsert_memory_node(_node(i))
            await second.upsert_memory_node(_node(100 + i))
        await second.checkpoint()
        for i in range(20, 30):
            await first.upsert_memory_node(_node(i))
        await first.checkpoint()
        await second.close()
        # The first store keeps working after the second one closed.
        await first.upsert_memory_node(_node(200))
        rows = await first._run("MATCH (m:Memory) RETURN count(m);")
        assert rows[0][0] == 51
    finally:
        await second.close()
        await first.close()
    assert os.path.realpath(path) not in gsk._SHARED_DBS

    db = kuzu.Database(path, read_only=True, **kuzu_database_kwargs())
    conn = kuzu.Connection(db)
    try:
        assert conn.execute("MATCH (m:Memory) RETURN count(m);").get_next()[0] == 51
    finally:
        conn.close()
        db.close()


@pytest.mark.asyncio
async def test_symlinked_alias_shares_the_database(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    a = KuzuGraphStore(_cfg(str(real / "g")))
    await a.initialize()
    b = KuzuGraphStore(_cfg(str(alias / "g")))
    await b.initialize()
    try:
        assert a._available and b._available
        assert a._db is b._db
    finally:
        await b.close()
        await a.close()
