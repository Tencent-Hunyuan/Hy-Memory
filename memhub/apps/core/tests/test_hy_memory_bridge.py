import asyncio
import gc
import json
import os
import sqlite3
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from one_memory.diagnostic_log import configure_diagnostics, reset_diagnostics
from one_memory.hy_memory_bridge import (
    HyMemoryBridge,
    HyMemoryBridgeError,
    HyMemoryIngestRequest,
    HyMemoryIngestResult,
    HyMemoryTurn,
    UnsupportedHyMemoryProviderError,
    _basic_info_memories,
    _collection_name,
    _with_native_mutation_delta,
    build_hy_memory_config,
    configure_hy_memory_environment,
    reattach_legacy_vector_collection,
    retire_mismatched_graph_store,
)
from one_memory.hy_runtime_config import (
    HY_MEMORY_DISTRIBUTION,
    HY_MEMORY_MODE,
    HY_MEMORY_READER,
    HY_MEMORY_VERSION,
)
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind


class FakeHyMemoryClient:
    def __init__(
        self,
        result: Mapping[str, object],
        *,
        record_request_id: str | None = None,
        search_result: Mapping[str, object] | None = None,
    ) -> None:
        self.result = result
        self.record_request_id = record_request_id
        self.search_result = search_result or {"results": []}
        self.update_succeeds = True
        self.calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.closed = False
        self.deleted: set[str] = set()
        self.list_result: Mapping[str, object] = {"vdb": {"memories": [], "total": 0}}
        self.write_outcome: Mapping[str, object] | None = None
        self.raw_result: Mapping[str, object] | None = None
        self.raw_list_result: Mapping[str, object] = {"raw": [], "total": 0}

    def add(
        self,
        data: Sequence[Mapping[str, str]],
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        metadata: Mapping[str, object],
        memory_at: datetime | None,
        scene: str,
        request_id: str,
    ) -> Mapping[str, object]:
        self.calls.append(
            {
                "data": data,
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "metadata": metadata,
                "memory_at": memory_at,
                "scene": scene,
                "request_id": request_id,
            }
        )
        return self.result

    def add_extracted(
        self,
        contents: str | Sequence[str],
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        metadata: Mapping[str, object],
        memory_at: datetime | None,
        request_id: str,
    ) -> Mapping[str, object]:
        data = [contents] if isinstance(contents, str) else list(contents)
        self.calls.append(
            {
                "contents": data,
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "metadata": metadata,
                "memory_at": memory_at,
                "request_id": request_id,
            }
        )
        return self.result

    def close(self) -> None:
        self.closed = True

    def get(self, memory_id: str) -> Mapping[str, object] | None:
        if memory_id in self.deleted:
            return None
        last = self.calls[-1] if self.calls else {}
        request_id = self.record_request_id or str(last.get("request_id") or "read")
        return {
            "memory_id": memory_id,
            "content": "Extracted memory",
            "layer": "l2_fact",
            "status": "active",
            "is_latest": True,
            "user_id": last.get("user_id") or "local",
            "agent_id": last.get("agent_id") or "one-memory",
            "custom": {"request_id": request_id},
        }

    def search(self, query: str, **kwargs) -> Mapping[str, object]:
        self.search_calls.append({"query": query, **kwargs})
        return self.search_result

    def list_memories(self, **kwargs) -> Mapping[str, object]:
        self.calls.append({"list_memories": dict(kwargs)})
        return self.list_result

    def get_write_outcome(self, request_id: str) -> Mapping[str, object] | None:
        self.calls.append({"get_write_outcome": request_id})
        return self.write_outcome

    def get_raw(self, raw_id: str) -> Mapping[str, object] | None:
        self.calls.append({"get_raw": raw_id})
        return self.raw_result

    def list_raw(self, **kwargs) -> Mapping[str, object]:
        self.calls.append({"list_raw": dict(kwargs)})
        return self.raw_list_result

    def update(self, memory_id: str, content: str) -> Mapping[str, object]:
        self.calls.append({"update_memory_id": memory_id, "update_content": content})
        return {"success": self.update_succeeds, "memory_id": memory_id}

    def delete(self, memory_id: str) -> Mapping[str, object]:
        self.calls.append({"delete_memory_id": memory_id})
        self.deleted.add(memory_id)
        return {"success": True, "memory_id": memory_id}

    def evolve(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append({"evolve": dict(kwargs)})
        return {"success": True}

    def create_collection(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append({"create_collection": dict(kwargs)})
        return {"success": True}

    def add_task(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append({"add_task": dict(kwargs)})
        return {"success": True}


class FailingHyMemoryClient(FakeHyMemoryClient):
    def add(self, *args, **kwargs):
        raise RuntimeError("private-provider-response-918273")


def snapshot(
    provider: ProviderKind,
    *,
    base_url: str | None,
    has_api_key: bool,
    embedding_provider: ProviderKind | None = None,
    embedding_base_url: str | None = None,
) -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="profile",
        revision=3,
        provider_kind=provider,
        base_url=base_url,
        model="memory-model",
        embedding_model="embedding-model",
        embedding_provider_kind=embedding_provider,
        embedding_base_url=embedding_base_url,
        embedding_dimensions=768,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8192,
        has_api_key=has_api_key,
    )


def test_hy_client_initialization_is_serial_across_bridges(tmp_path, monkeypatch) -> None:
    active = 0
    max_active = 0
    created: list[FakeHyMemoryClient] = []
    guard = threading.Lock()

    def create(*_args, **_kwargs):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        client = FakeHyMemoryClient({"success": True, "results": []})
        created.append(client)
        with guard:
            active -= 1
        return client

    monkeypatch.setattr("one_memory.hy_memory_bridge.create_hy_memory_client", create)
    profile = snapshot(
        ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://model.invalid",
        has_api_key=True,
    )
    bridges = [
        HyMemoryBridge(profile=profile, api_key="secret", data_dir=tmp_path) for _ in range(8)
    ]

    async def exercise() -> None:
        await asyncio.gather(*(bridge.prepare() for bridge in bridges))
        await asyncio.gather(*(bridge.close() for bridge in bridges))

    asyncio.run(exercise())

    assert len(created) == 8
    assert max_active == 1


def test_native_hy_clients_share_one_runtime_per_data_directory(tmp_path, monkeypatch) -> None:
    from one_memory import hy_memory_bridge

    class FakeRuntime:
        created = 0
        closed = 0

        def __init__(self) -> None:
            self.stop = threading.Event()
            self.threads = tuple(
                threading.Thread(
                    target=self.stop.wait,
                    name=f"n46-shared-loop-{index}",
                    daemon=True,
                )
                for index in range(2)
            )
            for thread in self.threads:
                thread.start()

        @classmethod
        async def create(cls, _config):
            cls.created += 1
            return cls()

        def close(self) -> None:
            type(self).closed += 1
            self.stop.set()
            for thread in self.threads:
                thread.join(timeout=1)

    class FakeNativeClient:
        runtimes: list[FakeRuntime] = []

        def __init__(self, *, config, mode, runtime=None):
            del config, mode
            assert runtime is not None
            self.runtime = runtime
            self.runtimes.append(runtime)

        def close(self) -> None:
            pass

    real_import = hy_memory_bridge.import_module

    def fake_import(name: str):
        if name == "hy_memory.client":
            return SimpleNamespace(HyMemoryClient=FakeNativeClient)
        if name == "hy_memory.runtime":
            return SimpleNamespace(SharedRuntime=FakeRuntime)
        return real_import(name)

    monkeypatch.setattr(hy_memory_bridge, "import_module", fake_import)
    monkeypatch.setattr(hy_memory_bridge, "reattach_legacy_vector_collection", lambda *_: None)
    monkeypatch.setattr(hy_memory_bridge, "inspect_graph_width_mismatch", lambda *_: None)
    monkeypatch.setattr(
        "one_memory.hy_response_guard.install_hy_write_guards", lambda _client: None
    )
    hy_memory_bridge.close_shared_hy_runtimes()
    baseline = threading.active_count()
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )

    clients = [
        hy_memory_bridge.create_hy_memory_client(profile, api_key=None, data_dir=tmp_path)
        for _ in range(50)
    ]
    for client in clients:
        client.close()

    assert FakeRuntime.created == 1
    assert len({id(runtime) for runtime in FakeNativeClient.runtimes}) == 1
    hy_memory_bridge.close_shared_hy_runtimes()
    assert FakeRuntime.closed == 1
    assert threading.active_count() <= baseline + 2


def test_ollama_profile_ignores_ambient_remote_storage_and_models(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")
    monkeypatch.setenv("MEMORY_EMBEDDER_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("MEMORY_VECTOR_HOST", "remote.example.com")
    monkeypatch.setenv("MEMORY_COLLECTION_NAME", "hostile-collection")
    monkeypatch.setenv("MEMORY_EMBEDDING_DIMS", "99")
    monkeypatch.delenv("MEMORY_CODING_TOOL_RESULT_MAX_BYTES", raising=False)
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )

    config = build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)

    assert config.llm.provider == "openai"
    assert config.llm.model == "memory-model"
    assert config.llm.base_url == "http://127.0.0.1:11434/v1"
    assert config.llm.api_key == "ollama"
    assert config.llm.timeout == 30
    assert config.embedder.model == "embedding-model"
    assert config.embedder.base_url == "http://127.0.0.1:11434/v1"
    assert config.embedder.api_key == "ollama"
    assert config.vector_store.provider == "chroma"
    assert config.vector_store.host is None
    assert config.vector_store.collection_name.startswith("one_memory_")
    assert config.vector_store.collection_name != "hostile-collection"
    assert config.vector_store.embedding_dims == 768
    assert config.graph_store.provider == "kuzu"
    assert str(tmp_path) in config.graph_store.db_path
    assert config.coding.tool_result_max_bytes == 512


def test_memhub_coding_tool_result_ceiling_is_configurable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_CODING_TOOL_RESULT_MAX_BYTES", "1024")
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )

    config = build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)

    assert config.coding.tool_result_max_bytes == 1024


