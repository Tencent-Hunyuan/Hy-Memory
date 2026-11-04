"""Re-embed into a new embedding space: vector stores, scan, diff and merge.

The switch model: writes are paused, every vector is computed again with the
new model into a new space, the source is never touched. The graph half lives
in ``test_reembed_graph.py``.
"""

import hashlib
import json
import math
import random
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hy_memory import (
    EmbeddingDimensionMismatchError,
    MemoryConfig,
    ReembedCancelled,
    VectorSpace,
    diff_spaces,
    drop_vector_space,
    plan_reembed,
    reembed_text,
    reembed_vector_space,
    scan_vector_spaces,
    space_identity,
    vector_space_names,
    verify_reembed,
)
from hy_memory.config import EmbedderConfig
from hy_memory.core.embed_service import EmbedService
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.reembed import VECTOR_ROLES, parse_collection_name


class FakeEmbedder:
    """Deterministic vectors: same (model, text) -> same unit vector."""

    def __init__(self, model: str, dims: int, *, wrong_width_after: int = -1):
        self.model = model
        self.dims = dims
        self.calls = []
        self.texts = 0
        self.wrong_width_after = wrong_width_after

    def vector(self, text: str):
        seed = int(hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        values = [rng.gauss(0.0, 1.0) for _ in range(self.dims)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    async def embed_batch(self, texts, use_cache=True):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            self.texts += 1
            vector = self.vector(text)
            if 0 <= self.wrong_width_after < self.texts:
                vector = vector + [0.0]
            out.append(vector)
        return out

    async def embed(self, text, use_cache=True):
        return self.vector(text)

    async def embed_queued(self, text, use_cache=True):
        return self.vector(text)

    def get_embedding_dims(self):
        return self.dims


def _client(path):
    import chromadb
    return chromadb.PersistentClient(path=str(path))


def _space(path, base, dims, model=None):
    return VectorSpace(base_name=base, dims=dims, persist_directory=str(path), model=model)


def _custom_state():
    return {
        "memory_kind": "canonical_state",
        "state_key": "home_city",
        "state_value": "Berlin",
        "state_retrieval_text": "user lives in Berlin (home city)",
    }


async def _seed(path, base, dims, *, model="old-model", users=("alice", "bob"), per_user=7):
    """Write a realistic source space through the SDK's own stores."""
    from hy_memory.data.vector_store_chroma import ChromaVectorStore

    old = FakeEmbedder(model, dims)
    cfg = MemoryConfig()
    cfg.vector_store.provider = "chroma"
    cfg.vector_store.persist_directory = str(path)
    cfg.vector_store.collection_name = base
    cfg.vector_store.embedding_dims = dims
    store = ChromaVectorStore(cfg)
    await store.initialize()
    nodes = []
    for user in users:
        for i in range(per_user):
            layer = [MemoryLayer.L1_RAW, MemoryLayer.L2_FACT, MemoryLayer.L3_SUMMARY][i % 3]
            node = MemoryNode(
                user_id=user, agent_id="a", session_id=f"s{i % 2}",
                layer=layer, content=f"{user} fact {i}",
                status=MemoryStatus.SUPERSEDED if i == 4 else MemoryStatus.ACTIVE,
                tags=["t1", f"t{i}"],
                custom={"k": i, "nested": {"x": [1, 2]}},
            )
            if i == 4:
                node.is_latest = False
                node.superseded_by = ["later"]
            if i == 5:
                node.custom = _custom_state()
            node.embedding = old.vector(node.content)
            nodes.append(node)
    await store.upsert_batch(nodes)
    entities = await store._get_entity_collection()
    entities.add(
        ids=["e1", "e2"],
        embeddings=[old.vector("Berlin"), old.vector("Dylan")],
        documents=["Berlin", "Dylan"],
        metadatas=[{"user_id": "alice", "label": "GPE"}, {"user_id": "bob", "label": "PERSON"}],
    )
    await store.close()
    client = _client(path)
    names = vector_space_names(_space(path, base, dims))
    for role, doc in (("coding_keys", "fix the flaky test"), ("working_facts_cv", "repo: uses uv")):
        coll = client.get_or_create_collection(names[role], metadata={"hnsw:space": "cosine"})
        coll.add(
            ids=[f"{role}:0"],
            embeddings=[old.vector(doc)],
            documents=[doc],
            metadatas=[{"user_id": "alice", "layer": "l1_raw",
                        "custom": json.dumps({"coding_key_text": doc})}],
        )
    return names


def _snapshot(path, names):
    client = _client(path)
    out = {}
    for role, name in names.items():
        try:
            coll = client.get_collection(name)
        except Exception:
            continue
        got = coll.get(include=["documents", "metadatas", "embeddings"])
        out[role] = sorted(
            (rid, doc, json.dumps(meta, sort_keys=True), [round(float(x), 6) for x in emb])
            for rid, doc, meta, emb in zip(got["ids"], got["documents"], got["metadatas"], got["embeddings"])
        )
    return hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()


def _records(path, name):
    got = _client(path).get_collection(name).get(include=["documents", "metadatas", "embeddings"])
    return {
        rid: (doc, meta, list(emb))
        for rid, doc, meta, emb in zip(got["ids"], got["documents"], got["metadatas"], got["embeddings"])
    }


# ----------------------------------------------------------------------
# naming
# ----------------------------------------------------------------------

class TestVectorSpaceNames:
    @pytest.mark.parametrize("dims", [512, None])
    async def test_names_equal_what_the_stores_open(self, tmp_path, dims):
        from hy_memory.coding.store import CodingMemoryStore
        from hy_memory.data.vector_store_chroma import ChromaVectorStore

        cfg = MemoryConfig()
        cfg.vector_store.provider = "chroma"
        cfg.vector_store.persist_directory = str(tmp_path / "v")
        cfg.vector_store.collection_name = "memhub_abc123"
        cfg.vector_store.embedding_dims = dims
        cfg.history.db_path = str(tmp_path / "history.sqlite3")
        store = ChromaVectorStore(cfg)
        await store.initialize()
        await store._get_entity_collection()
        coding = []
        for variant in ("working", "coding"):
            cs = CodingMemoryStore(cfg, FakeEmbedder("m", dims or 8), variant=variant)
            await cs.initialize()
            coding.append(cs)
        opened = {c.name for c in _client(tmp_path / "v").list_collections()}
        names = vector_space_names(cfg)
        assert set(names) == set(VECTOR_ROLES)
        assert set(names.values()) == opened
        suffix = f"_{dims}" if dims else ""
        assert names["memory"] == f"memhub_abc123{suffix}"
        assert names["coding_keys_cv"] == f"memhub_abc123_coding_keys_cv{suffix}"
        await store.close()
        for cs in coding:
            await cs.close()

    def test_explicit_base_name_and_space(self, tmp_path):
        cfg = MemoryConfig()
        cfg.vector_store.provider = "chroma"
        cfg.vector_store.embedding_dims = 1024
        names = vector_space_names(cfg, base_name="staging_x")
        assert names["memory"] == "staging_x_1024"
        assert names["entities"] == "staging_x_1024_entities"
        assert names["working_facts"] == "staging_x_working_facts_1024"
        assert vector_space_names(_space(tmp_path, "b", 512))["memory"] == "b_512"

    def test_parse_collection_name_round_trips(self):
        for dims in (512, None):
            for role, name in vector_space_names(VectorSpace("memhub_9f", dims)).items():
                assert parse_collection_name(name) == ("memhub_9f", dims, role)
        assert parse_collection_name("memhub_9f_512_tag_index") is None

    def test_space_identity_is_model_and_width_only(self):
        a = space_identity("BAAI/bge-small-zh-v1.5", 512)
        b = space_identity(" BAAI/bge-small-zh-v1.5 ", 512)
        assert a == b and a.key == "BAAI/bge-small-zh-v1.5#512"
        assert a.digest != space_identity("BAAI/bge-small-zh-v1.5", 1024).digest
        with pytest.raises(ValueError):
            space_identity("", 512)

    def test_non_chroma_provider_is_not_supported(self):
        cfg = MemoryConfig()
        cfg.vector_store.provider = "qdrant"
        with pytest.raises(NotImplementedError):
            vector_space_names(cfg)


class TestReembedText:
    def test_canonical_state_uses_state_retrieval_text(self):
        meta = {"content": "住在柏林", "custom": json.dumps(_custom_state())}
        assert reembed_text("memory", "住在柏林", meta) == "user lives in Berlin (home city)"

    def test_retrieval_text_when_present(self):
        meta = {"custom": json.dumps({"retrieval_text": "  pattern: plans ahead  "})}
        assert reembed_text("memory", "画像", meta) == "pattern: plans ahead"

    def test_document_otherwise_with_content_fallback(self):
        assert reembed_text("memory", "plain fact", {"custom": "{}"}) == "plain fact"
        assert reembed_text("memory", None, {"content": "from metadata"}) == "from metadata"
        assert reembed_text("memory", "doc", {"custom": "not json"}) == "doc"

    def test_coding_key_and_working_fact_use_the_document(self):
        coding = {"custom": json.dumps({"coding_key_text": "fix flaky test",
                                        "retrieval_text": "ignored outside memory"})}
        assert reembed_text("coding_keys", "fix flaky test", coding) == "fix flaky test"
        assert reembed_text("working_facts", "repo: uses uv", {}) == "repo: uses uv"


# ----------------------------------------------------------------------
# re-embed
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "src_dims,tgt_dims,src_model,tgt_model",
    [(512, 512, "old-model", "new-model"), (512, 1024, "old", "new"), (1024, 512, "old", "new")],
)
async def test_reembed_copies_everything_and_never_touches_source(
    tmp_path, src_dims, tgt_dims, src_model, tgt_model,
):
    path = tmp_path / "v"
    names = await _seed(path, "memhub_src", src_dims, model=src_model)
    before = _snapshot(path, names)
    source = _space(path, "memhub_src", src_dims)
    target = _space(path, "memhub_dst", tgt_dims, model=tgt_model)
    new = FakeEmbedder(tgt_model, tgt_dims)

    plan = await plan_reembed(source, target)
    assert plan.per_role["memory"] == 14 and plan.per_role["entities"] == 2
    assert plan.total_vectors == 18 == plan.estimated_vectors
    assert plan.source_dims == src_dims and plan.target_dims == tgt_dims

    seen = []
    report = await reembed_vector_space(
        source, target, embedding=new, batch_size=4, on_progress=seen.append,
    )
    assert report.embedded == 18 and report.reused == 0
    assert seen and seen[-1].done == seen[-1].total

    assert _snapshot(path, names) == before  # source unchanged, byte for byte
    target_names = vector_space_names(target)
    for role in ("memory", "entities", "coding_keys", "working_facts_cv"):
        src = _records(path, names[role])
        dst = _records(path, target_names[role])
        assert set(src) == set(dst)
        for rid, (doc, meta, _) in src.items():
            assert dst[rid][0] == doc
            assert dst[rid][1] == meta  # status, is_latest, superseded_by, timestamps, custom
            assert len(dst[rid][2]) == tgt_dims
            assert dst[rid][2] == pytest.approx(new.vector(reembed_text(role, doc, meta)), abs=1e-5)
    memory = _records(path, target_names["memory"])
    layers = {meta["layer"] for _, meta, _ in memory.values()}
    users = {meta["user_id"] for _, meta, _ in memory.values()}
    assert "l1_raw" in layers and users == {"alice", "bob"}
    statuses = {meta["status"] for _, meta, _ in memory.values()}
    assert statuses == {"active", "superseded"}
    state = [r for r in memory.values() if "canonical_state" in r[1]["custom"]]
    assert state and state[0][2] == pytest.approx(
        new.vector("user lives in Berlin (home city)"), abs=1e-5,
    )
    meta = _client(path).get_collection(target_names["memory"]).metadata
    assert meta["hnsw:space"] == "cosine" and meta["hy_embedding_model"] == tgt_model

    verified = await verify_reembed(source, target, embedding=new)
    assert verified.ok, verified.problems
    assert verified.per_user == {"alice": 10, "bob": 8}
    assert verified.self_recall_samples == 18 and verified.self_recall_hits == 18


async def test_resume_after_kill_only_embeds_the_rest(tmp_path):
    path = tmp_path / "v"
    await _seed(path, "src", 512, per_user=20)
    source, target = _space(path, "src", 512), _space(path, "dst", 1024)

    class Killed(Exception):
        pass

    first = FakeEmbedder("new", 1024)
    batches = {"n": 0}

    def _kill():
        batches["n"] += 1
        if batches["n"] > 3:
            raise Killed()
        return False

    with pytest.raises(Killed):
        await reembed_vector_space(
            source, target, embedding=first, batch_size=5, max_inflight=1,
            roles=["memory"], cancel=_kill,
        )
    assert first.texts == 15
    second = FakeEmbedder("new", 1024)
    report = await reembed_vector_space(
        source, target, embedding=second, batch_size=5, max_inflight=1, roles=["memory"],
    )
    assert second.texts == 40 - 15
    assert report.per_role["memory"].reused == 15
    # The end state equals an uninterrupted run: every source record, verbatim,
    # with the vector of its own text.
    resumed = _records(path, "dst_1024")
    src = _records(path, "src_512")
    assert set(resumed) == set(src)
    for rid, (doc, meta, vector) in resumed.items():
        assert (doc, meta) == src[rid][:2]
        assert vector == pytest.approx(second.vector(reembed_text("memory", doc, meta)), abs=1e-5)
    assert (await verify_reembed(source, target, roles=["memory"], samples=0)).ok


async def test_cancel_at_batch_boundary_leaves_no_partial_batch(tmp_path):
    path = tmp_path / "v"
    await _seed(path, "src", 512)
    calls = {"n": 0}

    def _cancel():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(ReembedCancelled) as info:
        await reembed_vector_space(
            _space(path, "src", 512), _space(path, "dst", 512),
            embedding=FakeEmbedder("new", 512), batch_size=3, max_inflight=1,
            roles=["memory"], cancel=_cancel,
        )
    assert info.value.report.per_role["memory"].embedded == 6
    assert _client(path).get_collection("dst_512").count() == 6


async def test_wrong_width_raises_before_any_upsert(tmp_path):
    path = tmp_path / "v"
    await _seed(path, "src", 512)
    bad = FakeEmbedder("new", 1024, wrong_width_after=2)
    with pytest.raises(EmbeddingDimensionMismatchError):
        await reembed_vector_space(
            _space(path, "src", 512), _space(path, "dst", 1024),
            embedding=bad, batch_size=4, roles=["memory"],
        )
    assert _client(path).get_collection("dst_1024").count() == 0
    declared_wrong = FakeEmbedder("new", 768)
    with pytest.raises(EmbeddingDimensionMismatchError):
        await reembed_vector_space(
            _space(path, "src", 512), _space(path, "dst", 1024), embedding=declared_wrong,
        )


async def test_refuses_to_write_into_the_source_collection(tmp_path):
    path = tmp_path / "v"
    await _seed(path, "src", 512)
    with pytest.raises(ValueError):
        await reembed_vector_space(
            _space(path, "src", 512), _space(path, "src", 512),
            embedding=FakeEmbedder("new", 512),
        )


async def test_verify_detects_missing_extra_drift_and_width(tmp_path):
    path = tmp_path / "v"
    await _seed(path, "src", 512)
    source, target = _space(path, "src", 512), _space(path, "dst", 512)
    new = FakeEmbedder("new", 512)
    await reembed_vector_space(source, target, embedding=new, roles=["memory"])
    coll = _client(path).get_collection("dst_512")
    got = coll.get(include=["documents", "metadatas"])
    victim, drifted = got["ids"][0], got["ids"][1]
    coll.delete(ids=[victim])
    meta = dict(got["metadatas"][1])
    meta["status"] = "archived"
    coll.update(ids=[drifted], metadatas=[meta])
    coll.add(ids=["stranger"], embeddings=[new.vector("x")], documents=["x"], metadatas=[{"user_id": "eve"}])
    result = await verify_reembed(source, target, embedding=new, roles=["memory"], samples=5)
    assert not result.ok
    counts = result.per_role["memory"]
    assert (counts["missing"], counts["extra"], counts["mismatched"]) == (1, 1, 1)
    assert any("eve" in p for p in result.problems)

    wide = _space(path, "dst", 1024)
    wide_coll = _client(path).get_or_create_collection("dst_1024", metadata={"hnsw:space": "cosine"})
    src = _records(path, "src_512")
    wide_coll.add(
        ids=list(src),
        embeddings=[[0.0] * 512 for _ in src],
        documents=[v[0] for v in src.values()],
        metadatas=[v[1] for v in src.values()],
    )
    result = await verify_reembed(source, wide, roles=["memory"], samples=0)
    assert result.per_role["memory"]["bad_dims"] == len(src) and not result.ok


async def test_drop_vector_space_refuses_live_collections(tmp_path):
    from hy_memory.data.vector_store_chroma import ChromaVectorStore

    path = tmp_path / "v"
    await _seed(path, "src", 512)
    cfg = MemoryConfig()
    cfg.vector_store.provider = "chroma"
    cfg.vector_store.persist_directory = str(path)
    cfg.vector_store.collection_name = "src"
    cfg.vector_store.embedding_dims = 512
    live = ChromaVectorStore(cfg)
    await live.initialize()
    with pytest.raises(RuntimeError):
        await drop_vector_space(_space(path, "src", 512))
    await live.close()
    dropped = await drop_vector_space(_space(path, "src", 512))
    assert set(dropped) == {"src_512", "src_512_entities", "src_coding_keys_512", "src_working_facts_cv_512"}
    assert not {c.name for c in _client(path).list_collections()}


# ----------------------------------------------------------------------
# scan, diff, merge
# ----------------------------------------------------------------------

async def _two_spaces(path):
    """A live 1024 space and a stray 512 space sharing some ids."""
    from hy_memory.data.vector_store_chroma import ChromaVectorStore

    stable_time = datetime(2025, 1, 1)

    async def _store(base, dims, model, contents):
        cfg = MemoryConfig()
        cfg.vector_store.provider = "chroma"
        cfg.vector_store.persist_directory = str(path)
        cfg.vector_store.collection_name = base
        cfg.vector_store.embedding_dims = dims
        store = ChromaVectorStore(cfg)
        await store.initialize()
        emb = FakeEmbedder(model, dims)
        nodes = []
        for node_id, user, content, status in contents:
            node = MemoryNode(
                node_id=node_id, user_id=user, agent_id="a", session_id="s",
                layer=MemoryLayer.L2_FACT, content=content, status=status,
                gmt_created=stable_time, gmt_modified=stable_time, valid_from=stable_time,
            )
            node.embedding = emb.vector(content)
            nodes.append(node)
        await store.upsert_batch(nodes)
        await store.close()

    ids = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(6)]
    await _store("memhub_old", 512, "bge", [
        (ids[0], "alice", "shared one", MemoryStatus.ACTIVE),
        (ids[1], "alice", "shared two (stray wording)", MemoryStatus.ACTIVE),
        (ids[2], "bob", "only in stray", MemoryStatus.ACTIVE),
        (ids[3], "bob", "only in stray, deleted", MemoryStatus.SHADOW),
    ])
    await _store("memhub_new", 1024, "e5", [
        (ids[0], "alice", "shared one", MemoryStatus.ACTIVE),
        (ids[1], "alice", "shared two (live wording)", MemoryStatus.ARCHIVED),
        (ids[4], "carol", "only in live", MemoryStatus.ACTIVE),
    ])
    return ids


