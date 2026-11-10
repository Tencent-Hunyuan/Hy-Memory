"""The `storage_backends` setting: what it accepts, where its secrets go, what it builds.

Neo4j/Memgraph and Qdrant servers are not available in CI: the probe is a
stand-in here and the SDK client is never opened against them. The Qdrant
path end to end, through the pinned SDK, is `test_storage_backend_qdrant`;
`test_storage_backend_neo4j_live` runs against a real server when one is named.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from one_memory import hy_sdk_runtime
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.hy_graph_admin import inspect_graph_width_mismatch
from one_memory.hy_graph_projection import _listed
from one_memory.memory_server.config_state import ConfigStore
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind
from one_memory.secrets import InMemorySecretStore
from one_memory.storage_backend_probe import (
    BackendProbe,
    StorageBackendHealth,
    StorageProbe,
    classify_probe_error,
    driver_available,
    probe_storage,
)
from one_memory.storage_backends import (
    GRAPH_SECRET_REF,
    KEEP,
    VECTOR_SECRET_REF,
    GraphBackend,
    ResolvedStorage,
    SecretChange,
    StorageBackends,
    StorageBackendStore,
    VectorBackend,
    capabilities,
    custom_backends_configured,
    embedding_switch_refusal,
    storage_kwargs,
)

TOKEN = "storage-backends-token"
NEO4J = {"provider": "neo4j", "url": "bolt://graph.internal:7687", "username": "neo4j"}
QDRANT = {"provider": "qdrant", "host": "vectors.internal", "port": 6333}


def _profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="p",
        revision=1,
        provider_kind=ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        model="memory-model",
        embedding_provider_kind=ProviderKind.LOCAL_ONNX,
        embedding_model="builtin",
        embedding_dimensions=512,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8192,
        has_api_key=False,
    )


# --- the setting ------------------------------------------------------------------


def _code(error: pytest.ExceptionInfo[ValidationError]) -> str:
    return str(error.value.errors()[0]["ctx"]["error"])


@pytest.mark.parametrize(
    ("graph", "code"),
    [
        (
            {"provider": "kuzu", "url": "bolt://x:7687"},
            "storage_backend_graph_kuzu_takes_no_connection",
        ),
        ({"provider": "neo4j"}, "storage_backend_graph_url_required"),
        ({"provider": "neo4j", "url": "http://x:7474"}, "storage_backend_graph_url_invalid"),
        ({"provider": "neo4j", "url": "bolt://u:p@x:7687"}, "storage_backend_graph_url_invalid"),
        ({"provider": "neo4j", "url": "bolt://x:99999"}, "storage_backend_graph_url_invalid"),
        (
            {"provider": "memgraph", "url": "bolt://x", "database": "a b"},
            "storage_backend_graph_database_invalid",
        ),
    ],
)
def test_a_graph_setting_that_cannot_be_used_is_refused_with_its_code(graph, code) -> None:
    with pytest.raises(ValidationError) as error:
        GraphBackend(**graph)
    assert _code(error) == code


@pytest.mark.parametrize(
    ("vector", "code"),
    [
        ({"provider": "chroma", "host": "x"}, "storage_backend_vector_chroma_takes_no_connection"),
        ({"provider": "faiss"}, "storage_backend_faiss_unsupported"),
        ({"provider": "qdrant"}, "storage_backend_vector_host_required"),
        ({"provider": "qdrant", "host": "bad host"}, "storage_backend_vector_host_invalid"),
        ({"provider": "qdrant", "host": "x", "port": 0}, "storage_backend_vector_port_invalid"),
        (
            {"provider": "qdrant", "host": "x", "collection_prefix": "UP"},
            "storage_backend_vector_prefix_invalid",
        ),
    ],
)
def test_a_vector_setting_that_cannot_be_used_is_refused_with_its_code(vector, code) -> None:
    with pytest.raises(ValidationError) as error:
        VectorBackend(**vector)
    assert _code(error) == code


def test_the_default_is_kuzu_and_chroma_and_resolves_to_nothing(tmp_path: Path) -> None:
    store = StorageBackendStore(ConfigStore(tmp_path), InMemorySecretStore())
    assert store.settings() == StorageBackends()
    assert store.settings().is_default and store.settings().label == "Kuzu + Chroma"
    assert store.resolved() is None
    assert storage_kwargs(None) == {}
    # An untouched install's config.json keeps the shape older builds read.
    ConfigStore(tmp_path).set_sedimentation_enabled(True)
    assert "storage_backends" not in json.loads((tmp_path / "config.json").read_text())


def test_settings_round_trip_with_secrets_only_in_the_secret_store(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    store = StorageBackendStore(ConfigStore(tmp_path), secrets)
    target = StorageBackends(
        graph=GraphBackend(**NEO4J, database="memories"),
        vector=VectorBackend(**QDRANT),
    ).with_namespace()
    store.apply(
        target,
        SecretChange(keep=False, value="graph-pass"),
        SecretChange(keep=False, value="vector-key"),
    )

    written = (tmp_path / "config.json").read_text()
    assert "graph-pass" not in written and "vector-key" not in written
    document = json.loads(written)["storage_backends"]
    assert document["graph"] == {**NEO4J, "database": "memories"}
    assert document["vector"] == QDRANT
    assert document["namespace"].startswith("mh") and len(document["namespace"]) == 10
    assert secrets.resolve(GRAPH_SECRET_REF) == "graph-pass"
    assert secrets.resolve(VECTOR_SECRET_REF) == "vector-key"

    # A new process reads the same setting back, secrets included.
    again = StorageBackendStore(ConfigStore(tmp_path), secrets)
    resolved = again.resolved()
    assert resolved == ResolvedStorage(
        graph_provider="neo4j",
        vector_provider="qdrant",
        graph_url=NEO4J["url"],
        graph_username="neo4j",
        graph_database="memories",
        vector_host="vectors.internal",
        vector_port=6333,
        vector_prefix=document["namespace"],
        graph_password="graph-pass",
        vector_api_key="vector-key",
    )
    assert "graph-pass" not in repr(resolved) and "vector-key" not in repr(resolved)
    assert custom_backends_configured(tmp_path)

    # Kuzu again: the graph password goes; the vector key stays with Qdrant.
    back = target.model_copy(update={"graph": GraphBackend()})
    again.apply(back, KEEP, KEEP)
    assert secrets.resolve(GRAPH_SECRET_REF) is None
    assert secrets.resolve(VECTOR_SECRET_REF) == "vector-key"
    assert again.resolved().graph_password is None  # type: ignore[union-attr]

    # Reset: every secret goes, the namespace stays for the next `set`.
    removing = SecretChange(keep=False, value=None)
    again.apply(StorageBackends(namespace=target.namespace), removing, removing)
    assert secrets.resolve(VECTOR_SECRET_REF) is None
    assert again.resolved() is None and not custom_backends_configured(tmp_path)
    assert json.loads((tmp_path / "config.json").read_text())["storage_backends"] == {
        "graph": {"provider": "kuzu"},
        "vector": {"provider": "chroma"},
        "namespace": target.namespace,
    }


def test_an_unreadable_setting_is_never_taken_for_the_default(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"storage_backends": {"vector": {"provider": "milvus"}}})
    )
    assert custom_backends_configured(tmp_path)


# --- the SDK config ---------------------------------------------------------------


def test_the_setting_is_applied_to_the_sdk_config_and_stray_env_is_not(
    tmp_path: Path, monkeypatch
) -> None:
    for name, value in (
        ("MEMORY_GRAPH_PROVIDER", "memgraph"),
        ("MEMORY_VECTOR_STORE", "faiss"),
        ("MEMORY_VECTOR_HOST", "ambient.example"),
    ):
        monkeypatch.setenv(name, value)
    storage = ResolvedStorage(
        graph_provider="neo4j",
        vector_provider="qdrant",
        graph_url="bolt://graph.internal:7687",
        graph_username="neo4j",
        graph_database="memories",
        vector_host="vectors.internal",
        vector_port=6334,
        vector_prefix="mh0123abcd",
        graph_password="graph-pass",
        vector_api_key="vector-key",
    )
    default = hy_sdk_runtime.build_sdk_config(_profile(), api_key=None, data_dir=tmp_path)
    config = hy_sdk_runtime.build_sdk_config(
        _profile(), api_key=None, data_dir=tmp_path, storage=storage
    )

    vector, graph = config.vector_store, config.graph_store
    assert (vector.provider, vector.host, vector.port, vector.api_key) == (
        "qdrant",
        "vectors.internal",
        6334,
        "vector-key",
    )
    assert vector.collection_name == f"mh0123abcd_{default.vector_store.collection_name}"
    assert vector.embedding_dims == default.vector_store.embedding_dims
    assert (graph.provider, graph.url, graph.username, graph.password, graph.database) == (
        "neo4j",
        "bolt://graph.internal:7687",
        "neo4j",
        "graph-pass",
        "memories",
    )
    # Raw, coding, cache and history stay in this home's SQLite files.
    assert config.cache.db_path == default.cache.db_path
    assert config.coding.db_path == default.coding.db_path
    # The default pair is exactly what it always was.
    assert (default.vector_store.provider, default.graph_store.provider) == ("chroma", "kuzu")
    assert default.vector_store.host is None
    # A server graph has no file to read a width from.
    assert inspect_graph_width_mismatch(config) is None


def test_only_the_vector_store_moves_when_only_it_is_set(tmp_path: Path) -> None:
    storage = ResolvedStorage(
        graph_provider="kuzu", vector_provider="qdrant", vector_host="v", vector_port=6333
    )
    config = hy_sdk_runtime.build_sdk_config(
        _profile(), api_key=None, data_dir=tmp_path, storage=storage
    )
    assert config.graph_store.provider == "kuzu" and config.graph_store.url is None
    assert config.vector_store.provider == "qdrant"


def test_a_handle_passes_its_storage_to_every_client_it_builds(tmp_path: Path, monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    def acquire(profile, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(client=object(), close=lambda: None)

    monkeypatch.setattr(hy_sdk_runtime, "acquire_client", acquire)
    storage = ResolvedStorage(
        graph_provider="memgraph", vector_provider="chroma", graph_url="bolt://g"
    )
    for value in (None, storage):
        handle = hy_sdk_runtime.SdkClientHandle(
            _profile(), api_key=None, data_dir=tmp_path, mode="pro", storage=value
        )
        asyncio.run(handle.client())
    assert "storage" not in seen[0]
    assert seen[1]["storage"] == storage


# --- what works where ---------------------------------------------------------------


def test_capabilities_per_backend_and_mode() -> None:
    default = StorageBackends()
    neo4j = StorageBackends(graph=GraphBackend(**NEO4J))
    qdrant = StorageBackends(vector=VectorBackend(**QDRANT))

    assert {name: value.state for name, value in capabilities(default, mode="ultra").items()} == {
        "embedding_switch": "works",
        "backup": "works",
        "restore": "works",
        "graph_export": "works",
        "graph_view": "works",
        "graph_width_check": "works",
        "embedding_space_scan": "works",
        "graph_open_guard": "works",
        "upgrade_migration": "works",
    }
    # Neo4j blocks a switch only where the graph is open: ultra.
    assert embedding_switch_refusal(neo4j, mode="pro") is None
    assert embedding_switch_refusal(neo4j, mode="ultra") == "embedding_switch_backend_unsupported"
    on_neo4j = capabilities(neo4j, mode="ultra")
    assert on_neo4j["graph_open_guard"].code == "graph_open_guard_not_kuzu"
    assert on_neo4j["graph_export"].code == "graph_export_backend_unsupported"
    assert on_neo4j["graph_width_check"].state == "skipped"
    assert on_neo4j["embedding_space_scan"].state == "works"
    assert on_neo4j["upgrade_migration"].code == "upgrade_migration_skipped_custom_backend"
    # Qdrant: the SDK cannot re-embed it (hy-memory 2.0.15), in any mode.
    assert embedding_switch_refusal(qdrant, mode="lite") == "embedding_switch_backend_unsupported"
    assert capabilities(qdrant, mode="pro")["embedding_space_scan"].state == "skipped"
    # A backup exports Qdrant's memories; writing them back needs an SDK API.
    assert capabilities(qdrant, mode="pro")["backup"].state == "works"
    assert capabilities(qdrant, mode="pro")["restore"].code == "backup_restore_remote_sdk_unsupported"
    assert capabilities(neo4j, mode="ultra")["restore"].state == "works"


def test_the_graph_view_reads_rows_from_either_graph_store() -> None:
    kuzu_rows = [["m1", "content", "L2", 0.9]]
    neo4j_rows = [{"m.node_id": "m1", "m.content": "content", "m.layer": "L2", "m.confidence": 0.9}]
    assert _listed(kuzu_rows) == _listed(neo4j_rows) == kuzu_rows


# --- the probe --------------------------------------------------------------------


def _closed_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_the_probe_reports_an_unreachable_server_with_a_stable_code() -> None:
    port = _closed_port()
    probe = probe_storage(
        ResolvedStorage(
            graph_provider="neo4j",
            vector_provider="qdrant",
            graph_url=f"bolt://127.0.0.1:{port}",
            graph_username="neo4j",
            graph_password="secret-pass",
            vector_host="127.0.0.1",
            vector_port=port,
        ),
        timeout_s=2,
    )
    assert not probe.ok
    assert probe.graph.code == probe.vector.code == "storage_backend_unreachable"
    assert "secret-pass" not in probe.model_dump_json()


def test_the_default_pair_is_local_and_never_probed_over_a_network() -> None:
    probe = probe_storage(None)
    assert probe.ok and probe.graph.state == probe.vector.state == "local"


def test_the_drivers_of_every_shipped_backend_are_importable() -> None:
    for provider in ("kuzu", "neo4j", "memgraph", "chroma", "qdrant"):
        assert driver_available(provider), provider


def test_probe_error_classification() -> None:
    class AuthError(Exception):
        pass

    class Forbidden(Exception):
        status_code = 403

    assert classify_probe_error(AuthError()) == "storage_backend_auth_failed"
    assert classify_probe_error(Forbidden()) == "storage_backend_auth_failed"
    assert classify_probe_error(ConnectionRefusedError()) == "storage_backend_unreachable"
    assert classify_probe_error(ValueError("x")) == "storage_backend_probe_failed"


def test_health_never_holds_a_status_request_on_the_network() -> None:
    storage = ResolvedStorage(graph_provider="kuzu", vector_provider="qdrant", vector_host="v")
    health = StorageBackendHealth(resolved=lambda: storage, max_age_s=3600)
    ok = StorageProbe(
        ok=True,
        graph=BackendProbe(provider="kuzu", ok=True, state="local"),
        vector=BackendProbe(provider="qdrant", ok=True, state="ok"),
    )
    health._running = True  # a refresh already under way: snapshot must not start one
    assert health.snapshot() is None
    health.record(storage, ok)
    assert health.snapshot() == ok


# --- the HTTP surface ---------------------------------------------------------------


def _probe(*, graph_nodes: int | None = None, vector_ok: bool = True):
    calls: list[ResolvedStorage | None] = []

    def probe(resolved: ResolvedStorage | None) -> StorageProbe:
        calls.append(resolved)
        graph_provider = resolved.graph_provider if resolved else "kuzu"
        vector_provider = resolved.vector_provider if resolved else "chroma"
        graph = BackendProbe(
            provider=graph_provider,
            ok=True,
            state="local" if graph_provider == "kuzu" else "ok",
            existing_memory_nodes=graph_nodes if graph_provider != "kuzu" else None,
        )
        vector = BackendProbe(
            provider=vector_provider,
            ok=vector_ok,
            state="local" if vector_provider == "chroma" else ("ok" if vector_ok else "failed"),
            code=None if vector_ok else "storage_backend_unreachable",
        )
        return StorageProbe(ok=graph.ok and vector.ok, graph=graph, vector=vector)

    return probe, calls


def run_app(tmp_path, monkeypatch, scenario, *, probe=None) -> None:
    async def main() -> None:
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr(TOKEN),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )
        app = create_app(settings, secret_store=InMemorySecretStore())

        @asynccontextmanager
        async def write_client():
            yield SimpleNamespace()

        async with app.router.lifespan_context(app):
            monkeypatch.setattr(app.state.sdk_write_coordinator, "use_client", write_client)
            if probe is not None:
                app.state.storage_backend_hooks.probe = probe
            # Post-ready recovery decides the startup scan; until then a
            # backend change is refused as embedding maintenance.
            for _ in range(500):
                if app.state.recovery in {"done", "failed"}:
                    break
                await asyncio.sleep(0.01)
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as client:
                await scenario(app, client)

    asyncio.run(main())


PATH = "/api/v1/settings/storage-backends"


@pytest.mark.fresh_sdk
def test_api_show_set_test_reset_and_client_rebuild(tmp_path, monkeypatch) -> None:
    probe, calls = _probe()

    async def scenario(app, client):
        shown = (await client.get(PATH)).json()
        assert shown["is_default"] is True and shown["label"] == "Kuzu + Chroma"
        assert shown["graph"]["provider"] == "kuzu" and shown["vector"]["provider"] == "chroma"
        assert shown["capabilities"]["embedding_switch"]["state"] == "works"
        assert [item["provider"] for item in shown["providers"]["vector"]] == [
            "chroma",
            "qdrant",
            "faiss",
        ]
        assert shown["providers"]["vector"][2]["supported"] is False
        reads = app.state.sdk_client_handle
        writes = app.state.sdk_write_coordinator
        assert reads.current_storage() is None and writes.storage_backends() is None
        retired: list[str] = []
        original = writes.retire_stale_handles

        async def retire():
            retired.append("writes")
            await original()

        monkeypatch.setattr(writes, "retire_stale_handles", retire)

        tested = await client.post(
            f"{PATH}/test", json={"graph": {**NEO4J, "password": "graph-pass"}}
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["label"] == "Neo4j + Chroma"
        assert calls[-1].graph_password == "graph-pass"
        # A test saves nothing.
        assert (await client.get(PATH)).json()["is_default"] is True

        changed = await client.put(
            PATH,
            json={
                "graph": {**NEO4J, "password": "graph-pass"},
                "vector": {**QDRANT, "api_key": "vector-key"},
            },
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["changed"] is True and body["notice"] == "existing_memories_not_moved"
        assert body["label"] == "Neo4j + Qdrant" and body["previous_label"] == "Kuzu + Chroma"
        assert body["graph"]["has_password"] is True and body["vector"]["has_api_key"] is True
        assert "graph-pass" not in changed.text and "vector-key" not in changed.text
        assert retired == ["writes"]
        # Every client built from here on is built on the new stores.
        resolved = reads.current_storage()
        assert resolved == writes.storage_backends() == calls[-1]
        assert resolved.graph_password == "graph-pass" and resolved.vector_api_key == "vector-key"

        processing = (await client.get("/api/v1/memory-processing")).json()
        assert processing["pipeline"]["storage_backends"] == {
            "graph": "neo4j",
            "vector": "qdrant",
            "is_default": False,
            "label": "Neo4j + Qdrant",
            "health": "ok",
            "health_code": None,
        }

        # Setting the same again changes nothing and rebuilds nothing.
        same = (await client.put(PATH, json={"graph": NEO4J})).json()
        assert same["changed"] is False and same["notice"] is None and retired == ["writes"]

        # An absent password keeps the stored one; null removes it.
        removed = (await client.put(PATH, json={"graph": {**NEO4J, "password": None}})).json()
        assert removed["graph"]["has_password"] is False
        assert reads.current_storage().graph_password is None

        # A stored secret is never sent to another server.
        await client.put(PATH, json={"graph": {**NEO4J, "password": "graph-pass"}})
        moved = (
            await client.put(PATH, json={"graph": {**NEO4J, "url": "bolt://other.internal:7687"}})
        ).json()
        assert moved["graph"]["has_password"] is False
        assert reads.current_storage().graph_password is None
        assert reads.current_storage().vector_api_key == "vector-key"

        reset = await client.delete(PATH)
        assert reset.status_code == 200, reset.text
        assert reset.json()["is_default"] is True
        assert reset.json()["notice"] == "existing_memories_not_moved"
        assert reads.current_storage() is None

        for bad, code in (
            ({"vector": {"provider": "faiss"}}, "storage_backend_faiss_unsupported"),
            ({"graph": {"provider": "neo4j"}}, "storage_backend_graph_url_required"),
            ({"graph": {"provider": "orientdb"}}, None),
            ({"vector": QDRANT, "extra": 1}, None),
        ):
            response = await client.put(PATH, json=bad)
            assert response.status_code == 422, bad
            if code is not None:
                assert response.json()["detail"] == code
        assert (await client.get(PATH, params={"x": "1"})).status_code == 422

    run_app(tmp_path, monkeypatch, scenario, probe=probe)


@pytest.mark.fresh_sdk
def test_a_store_that_does_not_answer_is_never_saved(tmp_path, monkeypatch) -> None:
    probe, _calls = _probe(vector_ok=False)

    async def scenario(app, client):
        response = await client.put(PATH, json={"vector": QDRANT})
        assert response.status_code == 422
        assert response.json()["detail"] == "storage_backend_unreachable"
        assert response.json()["probe"]["vector"]["code"] == "storage_backend_unreachable"
        assert (await client.get(PATH)).json()["is_default"] is True
        assert not custom_backends_configured(tmp_path / "data")

    run_app(tmp_path, monkeypatch, scenario, probe=probe)


@pytest.mark.fresh_sdk
def test_a_graph_database_that_already_holds_a_graph_must_be_adopted(tmp_path, monkeypatch) -> None:
    probe, _calls = _probe(graph_nodes=12)

    async def scenario(app, client):
        tested = (await client.post(f"{PATH}/test", json={"graph": NEO4J})).json()
        assert tested["graph_adoption_required"] is True
        refused = await client.put(PATH, json={"graph": NEO4J})
        assert refused.status_code == 409
        assert refused.json()["detail"] == "storage_backend_graph_not_empty"
        adopted = await client.put(PATH, json={"graph": NEO4J, "adopt_existing_graph": True})
        assert adopted.status_code == 200, adopted.text
        # The graph in use is not asked again.
        again = await client.put(PATH, json={"graph": NEO4J, "vector": QDRANT})
        assert again.status_code == 200, again.text

    run_app(tmp_path, monkeypatch, scenario, probe=probe)


@pytest.mark.fresh_sdk
def test_a_server_graph_skips_kuzu_maintenance_and_refuses_an_ultra_switch(
    tmp_path, monkeypatch
) -> None:
    from one_memory.api import _open_graph_store_at_startup

    probe, _calls = _probe()

    async def scenario(app, client):
        assert (await client.put(PATH, json={"graph": NEO4J})).status_code == 200
        monkeypatch.setattr(
            app.state.model_profile_store, "active_identity", lambda: ("active-profile", 1)
        )
        assert (
            await client.put("/api/v1/settings/memory-mode", json={"mode": "ultra"})
        ).status_code == 200
        await app.state.memory_graph_backfill.wait()

        # The #413 open probe and quarantine guard a Kuzu file only.
        probed: list[object] = []
        monkeypatch.setattr(
            app.state.graph_open_guard, "open_at_startup", lambda *a, **k: probed.append(a)
        )
        assert await _open_graph_store_at_startup(app) == "skipped" and probed == []

        width = await client.get("/api/v1/graph-width")
        assert width.status_code == 200 and width.json()["mismatch"] is None
        decided = await client.post("/api/v1/graph-width", json={"decision": "export_first"})
        assert decided.status_code == 409
        assert decided.json()["detail"] == "graph_export_backend_unsupported"

        shown = (await client.get(PATH)).json()
        assert shown["capabilities"]["embedding_switch"] == {
            "state": "refused",
            "code": "embedding_switch_backend_unsupported",
        }
        assert app.state.embedding_switch.storage_refusal() == (
            "embedding_switch_backend_unsupported"
        )
        # Chroma is still the vector store: the scan and its maintenance run.
        assert app.state.embedding_switch.chroma_is_live() is True

    run_app(tmp_path, monkeypatch, scenario, probe=probe)


@pytest.mark.fresh_sdk
def test_backends_do_not_change_during_embedding_maintenance(tmp_path, monkeypatch) -> None:
    probe, calls = _probe()

    async def scenario(app, client):
        app.state.embedding_switch._scan_hold = True
        response = await client.put(PATH, json={"vector": QDRANT})
        assert response.status_code == 409
        assert response.json()["detail"] == "embedding_maintenance_in_progress"
        assert calls == []
        app.state.embedding_switch._scan_hold = False

    run_app(tmp_path, monkeypatch, scenario, probe=probe)


# --- upgrade migrations -------------------------------------------------------------


def _custom_home(tmp_path: Path) -> Path:
    from one_memory.target_schema import bootstrap_fresh_management_schema

    assert bootstrap_fresh_management_schema(tmp_path / "one-memory.sqlite3")
    with sqlite3.connect(tmp_path / "one-memory.sqlite3") as database:
        database.execute(
            "UPDATE memory_authority_state SET authority='sdk', migration_id='m1' WHERE singleton=1"
        )
        # Leftovers a default home's upgrade would clean up.
        database.execute("CREATE TABLE evidence (evidence_id TEXT)")
    store = ConfigStore(tmp_path)
    store.set_storage_backends(StorageBackends(vector=VectorBackend(**QDRANT)).with_namespace())
    vector = tmp_path / "hy-memory" / "governed-v2" / "vector"
    vector.mkdir(parents=True)
    (vector / "chroma.sqlite3").write_bytes(b"the store that was left")
    return vector


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_upgrade_migration_skips_a_custom_backend_home_and_touches_nothing(tmp_path) -> None:
    from one_memory.sdk_migration_runner import run_startup_migration
    from one_memory.target_schema import legacy_tables_present

    vector = _custom_home(tmp_path)
    before = _snapshot(vector)

    result = run_startup_migration(
        data_dir=tmp_path, runtime_build="99", sdk_version="2.0.15", namespace="governed-v2"
    )

    assert result.state == "not_required"
    assert result.as_document()["skipped"] == "upgrade_migration_skipped_custom_backend"
    assert legacy_tables_present(tmp_path / "one-memory.sqlite3") == ("evidence",)
    assert _snapshot(vector) == before


def test_upgrade_status_reports_a_custom_backend_home_as_not_required(
    tmp_path, monkeypatch, capsys
) -> None:
    from one_memory import cli

    _custom_home(tmp_path)
    monkeypatch.setenv("ONE_MEMORY_DATA_DIR", str(tmp_path))
    assert cli.run(["sdk-migration-status", "--json"]) == 0
    document = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert document["state"] == "not_required"
    assert document["skipped"] == "upgrade_migration_skipped_custom_backend"


def test_a_legacy_home_naming_a_custom_backend_is_refused_not_migrated(tmp_path) -> None:
    from one_memory.sdk_data_plane_migration import DataPlaneMigrationError
    from one_memory.sdk_migration_runner import run_startup_migration

    _custom_home(tmp_path)
    with sqlite3.connect(tmp_path / "one-memory.sqlite3") as database:
        database.execute("UPDATE memory_authority_state SET authority='legacy' WHERE singleton=1")
    with pytest.raises(DataPlaneMigrationError) as refused:
        run_startup_migration(
            data_dir=tmp_path, runtime_build="99", sdk_version="2.0.15", namespace="governed-v2"
        )
    assert refused.value.code == "sdk_migration_custom_backend_unsupported"