def _kuzu_graph_with_embedding_width(db_path: Path, width: int) -> None:
    kuzu = pytest.importorskip("kuzu")
    database = kuzu.Database(str(db_path))
    connection = kuzu.Connection(database)
    connection.execute(
        f"CREATE NODE TABLE Memory(node_id STRING, embedding FLOAT[{width}], PRIMARY KEY(node_id))"
    )
    del connection
    del database
    gc.collect()


def _chroma_with_collection(database: Path, name: str, dimension: int | None) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT, dimension INTEGER)"
        )
        connection.execute(
            "INSERT INTO collections VALUES (?, ?, ?)",
            ("segment-1", name, dimension),
        )


def test_collection_written_before_the_width_was_known_is_reattached(tmp_path) -> None:
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )
    config = build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)
    database = Path(config.vector_store.persist_directory) / "chroma.sqlite3"
    # Written when the profile had no measured width, so HY suffixed the
    # default 1536 onto a collection that really holds 768-wide vectors.
    legacy = _collection_name(
        ProviderKind.OLLAMA,
        "http://127.0.0.1:11434/v1",
        "embedding-model",
        declared_dimensions="auto",
    )
    _chroma_with_collection(database, f"{legacy}_1536", 768)

    migrated = reattach_legacy_vector_collection(profile, config)

    assert migrated == f"{legacy}_1536"
    with sqlite3.connect(database) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM collections")]
    assert names == [f"{config.vector_store.collection_name}_768"]


