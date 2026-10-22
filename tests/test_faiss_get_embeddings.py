# -*- coding: utf-8 -*-
"""FAISS 后端的 get_embeddings 必须能取回普通 upsert 写入的向量。

``_data`` 只保存 payload 与内部下标，向量本体在 FAISS index 里。去重和
episode 摘要依赖 get_embeddings，取不到向量会让这些能力静默失效。
"""

import asyncio

import pytest

pytest.importorskip("faiss")

from hy_memory.config import MemoryConfig  # noqa: E402
from hy_memory.data.vector_store_faiss import FaissVectorStore  # noqa: E402
from hy_memory.models.memory import MemoryLayer, MemoryNode  # noqa: E402


def _config(tmp_path):
    return MemoryConfig.from_dict({
        "vector_store": {
            "provider": "faiss",
            "collection_name": "get_embeddings_test",
            "persist_directory": str(tmp_path / "faiss"),
            "embedding_dims": 4,
        },
    })


def test_get_embeddings_reconstructs_upserted_and_updated_vectors(tmp_path):
    async def _run():
        store = FaissVectorStore(_config(tmp_path))
        await store.initialize()
        try:
            node = MemoryNode(
                user_id="u1",
                agent_id="a1",
                session_id="s1",
                source="default",
                layer=MemoryLayer.L1_RAW,
                content="hello",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
            await store.upsert(node)

            got = await store.get_embeddings([node.node_id])
            assert got[node.node_id] == pytest.approx([1.0, 0.0, 0.0, 0.0])

            assert await store.update_embedding(node.node_id, [0.0, 1.0, 0.0, 0.0])
            got = await store.get_embeddings([node.node_id])
            assert got[node.node_id] == pytest.approx([0.0, 1.0, 0.0, 0.0])

            assert await store.get_embeddings(["missing"]) == {}
        finally:
            await store.close()

    asyncio.run(_run())
