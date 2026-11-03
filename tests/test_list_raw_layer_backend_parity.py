"""Layer filters must not widen when raw access is disabled."""

import asyncio
import uuid

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode


@pytest.mark.parametrize("backend", ["faiss", "chroma", "qdrant"])
def test_raw_only_filter_does_not_return_other_layers(tmp_path, monkeypatch, backend):
    if backend == "faiss":
        from hy_memory.data.vector_store_faiss import FaissVectorStore as Store
    elif backend == "chroma":
        from hy_memory.data.vector_store_chroma import ChromaVectorStore as Store
    else:
        from hy_memory.data.vector_store_qdrant import QdrantVectorStore as Store
        from hy_memory.pipelines._retrieval import bm25_fastembed

        # Sparse BM25 is unrelated to layer filtering and may download a model.
        monkeypatch.setattr(bm25_fastembed, "is_available", lambda: False)

    config = MemoryConfig.from_dict(
        {
            "vector_store": {
                "provider": backend,
                "collection_name": "raw_layer_parity",
                "persist_directory": str(tmp_path / backend),
                "embedding_dims": 3,
            }
        }
    )
    store = Store(config)

    async def exercise():
        await store.initialize()
        try:
            assert (
                await store.list_by_user(user_id="u", layers=[MemoryLayer.L1_RAW]) == []
            )
            await store.upsert_batch(
                [
                    MemoryNode(
                        node_id=str(uuid.uuid4()),
                        user_id="u",
                        agent_id="a",
                        session_id="s",
                        content=content,
                        layer=layer,
                        embedding=[1.0, 0.0, 0.0],
                    )
                    for content, layer in [
                        ("raw", MemoryLayer.L1_RAW),
                        ("fact", MemoryLayer.L2_FACT),
                    ]
                ]
            )
            assert (
                await store.list_by_user(user_id="u", layers=[MemoryLayer.L1_RAW]) == []
            )
            assert (
                await store.list_by_user(user_id="u", layers=["l1_raw", "l1_raw"]) == []
            )
            allowed = await store.list_by_user(
                user_id="u",
                layers=[MemoryLayer.L1_RAW],
                allow_raw_layer=True,
            )
            assert [node.content for node in allowed] == ["raw"]
            mixed = await store.list_by_user(
                user_id="u",
                layers=[MemoryLayer.L1_RAW, MemoryLayer.L2_FACT],
            )
            assert [node.content for node in mixed] == ["fact"]
        finally:
            await store.close()

    asyncio.run(exercise())
