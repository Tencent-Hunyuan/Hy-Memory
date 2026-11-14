from types import SimpleNamespace
from uuid import uuid4

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_qdrant import QdrantVectorStore
from hy_memory.models.memory import MemoryLayer

qdrant_models = pytest.importorskip("qdrant_client.models")


class _FakeQdrant:
    def __init__(self, *, sparse_modifier=None, server_version="1.19.1"):
        self.sparse_modifier = sparse_modifier
        self.server_version = server_version
        self.created = 0
        self.deleted = 0
        self.update_calls = 0
        self.records = [
            SimpleNamespace(
                id=str(uuid4()),
                payload={
                    "layer": MemoryLayer.L2_FACT.value,
                    "content": "GAMMA-9955 unique marker",
                    "search_text": "GAMMA-9955 unique marker",
                },
                vector=[1.0, 0.0],
            ),
            SimpleNamespace(
                id=str(uuid4()),
                payload={
                    "layer": MemoryLayer.L1_RAW.value,
                    "content": "raw conversation",
                },
                vector=[0.0, 1.0],
            ),
        ]

    def info(self):
        return SimpleNamespace(version=self.server_version)

    def get_collection(self, _collection_name):
        sparse = {}
        if self.sparse_modifier is not None:
            sparse["bm25"] = SimpleNamespace(modifier=self.sparse_modifier)
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(sparse_vectors=sparse),
            )
        )

    def count(self, *, collection_name, count_filter, exact):
        del collection_name, exact
        wants_sparse = any(
            getattr(condition, "has_vector", None) == "bm25"
            for condition in (count_filter.must or [])
        )
        count = 0
        for record in self.records:
            if record.payload.get("layer") == MemoryLayer.L1_RAW.value:
                continue
            if wants_sparse and not (
                isinstance(record.vector, dict) and "bm25" in record.vector
            ):
                continue
            count += 1
        return SimpleNamespace(count=count)

    def create_vector_name(
        self,
        *,
        collection_name,
        vector_name,
        vector_name_config,
        wait,
    ):
        del collection_name, vector_name, wait
        self.sparse_modifier = vector_name_config.sparse.modifier
        self.created += 1

    def delete_vector_name(self, *, collection_name, vector_name, wait):
        del collection_name, vector_name, wait
        self.sparse_modifier = None
        for record in self.records:
            if isinstance(record.vector, dict):
                record.vector.pop("bm25", None)
        self.deleted += 1

    def update_collection(self, *, collection_name, sparse_vectors_config):
        del collection_name
        self.sparse_modifier = sparse_vectors_config["bm25"].modifier

    def scroll(
        self,
        *,
        collection_name,
        limit,
        offset,
        with_payload,
        with_vectors,
    ):
        del collection_name, with_payload, with_vectors
        start = int(offset or 0)
        page = self.records[start : start + limit]
        next_offset = start + limit if start + limit < len(self.records) else None
        return page, next_offset

    def update_vectors(self, *, collection_name, points, wait):
        del collection_name, wait
        by_id = {record.id: record for record in self.records}
        for point in points:
            record = by_id[str(point.id)]
            dense = (
                record.vector
                if not isinstance(record.vector, dict)
                else record.vector[""]
            )
            record.vector = {"": dense, **point.vector}
        self.update_calls += 1


def _store(client, *, is_local=False):
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = client
    store._collection_name = "memhub"
    store._sparse_name = "bm25"
    store._supports_fulltext = False
    store._bm25_schema_ready = False
    store._is_local = is_local
    return store


@pytest.fixture(autouse=True)
def _direct_executor(monkeypatch):
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "hy_memory.data.vector_store_qdrant._run_in_vdb_pool",
        direct,
    )
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.encode_docs",
        lambda texts: [([index + 1], [1.0]) for index, _text in enumerate(texts)],
    )
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.encode_doc",
        lambda _text: ([1], [1.0]),
    )
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.encode_query",
        lambda _text: ([1], [1.0]),
    )


@pytest.mark.asyncio
async def test_dense_only_collection_reports_an_upgrade_plan():
    plan = await _store(_FakeQdrant()).plan_bm25_upgrade()

    assert plan["state"] == "upgrade_required"
    assert plan["reason"] == "sparse_schema_missing"
    assert plan["eligible_points"] == 1
    assert plan["sparse_points"] == 0
    assert plan["missing_points"] == 1
    assert plan["can_upgrade"] is True