async def test_scan_diff_and_merge(tmp_path):
    path = tmp_path / "v"
    ids = await _two_spaces(path)
    spaces = await scan_vector_spaces(path)
    by_base = {(s.base_name, s.dims): s for s in spaces}
    assert set(by_base) == {("memhub_old", 512), ("memhub_new", 1024)}
    old, live = by_base[("memhub_old", 512)], by_base[("memhub_new", 1024)]
    assert old.roles["memory"].count == 4 and old.width == 512
    assert live.roles["memory"].count == 3 and live.width == 1024
    assert old.per_user == {"alice": 2, "bob": 2}
    assert live.per_user == {"alice": 2, "carol": 1}

    stray, target = old.space(), _space(path, "memhub_new", 1024, model="e5")
    diff = await diff_spaces(stray, target)
    assert set(diff.missing["memory"]) == {ids[2], ids[3]}  # status ignored
    assert diff.total_missing == 2

    live_before = _records(path, "memhub_new_1024")
    stray_before = _snapshot(path, stray.names())
    merger = FakeEmbedder("e5", 1024)
    report = await reembed_vector_space(stray, target, embedding=merger)
    assert merger.texts == 2 and report.embedded == 2
    assert report.reused == 1 and report.kept == 1
    after = _records(path, "memhub_new_1024")
    assert set(after) == set(live_before) | {ids[2], ids[3]}
    for rid, record in live_before.items():  # the target wins
        assert after[rid][:2] == record[:2]
        assert after[rid][2] == pytest.approx(record[2], abs=1e-6)
    assert _snapshot(path, stray.names()) == stray_before
    assert (await diff_spaces(stray, target)).empty

    again = FakeEmbedder("e5", 1024)
    second = await reembed_vector_space(stray, target, embedding=again)
    assert again.texts == 0 and second.embedded == 0
    assert _records(path, "memhub_new_1024").keys() == after.keys()
    rescanned = {(s.base_name, s.dims): s for s in await scan_vector_spaces(path)}
    assert rescanned[("memhub_new", 1024)].roles["memory"].count == 5


