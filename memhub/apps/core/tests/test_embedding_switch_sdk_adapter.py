"""The re-embed adapter against the SDK's real re-embed module and Chroma.

Skipped on an SDK without `hy_memory.reembed` (before 2.0.10): there the
switch is unavailable and the plan says `sdk_reembed_unavailable`, which
`test_embedding_switch.py` covers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import hy_memory
import pytest

from one_memory.embedding_backend import EmbeddingTarget, PhysicalSpace
from one_memory.embedding_spaces import EmbeddingSpace, space_collection_names
from one_memory.hy_sdk_migration import SdkReembedBackend
from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

pytestmark = pytest.mark.skipif(
    SdkReembedBackend(Path("/nonexistent")).unavailable_reason() is not None,
    reason="the installed SDK has no re-embed module (hy-memory < 2.0.10)",
)

SOURCE = "memhub_" + "1" * 16
TARGET = "memhub_e_" + "2" * 16


class _Embedder:
    """A deterministic 8-wide stand-in for the target model."""

    def __init__(self, width: int = 8) -> None:
        self.width = width
        self.texts: list[str] = []

    async def embed_batch(self, texts: list[str], use_cache: bool = True) -> list[list[float]]:
        self.texts.extend(texts)
        return [[float(len(text) % 7 + 1)] + [0.5] * (self.width - 1) for text in texts]

    async def embed(self, text: str, use_cache: bool = True) -> list[float]:
        return (await self.embed_batch([text]))[0]

    def get_embedding_dims(self) -> int:
        return self.width


def _seed(data_dir: Path) -> None:
    import chromadb

    vector = data_dir / "hy-memory" / "governed-v2" / "vector"
    vector.mkdir(parents=True)
    client = chromadb.PersistentClient(path=str(vector))
    names = space_collection_names(SOURCE, 4)
    memory = client.get_or_create_collection(names["memory"], metadata={"hnsw:space": "cosine"})
    memory.add(
        ids=["m1", "m2", "m3"],
        embeddings=[[0.1, 0.2, 0.3, 0.4]] * 3,
        documents=["one", "two", "three"],
        metadatas=[{"user_id": "local"}, {"user_id": "hermes-a"}, {"user_id": "hermes-b"}],
    )
    keys = client.get_or_create_collection(names["coding_keys"], metadata={"hnsw:space": "cosine"})
    keys.add(
        ids=["e1:0"],
        embeddings=[[0.4, 0.3, 0.2, 0.1]],
        documents=["task"],
        metadatas=[{"user_id": "local"}],
    )


def _target() -> EmbeddingTarget:
    space = EmbeddingSpace(
        space_id="target",
        state="building",
        model="stand-in",
        dims=8,
        name_dims=8,
        collection_base=TARGET,
        naming="v2",
        created_at="2026-11-01T00:00:00+00:00",
    )
    return EmbeddingTarget(profile=builtin_retrieval_snapshot(), space=space, api_key=None)


def test_core_names_a_space_exactly_as_the_sdk_does() -> None:
    import hy_memory

    for dims in (None, 512, 1024):
        sdk = hy_memory.vector_space_names(hy_memory.VectorSpace(base_name=SOURCE, dims=dims))
        assert sdk == space_collection_names(SOURCE, dims)


def test_scan_reembed_verify_and_drop_through_the_sdk(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    backend = SdkReembedBackend(tmp_path)
    embedder = _Embedder()
    monkeypatch.setattr(backend, "_embedding_service", lambda _target: embedder)
    source = PhysicalSpace(collection_base=SOURCE, name_dims=4, dims=4)
    target = PhysicalSpace(collection_base=TARGET, name_dims=8, dims=8, model="stand-in")
    steps = []

    async def scenario() -> None:
        scanned = await backend.scan(per_user=True)
        found = {item.collection_base: item for item in scanned}
        assert found[SOURCE].vectors == 4
        assert found[SOURCE].per_user == {"local": 2, "hermes-a": 1, "hermes-b": 1}

        await backend.reembed(
            source, target, embedding=_target(), cancel=lambda: False, on_step=steps.append
        )
        assert not any((await backend.diff(source, target)).values())
        check = await backend.verify(source, target, embedding=_target())
        assert check.problems == ()
        assert check.per_user == {"local": 2, "hermes-a": 1, "hermes-b": 1}

        # Resumable: a second run embeds nothing more.
        before = len(embedder.texts)
        await backend.reembed(
            source, target, embedding=_target(), cancel=lambda: False, on_step=steps.append
        )
        assert len(embedder.texts) == before

        await backend.drop(target)
        remaining = {item.collection_base for item in await backend.scan()}
        assert TARGET not in remaining and SOURCE in remaining

    asyncio.run(scenario())
    assert {step.role for step in steps} >= {"memory", "coding_keys"}


def test_a_wrong_width_fails_before_anything_is_written(tmp_path: Path, monkeypatch) -> None:
    from one_memory.embedding_backend import EmbeddingWidthError

    _seed(tmp_path)
    backend = SdkReembedBackend(tmp_path)
    monkeypatch.setattr(backend, "_embedding_service", lambda _target: _Embedder(width=6))
    source = PhysicalSpace(collection_base=SOURCE, name_dims=4, dims=4)
    target = PhysicalSpace(collection_base=TARGET, name_dims=8, dims=8)

    async def scenario() -> None:
        with pytest.raises(EmbeddingWidthError):
            await backend.reembed(
                source, target, embedding=_target(), cancel=lambda: False, on_step=lambda _: None
            )
        found = {item.collection_base: item for item in await backend.scan()}
        assert TARGET not in found or found[TARGET].vectors == 0

    asyncio.run(scenario())


def test_cancel_stops_at_a_batch_boundary(tmp_path: Path, monkeypatch) -> None:
    from one_memory.embedding_backend import ReembedCancelledError

    _seed(tmp_path)
    backend = SdkReembedBackend(tmp_path)
    monkeypatch.setattr(backend, "_embedding_service", lambda _target: _Embedder())
    source = PhysicalSpace(collection_base=SOURCE, name_dims=4, dims=4)
    target = PhysicalSpace(collection_base=TARGET, name_dims=8, dims=8)

    async def scenario() -> None:
        with pytest.raises(ReembedCancelledError):
            await backend.reembed(
                source, target, embedding=_target(), cancel=lambda: True, on_step=lambda _: None
            )

    asyncio.run(scenario())


def test_a_whole_switch_through_the_real_sdk(tmp_path: Path, monkeypatch) -> None:
    """Registry, worker and commit over real Chroma collections and the SDK."""
    import chromadb
    from pydantic import SecretStr

    from one_memory.embedding_spaces import EmbeddingSpaceStore
    from one_memory.embedding_switch import EmbeddingSwitchService
    from one_memory.model_profiles import (
        ModelCapabilities,
        ModelProfileInput,
        ModelProfileStore,
        ProviderKind,
    )
    from one_memory.secrets import InMemorySecretStore

    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    spaces = EmbeddingSpaceStore(database)
    live = spaces.bootstrap(None)
    vector = tmp_path / "hy-memory" / "governed-v2" / "vector"
    vector.mkdir(parents=True)
    client = chromadb.PersistentClient(path=str(vector))
    memory = client.get_or_create_collection(
        live.collection_names()["memory"], metadata={"hnsw:space": "cosine"}
    )
    memory.add(
        ids=["a", "b", "c"],
        embeddings=[[0.1] * 512, [0.2] * 512, [0.3] * 512],
        documents=["one", "two", "three"],
        metadatas=[{"user_id": "local"}, {"user_id": "hermes-a"}, {"user_id": "local"}],
    )
    stray_base = "memhub_" + "9" * 16
    stray = client.get_or_create_collection(
        space_collection_names(stray_base, 768)["memory"], metadata={"hnsw:space": "cosine"}
    )
    stray.add(
        ids=["a", "z"],
        embeddings=[[0.5] * 768, [0.6] * 768],
        documents=["stale", "only in the stray"],
        metadatas=[{"user_id": "local"}, {"user_id": "hermes-b"}],
    )
    created = profiles.create(
        ModelProfileInput(
            name="remote",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example/v1",
            model="chat",
            embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            embedding_base_url="https://models.example/v1",
            embedding_model="stand-in",
            embedding_dimensions=8,
            api_key=SecretStr("sk-test"),
        ),
        secrets,
    )
    profiles.record_capabilities(
        created.profile_id,
        expected_revision=created.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    backend = SdkReembedBackend(tmp_path)
    monkeypatch.setattr(backend, "_embedding_service", lambda _target: _Embedder())
    service = EmbeddingSwitchService(
        database_path=database,
        data_dir=tmp_path,
        profiles=profiles,
        spaces=spaces,
        secrets=secrets,
        backend=backend,
        worker_enabled=False,
    )
    service.release_scan_hold()

    async def scenario() -> None:
        plan = await service.plan(created.profile_id)
        assert plan.needed and plan.total_vectors == 5 and plan.users == 3
        job = await service.start_switch(
            created.profile_id,
            expected_revision=created.revision,
            plan_token=plan.plan_token,
            requested_by="cli",
        )
        await service.run(job.job_id)
        finished = service.get_job(job.job_id)
        assert finished.state == "done", service.jobs.get(job.job_id)["verification_json"]

    asyncio.run(scenario())
    new = spaces.require_live()
    target = client.get_collection(new.collection_names()["memory"])
    got = target.get(include=["documents", "embeddings"])
    documents = dict(zip(got["ids"], got["documents"], strict=True))
    assert documents == {"a": "one", "b": "two", "c": "three", "z": "only in the stray"}
    assert {len(vector) for vector in got["embeddings"]} == {8}
    assert profiles.active_identity() == (created.profile_id, created.revision)
    states = {space.collection_base: space.state for space in spaces.list_spaces()}
    assert states[live.collection_base] == "retired"
    assert states[stray_base] == "retired"


# ----------------------------------------------------------------------
# Nothing stood in: the SDK's EmbedService over HTTP, real Chroma, real Kuzu
# ----------------------------------------------------------------------


class _EmbeddingServer:
    """A local OpenAI-compatible `/embeddings` endpoint; counts what it was sent."""

    def __init__(self, width: int) -> None:
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.width = width
        self.inputs: list[str] = []
        self.dimensions: list[object] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
                server.inputs.extend(texts)
                server.dimensions.append(body.get("dimensions"))
                payload = json.dumps(
                    {
                        "object": "list",
                        "model": body["model"],
                        "data": [
                            {
                                "object": "embedding",
                                "index": index,
                                "embedding": [(len(text) % 7 + 1) / 8.0] + [0.5] * (width - 1),
                            }
                            for index, text in enumerate(texts)
                        ],
                        "usage": {"prompt_tokens": 1, "total_tokens": 1},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/v1"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _home(tmp_path: Path, *, embedding_provider, embedding_url: str, api_key: str | None):
    from pydantic import SecretStr

    from one_memory.embedding_spaces import EmbeddingSpaceStore
    from one_memory.model_profiles import (
        ModelCapabilities,
        ModelProfileInput,
        ModelProfileStore,
        ProviderKind,
    )
    from one_memory.secrets import InMemorySecretStore

    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    spaces = EmbeddingSpaceStore(database)
    created = profiles.create(
        ModelProfileInput(
            name="target",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url=embedding_url,
            model="chat",
            embedding_provider_kind=embedding_provider,
            embedding_base_url=embedding_url,
            embedding_model="stand-in",
            embedding_dimensions=8,
            api_key=SecretStr(api_key) if api_key else None,
        ),
        secrets,
    )
    profiles.record_capabilities(
        created.profile_id,
        expected_revision=created.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    return database, secrets, profiles, spaces, created


def _service(database, data_dir, profiles, spaces, secrets, **options):
    from one_memory.embedding_switch import EmbeddingSwitchService

    service = EmbeddingSwitchService(
        database_path=database,
        data_dir=data_dir,
        profiles=profiles,
        spaces=spaces,
        secrets=secrets,
        backend=SdkReembedBackend(data_dir),
        worker_enabled=False,
        **options,
    )
    service.release_scan_hold()
    return service


async def _switch(service, created) -> object:
    plan = await service.plan(created.profile_id)
    assert plan.needed and not plan.blockers, plan
    job = await service.start_switch(
        created.profile_id,
        expected_revision=created.revision,
        plan_token=plan.plan_token,
        requested_by="cli",
    )
    await service.run(job.job_id)
    finished = service.get_job(job.job_id)
    assert finished.state == "done", (
        finished.error_code,
        service.jobs.get(job.job_id)["verification_json"],
        service.jobs.get(job.job_id)["error_detail"],
    )
    return finished


def _seed_vectors(data_dir: Path, space) -> None:
    import chromadb

    vector = data_dir / "hy-memory" / "governed-v2" / "vector"
    vector.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(vector))
    memory = client.get_or_create_collection(
        space.collection_names()["memory"], metadata={"hnsw:space": "cosine"}
    )
    memory.add(
        ids=["a", "b"],
        embeddings=[[0.1] * 512, [0.2] * 512],
        documents=["one", "two"],
        metadatas=[{"user_id": "local"}, {"user_id": "hermes-a"}],
    )


def test_a_switch_embeds_through_the_sdk_service_over_http(tmp_path: Path) -> None:
    from one_memory.model_profiles import ProviderKind

    server = _EmbeddingServer(8)
    try:
        database, secrets, profiles, spaces, created = _home(
            tmp_path,
            embedding_provider=ProviderKind.OPENAI_COMPATIBLE,
            embedding_url=server.url,
            api_key="sk-test",
        )
        _seed_vectors(tmp_path, spaces.bootstrap(None))
        service = _service(database, tmp_path, profiles, spaces, secrets)

        asyncio.run(_switch(service, created))

        assert {"one", "two"} <= set(server.inputs)
        assert set(server.dimensions) == {8}
    finally:
        server.close()


def test_an_ollama_embedding_needs_no_generation_key_to_re_embed(tmp_path: Path) -> None:
    """A switch only embeds: the generation model's key is not its business."""
    from one_memory.embedding_backend import EmbeddingTarget
    from one_memory.embedding_spaces import embedding_identity
    from one_memory.model_profiles import ProviderKind

    server = _EmbeddingServer(8)
    try:
        _database, secrets, profiles, spaces, created = _home(
            tmp_path,
            embedding_provider=ProviderKind.OLLAMA,
            embedding_url=server.url,
            api_key="sk-test",
        )
        profile = profiles.snapshot(created.profile_id, created.revision)
        identity = embedding_identity(profile)
        target = EmbeddingSpace(
            space_id="t",
            state="building",
            model=identity.model,
            dims=8,
            name_dims=8,
            collection_base=TARGET,
            naming="v2",
            created_at="2026-11-01T00:00:00+00:00",
        )
        backend = SdkReembedBackend(tmp_path)

        # `api_key=None` is what `_embedding_target` resolves for Ollama.
        service = backend._embedding_service(
            EmbeddingTarget(profile=profile, space=target, api_key=None)
        )

        assert asyncio.run(service.embed_batch(["hello"], use_cache=False))[0][1] == 0.5
    finally:
        server.close()


