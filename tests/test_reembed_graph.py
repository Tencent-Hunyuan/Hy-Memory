"""Re-embed the Kuzu graph into a new embedding space.

The source graph is opened read-only and never modified. A new graph is built
at the target path with the target width: every node and edge is copied, the
embedding columns are recomputed from their text, the HNSW index is rebuilt.
Runs in CI: kuzu is a core dependency.
"""

import hashlib
import json
import os

import pytest

from hy_memory import (
    EmbeddingDimensionMismatchError,
    MemoryConfig,
    ReembedCancelled,
    drop_graph,
    reembed_graph,
    verify_graph_reembed,
)
from hy_memory.models.memory import MemoryLayer, MemoryNode
from hy_memory.reembed import hierarchy_topic_label, plan_graph_reembed

from .test_reembed import FakeEmbedder


def _cfg(path, dims):
    cfg = MemoryConfig()
    cfg.graph_store.provider = "kuzu"
    cfg.graph_store.db_path = str(path)
    cfg.vector_store.embedding_dims = dims
    return cfg


async def _open(path, dims):
    from hy_memory.data.graph_store_kuzu import KuzuGraphStore

    store = KuzuGraphStore(_cfg(path, dims))
    await store.initialize()
    assert store._available
    return store


def _node(content, *, layer=MemoryLayer.L2_FACT, custom=None, user="alice"):
    return MemoryNode(
        user_id=user, agent_id="a", session_id="s1",
        layer=layer, content=content, custom=custom or {},
    )


async def _seed_graph(path, dims, model="old"):
    """A graph with every node and edge type the SDK writes."""
    old = FakeEmbedder(model, dims)
    gs = await _open(path, dims)
    schema = _node(
        "画像：喜欢提前规划", layer=MemoryLayer.L6_SCHEMA,
        custom={"schema_type": "basic", "retrieval_text": "plans trips weeks ahead",
                "_has_beh": True, "beh_abstraction": "prepares early under uncertainty"},
    )
    schema._graph_embedding = old.vector("plans trips weeks ahead")
    core = _node("core: anticipates", layer=MemoryLayer.L6_SCHEMA, custom={"schema_type": "abstract"})
    core._graph_embedding = old.vector("core: anticipates")
    stale_beh = _node("beh without text", layer=MemoryLayer.L6_SCHEMA, custom={})
    stale_beh._graph_embedding = old.vector("beh without text")
    facts = [_node(f"fact {i}", user="bob" if i % 2 else "alice") for i in range(5)]
    for node in [schema, core, stale_beh, *facts]:
        await gs.upsert_memory_node(node)
    await gs.update_embedding(schema.node_id, beh_embedding=old.vector("prepares early under uncertainty"))
    await gs.update_embedding(stale_beh.node_id, beh_embedding=old.vector("lost text"))
    await gs.add_cross_abstracts_to(schema.node_id, core.node_id)
    ik = facts[0].get_isolation_key()
    await gs.add_edge(facts[0].node_id, facts[1].node_id, "RELATED_TO", {"relation_type": "co_occurs", "weight": 0.7})
    await gs.add_temporal_edge(facts[0].node_id, facts[2].node_id, topic_id="t")
    for fact in facts[:3]:
        await gs.add_topic_tag(fact.node_id, "travel", ik, embed_service=old)
    await gs.add_topic_tag(facts[3].node_id, "work", ik)  # no embed service: NULL embedding
    await gs.add_topic_path(
        facts[0].node_id, ["Travel", "Japan trip"], ik, "alice",
        node_types=["topic", "series"], embed_service=old,
    )
    await gs.add_topic_path(facts[1].node_id, ["Work", "Standups"], ik, "alice", embed_service=old)
    await gs.add_entity_mention(facts[0].node_id, "Kyoto", "GPE", ik)
    await gs.ensure_vdbref("raw-1", "l1_raw")
    await gs.add_derived_from(schema.node_id, "raw-1")
    await gs.close()
    return old, {"schema": schema, "core": core, "stale_beh": stale_beh, "facts": facts}


