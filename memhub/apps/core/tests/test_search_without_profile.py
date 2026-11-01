"""Model-free SDK search remains available without an active write profile."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from one_memory.hy_sdk_runtime import (
    SdkClientHandle,
    builtin_retrieval_snapshot,
)
from one_memory.model_profiles import ProviderKind
from one_memory.sdk_memory_runtime import SdkMemoryRuntime


class _SearchClient:
    def __init__(self, reply: dict[str, object]) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict[str, object]]] = []

    def search(self, query: str, **options: object) -> dict[str, object]:
        self.calls.append((query, options))
        return self.reply


class _Handle:
    def __init__(self, client: _SearchClient) -> None:
        self._client = client

    async def client(self) -> _SearchClient:
        return self._client


def _runtime(client: _SearchClient) -> SdkMemoryRuntime:
    return SdkMemoryRuntime(
        coordinator=SimpleNamespace(),
        read_handle=_Handle(client),  # type: ignore[arg-type]
        write_queue=SimpleNamespace(),  # type: ignore[arg-type]
        extraction_settings=SimpleNamespace(),  # type: ignore[arg-type]
        data_dir=Path("/unused"),
    )


def test_builtin_retrieval_handle_needs_no_profile_or_key() -> None:
    profile = builtin_retrieval_snapshot()

    assert profile.profile_id == "builtin-retrieval"
    assert profile.provider_kind is ProviderKind.LOCAL_ONNX
    assert profile.embedding_provider_kind is ProviderKind.LOCAL_ONNX
    assert profile.has_api_key is False


@pytest.mark.anyio
async def test_sdk_client_handle_lazily_reuses_the_model_free_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SearchClient({"scene": "normal", "memories": []})
    lease = SimpleNamespace(client=client, close=lambda: None)
    acquired: list[object] = []

    def acquire(*args: object, **kwargs: object) -> object:
        acquired.append((args, kwargs))
        return lease

    monkeypatch.setattr("one_memory.hy_sdk_runtime.acquire_client", acquire)
    handle = SdkClientHandle(
        builtin_retrieval_snapshot(),
        api_key=None,
        data_dir=tmp_path,
    )

    assert await handle.client() is client
    assert await handle.client() is client
    assert len(acquired) == 1
    await handle.close()


@pytest.mark.anyio
async def test_search_without_profile_uses_public_sdk_search() -> None:
    client = _SearchClient(
        {
            "scene": "normal",
            "memories": [
                {"memory_id": "memory-1", "content": "偏好简短回答", "score": 0.91},
            ],
        }
    )

    found = await _runtime(client).semantic_search("回答偏好")

    assert found == (("memory-1", 0.91),)
    query, options = client.calls[0]
    assert query == "回答偏好"
    assert options["user_ids"] == ["local"]
    assert options["agent_ids"] == []
    assert options["scene"] == "normal"


def _activate(profiles, secrets, *, dimensions: int, model: str = "chat"):
    from pydantic import SecretStr

    from one_memory.model_profiles import ModelCapabilities, ModelProfileInput

    profile = profiles.create(
        ModelProfileInput(
            name=f"embedding-{dimensions}",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example/v1",
            model=model,
            embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            embedding_base_url="https://models.example/v1",
            embedding_model="qwen3-embedding-4b",
            embedding_dimensions=dimensions,
            api_key=SecretStr("sk-test"),
        ),
        secrets,
    )
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    profiles.activate(profile.profile_id, secrets)
    return profiles.snapshot(profile.profile_id, profiles.active_identity()[1])


class _Recorder:
    """Stands in for `acquire_client`: records the collection each client opens."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.opened: list[tuple[str, int | None, str | None]] = []
        self.closed: list[str] = []

    def __call__(
        self,
        profile,
        space=None,
        *,
        api_key,
        data_dir,
        http_transport_factory=None,
        mode=None,
        features=(),
    ):
        from one_memory.hy_sdk_runtime import build_sdk_config

        config = build_sdk_config(profile, space, mode, api_key=api_key, data_dir=data_dir)
        name = config.vector_store.collection_name
        self.opened.append((name, config.vector_store.embedding_dims, api_key))
        return SimpleNamespace(
            client=SimpleNamespace(collection=name),
            close=lambda: self.closed.append(name),
        )