@pytest.mark.asyncio
async def test_plan_does_not_load_or_download_the_bm25_model(monkeypatch):
    def unexpected_load():
        raise AssertionError("read-only plan must not load the model")

    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.is_available",
        unexpected_load,
    )

    plan = await _store(_FakeQdrant()).plan_bm25_upgrade()

    assert plan["state"] == "upgrade_required"


@pytest.mark.asyncio
async def test_old_qdrant_server_is_blocked_before_schema_changes():
    client = _FakeQdrant(server_version="1.17.9")

    result = await _store(client).upgrade_bm25(writers_stopped=True)

    assert result["success"] is False
    assert result["state"] == "blocked"
    assert result["reason"] == "qdrant_vector_schema_api_unavailable"
    assert result["server_version"] == "1.17.9"
    assert client.created == 0


@pytest.mark.asyncio
async def test_prerelease_qdrant_server_is_blocked_before_schema_changes():
    client = _FakeQdrant(server_version="1.18.0-rc.1")

    result = await _store(client).upgrade_bm25(writers_stopped=True)

    assert result["success"] is False
    assert result["reason"] == "qdrant_vector_schema_api_unavailable"
    assert client.created == 0


@pytest.mark.asyncio
async def test_upgrade_requires_an_explicit_stopped_writer_boundary():
    with pytest.raises(ValueError, match="writers_stopped"):
        await _store(_FakeQdrant()).upgrade_bm25(writers_stopped=False)


@pytest.mark.asyncio
async def test_upgrade_adds_idf_schema_and_backfills_without_touching_raw():
    client = _FakeQdrant()
    store = _store(client)

    result = await store.upgrade_bm25(writers_stopped=True, batch_size=1)

    assert result["success"] is True
    assert result["state"] == "ready"
    assert result["processed_points"] == 1
    assert result["skipped_raw"] == 1
    assert client.created == 1
    assert client.deleted == 0
    assert client.update_calls == 1
    assert client.sparse_modifier == qdrant_models.Modifier.IDF
    assert isinstance(client.records[0].vector, dict)
    assert client.records[0].vector[""] == [1.0, 0.0]
    assert "bm25" in client.records[0].vector
    assert client.records[1].vector == [0.0, 1.0]
    assert store._supports_fulltext is True

    repeated = await store.upgrade_bm25(writers_stopped=True)
    assert repeated["success"] is True
    assert repeated["changed"] is False
    assert repeated["processed_points"] == 0
    assert client.update_calls == 1


def test_schema_ready_writer_populates_sparse_while_reader_is_disabled():
    store = _store(_FakeQdrant())
    store._bm25_schema_ready = True
    store._supports_fulltext = False

    vector = store._build_vector_field(
        {
            "layer": MemoryLayer.L2_FACT.value,
            "search_text": "new write during maintenance",
        },
        [1.0, 0.0],
    )

    assert vector[""] == [1.0, 0.0]
    assert vector["bm25"].indices == [1]


def test_schema_ready_writer_marks_empty_text_with_empty_sparse():
    store = _store(_FakeQdrant())
    store._bm25_schema_ready = True
    store._supports_fulltext = False

    vector = store._build_vector_field(
        {
            "layer": MemoryLayer.L2_FACT.value,
            "search_text": "   ",
            "content": "",
        },
        [1.0, 0.0],
    )

    assert vector[""] == [1.0, 0.0]
    assert vector["bm25"].indices == []
    assert vector["bm25"].values == []


@pytest.mark.asyncio
async def test_non_idf_sparse_schema_is_rebuilt_before_backfill():
    client = _FakeQdrant(sparse_modifier="none")

    result = await _store(client).upgrade_bm25(writers_stopped=True)

    assert result["success"] is True
    assert client.deleted == 1
    assert client.created == 1
    assert client.sparse_modifier == qdrant_models.Modifier.IDF


@pytest.mark.asyncio
async def test_local_non_idf_sparse_schema_is_updated_in_place():
    client = _FakeQdrant(sparse_modifier="none")

    result = await _store(client, is_local=True).upgrade_bm25(
        writers_stopped=True
    )

    assert result["success"] is True
    assert client.deleted == 0
    assert client.created == 0
    assert client.sparse_modifier == qdrant_models.Modifier.IDF


@pytest.mark.asyncio
async def test_zero_token_document_gets_empty_sparse_marker(monkeypatch):
    client = _FakeQdrant()
    store = _store(client)
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.encode_docs",
        lambda texts: [None for _text in texts],
    )

    result = await store.upgrade_bm25(writers_stopped=True)

    assert result["success"] is True
    assert result["reason"] == "ready"
    assert result["skipped_unencodable"] == 1
    assert store._supports_fulltext is True
    assert client.records[0].vector["bm25"].indices == []