def test_reattach_leaves_a_collection_from_another_embedding_width_alone(tmp_path) -> None:
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )
    config = build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)
    database = Path(config.vector_store.persist_directory) / "chroma.sqlite3"
    legacy = _collection_name(
        ProviderKind.OLLAMA,
        "http://127.0.0.1:11434/v1",
        "embedding-model",
        declared_dimensions="auto",
    )
    _chroma_with_collection(database, f"{legacy}_1536", 1536)

    assert reattach_legacy_vector_collection(profile, config) is None
    with sqlite3.connect(database) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM collections")]
    assert names == [f"{legacy}_1536"]


def test_graph_store_created_for_another_embedding_width_is_retired(tmp_path) -> None:
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )
    config = build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _kuzu_graph_with_embedding_width(db_path, 1536)

    archive = retire_mismatched_graph_store(config)

    assert archive is not None
    assert archive.exists()
    assert "dim1536" in archive.name
    assert not db_path.exists()


def test_graph_store_matching_the_embedding_width_is_kept(tmp_path) -> None:
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )
    config = build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)
    db_path = Path(config.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _kuzu_graph_with_embedding_width(db_path, 768)

    assert retire_mismatched_graph_store(config) is None
    assert db_path.exists()


def test_reading_the_profile_from_the_wrong_field_is_an_error_not_an_absence() -> None:
    """The two replies are shaped differently, and confusing them read empty.

    `list_memories` wraps rows in `vdb`, `get_user_basic_info` returns them at
    the top level. Parsed with the wrong one, a store holding three profile
    facts reported none — indistinguishable from a person we know nothing
    about, which is why it went unnoticed. Now only the second of those is
    allowed to be quiet.
    """
    assert _basic_info_memories({"memories": []}) == ()
    found = _basic_info_memories(
        {"memories": [{"memory_id": "m1", "content": "c", "layer": "l0_basic_info"}]}
    )
    assert len(found) == 1

    for wrong in ({"vdb": {"memories": [{"memory_id": "m1"}]}}, {}, None, []):
        with pytest.raises(HyMemoryBridgeError, match="hy_memory_profile_shape_unknown"):
            _basic_info_memories(wrong)


def test_a_snapshot_silent_about_its_width_follows_the_store_it_finds(
    tmp_path,
) -> None:
    """Snapshots predate the width field, and one of them retired a live graph.

    A task carries the snapshot it was queued with, so a snapshot written
    before `embedding_dimensions` existed still runs today. Left to guess, HY
    applies its own default; a default that disagrees with the graph archives
    the graph. That happened on this machine — a 13MB store renamed aside and
    an empty one opened in its place. The store states its own geometry, so it
    is read rather than guessed.
    """
    stated = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )
    silent = stated.model_copy(update={"embedding_dimensions": None})

    # Somewhere for the graph to already exist, built at the width the
    # snapshot that does state one would use.
    seeded = build_hy_memory_config(stated, api_key=None, data_dir=tmp_path)
    db_path = Path(seeded.graph_store.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _kuzu_graph_with_embedding_width(db_path, 768)

    config = build_hy_memory_config(silent, api_key=None, data_dir=tmp_path)

    assert config.vector_store.embedding_dims == 768
    assert config.embedder.embedding_dims == 768
    # The point of all of it: the graph survives the run.
    assert retire_mismatched_graph_store(config) is None
    assert db_path.exists()


def test_a_snapshot_silent_about_its_width_is_left_alone_on_a_fresh_machine(
    tmp_path,
) -> None:
    """With no store to read, there is nothing to follow and nothing to lose."""
    silent = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    ).model_copy(update={"embedding_dimensions": None})

    config = build_hy_memory_config(silent, api_key=None, data_dir=tmp_path)

    assert not Path(config.graph_store.db_path).exists()


def test_remote_generation_uses_separate_local_embedding_endpoint(tmp_path) -> None:
    profile = snapshot(
        ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.com/v1",
        has_api_key=True,
        embedding_provider=ProviderKind.OLLAMA,
        embedding_base_url="http://127.0.0.1:11434",
    )

    config = build_hy_memory_config(profile, api_key="secret", data_dir=tmp_path)

    assert config.llm.base_url == "https://models.example.com/v1"
    assert config.llm.api_key == "secret"
    assert config.embedder.base_url == "http://127.0.0.1:11434/v1"
    assert config.embedder.api_key == "ollama"


def test_vector_collection_changes_with_embedding_endpoint(tmp_path) -> None:
    first = snapshot(
        ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.com/v1",
        has_api_key=True,
        embedding_provider=ProviderKind.OLLAMA,
        embedding_base_url="http://127.0.0.1:11434",
    )
    second = first.model_copy(update={"embedding_base_url": "http://127.0.0.1:11435"})

    first_config = build_hy_memory_config(first, api_key="secret", data_dir=tmp_path)
    second_config = build_hy_memory_config(second, api_key="secret", data_dir=tmp_path)

    assert first_config.vector_store.collection_name != second_config.vector_store.collection_name


def test_bridge_warm_loads_first_write_tokenizer_off_the_event_loop(tmp_path, monkeypatch) -> None:
    client = FakeHyMemoryClient({"success": True, "memory_ids": []})
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    event_loop_thread = threading.get_ident()
    calls: list[tuple[str, int]] = []

    from hy_memory.core import embed_service

    monkeypatch.setattr(
        embed_service,
        "_truncate_for_embedding",
        lambda text: calls.append((text, threading.get_ident())) or text,
    )

    asyncio.run(bridge.warm())

    assert calls == [("one-memory-warmup", calls[0][1])]
    assert calls[0][1] != event_loop_thread