@pytest.mark.anyio
async def test_reads_open_the_live_space_the_active_profile_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#242: a 2560-dim profile wrote `memhub_<sha>_2560`, reads opened `_512`."""
    from one_memory.embedding_spaces import EmbeddingSpaceStore
    from one_memory.hy_sdk_runtime import build_sdk_config
    from one_memory.model_profiles import ModelProfileStore
    from one_memory.sdk_read_handle import ActiveProfileReadHandle
    from one_memory.secrets import InMemorySecretStore

    recorder = _Recorder(tmp_path)
    monkeypatch.setattr("one_memory.hy_sdk_runtime.acquire_client", recorder)
    profiles = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    # A home whose custom profile was active when this build first started:
    # the registry pins the collection that profile has been writing.
    written = _activate(profiles, secrets, dimensions=2560)
    spaces = EmbeddingSpaceStore(profiles.database_path)
    spaces.bootstrap(written)
    handle = ActiveProfileReadHandle(profiles=profiles, secrets=secrets, data_dir=tmp_path)
    writes = build_sdk_config(written, api_key="sk-test", data_dir=tmp_path)
    builtin = build_sdk_config(builtin_retrieval_snapshot(), api_key=None, data_dir=tmp_path)

    async def read() -> str:
        return str((await handle.client()).collection)

    assert await asyncio.create_task(read()) == writes.vector_store.collection_name
    assert recorder.opened[-1] == (writes.vector_store.collection_name, 2560, "sk-test")
    assert writes.vector_store.collection_name != builtin.vector_store.collection_name
    await handle.close()


@pytest.mark.anyio
async def test_an_activation_that_skips_the_switch_never_moves_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a switch's commit moves the space; reads stay where the data is."""
    from one_memory.embedding_spaces import EmbeddingSpaceStore
    from one_memory.model_profiles import ModelProfileStore
    from one_memory.sdk_read_handle import ActiveProfileReadHandle
    from one_memory.secrets import InMemorySecretStore

    recorder = _Recorder(tmp_path)
    monkeypatch.setattr("one_memory.hy_sdk_runtime.acquire_client", recorder)
    profiles = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    first = _activate(profiles, secrets, dimensions=1024)
    live = EmbeddingSpaceStore(profiles.database_path).bootstrap(first)
    handle = ActiveProfileReadHandle(profiles=profiles, secrets=secrets, data_dir=tmp_path)
    _activate(profiles, secrets, dimensions=2560, model="chat-2")

    collection = (await asyncio.create_task(handle.client())).collection

    assert collection == live.collection_base
    await handle.close()


@pytest.mark.anyio
async def test_a_committed_switch_rebuilds_reads_without_closing_one_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from one_memory.embedding_spaces import (
        EmbeddingSpaceStore,
        derived_space,
        insert_space_in,
        set_state_in,
    )
    from one_memory.model_profiles import ModelProfileStore
    from one_memory.sdk_read_handle import ActiveProfileReadHandle
    from one_memory.secrets import InMemorySecretStore

    recorder = _Recorder(tmp_path)
    monkeypatch.setattr("one_memory.hy_sdk_runtime.acquire_client", recorder)
    profiles = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    spaces = EmbeddingSpaceStore(profiles.database_path)
    old_space = spaces.bootstrap(_activate(profiles, secrets, dimensions=1024))
    handle = ActiveProfileReadHandle(
        profiles=profiles, secrets=secrets, data_dir=tmp_path, spaces=spaces
    )

    holding = asyncio.Event()
    finish = asyncio.Event()

    async def slow_read() -> str:
        client = await handle.client()
        holding.set()
        await finish.wait()
        return str(client.collection)

    in_flight = asyncio.create_task(slow_read())
    await holding.wait()
    [(old, _, _)] = recorder.opened

    # What a switch's commit does: a new live space, the old one retired, and
    # the target profile active -- one transaction.
    target = _activate(profiles, secrets, dimensions=2560, model="chat-2")
    with spaces.transaction() as connection:
        set_state_in(connection, old_space.space_id, state="retired", expected=("live",))
        insert_space_in(
            connection,
            derived_space(target).model_copy(update={"space_id": "new-space"}),
        )

    async def read() -> str:
        return str((await handle.client()).collection)

    new = await asyncio.create_task(read())
    assert new != old
    # The read that started on the old space still has its client.
    assert recorder.closed == []

    finish.set()
    assert await in_flight == old
    for _ in range(5):
        await asyncio.sleep(0)
    assert recorder.closed == [old]
    await handle.close()
    assert recorder.closed == [old, new]


def test_every_api_read_shares_the_active_profile_handle(tmp_path: Path) -> None:
    """Library, search, recall, graph, profile, raw, ask and context all read
    through `_sdk_client_handle`, so it must be the active-profile handle."""
    from one_memory.api import _sdk_client_handle
    from one_memory.config import Settings
    from one_memory.model_profiles import ModelProfileStore
    from one_memory.sdk_read_handle import ActiveProfileReadHandle
    from one_memory.secrets import InMemorySecretStore

    state = SimpleNamespace(
        sdk_client_handle=None,
        model_profile_store=ModelProfileStore(tmp_path / "one-memory.sqlite3"),
        secret_store=InMemorySecretStore(),
        settings=Settings(_env_file=None, data_dir=tmp_path),
    )
    app = SimpleNamespace(state=state)

    handle = _sdk_client_handle(app)  # type: ignore[arg-type]

    assert isinstance(handle, ActiveProfileReadHandle)
    assert _sdk_client_handle(app) is handle is state.sdk_client_handle  # type: ignore[arg-type]
