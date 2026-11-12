"""Re-embed a Qdrant vector space, through the SDK's own Qdrant store.

The source is written by `QdrantVectorStore` itself (local mode in a temporary
directory, a deterministic sparse "BM25" encoder standing in for fastembed),
so the records are exactly what the write path stores: memories, entities,
the tag index, hidden retrieval clauses, Coding keys and Working facts.
"""

import hashlib
import uuid

import pytest

from hy_memory import (
    MemoryConfig,
    ReembedCancelled,
    VectorSpace,
    close_vector_clients,
    diff_spaces,
    drop_vector_space,
    plan_reembed,
    reembed_text,
    reembed_vector_space,
    scan_vector_spaces,
    vector_space_names,
    verify_reembed,
)
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.pipelines._retrieval.retrieval_clauses import split_retrieval_clauses
from tests.test_reembed import FakeEmbedder, _custom_state

pytest.importorskip("qdrant_client")

PREFIX = "mh1a2b3c4d"
BASE = f"{PREFIX}_memhub_0123456789abcdef"
LONG = (
    "The user moved to Berlin in 2021 and works as a nurse. "
    "She runs along the river every morning before her shift starts. "
    "Her sister visits twice a year from Lisbon with the kids."
)


@pytest.fixture(autouse=True)
def _fake_bm25(monkeypatch):
    """A deterministic sparse encoder, so the SDK store writes its bm25 vector."""
    from hy_memory.pipelines._retrieval import bm25_fastembed

    def encode(text):
        words = sorted({w for w in str(text).split() if w})
        indices = [int(hashlib.sha256(w.encode()).hexdigest()[:6], 16) for w in words]
        pairs = sorted(dict(zip(indices, [1.0] * len(indices))).items())
        return [i for i, _ in pairs], [v for _, v in pairs]

    monkeypatch.setattr(bm25_fastembed, "is_available", lambda: True)
    monkeypatch.setattr(bm25_fastembed, "encode_doc", encode)
    yield
    close_vector_clients()


def _cfg(path, collection, dims):
    cfg = MemoryConfig()
    cfg.vector_store.provider = "qdrant"
    cfg.vector_store.persist_directory = str(path)
    cfg.vector_store.collection_name = collection
    cfg.vector_store.embedding_dims = dims
    cfg.vector_store.host = None
    return cfg


def _space(path, base, dims, model=None):
    return VectorSpace(
        base_name=base, dims=dims, persist_directory=str(path), model=model, provider="qdrant",
    )


def _node(user, i, content, *, layer=MemoryLayer.L2_FACT, custom=None):
    return MemoryNode(
        node_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{user}:{i}")),
        user_id=user, agent_id="a", session_id="s", layer=layer, content=content,
        status=MemoryStatus.ACTIVE, tags=["t1", f"t{i}"], custom=custom or {"k": i},
    )


def _nodes():
    out = []
    for user in ("alice", "bob"):
        for i in range(5):
            out.append(_node(user, i, f"{user} fact {i}",
                             layer=MemoryLayer.L1_RAW if i == 0 else MemoryLayer.L2_FACT))
        out.append(_node(user, 5, f"{user} state", custom=_custom_state()))
        out.append(_node(user, 6, LONG))
    return out


async def _seed(path, dims=8, model="old-model"):
    """Write a source space through the SDK's Qdrant stores; returns the nodes."""
    from hy_memory.coding.store import _derive_coding_collection_name, _derive_fact_collection_name
    from hy_memory.data.vector_store_qdrant import QdrantVectorStore

    old = FakeEmbedder(model, dims)
    store = QdrantVectorStore(_cfg(path, BASE, dims))
    await store.initialize()
    nodes = _nodes()
    for node in nodes:
        node.embedding = old.vector(reembed_text("memory", node.content, {"custom": node.custom}))
    await store.upsert_batch(nodes)
    await store.upsert_entity(
        entity_text="Berlin", entity_type="GPE", embedding=old.vector("Berlin"),
        memory_id=nodes[0].node_id, user_id="alice",
    )
    for user in ("alice", "bob"):
        await store.upsert_tag_embedding(user_id=user, tag="t1", embedding=old.vector("t1"))
    long_node = nodes[6]
    clauses = split_retrieval_clauses(long_node.content)
    await store.replace_retrieval_clauses(long_node, clauses, [old.vector(c) for c in clauses])
    await store.close()
    for name, doc in (
        (_derive_coding_collection_name(BASE, ""), "fix the flaky test"),
        (_derive_fact_collection_name(BASE, "_cv"), "repo: uses uv"),
    ):
        side = QdrantVectorStore(_cfg(path, name, dims))
        await side.initialize()
        node = _node("alice", 99, doc, custom={})
        node.embedding = old.vector(doc)
        await side.upsert(node)
        await side.close()
    return nodes