async def _seed_graph(path: Path, dims: int) -> None:
    from hy_memory.data.graph_store_kuzu import KuzuGraphStore
    from hy_memory.models.memory import MemoryLayer, MemoryNode

    config = hy_memory.MemoryConfig()
    config.graph_store.provider = "kuzu"
    config.graph_store.db_path = str(path)
    config.vector_store.embedding_dims = dims
    store = KuzuGraphStore(config)
    await store.initialize()
    assert store._available
    node = MemoryNode(
        user_id="local",
        agent_id="a",
        session_id="s",
        layer=MemoryLayer.L6_SCHEMA,
        content="plans ahead",
        custom={"retrieval_text": "plans trips weeks ahead"},
    )
    node._graph_embedding = [0.1] * dims
    await store.upsert_memory_node(node)
    await store.close()


@pytest.mark.parametrize("mode", ["ultra", "pro"])
def test_a_real_graph_moves_to_the_target_width(tmp_path: Path, mode: str) -> None:
    """In any mode: a pro home's old graph must not be lost with the old space."""
    from one_memory.hy_graph_admin import forget_graph_widths
    from one_memory.model_profiles import ProviderKind

    server = _EmbeddingServer(8)
    try:
        database, secrets, profiles, spaces, created = _home(
            tmp_path,
            embedding_provider=ProviderKind.OPENAI_COMPATIBLE,
            embedding_url=server.url,
            api_key="sk-test",
        )
        live = spaces.bootstrap(None)
        _seed_vectors(tmp_path, live)
        root = tmp_path / "hy-memory" / "governed-v2"
        asyncio.run(_seed_graph(root / "graph", 512))
        service = _service(
            database, tmp_path, profiles, spaces, secrets, graph_enabled=lambda: mode == "ultra"
        )

        asyncio.run(_switch(service, created))

        new = spaces.require_live()
        assert new.graph_dir != "graph" and (root / new.graph_dir).exists()
        assert (root / "graph").exists()
        assert "plans trips weeks ahead" in server.inputs
        forget_graph_widths()
    finally:
        server.close()