def test_bridge_writes_evidence_refs_and_uses_source_as_provenance(tmp_path) -> None:
    client = FakeHyMemoryClient({"success": True, "memory_ids": ["memory-1"]})
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="ingest-1",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="Remember this"),),
        evidence_ids=("evidence-1",),
    )

    result = asyncio.run(bridge.ingest(request))
    asyncio.run(bridge.close())

    assert tuple(memory.memory_id for memory in result.memories) == ("memory-1",)
    assert result.memories[0].content == "Extracted memory"
    assert client.calls[0]["agent_id"] == "one-memory"
    assert client.calls[0]["metadata"] == {
        "source_id": "cursor",
        "evidence_ids": ["evidence-1"],
        "workspace_ids": [],
        "input_kind": "raw_conversation",
    }
    assert client.calls[0]["scene"] is None
    assert client.closed is True


def test_bridge_recovers_only_outputs_with_the_exact_request_identity(tmp_path) -> None:
    client = FakeHyMemoryClient({"success": True})
    client.list_result = {
        "vdb": {
            "total": 3,
            "memories": [
                {
                    "memory_id": "matched",
                    "content": "Recovered memory",
                    "layer": "l2_fact",
                    "owner": "user",
                    "tags": ["recovered"],
                    "user_id": "local",
                    "agent_id": "one-memory",
                    "custom": {"request_id": "request-1"},
                },
                {
                    "memory_id": "other-request",
                    "content": "Wrong request",
                    "user_id": "local",
                    "agent_id": "one-memory",
                    "custom": {"request_id": "request-2"},
                },
                {
                    "memory_id": "other-agent",
                    "content": "Wrong agent",
                    "user_id": "local",
                    "agent_id": "other",
                    "custom": {"request_id": "request-1"},
                },
            ],
        }
    }
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    outputs = asyncio.run(
        bridge.outputs_for_request(
            request_id="request-1",
            user_id="local",
            agent_id="one-memory",
            evidence_ids=("evidence-1",),
        )
    )

    assert [output.memory_id for output in outputs] == ["matched"]
    assert outputs[0].evidence_ids == ("evidence-1",)


def test_bridge_reads_sdk_memory_lifecycle_without_governance(tmp_path) -> None:
    client = FakeHyMemoryClient({"success": True})
    client.list_result = {
        "vdb": {
            "total": 1,
            "memories": [
                {
                    "memory_id": "old-memory",
                    "content": "Old wording",
                    "layer": "l2_fact",
                    "status": "superseded",
                    "is_latest": False,
                    "superseded_by": ["new-memory"],
                    "user_id": "local",
                    "agent_id": "one-memory",
                    "session_id": "session-1",
                    "tags": ["preference"],
                }
            ],
        }
    }
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    page = asyncio.run(
        bridge.list_sdk_memories(
            user_id="local",
            statuses=("superseded",),
            limit=20,
        )
    )

    assert page.total == 1
    assert page.memories[0].status == "superseded"
    assert page.memories[0].superseded_by == ("new-memory",)
    assert client.calls[-1]["list_memories"]["statuses"] == ["superseded"]


def test_bridge_reads_raw_and_write_outcome_through_public_sdk(tmp_path) -> None:
    client = FakeHyMemoryClient({"success": True})
    client.write_outcome = {
        "request_id": "request-1",
        "status": "succeeded",
        "result": {"success": True, "memory_ids": ["memory-1"]},
    }
    client.raw_result = {
        "raw_id": "raw-1",
        "content": "verbatim",
        "user_id": "local",
        "agent_id": "one-memory",
        "session_id": "session-1",
        "source": "cursor",
        "custom": {"role": "user"},
    }
    client.raw_list_result = {"raw": [client.raw_result], "total": 1}
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    outcome = asyncio.run(bridge.get_sdk_write_outcome("request-1"))
    raw = asyncio.run(bridge.get_sdk_raw("raw-1"))
    rows, total = asyncio.run(bridge.list_sdk_raw(user_id="local"))

    assert outcome["status"] == "succeeded"
    assert raw.content == "verbatim"
    assert raw.custom == {"role": "user"}
    assert total == 1
    assert rows == (raw,)


def test_hy_failure_log_identifies_stage_without_provider_content(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    receipt = configure_diagnostics(
        tmp_path,
        session_id="11111111-1111-4111-8111-111111111111",
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=FailingHyMemoryClient({}),
    )
    request = HyMemoryIngestRequest(
        request_id="ingest-failure-1",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="private input"),),
        evidence_ids=("evidence-1",),
    )
    try:
        with pytest.raises(HyMemoryBridgeError, match="hy_memory_runtime_failed"):
            asyncio.run(bridge.ingest(request))
    finally:
        reset_diagnostics()
    written = receipt.path.read_text()
    events = [json.loads(line) for line in written.splitlines()]
    assert events[-1]["event"] == "hy.ingest_failed"
    assert events[-1]["request_id"] == "ingest-failure-1"
    assert events[-1]["error_type"] == "RuntimeError"
    assert "private-provider-response-918273" not in written
    assert "private input" not in written


def test_hy_bridge_preserves_profile_stopping_model_error(tmp_path) -> None:
    from types import SimpleNamespace

    class PaymentRequiredClient(FakeHyMemoryClient):
        def add(self, *args, **kwargs):
            self._one_memory_write_guards[0].failures.append("hy_memory_model_payment_required")
            raise RuntimeError("private provider body")

    client = PaymentRequiredClient({})
    client._one_memory_write_guards = [SimpleNamespace(failures=[])]
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="payment-required",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="private input"),),
        evidence_ids=("evidence-1",),
    )

    with pytest.raises(HyMemoryBridgeError, match="^hy_memory_model_payment_required$"):
        asyncio.run(bridge.ingest(request))


