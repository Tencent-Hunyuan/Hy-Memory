# -*- coding: utf-8 -*-
"""
export_user_memories / import_user_memories：按原 id 与向量导出、写回一个用户。

备份恢复需要把记忆写回另一个（空的）存储，且 id、层级、状态、演化链、时间与
向量都不变；clone_user 只能在同一存储内换新 id 复制，add_extracted 会重跑
reconcile 并生成新 id，都不行。本文件在真实的 Chroma 与 Qdrant（本地引擎）上
覆盖：

- 导出含 L1_RAW 原始对话与 SUPERSEDED / SHADOW 等全部状态，带向量；
- 写回空存储后逐条一致（id、内容、层级、状态、演化指针、时间、向量）；
- 没带向量的节点用当前 embedding 服务重算；
- 目标非空、维度不符、user_id 不符时拒绝且不写；写入中途失败回滚。
"""

import asyncio
import random
import uuid

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus

DIMS = 16


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _vector(seed):
    rng = random.Random(seed)
    values = [rng.gauss(0, 1) for _ in range(DIMS)]
    norm = sum(v * v for v in values) ** 0.5 or 1.0
    return [v / norm for v in values]


def _node(user_id, content, layer=MemoryLayer.L2_FACT, status=MemoryStatus.ACTIVE, **extra):
    node = MemoryNode(
        node_id=str(uuid.uuid4()),
        user_id=user_id,
        agent_id="a1",
        session_id="s1",
        content=content,
        layer=layer,
        status=status,
        is_latest=status == MemoryStatus.ACTIVE,
        **extra,
    )
    node.embedding = _vector(content)
    return node


class _Embedder:
    def __init__(self):
        self.calls = []

    async def embed_batch(self, texts, use_cache=True):
        self.calls.append(list(texts))
        return [_vector(text) for text in texts]


def _chroma(tmp_path, name):
    from hy_memory.data.vector_store_chroma import ChromaVectorStore

    config = MemoryConfig.from_dict({"vector_store": {
        "provider": "chroma",
        "collection_name": f"t_{name}_{uuid.uuid4().hex[:6]}",
        "persist_directory": str(tmp_path / name),
        "embedding_dims": DIMS,
    }})
    store = ChromaVectorStore(config)
    run(store.initialize())
    return config, store


def _qdrant(tmp_path, name):
    pytest.importorskip("qdrant_client")
    from hy_memory.data.vector_store_qdrant import QdrantVectorStore

    config = MemoryConfig.from_dict({"vector_store": {
        "provider": "qdrant",
        "collection_name": f"t_{name}_{uuid.uuid4().hex[:6]}",
        "persist_directory": str(tmp_path / name),
        "embedding_dims": DIMS,
    }})
    store = QdrantVectorStore(config)
    run(store.initialize())
    return config, store


def _client(config, store, embedder=None):
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._config = config
    client._vector_store = store
    client._embed_service = embedder or _Embedder()
    return client


def _export(client, user_id, **kwargs):
    return run(HyMemoryClient.async_export_user_memories.__wrapped__(client, user_id, **kwargs))


def _import(client, user_id, nodes, **kwargs):
    return run(
        HyMemoryClient.async_import_user_memories.__wrapped__(client, user_id, nodes, **kwargs)
    )


def _seed(store):
    old = _node("u1", "lived in Paris", status=MemoryStatus.SUPERSEDED)
    new = _node("u1", "lives in Lyon", supersedes=[old.node_id])
    old.superseded_by = [new.node_id]
    nodes = [
        old,
        new,
        _node("u1", "[user]: hi\n[assistant]: hello", layer=MemoryLayer.L1_RAW),
        _node("u1", "[user]: bees\n[assistant]: noted", layer=MemoryLayer.L1_RAW,
              status=MemoryStatus.SHADOW),
        _node("u1", "name: U One", layer=MemoryLayer.L0_BASIC_INFO),
        _node("u2", "someone else"),
    ]
    run(store.upsert_batch(nodes))
    return [node for node in nodes if node.user_id == "u1"]


def _key(item):
    return (
        item["node_id"], item["content"], item["layer"], item["status"],
        tuple(item.get("supersedes") or ()), tuple(item.get("superseded_by") or ()),
        item["memory_at"], item["gmt_created"],
    )


