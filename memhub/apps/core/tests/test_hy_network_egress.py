"""Public local-embedding injection must not create network egress."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

from one_memory.hy_sdk_runtime import LocalOnnxEmbeddingService, build_sdk_config
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind


class _Watcher:
    def __init__(self, monkeypatch) -> None:
        self.targets: list[str] = []
        real_connect = socket.socket.connect

        def connect(sock, address, *args, **kwargs):
            self.targets.append(str(address))
            return real_connect(sock, address, *args, **kwargs)

        monkeypatch.setattr(socket.socket, "connect", connect)


class _LocalEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _text in texts]


def _profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="egress",
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


def test_the_watcher_reports_a_connection_that_really_happens(monkeypatch) -> None:
    watcher = _Watcher(monkeypatch)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        client = socket.socket()
        client.settimeout(2)
        client.connect(listener.getsockname())
        client.close()
    finally:
        listener.close()

    assert watcher.targets


def test_public_local_embedding_service_opens_no_socket(monkeypatch) -> None:
    watcher = _Watcher(monkeypatch)
    service = LocalOnnxEmbeddingService(_LocalEmbedder())  # type: ignore[arg-type]

    assert asyncio.run(service.embed("长期偏好")) == [1.0, 0.0]
    assert watcher.targets == []


def test_no_ambient_remote_backend_survives_sdk_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name, value in (
        ("MEMORY_LLM_BASE_URL", "https://ambient.example/v1"),
        ("MEMORY_GRAPH_PROVIDER", "memgraph"),
        ("MEMGRAPH_URL", "bolt://memgraph.example.com:7687"),
        ("MEMORY_VECTOR_PROVIDER", "qdrant"),
    ):
        monkeypatch.setenv(name, value)

    config = build_sdk_config(_profile(), api_key=None, data_dir=tmp_path)
    rendered = repr(config).lower()

    for forbidden in ("ambient.example", "memgraph", "qdrant", "bolt://"):
        assert forbidden not in rendered
    assert config.vector_store.provider == "chroma"
    assert config.graph_store.provider == "kuzu"
