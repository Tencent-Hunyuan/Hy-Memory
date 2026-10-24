"""Model-free SDK search remains available without an active write profile."""

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