def _digest(path):
    """Hash of the Kuzu DB file and any sidecar files next to it."""
    h = hashlib.sha256()
    for name in sorted(os.listdir(path.parent)):
        if name == path.name or name.startswith(path.name + "."):
            h.update(name.encode())
            with open(path.parent / name, "rb") as handle:
                h.update(handle.read())
    return h.hexdigest()


def _read(path, query):
    import kuzu

    db = kuzu.Database(str(path), read_only=True)
    conn = kuzu.Connection(db)
    try:
        result = conn.execute(query)
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        return rows
    finally:
        conn.close()
        db.close()


@pytest.mark.parametrize(
    "src_dims,tgt_dims,src_model,tgt_model",
    [(512, 512, "old", "new"), (512, 1024, "old", "new"), (1024, 512, "old", "new")],
)
async def test_reembed_graph_copies_everything_at_the_new_width(
    tmp_path, src_dims, tgt_dims, src_model, tgt_model,
):
    source, target = tmp_path / "graph", tmp_path / "graph-new"
    _, nodes = await _seed_graph(source, src_dims, model=src_model)
    before = _digest(source)
    plan = await plan_graph_reembed(source, target_dims=tgt_dims)
    assert plan.source_width == src_dims and plan.target_width == tgt_dims
    assert plan.memory_nodes == 8 and plan.topics == 2 and plan.hierarchy_topics == 4
    assert plan.memory_embeddings == 3 and plan.beh_embeddings == 2
    assert plan.topic_embeddings == 1 and plan.hierarchy_topic_embeddings == 4

    new = FakeEmbedder(tgt_model, tgt_dims)
    seen = []
    report = await reembed_graph(
        source, target, embedding=new, target_dims=tgt_dims, batch_size=3, on_progress=seen.append,
    )
    assert _digest(source) == before  # the source graph is untouched
    assert not os.path.exists(str(target) + ".reembed-partial")
    assert report.source_width == src_dims and report.target_width == tgt_dims
    assert report.node_counts == plan.node_counts
    assert report.rel_counts == plan.rel_counts
    assert report.embedded == {
        "Memory.embedding": 3, "Memory.beh_embedding": 1,
        "Topic.embedding": 1, "HierarchyTopic.embedding": 4,
    }
    assert report.cleared == {"Memory.beh_embedding": 1}  # beh vector with no beh_abstraction
    assert report.hierarchy_labels_exact == 4 and report.hierarchy_labels_inexact == ()
    assert report.vector_indexes == ("memory_content_idx",)
    assert seen

    check = await verify_graph_reembed(source, target, target_dims=tgt_dims)
    assert check.ok, check.problems
    assert check.vector_index and check.target_width == tgt_dims
    assert all(src == dst for src, dst in check.node_counts.values())
    assert all(src == dst for src, dst in check.rel_counts.values())

    schema = nodes["schema"]
    rows = _read(target, f"MATCH (m:Memory {{node_id: '{schema.node_id}'}}) RETURN m.embedding, m.beh_embedding, m.custom_json, m.content;")
    emb, beh, custom, content = rows[0]
    assert len(emb) == tgt_dims
    assert emb == pytest.approx(new.vector("plans trips weeks ahead"), abs=1e-5)
    assert beh == pytest.approx(new.vector("prepares early under uncertainty"), abs=1e-5)
    assert json.loads(custom)["retrieval_text"] == "plans trips weeks ahead" and content == schema.content
    rows = _read(target, f"MATCH (m:Memory {{node_id: '{nodes['core'].node_id}'}}) RETURN m.embedding;")
    assert rows[0][0] == pytest.approx(new.vector("core: anticipates"), abs=1e-5)
    rows = _read(target, f"MATCH (m:Memory {{node_id: '{nodes['facts'][0].node_id}'}}) RETURN m.embedding IS NULL;")
    assert rows[0][0] is True  # a NULL source vector stays NULL
    topics = dict(_read(target, "MATCH (t:Topic) RETURN t.name, t.embedding;"))
    assert topics["travel"] == pytest.approx(new.vector("travel"), abs=1e-5)
    assert topics["work"] is None
    for subject, path_json, embedding in _read(target, "MATCH (t:HierarchyTopic) RETURN t.subject, t.path_json, t.embedding;"):
        label, exact = hierarchy_topic_label(subject, path_json)
        assert exact and embedding == pytest.approx(new.vector(label), abs=1e-5)

    # The rebuilt graph serves the SDK: vector search on the new index works.
    gs = await _open(target, tgt_dims)
    try:
        hits = await gs.vector_search(
            query_embedding=new.vector("plans trips weeks ahead"),
            isolation_key=schema.get_isolation_key(), limit=1,
        )
        assert hits and hits[0]["node_id"] == schema.node_id
        width = await gs._run("CALL table_info('Memory') RETURN *;")
        assert dict((r[1], r[2]) for r in width)["embedding"] == f"FLOAT[{tgt_dims}]"
    finally:
        await gs.close()