@pytest.mark.asyncio
async def test_encoder_exception_is_retryable_and_does_not_mark_coverage(
    monkeypatch,
):
    client = _FakeQdrant()
    store = _store(client)

    def fail(_texts):
        raise RuntimeError("transient encoder failure")

    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.encode_docs",
        fail,
    )

    result = await store.upgrade_bm25(writers_stopped=True)

    assert result["success"] is False
    assert result["reason"] == "bm25_upgrade_failed"
    assert result["error_type"] == "RuntimeError"
    assert store._supports_fulltext is False
    assert client.records[0].vector == [1.0, 0.0]


@pytest.mark.asyncio
async def test_empty_text_gets_empty_sparse_marker_and_reports_skip():
    client = _FakeQdrant()
    client.records[0].payload["content"] = ""
    client.records[0].payload["search_text"] = ""

    result = await _store(client).upgrade_bm25(writers_stopped=True)

    assert result["success"] is True
    assert result["state"] == "ready"
    assert result["eligible_points"] == 1
    assert result["sparse_points"] == 1
    assert result["skipped_empty"] == 1
    assert client.records[0].vector["bm25"].indices == []


@pytest.mark.asyncio
async def test_unencodable_point_is_marked_and_does_not_block_later_batches(
    monkeypatch,
):
    client = _FakeQdrant()
    client.records.append(
        SimpleNamespace(
            id=str(uuid4()),
            payload={
                "layer": MemoryLayer.L2_FACT.value,
                "content": "second fact",
                "search_text": "second fact",
            },
            vector=[0.5, 0.5],
        )
    )
    calls = 0

    def encode(texts):
        nonlocal calls
        if not texts:
            return []
        calls += 1
        return [None] if calls == 1 else [([2], [1.0])]

    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.encode_docs",
        encode,
    )

    result = await _store(client).upgrade_bm25(
        writers_stopped=True,
        batch_size=1,
    )

    assert result["success"] is True
    assert result["reason"] == "ready"
    assert result["processed_points"] == 2
    assert result["skipped_unencodable"] == 1
    assert client.records[0].vector["bm25"].indices == []
    assert "bm25" in client.records[-1].vector


@pytest.mark.asyncio
async def test_failure_reports_points_committed_before_the_error():
    class FailingSecondBatch(_FakeQdrant):
        def update_vectors(self, **kwargs):
            if self.update_calls:
                raise RuntimeError("remote write failed")
            super().update_vectors(**kwargs)

    client = FailingSecondBatch()
    client.records.append(
        SimpleNamespace(
            id=str(uuid4()),
            payload={
                "layer": MemoryLayer.L2_FACT.value,
                "content": "second fact",
                "search_text": "second fact",
            },
            vector=[0.5, 0.5],
        )
    )

    result = await _store(client).upgrade_bm25(
        writers_stopped=True,
        batch_size=1,
    )

    assert result["success"] is False
    assert result["reason"] == "bm25_upgrade_failed"
    assert result["processed_points"] == 1
    assert result["changed"] is True