async def test_scan_of_missing_directory_is_empty(tmp_path):
    assert await scan_vector_spaces(tmp_path / "nope") == []


# ----------------------------------------------------------------------
# EmbedService fixes
# ----------------------------------------------------------------------

def _fake_raw(n, dims):
    items = []
    for i in range(n):
        item = MagicMock()
        item.embedding = [0.1] * dims
        item.index = i
        items.append(item)
    resp = MagicMock()
    resp.model = "m"
    resp.usage = None
    resp.data = items
    raw = MagicMock()
    raw.status_code = 200
    raw.headers = {}
    raw.parse.return_value = resp
    return raw


def _service(dims=8, **embedder):
    cfg = MemoryConfig(embedder=EmbedderConfig(
        provider="openai", model="m", api_key="sk", base_url="https://e.invalid/v1",
        embedding_dims=dims, max_retries=2, retry_delay=0.0, **embedder,
    ))
    return EmbedService(cfg)


async def test_embed_batch_chunks_to_batch_size():
    svc = _service()
    sizes = []

    async def _create(**kwargs):
        sizes.append(len(kwargs["input"]))
        return _fake_raw(len(kwargs["input"]), 8)

    client = MagicMock()
    client.embeddings.with_raw_response.create = AsyncMock(side_effect=_create)
    with patch("openai.AsyncOpenAI", return_value=client):
        vectors = await svc.embed_batch([f"t{i}" for i in range(1000)], use_cache=False)
    assert len(vectors) == 1000
    assert len(sizes) == math.ceil(1000 / 64) and max(sizes) == 64

    small = _service(batch_size=10)
    sizes.clear()
    with patch("openai.AsyncOpenAI", return_value=client):
        _, metas = await small.embed_batch_with_meta([f"u{i}" for i in range(25)], use_cache=False)
    assert sizes == [10, 10, 5]
    assert [m["batch_index"] for m in metas] == list(range(25))