async def test_source_labels_rebuild_exactly(tmp_path):
    """The stored HierarchyTopic vectors were computed from the rebuilt label."""
    source = tmp_path / "graph"
    old, _ = await _seed_graph(source, 64)
    rows = _read(source, "MATCH (t:HierarchyTopic) RETURN t.subject, t.path_json, t.embedding;")
    assert len(rows) == 4
    for subject, path_json, embedding in rows:
        label, exact = hierarchy_topic_label(subject, path_json)
        assert exact
        assert embedding == pytest.approx(old.vector(label), abs=1e-6)


def test_legacy_path_json_is_flagged_inexact():
    label, exact = hierarchy_topic_label("alice", json.dumps(["Travel", "Japan"]), "Japan")
    assert label == "alice: topic:Travel > facet:Japan" and not exact
    label, exact = hierarchy_topic_label("alice", "", "Japan")
    assert label == "alice: Japan" and not exact


async def test_cancel_and_failure_leave_no_target(tmp_path):
    source, target = tmp_path / "graph", tmp_path / "graph-new"
    await _seed_graph(source, 64)
    with pytest.raises(ReembedCancelled):
        await reembed_graph(
            source, target, embedding=FakeEmbedder("new", 32), target_dims=32,
            cancel=lambda: True,
        )
    with pytest.raises(EmbeddingDimensionMismatchError):
        await reembed_graph(
            source, target, embedding=FakeEmbedder("new", 32, wrong_width_after=1), target_dims=32,
        )
    assert not os.path.exists(target)
    assert not os.path.exists(str(target) + ".reembed-partial")
    # A restart starts over and succeeds.
    report = await reembed_graph(source, target, embedding=FakeEmbedder("new", 32), target_dims=32)
    assert report.node_counts["Memory"] == 8
    with pytest.raises(FileExistsError):
        await reembed_graph(source, target, embedding=FakeEmbedder("new", 32), target_dims=32)
    again = await reembed_graph(
        source, target, embedding=FakeEmbedder("new", 32), target_dims=32, overwrite=True,
    )
    assert again.rel_counts == report.rel_counts


async def test_refuses_open_source_and_drops_only_closed_graphs(tmp_path):
    source, target = tmp_path / "graph", tmp_path / "graph-new"
    await _seed_graph(source, 64)
    live = await _open(source, 64)
    try:
        with pytest.raises(RuntimeError):
            await reembed_graph(source, target, embedding=FakeEmbedder("new", 64), target_dims=64)
        with pytest.raises(RuntimeError):
            await drop_graph(source)
    finally:
        await live.close()
    with pytest.raises(ValueError):
        await reembed_graph(source, source, embedding=FakeEmbedder("new", 64), target_dims=64)
    await reembed_graph(source, target, embedding=FakeEmbedder("new", 64), target_dims=64)
    assert await drop_graph(target) is True
    assert not os.path.exists(target)
    assert await drop_graph(target) is False
    assert os.path.exists(source)