@pytest.mark.asyncio
async def test_local_qdrant_dense_collection_is_upgraded_in_place(tmp_path):
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    path = tmp_path / "qdrant"
    config = MemoryConfig.from_dict(
        {
            "vector_store": {
                "provider": "qdrant",
                "collection_name": "legacy_dense",
                "persist_directory": str(path),
                "host": "",
                "port": 6333,
                "embedding_dims": 2,
            }
        }
    )
    store = QdrantVectorStore(config)
    collection = store._collection_name
    client = QdrantClient(path=str(path))
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=2, distance=Distance.COSINE),
    )
    point_id = str(uuid4())
    empty_point_id = str(uuid4())
    client.upsert(
        collection_name=collection,
        points=[
            PointStruct(
                id=point_id,
                vector=[1.0, 0.0],
                payload={
                    "node_id": point_id,
                    "layer": MemoryLayer.L2_FACT.value,
                    "content": "GAMMA-9955",
                    "search_text": "GAMMA-9955",
                    "status": "active",
                    "is_latest": True,
                },
            ),
            PointStruct(
                id=empty_point_id,
                vector=[0.0, 1.0],
                payload={
                    "node_id": empty_point_id,
                    "layer": MemoryLayer.L2_FACT.value,
                    "content": "",
                    "search_text": "",
                    "status": "active",
                    "is_latest": True,
                },
            ),
        ],
    )
    client.close()

    await store.initialize()
    try:
        assert store._supports_fulltext is False
        result = await store.upgrade_bm25(writers_stopped=True)
        assert result["success"] is True
        assert result["state"] == "ready"
        assert result["processed_points"] == 2
        assert result["skipped_empty"] == 1

        records = store._client.retrieve(
            collection_name=collection,
            ids=[point_id, empty_point_id],
            with_payload=True,
            with_vectors=True,
        )
        record = next(row for row in records if str(row.id) == point_id)
        empty_record = next(
            row for row in records if str(row.id) == empty_point_id
        )
        assert record.vector[""] == [1.0, 0.0]
        assert "bm25" in record.vector
        assert record.payload["content"] == "GAMMA-9955"
        assert empty_record.vector[""] == [0.0, 1.0]
        assert empty_record.vector["bm25"].indices == []
        hits = await store.keyword_search(
            "GAMMA-9955",
            top_k=1,
            only_latest=False,
        )
        assert [hit["node_id"] for hit in hits] == [point_id]

        repeated = await store.upgrade_bm25(writers_stopped=True)
        assert repeated["success"] is True
        assert repeated["changed"] is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_local_qdrant_non_idf_schema_is_upgraded_in_place(tmp_path):
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        PointStruct,
        SparseVectorParams,
        VectorParams,
    )

    path = tmp_path / "qdrant"
    config = MemoryConfig.from_dict(
        {
            "vector_store": {
                "provider": "qdrant",
                "collection_name": "legacy_sparse",
                "persist_directory": str(path),
                "host": "",
                "port": 6333,
                "embedding_dims": 2,
            }
        }
    )
    store = QdrantVectorStore(config)
    collection = store._collection_name
    client = QdrantClient(path=str(path))
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=2, distance=Distance.COSINE),
        sparse_vectors_config={"bm25": SparseVectorParams()},
    )
    point_id = str(uuid4())
    client.upsert(
        collection_name=collection,
        points=[
            PointStruct(
                id=point_id,
                vector=[1.0, 0.0],
                payload={
                    "node_id": point_id,
                    "layer": MemoryLayer.L2_FACT.value,
                    "content": "DELTA-7788",
                    "search_text": "DELTA-7788",
                    "status": "active",
                    "is_latest": True,
                },
            )
        ],
    )
    client.close()

    await store.initialize()
    try:
        assert store._supports_fulltext is False
        result = await store.upgrade_bm25(writers_stopped=True)
        assert result["success"] is True
        assert result["state"] == "ready"

        info = store._client.get_collection(collection)
        sparse = info.config.params.sparse_vectors["bm25"]
        assert sparse.modifier == qdrant_models.Modifier.IDF
        record = store._client.retrieve(
            collection_name=collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )[0]
        assert record.vector[""] == [1.0, 0.0]
        assert "bm25" in record.vector
        assert record.payload["content"] == "DELTA-7788"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_public_async_api_reports_non_qdrant_as_not_applicable():
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._vector_store = object()
    client._config = SimpleNamespace(
        vector_store=SimpleNamespace(provider="chroma"),
    )

    plan = await HyMemoryClient.async_plan_qdrant_bm25_upgrade.__wrapped__(client)
    result = await HyMemoryClient.async_upgrade_qdrant_bm25.__wrapped__(
        client,
        writers_stopped=True,
    )

    assert plan["state"] == "not_applicable"
    assert plan["reason"] == "vector_store_is_not_qdrant"
    assert result["success"] is False
    assert result["changed"] is False


@pytest.mark.asyncio
async def test_public_async_api_delegates_to_qdrant_maintenance_contract():
    calls = []

    class Store:
        async def plan_bm25_upgrade(self):
            calls.append(("plan",))
            return {"state": "upgrade_required"}

        async def upgrade_bm25(self, *, writers_stopped, batch_size):
            calls.append(("upgrade", writers_stopped, batch_size))
            return {"success": True}

    client = HyMemoryClient.__new__(HyMemoryClient)
    client._vector_store = Store()

    plan = await HyMemoryClient.async_plan_qdrant_bm25_upgrade.__wrapped__(client)
    result = await HyMemoryClient.async_upgrade_qdrant_bm25.__wrapped__(
        client,
        writers_stopped=True,
        batch_size=17,
    )

    assert plan == {"state": "upgrade_required"}
    assert result == {"success": True}
    assert calls == [("plan",), ("upgrade", True, 17)]