def test_swallowed_hy_model_error_never_becomes_successful_governance_input(tmp_path):
    from types import SimpleNamespace

    class Client(FakeHyMemoryClient):
        def add(self, *args, **kwargs):
            self._one_memory_write_guards[0].failures.append("hy_memory_model_output_truncated")
            return super().add(*args, **kwargs)

    client = Client({"success": True, "memory_ids": ["raw-only"]})
    client._one_memory_write_guards = [SimpleNamespace(failures=[])]
    bridge = HyMemoryBridge(
        profile=snapshot(ProviderKind.OLLAMA, base_url="http://127.0.0.1:11434", has_api_key=False),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="swallowed-error",
        user_id="local",
        source_id="fixture",
        session_id="session",
        evidence_ids=("e1",),
        turns=(HyMemoryTurn(role="user", content="Input must be fully processed."),),
    )
    with pytest.raises(HyMemoryBridgeError, match="hy_memory_model_output_truncated"):
        asyncio.run(bridge.ingest(request))


def test_raw_hy_log_is_not_an_extracted_memory_when_no_durable_fact_was_produced(tmp_path):
    class Client(FakeHyMemoryClient):
        def get(self, memory_id):
            return {**super().get(memory_id), "layer": "L1_RAW"}

    client = Client({"success": True, "memory_id": "raw-log"})
    bridge = HyMemoryBridge(
        profile=snapshot(ProviderKind.OLLAMA, base_url="http://127.0.0.1:11434", has_api_key=False),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="raw-only",
        user_id="local",
        source_id="fixture",
        session_id="session",
        evidence_ids=("e1",),
        turns=(HyMemoryTurn(role="user", content="Hello"),),
    )
    result = asyncio.run(bridge.ingest(request))
    assert result.memories == ()


def test_native_read_preserves_owner_from_same_stored_node():
    from types import SimpleNamespace

    from one_memory.hy_memory_bridge import _read_hy_record

    class Store:
        async def get_by_id(self, memory_id):
            return SimpleNamespace(
                content="Agent proposed this", owner="agent", user_id="local", agent_id="one-memory"
            )

    client = SimpleNamespace(
        get=lambda key: {
            "memory_id": key,
            "content": "Agent proposed this",
            "source": "vdb",
            "user_id": "local",
            "agent_id": "one-memory",
        },
        _loop_thread=SimpleNamespace(run=asyncio.run),
        _vector_store=Store(),
    )
    assert _read_hy_record(client, "m")["owner"] == "agent"


def test_bridge_imports_existing_content_without_running_extractor(tmp_path) -> None:
    client = FakeHyMemoryClient({"success": True, "memory_ids": ["memory-1"]})
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="ingest-fast-1",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="我偏好简洁文案。"),),
        evidence_ids=("evidence-1",),
    )

    result = asyncio.run(bridge.ingest_extracted(request, ("用户偏好简洁文案。",)))

    assert tuple(memory.memory_id for memory in result.memories) == ("memory-1",)
    assert client.calls[0]["contents"] == ["用户偏好简洁文案。"]
    assert client.calls[0]["metadata"] == {
        "source_id": "cursor",
        "evidence_ids": ["evidence-1"],
        "workspace_ids": [],
        "input_kind": "memory_content_without_raw",
    }


@pytest.mark.parametrize("fails", [False, True])
def test_import_binding_is_cleared_after_success_or_native_exception(tmp_path, fails):
    from one_memory.hy_response_guard import HyReconcileGuard

    guard = HyReconcileGuard(None)

    class Client(FakeHyMemoryClient):
        _one_memory_write_guards = (guard,)

        def add_extracted(self, contents, **kwargs):
            assert guard.imported_contents == frozenset({"existing report"})
            if fails:
                raise RuntimeError("native failure")
            return super().add_extracted(contents, **kwargs)

    client = Client({"success": True, "memory_ids": []})
    bridge = HyMemoryBridge(
        profile=snapshot(ProviderKind.OLLAMA, base_url="http://127.0.0.1:11434", has_api_key=False),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="r",
        user_id="local",
        source_id="memory-content-export",
        session_id="s",
        turns=(HyMemoryTurn(role="assistant", content="existing report"),),
        evidence_ids=("e",),
    )
    if fails:
        with pytest.raises(HyMemoryBridgeError):
            asyncio.run(bridge.ingest_extracted(request, (" existing report\n",)))
    else:
        asyncio.run(bridge.ingest_extracted(request, (" existing report\n",)))
        assert client.search_calls[0]["query"] == "existing report"
    assert guard.imported_contents is None


def test_bridge_returns_machine_error_without_provider_body(tmp_path) -> None:
    client = FakeHyMemoryClient(
        {
            "success": False,
            "error_code": "unavailable",
            "error": "provider response containing private prompt",
        }
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="ingest-1",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="private prompt"),),
        evidence_ids=("evidence-1",),
    )

    with pytest.raises(HyMemoryBridgeError) as captured:
        asyncio.run(bridge.ingest(request))

    assert str(captured.value) == "hy_memory_unavailable"
    assert "private prompt" not in str(captured.value)


def test_bridge_rejects_deduplicated_memory_from_another_request(tmp_path) -> None:
    client = FakeHyMemoryClient(
        {"success": True, "memory_ids": ["stale-memory"]},
        record_request_id="another-request",
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="current-request",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="Current evidence"),),
        evidence_ids=("evidence-1",),
    )

    result = asyncio.run(bridge.ingest(request))

    assert result.memories == ()


def test_extracted_bridge_accepts_exact_deduplicated_fact_for_same_scope(
    tmp_path,
) -> None:
    client = FakeHyMemoryClient(
        {"success": True, "memory_ids": ["existing-memory"]},
        record_request_id="another-request",
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="current-request",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="Original evidence"),),
        evidence_ids=("evidence-1",),
    )

    result = asyncio.run(bridge.ingest_extracted(request, ("Extracted memory",)))

    assert tuple(memory.memory_id for memory in result.memories) == ("existing-memory",)