def _raw(path):
    from hy_memory._reembed_qdrant import qdrant_client

    return qdrant_client(_space(path, BASE, 8)).raw


def _points(path, name):
    raw = _raw(path)
    out = {}
    offset = None
    while True:
        points, offset = raw.scroll(
            collection_name=name, limit=500, offset=offset, with_payload=True, with_vectors=True,
        )
        for point in points:
            vector = point.vector
            if isinstance(vector, dict):
                vector = {key: (value if isinstance(value, list) else
                                (list(value.indices), list(value.values)))
                          for key, value in vector.items()}
            out[str(point.id)] = (point.payload, vector)
        if offset is None:
            return out


async def test_reembed_a_qdrant_space_into_a_new_width(tmp_path):
    nodes = await _seed(tmp_path)
    src, tgt = _space(tmp_path, BASE, 8), _space(tmp_path, BASE, 6, model="new-model")
    names = src.names()
    assert {"tag_index", "retrieval_clauses"} <= set(names)
    before = {role: _points(tmp_path, name) for role, name in names.items()
              if _raw(tmp_path).collection_exists(name)}

    plan = await plan_reembed(src, tgt)
    assert plan.per_role["memory"] == len(nodes)
    assert plan.per_role["entities"] == 1 and plan.per_role["tag_index"] == 2
    assert plan.per_role["retrieval_clauses"] == len(split_retrieval_clauses(LONG))
    assert plan.per_role["coding_keys"] == 1 and plan.per_role["working_facts_cv"] == 1
    diff = await diff_spaces(src, tgt)
    assert diff.total_missing == plan.total_vectors

    new = FakeEmbedder("new-model", 6)
    report = await reembed_vector_space(src, tgt, embedding=new, batch_size=4)
    assert report.embedded == plan.total_vectors

    # The source was never written.
    after = {role: _points(tmp_path, names[role]) for role in before}
    assert after == before

    tgt_names = tgt.names()
    target = {role: _points(tmp_path, tgt_names[role]) for role in before}
    # Ids and payloads copied verbatim; every vector is the new model's of the
    # text the write path embedded.
    expected = {
        "memory": {n.node_id: reembed_text("memory", n.content, {"custom": n.custom}) for n in nodes},
        "entities": {pid: "Berlin" for pid in before["entities"]},
        "tag_index": {pid: "t1" for pid in before["tag_index"]},
    }
    for role, points in before.items():
        assert set(target[role]) == set(points), role
        for pid, (payload, vector) in points.items():
            new_payload, new_vector = target[role][pid]
            assert new_payload == payload
            dense = new_vector[""] if isinstance(new_vector, dict) else new_vector
            assert len(dense) == 6
            if role in expected:
                assert dense == pytest.approx(new.vector(expected[role][pid]))
            if isinstance(vector, dict) and "bm25" in vector:
                assert new_vector["bm25"] == vector["bm25"]
    clause_texts = split_retrieval_clauses(LONG)
    for payload, vector in target["retrieval_clauses"].values():
        dense = vector[""] if isinstance(vector, dict) else vector
        assert dense == pytest.approx(new.vector(clause_texts[payload["clause_index"]]))
    # The memory collection kept its sparse BM25 vectors.
    assert any(isinstance(v, dict) and "bm25" in v for _, v in target["memory"].values())

    check = await verify_reembed(src, tgt, embedding=new, samples=20)
    assert check.ok, check.problems
    assert check.per_user["alice"] > 0 and check.per_user["bob"] > 0
    assert check.self_recall_samples > 0
    assert check.self_recall_hits == check.self_recall_samples

    # A rerun resumes: nothing is embedded twice.
    again = await reembed_vector_space(src, tgt, embedding=new)
    assert again.embedded == 0 and again.reused == plan.total_vectors
    assert (await diff_spaces(src, tgt)).empty


async def test_skip_existing_lets_the_target_win(tmp_path):
    await _seed(tmp_path)
    src, tgt = _space(tmp_path, BASE, 8), _space(tmp_path, BASE, 6)
    new = FakeEmbedder("new-model", 6)
    await reembed_vector_space(src, tgt, embedding=new, roles=["memory"])
    name = tgt.names()["memory"]
    raw = _raw(tmp_path)
    point = raw.scroll(collection_name=name, limit=1, with_payload=True)[0][0]
    raw.set_payload(collection_name=name, payload={"content": "edited"}, points=[point.id])
    kept = await reembed_vector_space(src, tgt, embedding=new, roles=["memory"])
    assert kept.kept == 1 and kept.embedded == 0
    assert raw.retrieve(collection_name=name, ids=[point.id])[0].payload["content"] == "edited"
    overwrite = await reembed_vector_space(
        src, tgt, embedding=new, roles=["memory"], skip_existing=False,
    )
    assert overwrite.embedded == 1


