"""Local ONNX is injected through the public HY SDK constructor contract."""

from __future__ import annotations

import asyncio
from pathlib import Path

from one_memory.embedding_assets import (
    BUILTIN_EMBEDDING_DIMENSIONS,
    BUILTIN_EMBEDDING_MODEL,
)
from one_memory.hy_sdk_runtime import (
    LocalOnnxEmbeddingService,
    acquire_client,
    build_sdk_config,
)
from one_memory.model_profiles import (
    ModelProfileInput,
    ModelProfileSnapshot,
    ModelProfileStore,
    ProviderKind,
    apply_builtin_embedding_defaults,
)
from one_memory.secrets import InMemorySecretStore


class _Embedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(index), 1.0] for index, _text in enumerate(texts)]


def _profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="local-onnx",
        revision=1,
        provider_kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://api.example.com/v1",
        model="generation-model",
        embedding_provider_kind=ProviderKind.LOCAL_ONNX,
        embedding_model=BUILTIN_EMBEDDING_MODEL,
        embedding_dimensions=BUILTIN_EMBEDDING_DIMENSIONS,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8192,
        has_api_key=True,
    )


def test_missing_embedding_defaults_to_the_builtin_model() -> None:
    provider, model, base_url, dimensions = apply_builtin_embedding_defaults(
        embedding_model=None,
        embedding_provider_kind=None,
        embedding_base_url=None,
        embedding_dimensions=None,
    )

    assert provider is ProviderKind.LOCAL_ONNX
    assert model == BUILTIN_EMBEDDING_MODEL
    assert base_url is None
    assert dimensions == BUILTIN_EMBEDDING_DIMENSIONS


def test_creating_a_profile_without_embedding_gets_the_builtin(tmp_path: Path) -> None:
    store = ModelProfileStore(tmp_path / "profiles.db")
    profile = store.create(
        ModelProfileInput(
            name="gen-only",
            provider_kind=ProviderKind.OLLAMA,
            model="qwen2.5",
        ),
        InMemorySecretStore(),
    )

    assert profile.embedding_provider_kind is ProviderKind.LOCAL_ONNX
    assert profile.embedding_model == BUILTIN_EMBEDDING_MODEL
    assert profile.embedding_dimensions == BUILTIN_EMBEDDING_DIMENSIONS


def test_local_embedding_config_needs_no_embedding_key(tmp_path: Path) -> None:
    config = build_sdk_config(_profile(), api_key="generation-key", data_dir=tmp_path)

    assert config.embedder.api_key is None
    assert config.embedder.base_url is None
    # The request-side dimension stays unset so the SDK never sends `dimensions`;
    # the store keeps the concrete width.
    assert config.embedder.embedding_dims is None
    assert config.vector_store.embedding_dims == BUILTIN_EMBEDDING_DIMENSIONS


def test_public_local_embedding_service_covers_single_batch_and_metadata() -> None:
    embedder = _Embedder()
    service = LocalOnnxEmbeddingService(embedder)  # type: ignore[arg-type]

    single = asyncio.run(service.embed("单条"))
    batch = asyncio.run(service.embed_batch(["第一条", "第二条"]))
    vector, metadata = asyncio.run(service.embed_with_meta("带元数据"))

    assert single == [0.0, 1.0]
    assert batch == [[0.0, 1.0], [1.0, 1.0]]
    assert vector == [0.0, 1.0]
    assert metadata == {"provider": "local-onnx"}
    assert service.get_embedding_dims() == BUILTIN_EMBEDDING_DIMENSIONS


def test_acquire_client_injects_public_embedding_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            return None

    monkeypatch.setattr("one_memory.hy_sdk_runtime.HyMemoryClient", Client)
    monkeypatch.setattr(
        "one_memory.hy_sdk_runtime._acquire_runtime",
        lambda _config, _key: object(),
    )

    lease = acquire_client(
        _profile(),
        api_key="generation-key",
        data_dir=tmp_path,
    )
    try:
        assert isinstance(captured["embedding_service"], LocalOnnxEmbeddingService)
        assert "runtime" in captured
    finally:
        lease.close()
