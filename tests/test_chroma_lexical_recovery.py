"""Authoritative writes and recoverable local lexical projection behavior."""

import asyncio
import multiprocessing
import os
import sqlite3
import threading

from filelock import FileLock
import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_chroma import ChromaVectorStore
from hy_memory.models.memory import MemoryLayer, MemoryNode


def _config(directory, *, enabled=True, collection="lexical_recovery"):
    return MemoryConfig.from_dict({"vector_store": {
        "provider": "chroma",
        "persist_directory": str(directory),
        "collection_name": collection,
        "embedding_dims": 4,
        "chroma_lexical_enabled": enabled,
    }})


def _node(node_id="node", content="OLDTOKEN repairs the socket"):
    return MemoryNode(
        node_id=node_id, user_id="u1", agent_id="a1", session_id="s1",
        layer=MemoryLayer.L2_FACT, content=content, embedding=[1.0, 0.0, 0.0, 0.0],
    )


async def _open(directory, *, enabled=True, collection="lexical_recovery"):
    store = ChromaVectorStore(_config(directory, enabled=enabled, collection=collection))
    await store.initialize()
    return store


async def _ids(store, query, top_k=10):
    return [hit["node_id"] for hit in await store.keyword_search(query, user_id="u1", top_k=top_k)]


@pytest.mark.parametrize("vector_method", ["update_embedding", "update_payload"])
@pytest.mark.parametrize("first_writer", ["content", "vector"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_content_and_vector_updates_serialize_across_clients(
    tmp_path, monkeypatch, vector_method, first_writer, enabled,
):
    content_store = await _open(tmp_path, enabled=enabled)
    vector_store = await _open(tmp_path, enabled=False)
    entered, release, competing_started, competing_entered = (
        threading.Event() for _ in range(4)
    )
    release_content_read = threading.Event()
    tasks = []
    original_get = content_store._collection.get
    original_update = vector_store._collection.update
    new_vector = [0.0, 1.0, 0.0, 0.0]

    def paused_read(**kwargs):
        result = original_get(**kwargs)
        if kwargs.get("include") == ["embeddings"]:
            if first_writer == "vector":
                competing_entered.set()
                if not release_content_read.wait(10):
                    raise TimeoutError("competing content read was not released")
            if first_writer == "content":
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("content read was not released")
        return result

    def paused_vector_write(**kwargs):
        if first_writer == "content":
            competing_entered.set()
        if first_writer == "vector":
            entered.set()
            if not release.wait(10):
                raise TimeoutError("vector write was not released")
        return original_update(**kwargs)

    async def write_content():
        return await content_store.update_payload("node", {"content": "NEWTOKEN"})

    async def write_vector():
        if vector_method == "update_embedding":
            return await vector_store.update_embedding("node", new_vector)
        return await vector_store.update_payload("node", {"embedding": new_vector})

    try:
        await content_store.upsert(_node())
        monkeypatch.setattr(content_store._collection, "get", paused_read)
        monkeypatch.setattr(vector_store._collection, "update", paused_vector_write)
        first, second = (
            (write_content, write_vector) if first_writer == "content"
            else (write_vector, write_content)
        )
        tasks.append(asyncio.create_task(first()))
        assert await asyncio.to_thread(entered.wait, 5)

        # Observe executor submission, then give the competing worker time to
        # reach the lock; all data operations still use the real Chroma client.
        import hy_memory.data.vector_store_chroma as module
        original_run = module._run_in_vdb_pool

        async def competing_run(func, *args, **kwargs):
            def started():
                competing_started.set()
                return func(*args, **kwargs)
            return await original_run(started)

        monkeypatch.setattr(module, "_run_in_vdb_pool", competing_run)
        tasks.append(asyncio.create_task(second()))
        assert await asyncio.to_thread(competing_started.wait, 5)
        serialized = not await asyncio.to_thread(competing_entered.wait, 0.2)
        if first_writer == "content" and not serialized:
            # On the broken version, commit the vector before letting the
            # content writer restore the stale value it has already read.
            assert await tasks[1]
        release.set()
        if first_writer == "vector":
            # The vector writer can also start before the content read. Keep
            # that read paused until the new vector has committed, exposing
            # the opposite lost-update interleaving on the broken version.
            assert await tasks[0]
        release_content_read.set()
        assert all(await asyncio.gather(*tasks))
        assert competing_entered.is_set()
        result = await content_store.get_by_id("node")
        assert result.content == "NEWTOKEN"
        assert result.embedding == new_vector
        assert serialized, "competing writer entered the collection critical section"
        if enabled:
            assert await _ids(content_store, "NEWTOKEN") == ["node"]
    finally:
        release.set()
        release_content_read.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await content_store.close()
        await vector_store.close()


def _hold_lock(lock_path, acquired, release):
    with FileLock(lock_path, timeout=10):
        acquired.set()
        if not release.wait(30):
            raise TimeoutError("test lock was not released")


def _crash_after_authoritative_write(directory):
    async def work():
        store = await _open(directory)

        def stop_before_projection(*_args, **_kwargs):
            os._exit(73)

        store._lexical_index._sync_ids = stop_before_projection
        await store.upsert(_node(content="NEWTOKEN survives the interrupted projection"))

    asyncio.run(work())


@pytest.mark.asyncio
async def test_known_primary_success_survives_projection_failure_and_reopens_clean(tmp_path, monkeypatch):
    store = await _open(tmp_path)
    try:
        await store.upsert_batch([
            _node(), _node("unchanged", "UNTOUCHEDTOKEN is still authoritative"),
        ])
        assert await _ids(store, "OLDTOKEN") == ["node"]

        def fail_projection(*_args, **_kwargs):
            raise sqlite3.OperationalError("injected projection commit failure")

        with monkeypatch.context() as failure:
            failure.setattr(store._lexical_index, "_sync_ids", fail_projection)
            assert await store.upsert(_node(content="NEWTOKEN repairs the socket")) == "node"

        assert (await store.get_by_id("node")).content == "NEWTOKEN repairs the socket"
        # Check an unchanged row first: a per-row hash mismatch cannot mask a
        # broken collection-level dirty gate by disabling the index for us.
        assert await _ids(store, "UNTOUCHEDTOKEN") == []
        assert await _ids(store, "OLDTOKEN") == []
        assert await _ids(store, "NEWTOKEN") == []
        assert (await store.get_stats())["lexical_index"]["state"] == "dirty"
    finally:
        await store.close()

    reopened = await _open(tmp_path)
    try:
        assert await _ids(reopened, "OLDTOKEN") == []
        assert await _ids(reopened, "NEWTOKEN") == ["node"]
        assert await _ids(reopened, "UNTOUCHEDTOKEN") == ["unchanged"]
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_failure_to_persist_dirty_marker_prevents_authoritative_write(tmp_path, monkeypatch):
    store = await _open(tmp_path)
    try:
        await store.upsert(_node())

        def fail_dirty(*_args, **_kwargs):
            raise sqlite3.OperationalError("injected disk full before authoritative write")

        def fail_revocation():
            raise OSError("injected read-only sidecar directory")

        with monkeypatch.context() as failure:
            failure.setattr(store._lexical_index, "_mark_dirty", fail_dirty)
            failure.setattr(store._lexical_index, "_discard_locked", fail_revocation)
            with pytest.raises(OSError, match="read-only sidecar"):
                await store.upsert(_node("must-not-exist", "NEWTOKEN must not be committed"))

        assert await store.get_by_id("must-not-exist") is None
        assert await _ids(store, "OLDTOKEN") == ["node"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_authoritative_exception_leaves_index_unavailable_until_audit(tmp_path, monkeypatch):
    store = await _open(tmp_path)
    try:
        await store.upsert(_node())

        def fail_primary(**_kwargs):
            raise RuntimeError("injected uncertain Chroma outcome")

        with monkeypatch.context() as failure:
            failure.setattr(store._collection, "upsert", fail_primary)
            with pytest.raises(RuntimeError, match="uncertain Chroma"):
                await store.upsert(_node(content="NEWTOKEN was never committed"))

        assert await _ids(store, "OLDTOKEN") == []
        await store.rebuild_lexical_index()
        assert await _ids(store, "OLDTOKEN") == ["node"]
        assert await _ids(store, "NEWTOKEN") == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_disabled_client_invalidates_other_clients_projection_and_close_is_local(tmp_path):
    enabled = await _open(tmp_path)
    disabled = await _open(tmp_path, enabled=False)
    try:
        await enabled.upsert(_node())
        assert await disabled.update_payload("node", {"content": "NEWTOKEN changed with lexical disabled"})
        assert await _ids(enabled, "OLDTOKEN") == []
        assert await _ids(enabled, "NEWTOKEN") == []
        await disabled.close()
        assert (await enabled.get_by_id("node")).content == "NEWTOKEN changed with lexical disabled"
        await enabled.rebuild_lexical_index()
        assert await _ids(enabled, "NEWTOKEN") == ["node"]
    finally:
        await disabled.close()
        await enabled.close()


@pytest.mark.asyncio
async def test_initialization_audits_same_count_offline_content_and_source_changes(tmp_path):
    store = await _open(tmp_path)
    await store.upsert(_node())
    collection = store._collection
    point_id = store._node_id_to_point_id("node")
    await store.close()
    original = collection.get(ids=[point_id], include=["metadatas"])["metadatas"][0]
    changed = dict(original, content="NEWTOKEN restored from a backup", source="restored")
    collection.update(
        ids=[point_id], metadatas=[changed], documents=[changed["content"]],
        embeddings=[[1.0, 0.0, 0.0, 0.0]],
    )

    reopened = await _open(tmp_path)
    try:
        assert await _ids(reopened, "OLDTOKEN") == []
        hits = await reopened.keyword_search("NEWTOKEN", user_id="u1", include_sources=["restored"])
        assert [hit["node_id"] for hit in hits] == ["node"]
        assert await reopened.keyword_search("NEWTOKEN", user_id="u1", exclude_sources=["restored"]) == []
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_same_name_new_collection_does_not_reuse_old_projection(tmp_path):
    store = await _open(tmp_path)
    await store.upsert(_node())
    client = store._client
    collection_name = store._collection.name
    await store.close()
    client.delete_collection(name=collection_name)

    replacement = await _open(tmp_path)
    try:
        assert await _ids(replacement, "OLDTOKEN") == []
        await replacement.upsert(_node("replacement", "NEWTOKEN belongs to a new collection"))
        assert await _ids(replacement, "NEWTOKEN") == ["replacement"]
    finally:
        await replacement.close()


@pytest.mark.asyncio
async def test_rebuild_scans_every_page_and_matches_fresh_results(tmp_path):
    legacy = await _open(tmp_path, enabled=False)
    try:
        await legacy.upsert_batch([
            _node(f"page-{number:04d}", f"PAGEMARK indexed record number {number}")
            for number in range(1201)
        ])
    finally:
        await legacy.close()
    store = await _open(tmp_path)
    try:
        before = await _ids(store, "PAGEMARK", top_k=1300)
        assert set(before) == {f"page-{number:04d}" for number in range(1201)}
        await store.rebuild_lexical_index()
        assert await _ids(store, "PAGEMARK", top_k=1300) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_other_process_lock_makes_query_degrade_and_does_not_lose_write(tmp_path, monkeypatch):
    store = await _open(tmp_path)
    context = multiprocessing.get_context("spawn")
    acquired, release = context.Event(), context.Event()
    process = context.Process(target=_hold_lock, args=(str(store._lexical_index.lock_path), acquired, release))
    try:
        await store.upsert(_node())
        process.start()
        assert await asyncio.to_thread(acquired.wait, 20), "child must hold the collection lock"
        assert await _ids(store, "OLDTOKEN") == []
        write_started = threading.Event()
        original_mutate = store._mutate

        def started_mutation(operation):
            write_started.set()
            return original_mutate(operation)

        monkeypatch.setattr(store, "_mutate", started_mutation)
        pending = asyncio.create_task(store.upsert(_node("second", "NEWTOKEN serialized writer")))
        assert await asyncio.to_thread(write_started.wait, 5)
        assert await store.get_by_id("second") is None
        release.set()
        assert await pending == "second"
        await asyncio.to_thread(process.join, 20)
        assert process.exitcode == 0
        assert await _ids(store, "OLDTOKEN") == ["node"]
        assert await _ids(store, "NEWTOKEN") == ["second"]
    finally:
        release.set()
        if process.pid:
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_await_keeps_worker_lock_until_chroma_write_finishes(tmp_path, monkeypatch):
    first = await _open(tmp_path)
    second = await _open(tmp_path)
    entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
    original_first, original_second = first._collection.upsert, second._collection.upsert

    def blocked_write(**kwargs):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test worker not released")
        return original_first(**kwargs)

    def competing_write(**kwargs):
        second_entered.set()
        return original_second(**kwargs)

    try:
        monkeypatch.setattr(first._collection, "upsert", blocked_write)
        monkeypatch.setattr(second._collection, "upsert", competing_write)
        cancelled = asyncio.create_task(first.upsert(_node("first", "FIRSTTOKEN accepted worker")))
        assert await asyncio.to_thread(entered.wait, 5)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        competing = asyncio.create_task(second.upsert(_node("second", "SECONDTOKEN later writer")))
        assert not await asyncio.to_thread(second_entered.wait, 0.1)
        assert await _ids(second, "FIRSTTOKEN") == []
        release.set()
        assert await competing == "second"
        assert second_entered.is_set()
        assert await _ids(second, "FIRSTTOKEN") == ["first"]
        assert await _ids(second, "SECONDTOKEN") == ["second"]
    finally:
        release.set()
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_process_exit_after_chroma_commit_recovers_from_authoritative_data(tmp_path):
    store = await _open(tmp_path)
    await store.upsert(_node())
    await store.close()
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_after_authoritative_write, args=(str(tmp_path),))
    try:
        process.start()
        await asyncio.to_thread(process.join, 30)
        assert process.exitcode == 73, "child must exit at the injected post-Chroma boundary"
        reopened = await _open(tmp_path)
        try:
            assert await _ids(reopened, "OLDTOKEN") == []
            assert await _ids(reopened, "NEWTOKEN") == ["node"]
        finally:
            await reopened.close()
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)