async def test_a_cancel_stops_at_a_batch_boundary(tmp_path):
    await _seed(tmp_path)
    src, tgt = _space(tmp_path, BASE, 8), _space(tmp_path, BASE, 6)
    with pytest.raises(ReembedCancelled):
        await reembed_vector_space(src, tgt, embedding=FakeEmbedder("n", 6), cancel=lambda: True)
    assert not _raw(tmp_path).collection_exists(tgt.names()["memory"]) or (
        _raw(tmp_path).count(collection_name=tgt.names()["memory"]).count == 0
    )


async def test_text_parity_with_the_chroma_store_for_the_same_nodes(tmp_path):
    from hy_memory.data.vector_store_chroma import ChromaVectorStore
    from tests.test_reembed import _client

    nodes = await _seed(tmp_path / "q")
    chroma_cfg = MemoryConfig()
    chroma_cfg.vector_store.provider = "chroma"
    chroma_cfg.vector_store.persist_directory = str(tmp_path / "c")
    chroma_cfg.vector_store.collection_name = BASE
    chroma_cfg.vector_store.embedding_dims = 8
    store = ChromaVectorStore(chroma_cfg)
    await store.initialize()
    for node in nodes:
        node.embedding = [0.1] * 8
    await store.upsert_batch(nodes)
    await store.close()
    chroma = _client(tmp_path / "c").get_collection(vector_space_names(chroma_cfg)["memory"])
    got = chroma.get(include=["documents", "metadatas"])
    chroma_text = {
        rid: reembed_text("memory", doc, meta)
        for rid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"])
    }
    from hy_memory._reembed_qdrant import qdrant_client

    space = _space(tmp_path / "q", BASE, 8)
    coll = qdrant_client(space).get_collection(space.names()["memory"])
    page = coll.get(limit=100, include=["documents", "metadatas"])
    qdrant_text = {
        rid: reembed_text("memory", doc, meta)
        for rid, doc, meta in zip(page["ids"], page["documents"], page["metadatas"])
    }
    assert qdrant_text == chroma_text
    assert qdrant_text[nodes[5].node_id] == _custom_state()["state_retrieval_text"]


async def test_scan_and_drop_stay_inside_the_prefix(tmp_path):
    await _seed(tmp_path)
    raw = _raw(tmp_path)
    from qdrant_client.models import Distance, VectorParams

    raw.create_collection(
        collection_name="other_memhub_0123456789abcdef_8",
        vectors_config=VectorParams(size=8, distance=Distance.COSINE),
    )
    found = await scan_vector_spaces(tmp_path, provider="qdrant", prefix=f"{PREFIX}_")
    assert [space.base_name for space in found] == [BASE]
    scanned = found[0]
    assert scanned.dims == 8 and scanned.width == 8
    assert scanned.per_user["alice"] > 0
    assert {"memory", "entities", "tag_index", "retrieval_clauses", "coding_keys",
            "working_facts_cv"} <= set(scanned.roles)
    space = scanned.space(model="old-model")
    assert space.provider == "qdrant" and space.names()["memory"] == f"{BASE}_8"
    everything = await scan_vector_spaces(tmp_path, provider="qdrant")
    assert len(everything) == 2

    with pytest.raises(ValueError):
        await drop_vector_space(
            _space(tmp_path, "other_memhub_0123456789abcdef", 8), require_prefix=f"{PREFIX}_",
        )
    dropped = await drop_vector_space(space, require_prefix=f"{PREFIX}_")
    assert dropped and all(name.startswith(PREFIX) for name in dropped)
    assert raw.collection_exists("other_memhub_0123456789abcdef_8")
    assert await scan_vector_spaces(tmp_path, provider="qdrant", prefix=f"{PREFIX}_") == []


async def test_a_space_never_spans_two_stores(tmp_path):
    chroma = VectorSpace(base_name=BASE, dims=8, persist_directory=str(tmp_path))
    with pytest.raises(ValueError, match="same vector store"):
        await reembed_vector_space(
            chroma, _space(tmp_path, BASE, 6), embedding=FakeEmbedder("n", 6),
        )
    assert "secret" not in repr(VectorSpace(base_name="b", dims=1, provider="qdrant",
                                            host="h", api_key="secret"))


def test_from_config_reads_a_qdrant_server():
    cfg = MemoryConfig()
    cfg.vector_store.provider = "qdrant"
    cfg.vector_store.host = "vectors.internal"
    cfg.vector_store.port = 6334
    cfg.vector_store.api_key = "k"
    cfg.vector_store.collection_name = BASE
    cfg.vector_store.embedding_dims = 8
    space = VectorSpace.from_config(cfg)
    assert (space.provider, space.host, space.port, space.api_key) == (
        "qdrant", "vectors.internal", 6334, "k",
    )
    assert space.persist_directory is None
    assert space.location() == ("qdrant", "vectors.internal:6334")