async def test_429_honours_retry_after():
    svc = _service()

    class RateLimited(Exception):
        status_code = 429

        def __init__(self):
            super().__init__("rate limited")
            self.response = MagicMock()
            self.response.headers = {"retry-after": "2"}

    client = MagicMock()
    client.embeddings.with_raw_response.create = AsyncMock(
        side_effect=[RateLimited(), _fake_raw(1, 8)],
    )
    sleeps = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    with patch("openai.AsyncOpenAI", return_value=client), \
            patch("hy_memory.core.embed_service.asyncio.sleep", _sleep):
        vectors = await svc.embed_batch(["x"], use_cache=False)
    assert len(vectors) == 1
    assert len(sleeps) == 1 and 2.0 <= sleeps[0] <= 2.5


async def test_per_call_max_retries():
    svc = _service()

    class Down(Exception):
        status_code = 503

    client = MagicMock()
    client.embeddings.with_raw_response.create = AsyncMock(side_effect=Down("down"))
    with patch("openai.AsyncOpenAI", return_value=client), pytest.raises(Down):
        await svc.embed_batch(["x"], use_cache=False, max_retries=5)
    assert client.embeddings.with_raw_response.create.await_count == 6


async def test_returned_width_is_validated_and_not_retried():
    svc = _service(dims=8)
    client = MagicMock()
    client.embeddings.with_raw_response.create = AsyncMock(return_value=_fake_raw(2, 7))
    with patch("openai.AsyncOpenAI", return_value=client), \
            pytest.raises(EmbeddingDimensionMismatchError):
        await svc.embed_batch(["a", "b"], use_cache=False)
    assert client.embeddings.with_raw_response.create.await_count == 1