def test_verifying_the_graph_closes_what_reads_reopened(tmp_path: Path) -> None:
    """Reads are served during a switch, so a client may hold the graph again."""
    from one_memory.model_profiles import ProviderKind

    server = _EmbeddingServer(8)
    quiesced: list[str] = []

    async def quiesce():
        quiesced.append("closed")
        return lambda: quiesced.append("released")

    try:
        database, secrets, profiles, spaces, created = _home(
            tmp_path,
            embedding_provider=ProviderKind.OPENAI_COMPATIBLE,
            embedding_url=server.url,
            api_key="sk-test",
        )
        live = spaces.bootstrap(None)
        _seed_vectors(tmp_path, live)
        asyncio.run(_seed_graph(tmp_path / "hy-memory" / "governed-v2" / "graph", 512))
        service = _service(
            database,
            tmp_path,
            profiles,
            spaces,
            secrets,
            graph_enabled=lambda: True,
            quiesce_clients=quiesce,
        )

        asyncio.run(_switch(service, created))

        # Once to copy the graph, once to verify it.
        assert quiesced == ["closed", "released", "closed", "released"]
    finally:
        server.close()


def test_the_first_chroma_open_waits_for_a_client_under_construction(tmp_path: Path) -> None:
    """Two first opens on one directory race over Chroma's unlocked System cache."""
    import threading

    from one_memory.hy_sdk_runtime import first_store_open

    _seed(tmp_path)
    backend = SdkReembedBackend(tmp_path)
    finished = threading.Event()

    def scan() -> None:
        asyncio.run(backend.scan())
        finished.set()

    with first_store_open(tmp_path):
        thread = threading.Thread(target=scan)
        thread.start()
        assert not finished.wait(0.3)
    thread.join(10)
    assert finished.is_set()