def test_extracted_bridge_finds_exact_noop_memory_when_sdk_returns_no_id(
    tmp_path,
) -> None:
    client = FakeHyMemoryClient(
        {"success": True, "memory_ids": []},
        search_result={
            "results": [
                {
                    "memory_id": "existing-memory",
                    "content": "Extracted memory",
                    "layer": "L2_FACT",
                    "user_id": "local",
                    "agent_id": "one-memory",
                    "custom": {"request_id": "older-request"},
                }
            ]
        },
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="current-request",
        user_id="local",
        source_id="cursor",
        session_id="conversation-1",
        turns=(HyMemoryTurn(role="user", content="Original evidence"),),
        evidence_ids=("evidence-1",),
    )

    result = asyncio.run(bridge.ingest_extracted(request, ("Extracted memory",)))

    assert tuple(memory.memory_id for memory in result.memories) == ("existing-memory",)


def test_bridge_cascade_delete_calls_hy_memory(tmp_path) -> None:
    from types import SimpleNamespace

    from tests.hy_stores import chroma_store

    client = FakeHyMemoryClient({"success": True})

    def points(*, ids, include=None):
        return {"ids": [] if "memory-to-delete" in client.deleted else list(ids)}

    # Deletion is read back from HY's stores themselves, not through `get`.
    client._loop_thread = SimpleNamespace(run=asyncio.run)
    client._vector_store = chroma_store(points)
    client._graph_store = None
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    result = asyncio.run(bridge.delete("memory-to-delete"))

    assert result["success"] is True
    assert client.calls == [{"delete_memory_id": "memory-to-delete"}]


def test_bridge_update_rewrites_in_place_and_keeps_the_id(tmp_path) -> None:
    """A correction must not become a delete and an add.

    HY re-embeds the same node, so every id already handed out — evidence
    links, recall receipts, whatever a host wrote down — still resolves.
    """
    client = FakeHyMemoryClient({"success": True})
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    result = asyncio.run(bridge.update("memory-to-correct", "用户的花名是白泽"))

    assert result["success"] is True
    assert client.calls == [
        {
            "update_memory_id": "memory-to-correct",
            "update_content": "用户的花名是白泽",
        }
    ]


def test_hy_saying_it_did_not_update_is_not_read_as_a_success(tmp_path) -> None:
    """HY answers `success: False` for a memory its vector store never held,
    without raising. Passing that back as a normal result would have the
    caller record an index that matches when nothing was written."""
    client = FakeHyMemoryClient({"success": True})
    client.update_succeeds = False
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    with pytest.raises(HyMemoryBridgeError) as error:
        asyncio.run(bridge.update("memory-hy-never-had", "用户的花名是白泽"))

    assert str(error.value) == "hy_memory_update_rejected"


