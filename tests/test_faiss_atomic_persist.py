"""一次失败的保存不能破坏上一次已经提交的 FAISS collection（#17）。"""

import asyncio
import json
from copy import deepcopy

import pytest

pytest.importorskip("faiss")

import faiss

from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_faiss import FaissVectorStore
from hy_memory.models.memory import MemoryLayer, MemoryNode


def _config(tmp_path):
    return MemoryConfig.from_dict({
        "vector_store": {
            "provider": "faiss",
            "collection_name": "atomic_persist",
            "persist_directory": str(tmp_path),
            "embedding_dims": 4,
        },
    })


def _node(content="original", embedding=None):
    return MemoryNode(
        user_id="test-user", agent_id="test-agent", session_id="test-session",
        layer=MemoryLayer.L2_FACT, content=content,
        embedding=embedding if embedding is not None else [1.0, 0.0, 0.0, 0.0],
    )


def _pair(tmp_path):
    index_path = tmp_path / "atomic_persist_4.faiss"
    payload_path = tmp_path / "atomic_persist_4.json"
    return index_path, payload_path


def _committed_bytes(tmp_path):
    index_path, payload_path = _pair(tmp_path)
    return index_path.read_bytes(), payload_path.read_bytes()


async def _store_with_one_memory(tmp_path):
    store = FaissVectorStore(_config(tmp_path))
    await store.initialize()
    original = _node()
    await store.upsert(original)
    return store, original


def test_interrupted_index_write_keeps_the_committed_pair(tmp_path, monkeypatch):
    async def run():
        store, original = await _store_with_one_memory(tmp_path)
        before = _committed_bytes(tmp_path)

        def failing_write(index, path):
            # The index file is opened and truncated before the write fails.
            with open(path, "wb") as f:
                f.write(b"\x00\x00")
            raise RuntimeError("No space left on device")

        monkeypatch.setattr(faiss, "write_index", failing_write)
        with pytest.raises(RuntimeError):
            await store.upsert(_node("second", [0.0, 1.0, 0.0, 0.0]))
        assert _committed_bytes(tmp_path) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "atomic_persist_4.faiss", "atomic_persist_4.json",
        ]
        monkeypatch.undo()
        # The store is deliberately not closed: an interrupted process has no
        # chance to save again, which is the case this test is about.

        reopened = FaissVectorStore(_config(tmp_path))
        await reopened.initialize()
        try:
            hits = await reopened.search(original.embedding, user_id="test-user")
            assert [(hit["node_id"], hit["node"].content) for hit in hits] == [
                (original.node_id, "original"),
            ]
        finally:
            await reopened.close()

    asyncio.run(run())


def test_interrupted_payload_write_keeps_the_committed_pair(tmp_path, monkeypatch):
    async def run():
        store, original = await _store_with_one_memory(tmp_path)
        before = _committed_bytes(tmp_path)

        def failing_dump(obj, f, **kwargs):
            # The live file used to be truncated before this raised.
            f.write('{"data":')
            raise RuntimeError("payload serialization failed")

        monkeypatch.setattr(json, "dump", failing_dump)
        with pytest.raises(RuntimeError):
            await store.upsert(_node("second", [0.0, 1.0, 0.0, 0.0]))
        assert _committed_bytes(tmp_path) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "atomic_persist_4.faiss", "atomic_persist_4.json",
        ]
        monkeypatch.undo()

        reopened = FaissVectorStore(_config(tmp_path))
        await reopened.initialize()
        try:
            hits = await reopened.search(original.embedding, user_id="test-user")
            assert [hit["node"].content for hit in hits] == ["original"]
        finally:
            await reopened.close()

    asyncio.run(run())


def test_payload_behind_the_index_does_not_reuse_internal_ids(tmp_path):
    """payload 落后于 index 时，后续写入的 id 不能撞上索引里的既有位置。"""

    async def run():
        store = FaissVectorStore(_config(tmp_path))
        await store.initialize()
        try:
            first = _node("first", [1.0, 0.0, 0.0, 0.0])
            second = _node("second", [0.0, 1.0, 0.0, 0.0])
            await store.upsert(first)
            await store.upsert(second)
        finally:
            await store.close()

        # 模拟"index 已提交、payload 还停在上一版"：索引里有 2 个向量，
        # payload 只认第一个，并声称 next_idx=1。
        index_path, payload_path = _pair(tmp_path)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        stale = {
            "data": {k: v for k, v in payload["data"].items()},
            "idx_to_id": {"0": payload["idx_to_id"]["0"]},
            "next_idx": 1,
        }
        payload_path.write_text(json.dumps(stale), encoding="utf-8")

        reopened = FaissVectorStore(_config(tmp_path))
        await reopened.initialize()
        try:
            assert reopened._next_idx == reopened._index.ntotal == 2
            third = _node("third", [0.0, 0.0, 1.0, 0.0])
            await reopened.upsert(third)
            hits = await reopened.search(third.embedding, user_id="test-user")
            assert hits[0]["node_id"] == third.node_id
            assert hits[0]["node"].content == "third"
        finally:
            await reopened.close()

    asyncio.run(run())


def test_payload_ahead_of_the_index_drops_points_without_a_vector(tmp_path):
    """payload 比 index 新时，没有向量的条目按未提交处理，内存视图与索引一致。"""

    async def run():
        store, original = await _store_with_one_memory(tmp_path)
        await store.close()

        index_path, payload_path = _pair(tmp_path)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        ghost = deepcopy(payload["data"][original.node_id])
        ghost["idx"] = 1
        ghost["payload"] = dict(ghost["payload"], content="never committed")
        payload["data"]["ghost-point"] = ghost
        payload["idx_to_id"]["1"] = "ghost-point"
        payload["next_idx"] = 2
        payload_path.write_text(json.dumps(payload), encoding="utf-8")

        reopened = FaissVectorStore(_config(tmp_path))
        await reopened.initialize()
        try:
            assert reopened._index.ntotal == 1
            assert reopened._next_idx == 1
            assert "ghost-point" not in reopened._data
            assert 1 not in reopened._idx_to_id
            hits = await reopened.search(original.embedding, user_id="test-user")
            assert [hit["node"].content for hit in hits] == ["original"]
            following = _node("following", [0.0, 1.0, 0.0, 0.0])
            await reopened.upsert(following)
            hits = await reopened.search(following.embedding, user_id="test-user")
            assert hits[0]["node_id"] == following.node_id
        finally:
            await reopened.close()

    asyncio.run(run())


def test_half_a_pair_is_refused_instead_of_reset_to_empty(tmp_path):
    """只剩一个文件时不能静默建空库，否则下一次保存会覆盖掉剩下的数据。"""

    async def run():
        store, _ = await _store_with_one_memory(tmp_path)
        await store.close()

        index_path, payload_path = _pair(tmp_path)
        payload_path.unlink()
        committed_index = index_path.read_bytes()

        broken = FaissVectorStore(_config(tmp_path))
        with pytest.raises(RuntimeError, match=r"atomic_persist_4\.json"):
            await broken.initialize()
        assert index_path.read_bytes() == committed_index

    asyncio.run(run())
