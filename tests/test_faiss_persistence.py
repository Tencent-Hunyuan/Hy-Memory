# -*- coding: utf-8 -*-
"""FAISS persistence must not let one failed save destroy the previous one.

Issue #17: `_persist()` wrote the index and payload straight over the live
files. A disk-full failure left a half-written index, and killing the process
after the JSON was opened for writing left an empty payload; both made the
next `initialize()` fail even though an earlier save had been complete.

The store now writes both into one container file whose header carries the
length and SHA-256 of the index bytes and the payload. One atomic replace
means the two can never come from different generations.
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


def _container(tmp_path, store) -> Path:
    return tmp_path / "faiss_data" / f"{store._collection_name}.omfaiss"


def test_a_failed_index_write_keeps_the_previous_generation(tmp_path, monkeypatch):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    container = _container(tmp_path, store)
    before = container.read_bytes()

    def fail_write(index, path):
        # FAISS truncates its target before writing; reproduce a disk-full
        # failure that leaves a partial file behind.
        Path(path).write_bytes(b"not a faiss index")
        raise OSError("No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(faiss, "write_index", fail_write)
        with pytest.raises(OSError):
            run(store.upsert(_node("b", 2)))

    assert container.read_bytes() == before
    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a"}


def test_a_failed_backup_aborts_before_replacing_the_live_container(
    tmp_path, monkeypatch
):
    """P1: no recovery point means no replacement.

    The old implementation logged a backup failure and replaced the live file
    anyway; a failed index rename then left no usable backup at all.
    """
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))
    container = _container(tmp_path, store)
    before = container.read_bytes()

    def unavailable(*args, **kwargs):
        raise OSError("backup unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(faiss_store, "_hard_link", unavailable)
        patch.setattr(faiss_store, "_copy_file", unavailable)
        with pytest.raises(OSError):
            run(store.upsert(_node("b", 2)))

    assert container.read_bytes() == before
    assert not (
        tmp_path / "faiss_data" / f"{store._collection_name}.omfaiss.bak"
    ).exists()
    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a"}


def test_a_corrupted_container_recovers_the_previous_generation(tmp_path):
    """P2: one file, so a live container cannot mix generations.

    The backup holds the complete generation before the last save. A corrupted
    live container must not be accepted just because its counts happen to look
    plausible.
    """
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))
    run(store.upsert(_node("b", 2)))

    container = _container(tmp_path, store)
    damaged = bytearray(container.read_bytes())
    damaged[-1] ^= 0xFF
    container.write_bytes(damaged)

    assert faiss_store._load_container(faiss, container) is None

    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a"}

    # The restore repaired the live container; the next open is a normal one.
    again = _store(tmp_path)
    run(again.initialize())
    assert _ids(again) == {"a"}


def test_a_failed_restore_does_not_stage_the_damaged_file_as_backup(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))
    run(store.upsert(_node("b", 2)))
    container = _container(tmp_path, store)
    backup = container.with_name(container.name + ".bak")
    damaged = bytearray(container.read_bytes())
    damaged[-1] ^= 0xFF
    container.write_bytes(damaged)

    def unavailable(*args, **kwargs):
        raise OSError("restore unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(faiss_store, "_copy_file", unavailable)
        reopened = _store(tmp_path)
        run(reopened.initialize())
    assert _ids(reopened) == {"a"}

    # The live container is still damaged, but the next save must not snapshot
    # it over the good backup before replacing it.
    run(reopened.upsert(_node("c", 3)))
    loaded = faiss_store._load_container(faiss, backup)
    assert loaded is not None
    _index, saved = loaded
    assert {entry["payload"]["node_id"] for entry in saved["data"].values()} == {"a"}


def test_a_successful_save_reopens_with_every_record(tmp_path):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))
    run(store.upsert(_node("b", 2)))

    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a", "b"}


def test_a_corrupted_container_without_a_backup_is_refused(tmp_path):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    container = _container(tmp_path, store)
    container.write_bytes(container.read_bytes()[:-4])

    with pytest.raises(RuntimeError, match="cannot be loaded consistently"):
        run(_store(tmp_path).initialize())


def test_a_legacy_pair_still_loads_and_becomes_a_container_on_the_next_save(
    tmp_path,
):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    container = _container(tmp_path, store)
    loaded = faiss_store._load_container(faiss, container)
    assert loaded is not None
    index, saved = loaded
    data_dir = container.parent
    legacy_index = data_dir / f"{store._collection_name}.faiss"
    legacy_payload = data_dir / f"{store._collection_name}.json"
    faiss.write_index(index, str(legacy_index))
    legacy_payload.write_text(json.dumps(saved), encoding="utf-8")
    container.unlink()

    reopened = _store(tmp_path)
    run(reopened.initialize())
    assert _ids(reopened) == {"a"}
    assert not container.exists()

    run(reopened.upsert(_node("b", 2)))
    assert container.exists()

    again = _store(tmp_path)
    run(again.initialize())
    assert _ids(again) == {"a", "b"}


def test_the_payload_and_index_describe_the_same_generation(tmp_path):
    store = _store(tmp_path)
    run(store.initialize())
    run(store.upsert(_node("a", 1)))

    container = _container(tmp_path, store)
    loaded = faiss_store._load_container(faiss, container)
    assert loaded is not None
    index, payload = loaded

    assert faiss_store._pair_is_consistent(index, payload)
    payload["next_idx"] += 1
    assert not faiss_store._pair_is_consistent(index, payload)