def test_bridge_semantic_search_can_span_isolated_hy_agents(tmp_path) -> None:
    client = FakeHyMemoryClient(
        {"success": True},
        search_result={
            "memories": [
                {"memory_id": "semantic-1"},
                {"memory_id": "semantic-1"},
                {"memory_id": "semantic-2"},
            ]
        },
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    memory_ids = asyncio.run(
        bridge.search_ids(
            "我的长期偏好是什么？",
            user_id="local",
            agent_id="*",
        )
    )

    assert memory_ids == ("semantic-1", "semantic-2")
    assert client.search_calls[0]["agent_ids"] == []
    assert client.search_calls[0]["reader"] == "hybrid"
    assert client.search_calls[0]["schema_min_score"] == client.search_calls[0]["min_score"]
    assert client.search_calls[0]["schema_limit"] == 12
    assert "profile_min_score" not in client.search_calls[0]
    assert "profile_limit" not in client.search_calls[0]


def test_bridge_preserves_sdk_formatted_search_content(tmp_path) -> None:
    formatted = "Memory: 用户偏好本地优先。\nWhen: 2026-09-12 08:00:00 UTC"
    client = FakeHyMemoryClient(
        {"success": True},
        search_result={
            "memories": [
                {
                    "memory_id": "semantic-1",
                    "content": formatted,
                    "score": 0.91,
                    "memory_at": 1789200000,
                }
            ]
        },
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    hits = asyncio.run(
        bridge.search_memories(
            "我的长期偏好是什么？",
            user_id="local",
            agent_id="*",
        )
    )

    assert hits[0].formatted_content == formatted
    assert hits[0].sdk_formatted is True
    assert hits[0].formatting_complete is True


def test_search_ids_does_not_create_nodes_tasks_or_collections(tmp_path) -> None:
    """Search is a read. Creating store objects here would invent a graph.

    A query that only needs IDs must not add memories, digest System 2,
    open a new collection, or enqueue a task. The fake records those
    mutations; this test is meaningless unless a write on the search
    path would turn it red.
    """
    client = FakeHyMemoryClient(
        {"success": True},
        search_result={"memories": [{"memory_id": "semantic-read-only"}]},
    )
    bridge = HyMemoryBridge(
        profile=snapshot(
            ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            has_api_key=False,
        ),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )

    memory_ids = asyncio.run(
        bridge.search_ids(
            "我的长期偏好是什么？",
            user_id="local",
            agent_id="*",
        )
    )

    assert memory_ids == ("semantic-read-only",)
    assert len(client.search_calls) == 1
    assert client.calls == []


def test_anthropic_profile_is_not_claimed_supported_by_current_sdk(
    tmp_path,
) -> None:
    profile = snapshot(
        ProviderKind.ANTHROPIC,
        base_url="https://api.anthropic.com",
        has_api_key=True,
    )

    with pytest.raises(UnsupportedHyMemoryProviderError):
        build_hy_memory_config(profile, api_key="secret", data_dir=tmp_path)


def test_hy_default_does_not_fall_back_to_ambient_openai(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")
    profile = snapshot(
        ProviderKind.HY_DEFAULT,
        base_url=None,
        has_api_key=False,
    )

    with pytest.raises(UnsupportedHyMemoryProviderError) as captured:
        build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)

    assert str(captured.value) == "hy_default_not_available"


def test_hy_environment_confines_logs_and_disables_telemetry(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEMORY_LOG_DIR", "/tmp/hostile")
    monkeypatch.setenv("CHROMA_OTEL_COLLECTION_ENDPOINT", "https://collector.example")

    configure_hy_memory_environment(tmp_path)

    expected_root = str((tmp_path / "hy-memory" / "governed-v2").resolve())
    assert os.environ["MEMORY_DATA_DIR"] == expected_root
    assert os.environ["MEMORY_LOG_DIR"] == f"{expected_root}/logs"
    assert os.environ["MEMORY_LOG_LEVEL"] == "INFO"
    assert os.environ["ANONYMIZED_TELEMETRY"] == "False"
    assert "CHROMA_OTEL_COLLECTION_ENDPOINT" not in os.environ


def test_hy_runtime_matches_public_sdk_without_reranker_or_service_backends(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEMORY_VECTOR_STORE", "qdrant")
    monkeypatch.setenv("MEMORY_CACHE_BACKEND", "mysql")
    monkeypatch.setenv("MEMORY_GRAPH_PROVIDER", "neo4j")
    monkeypatch.setenv("MEMORY_MYSQL_PASSWORD", "ambient-database-secret")
    monkeypatch.setenv("NEO4J_PASSWORD", "ambient-graph-secret")
    monkeypatch.setenv("MEMORY_LOCAL_RERANK_ENABLED", "true")
    monkeypatch.setenv("MEMORY_GRAPH_RERANK_ENABLED", "true")
    monkeypatch.setenv("MEMORY_LLM_API_KEY", "ambient-secret")
    monkeypatch.setenv("MEMORY_LLM_EXTRA_HEADERS", '{"Authorization":"ambient"}')
    monkeypatch.setenv("MEMORY_EMBEDDER_EXTRA_HEADERS", '{"X-Ambient":"true"}')
    monkeypatch.setenv("MEMORY_EMBEDDER_EXTRA_BODY", '{"route":"ambient"}')
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )

    configure_hy_memory_environment(tmp_path)
    config = build_hy_memory_config(profile, api_key=None, data_dir=tmp_path)

    assert HY_MEMORY_DISTRIBUTION == "hy-memory"
    assert HY_MEMORY_VERSION == "2.0.4"
    assert HY_MEMORY_MODE == "ultra"
    assert HY_MEMORY_READER == "hybrid"
    assert os.environ["MEMORY_MODE"] == "ultra"
    # The graph store is closed by default (see `graph_store_enabled`): opening
    # kuzu is what produced the teardown crash. `ultra` would force it open, so
    # the mode moves to `pro` and the graph stays off until someone opts in.
    assert os.environ["MEMORY_ENABLE_GRAPH"] == "false"
    assert config.mode == "pro"
    assert os.environ["HY_MEMORY_READER"] == "hybrid"
    assert os.environ["MEMORY_ENABLE_INTENTION"] == "false"
    assert os.environ["MEMORY_FEW_SHOT_ENABLED"] == "true"
    assert os.environ["MEMORY_HYBRID_V2_QUERY_EXPAND"] == "1"
    assert os.environ["MEMORY_HYBRID_V2_GEO_INTENT_PRIOR"] == "0.6"
    assert os.environ["MEMORY_HYBRID_V2_DIVERSITY"] == "1"
    assert os.environ["MEMORY_HYBRID_V2_TEMPORAL_ADMIT_PRIOR"] == "0.55"
    assert os.environ["MEMORY_HYBRID_V2_TEMPORAL_BOOST"] == "0.18"
    assert os.environ["MEMORY_CROSS_DOMAIN_CON_THRESHOLD"] == "0.5"
    assert os.environ["MEMORY_READER_ENABLE_SUMMARY"] == "false"
    assert os.environ["MEMORY_EPISODE_RECALL_ENABLED"] == "true"
    assert os.environ["MEMORY_DYNAMIC_EPISODE_ENABLED"] == "true"
    assert os.environ["MEMORY_EPISODE_CAPSULE_CONTEXT_ENABLED"] == "true"
    assert os.environ["MEMORY_LOCAL_RERANK_ENABLED"] == "false"
    assert os.environ["MEMORY_GRAPH_RERANK_ENABLED"] == "false"
    assert config.vector_store.provider == "chroma"
    assert config.cache.backend == "sqlite"
    assert config.cache.mysql_password is None
    assert config.graph_store.provider == "kuzu"
    assert config.graph_store.password is None
    assert config.llm.api_key == "ollama"
    assert config.llm.extra_headers is None
    assert config.llm.eval_user is None
    assert config.llm.eval_apikey is None
    assert config.llm.few_shot_enabled is True
    assert config.llm.enable_query_rewrite is False
    assert config.llm.enable_intention is False
    assert config.recall.episode_recall_enabled is True
    assert config.recall.dynamic_episode_enabled is True
    assert config.recall.episode_capsule_context_enabled is True
    assert config.recall.strength_enabled is False
    assert config.recall.access_tracking_enabled is False
    assert config.recall.local_rerank_enabled is False
    assert config.graph_store.graph_rerank_enabled is False
    assert config.embedder.extra_headers is None
    assert config.embedder.extra_body is None
    assert config.api.api_keys == []


def test_ingest_refuses_a_request_whose_workspace_ids_disagree_with_the_context(tmp_path):
    """The bridge identity check must include the workspace set.

    HyWriteBindings already refuses a context that mixes workspaces, but the
    bridge's own request-compatibility check compared only
    (user_id, source_id, conversation_id). A request could therefore declare a
    different `workspace_ids` than the bound context and still be sent to HY,
    so HY would scope the write to a workspace the evidence never came from.
    The bridge must refuse that disagreement, exactly as it refuses a
    conversation/source mismatch.
    """
    from one_memory.hy_source_context import HySourceContext
    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore

    store = EvidenceStore(tmp_path / "evidence.db")
    record = store.put(
        EvidenceInput(
            source_id="cursor",
            workspace="workspace:v1:project-a",
            conversation_id="atlas",
            message_id="now",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="项目 A 的一轮。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    client = FakeHyMemoryClient(
        {
            "success": True,
            "memories": [
                {
                    "content": "项目 A 的一轮。",
                    "owner": "user",
                    "memory_id": "m1",
                    "layer": "working_fact",
                }
            ],
        }
    )
    bridge = HyMemoryBridge(
        profile=snapshot(ProviderKind.OLLAMA, base_url="http://127.0.0.1:11434", has_api_key=False),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )
    request = HyMemoryIngestRequest(
        request_id="ingest-1",
        user_id="local",
        source_id="cursor",
        session_id="atlas",
        turns=(HyMemoryTurn(role="user", content="项目 A 的一轮。"),),
        evidence_ids=(record.evidence_id,),
        # The bound context is project-a; the request lies and says project-b.
        workspace_ids=("workspace:v1:project-b",),
        source_context=HySourceContext(current=(record,)),
    )
    with pytest.raises(HyMemoryBridgeError, match="hy_memory_write_verification_context_scope"):
        asyncio.run(bridge.ingest(request))
    assert client.calls == [], "a workspace-mismatched request must not reach HY"


def test_the_graph_store_stays_closed_by_default(monkeypatch) -> None:
    """The packaged app sets nothing, so kuzu must not be opened."""
    from one_memory.hy_memory_bridge import graph_store_enabled

    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)
    assert graph_store_enabled() is False


def test_the_graph_store_opens_only_when_opted_in(monkeypatch) -> None:
    from one_memory.hy_memory_bridge import graph_store_enabled

    monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "kuzu")
    assert graph_store_enabled() is True
    monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "none")
    assert graph_store_enabled() is False


def test_the_client_mode_follows_the_graph_store_switch(tmp_path, monkeypatch) -> None:
    """`ultra` forces kuzu open, so the mode handed to HY must not be constant.

    build_hy_memory_config picks the mode from `graph_store_enabled`, but the
    client was constructed with `HY_MEMORY_MODE` hard-coded, which reopened
    the graph the config had just closed. The packaged app then segfaulted in
    kuzu::main::Database::~Database(). Assert on the value HY actually
    receives, so the switch cannot silently stop being wired again.
    """
    from one_memory import hy_memory_bridge

    seen: list[str] = []

    class ModeSpyClient:
        def __init__(self, *, config, mode, runtime=None):
            del config
            assert runtime is not None
            seen.append(mode)

        def close(self) -> None:
            pass

    real_import = hy_memory_bridge.import_module

    def fake_import(name: str):
        if name == "hy_memory.client":
            return SimpleNamespace(HyMemoryClient=ModeSpyClient)
        return real_import(name)

    monkeypatch.setattr(hy_memory_bridge, "import_module", fake_import)
    monkeypatch.setattr(hy_memory_bridge, "_shared_hy_runtime", lambda *args: object())
    monkeypatch.setattr(hy_memory_bridge, "_release_shared_hy_runtime", lambda *args: None)
    monkeypatch.setattr(hy_memory_bridge, "reattach_legacy_vector_collection", lambda *args: None)
    monkeypatch.setattr(hy_memory_bridge, "inspect_graph_width_mismatch", lambda *args: None)
    monkeypatch.setattr(
        "one_memory.hy_response_guard.install_hy_write_guards", lambda _client: None
    )
    profile = snapshot(
        ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        has_api_key=False,
    )

    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)
    hy_memory_bridge.create_hy_memory_client(profile, api_key=None, data_dir=tmp_path).close()

    monkeypatch.setenv("ONE_MEMORY_GRAPH_STORE", "kuzu")
    hy_memory_bridge.create_hy_memory_client(profile, api_key=None, data_dir=tmp_path).close()

    assert seen == ["pro", HY_MEMORY_MODE]


