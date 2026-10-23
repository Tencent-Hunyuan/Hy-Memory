# -*- coding: utf-8 -*-
"""FAISS persistence must not let one failed save destroy the previous one.

Issue #17: `_persist()` wrote the index and payload straight over the live
files. A disk-full failure left a half-written index, and killing the process
after the JSON was opened for writing left an empty payload; both made the
next `initialize()` fail even though an earlier save had been complete.
"""

import asyncio
import json
from pathlib import Path

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus

faiss_store = pytest.importorskip("hy_memory.data.vector_store_faiss")
faiss = pytest.importorskip("faiss")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _store(tmp_path) -> "faiss_store.FaissVectorStore":
    config = MemoryConfig.from_dict(
        {
            "vector_store": {
                "provider": "faiss",
                "collection_name": "persistence",
                "persist_directory": str(tmp_path / "faiss_data"),
                "embedding_dims": 8,
            }
        }
    )
    return faiss_store.FaissVectorStore(config)


def _node(node_id: str, offset: int) -> MemoryNode:
    return MemoryNode(
        node_id=node_id,
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        content=f"memory {node_id}",
        layer=MemoryLayer.L2_FACT,
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        embedding=[float(offset + index) for index in range(8)],
    )


def _ids(store) -> set[str]:
    return {node.node_id for node in run(store.list_by_user(user_id="u1"))}


def _crash_after_payload(patch, module) -> None:
    real_replace = module._atomic_replace
    calls = {"count": 0}

    def crash(source, destination):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("power loss")
        return real_replace(source, destination)

    patch.setattr(module, "_atomic_replace", crash)


def test_a_failed_index_write_keeps_the_previous_files(tmp_path, monkeypatch):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    data_dir = tmp_path / "faiss_data"
    assert (data_dir / f"{store._collection_name}.faiss").exists()
    assert (data_dir / f"{store._collection_name}.json").exists()

    def fail_write(index, path):
        # FAISS truncates its target before writing; reproduce a disk-full
        # failure that leaves a partial file behind.
        Path(path).write_bytes(b"not a faiss index")
        raise OSError("No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(faiss, "write_index", fail_write)
        with pytest.raises(OSError):
            run(store.upsert(_node("b", 2)))

    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a"}


def test_a_crash_between_the_two_renames_recovers_the_previous_generation(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    with monkeypatch.context() as patch:
        _crash_after_payload(patch, faiss_store)
        with pytest.raises(OSError):
            run(store.upsert(_node("b", 2)))

    # The live pair is now new payload + old index. It is inconsistent, so the
    # loader must fall back to the complete pre-save backup and restore it.
    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a"}

    # The restore repaired the live pair; a later open is a normal one.
    again = _store(tmp_path)
    run(again.initialize())
    assert _ids(again) == {"a"}


def test_a_second_failed_save_does_not_destroy_the_last_good_backup(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    # Two saves in a row die between the payload and index renames. The first
    # leaves a mixed live pair; the second must not back that mixture up over
    # the complete pre-save generation.
    for index, node_id in enumerate(("b", "c")):
        with monkeypatch.context() as patch:
            _crash_after_payload(patch, faiss_store)
            with pytest.raises(OSError):
                run(store.upsert(_node(node_id, index + 2)))

    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a"}


def test_a_successful_save_reopens_with_every_record(tmp_path):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))
    run(store.upsert(_node("b", 2)))

    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a", "b"}


def test_an_incomplete_pair_without_a_backup_is_refused(tmp_path):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    (tmp_path / "faiss_data" / f"{store._collection_name}.json").unlink()

    with pytest.raises(RuntimeError, match="cannot be loaded consistently"):
        run(_store(tmp_path).initialize())


def test_the_persisted_payload_and_index_describe_the_same_generation(tmp_path):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    data_dir = tmp_path / "faiss_data"
    payload = json.loads((data_dir / f"{store._collection_name}.json").read_text())
    index = faiss.read_index(str(data_dir / f"{store._collection_name}.faiss"))

    assert faiss_store._pair_is_consistent(index, payload)
    payload["next_idx"] += 1
    assert not faiss_store._pair_is_consistent(index, payload)