@pytest.fixture(params=["chroma", "qdrant"])
def stores(request, tmp_path):
    make = _chroma if request.param == "chroma" else _qdrant
    source = make(tmp_path, "source")
    target = make(tmp_path, "target")
    yield source, target
    for _config, store in (source, target):
        run(store.close())


def test_export_holds_every_layer_and_status_with_vectors(stores):
    (config, store), _ = stores
    seeded = _seed(store)
    exported = _export(_client(config, store), "u1")
    assert exported["success"] is True
    assert exported["count"] == len(seeded) == 5
    assert exported["by_layer"] == {"l2_fact": 2, "l1_raw": 2, "l0_basic_info": 1}
    assert {item["status"] for item in exported["nodes"]} == {"active", "superseded", "shadow"}
    assert exported["embedding_dims"] == DIMS
    assert all(len(item["embedding"]) == DIMS for item in exported["nodes"])
    raw = _export(_client(config, store), "u1", layers=["l1_raw"])
    assert raw["count"] == 2
    bare = _export(_client(config, store), "u1", include_embeddings=False)
    assert all(item["embedding"] is None for item in bare["nodes"])


def test_import_writes_back_every_node_with_its_id_and_vector(stores):
    (source_config, source), (target_config, target) = stores
    _seed(source)
    exported = _export(_client(source_config, source), "u1")
    embedder = _Embedder()
    imported = _import(_client(target_config, target, embedder), "u1", exported["nodes"])
    assert imported["success"] is True, imported
    assert imported["imported_count"] == 5 and imported["re_embedded"] == 0
    assert embedder.calls == []
    again = _export(_client(target_config, target), "u1")
    assert sorted(map(_key, again["nodes"])) == sorted(map(_key, exported["nodes"]))
    vectors = {item["node_id"]: item["embedding"] for item in exported["nodes"]}
    for item in again["nodes"]:
        assert item["embedding"] == pytest.approx(vectors[item["node_id"]], abs=1e-5)
    # Nothing of another user came along.
    assert _export(_client(target_config, target), "u2")["count"] == 0


def test_a_node_without_its_vector_is_re_embedded(stores):
    (source_config, source), (target_config, target) = stores
    _seed(source)
    exported = _export(_client(source_config, source), "u1", include_embeddings=False)
    embedder = _Embedder()
    imported = _import(_client(target_config, target, embedder), "u1", exported["nodes"])
    assert imported["success"] is True and imported["re_embedded"] == 5
    assert sorted(len(batch) for batch in embedder.calls) == [5]
    again = _export(_client(target_config, target), "u1")
    for item in again["nodes"]:
        assert item["embedding"] == pytest.approx(_vector(item["content"]), abs=1e-5)


def test_refusals_write_nothing(stores):
    (source_config, source), (target_config, target) = stores
    _seed(source)
    nodes = _export(_client(source_config, source), "u1")["nodes"]
    target_client = _client(target_config, target)

    wrong_user = _import(target_client, "someone", nodes)
    assert wrong_user["success"] is False and "another user_id" in wrong_user["error_message"]
    narrow = [dict(item, embedding=item["embedding"][:4]) for item in nodes]
    assert "does not match" in _import(target_client, "u1", narrow)["error_message"]
    assert _export(target_client, "u1")["count"] == 0

    assert _import(target_client, "u1", nodes[:1])["success"] is True
    occupied = _import(target_client, "u1", nodes[1:])
    assert occupied["success"] is False and "already has memories" in occupied["error_message"]
    assert _export(target_client, "u1")["count"] == 1


def test_a_failed_write_is_rolled_back(stores):
    (source_config, source), (target_config, target) = stores
    _seed(source)
    nodes = _export(_client(source_config, source), "u1")["nodes"]
    original = target.upsert_batch

    async def partial(batch):
        await original(batch[:2])
        raise RuntimeError("server went away")

    target.upsert_batch = partial
    try:
        result = _import(_client(target_config, target), "u1", nodes)
    finally:
        target.upsert_batch = original
    assert result["success"] is False and "server went away" in result["error_message"]
    assert _export(_client(target_config, target), "u1")["count"] == 0