def test_native_mutation_delta_reports_coding_deletes_and_supersedes() -> None:
    raw = {
        "ops": [
            {
                "action": "DELETE",
                "target_experience_id": "old-experience",
            }
        ],
        "fact_ops": [
            {
                "action": "SUPERSEDE",
                "fact_id": "old-fact",
                "superseded_by": "new-fact",
            }
        ],
    }

    result = asyncio.run(
        _with_native_mutation_delta(
            SimpleNamespace(),
            HyMemoryIngestResult(success=True),
            raw,
            (),
        )
    )

    assert result.retired_memory_ids == ("old-experience", "old-fact")
    assert result.superseded_memory_ids == {"old-fact": ("new-fact",)}


def test_native_mutation_delta_reads_chat_status_from_the_sdk() -> None:
    class Client:
        def get(self, memory_id: str) -> Mapping[str, object]:
            assert memory_id == "old-chat"
            return {
                "memory_id": memory_id,
                "content": "old",
                "status": "superseded",
                "superseded_by": ["new-chat"],
            }

    guard = SimpleNamespace(mutation_target_ids={"old-chat"})
    result = asyncio.run(
        _with_native_mutation_delta(
            Client(),
            HyMemoryIngestResult(success=True),
            {},
            (guard,),
        )
    )

    assert result.retired_memory_ids == ("old-chat",)
    assert result.superseded_memory_ids == {"old-chat": ("new-chat",)}
