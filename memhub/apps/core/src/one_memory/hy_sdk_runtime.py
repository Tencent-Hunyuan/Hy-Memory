"""HY SDK client construction and shared-runtime leasing.

This module deliberately depends only on the SDK's package-root exports.  A
lease owns one client while the process shares one ``SharedRuntime`` per
physical MemHub data directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from hy_memory import (  # type: ignore[import-untyped]
    AmbiguousMemoryIdError as AmbiguousMemoryIdError,  # re-export for api.py
)
from hy_memory import (
    HyMemoryClient,
    MemoryConfig,
    SharedRuntime,
)

from one_memory.embedding_assets import (
    BUILTIN_EMBEDDING_DIMENSIONS,
    BUILTIN_EMBEDDING_MODEL,
)
from one_memory.hy_graph_admin import (
    GraphWidthMismatchError,
    graph_store_enabled,
    inspect_graph_width_mismatch,
)
from one_memory.hy_sdk_types import HySdkRuntimeError
from one_memory.local_embedding import LocalEmbedder
from one_memory.log_file import confine_hy_sdk_logging
from one_memory.model_profiles import (
    ModelProfileSnapshot,
    ProviderKind,
    disable_thinking_params,
)

_RUNTIME_LOCK = threading.RLock()
BUILTIN_RETRIEVAL_PROFILE_ID = "builtin-retrieval"
_LOCAL_ONNX_PLACEHOLDER_URL = "http://127.0.0.1/local-onnx"
DEFAULT_RELEVANCE_FLOOR = 0.35


@dataclass
class _RuntimeEntry:
    runtime: SharedRuntime
    references: int


_RUNTIMES: dict[Path, _RuntimeEntry] = {}

# Homes whose SDK-owned local stores are held offline for maintenance, and
# the client constructions in flight per home. Chroma keeps one process-wide
# System per path with no lock around create, lookup and release, so a
# second client that opens or closes beside the SDK's pulls the System out
# from under it (KeyError on the next lookup). Maintenance therefore runs
# only while no SDK client exists for that home, and new clients wait.
_STORE_GATE = threading.Condition()
_OFFLINE_STORES: set[Path] = set()
_OPENING: dict[Path, int] = {}


class LocalOnnxEmbeddingService:
    """Public ``EmbeddingService`` implementation backed by MemHub's ONNX model."""

    _embedder: ClassVar[LocalEmbedder | None] = None
    _embedder_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, embedder: LocalEmbedder | None = None) -> None:
        if embedder is None:
            with self._embedder_lock:
                if self._embedder is None:
                    self.__class__._embedder = LocalEmbedder()
                embedder = self._embedder
        if embedder is None:  # pragma: no cover - guarded by the assignment above
            raise HySdkRuntimeError("hy_sdk_local_embedding_unavailable")
        self._local_embedder = embedder

    async def embed(self, text: str, use_cache: bool = True) -> list[float]:
        del use_cache
        vectors = await asyncio.to_thread(self._local_embedder.encode, [text])
        return vectors[0]

    async def embed_with_meta(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple[list[float], dict[str, Any]]:
        return await self.embed(text, use_cache), {"provider": "local-onnx"}

    async def embed_batch(
        self,
        texts: list[str],
        use_cache: bool = True,
    ) -> list[list[float]]:
        del use_cache
        if not texts:
            return []
        return await asyncio.to_thread(self._local_embedder.encode, list(texts))

    async def embed_queued(self, text: str, use_cache: bool = True) -> list[float]:
        return await self.embed(text, use_cache)

    async def embed_queued_with_meta(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple[list[float], dict[str, Any]]:
        return await self.embed_with_meta(text, use_cache)

    def get_embedding_dims(self) -> int:
        return BUILTIN_EMBEDDING_DIMENSIONS


class ClientLease:
    """Own a client and release its shared runtime exactly once."""

    def __init__(self, client: HyMemoryClient, data_dir: Path) -> None:
        self.client = client
        self.data_dir = data_dir.resolve()
        self._closed = False
        self._close_lock = threading.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.client.close()
            finally:
                _release_runtime(self.data_dir)

    def __enter__(self) -> ClientLease:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class SdkClientHandle:
    """Lazy async owner for one profile-bound public SDK client lease."""

    def __init__(
        self,
        profile: ModelProfileSnapshot,
        *,
        api_key: str | None,
        data_dir: Path,
        http_transport_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._profile = profile
        self._api_key = api_key
        self._data_dir = data_dir
        self._http_transport_factory = http_transport_factory
        self._lease: ClientLease | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> HyMemoryClient:
        if self._lease is not None:
            return self._lease.client
        async with self._lock:
            if self._lease is None:
                self._lease = await asyncio.to_thread(
                    acquire_client,
                    self._profile,
                    api_key=self._api_key,
                    data_dir=self._data_dir,
                    http_transport_factory=self._http_transport_factory,
                )
            return self._lease.client

    async def close(self) -> None:
        async with self._lock:
            lease, self._lease = self._lease, None
        if lease is not None:
            await asyncio.to_thread(lease.close)


def builtin_retrieval_snapshot() -> ModelProfileSnapshot:
    """Return the model-free profile used by SDK-native read operations."""
    return ModelProfileSnapshot(
        profile_id=BUILTIN_RETRIEVAL_PROFILE_ID,
        revision=0,
        provider_kind=ProviderKind.LOCAL_ONNX,
        base_url=_LOCAL_ONNX_PLACEHOLDER_URL,
        model=BUILTIN_EMBEDDING_MODEL,
        embedding_provider_kind=ProviderKind.LOCAL_ONNX,
        embedding_model=BUILTIN_EMBEDDING_MODEL,
        embedding_dimensions=BUILTIN_EMBEDDING_DIMENSIONS,
        timeout_seconds=60,
        max_concurrency=1,
        context_window=None,
        has_api_key=False,
    )


def sdk_relevance_floor() -> float:
    raw = os.environ.get("ONE_MEMORY_HY_RELEVANCE_FLOOR", "").strip()
    if not raw:
        return DEFAULT_RELEVANCE_FLOOR
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RELEVANCE_FLOOR
    return min(1.0, max(0.0, value))


def build_sdk_config(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
) -> MemoryConfig:
    """Map a model snapshot to SDK models, endpoints, backends, and local paths."""
    generation_endpoint, generation_key = _generation_access(profile, api_key)
    embedding_provider = profile.embedding_provider_kind or profile.provider_kind
    embedding_service_is_local = embedding_provider is ProviderKind.LOCAL_ONNX
    embedding_model = profile.embedding_model or (
        BUILTIN_EMBEDDING_MODEL if embedding_service_is_local else None
    )
    if not embedding_model:
        raise HySdkRuntimeError("hy_sdk_embedding_model_required")
    dimensions: int | None
    if embedding_service_is_local:
        embedding_endpoint = None
        embedding_key = None
        dimensions = profile.embedding_dimensions or BUILTIN_EMBEDDING_DIMENSIONS
    else:
        embedding_endpoint = _openai_endpoint(
            embedding_provider,
            profile.embedding_base_url or profile.base_url,
        )
        embedding_key = "ollama" if embedding_provider is ProviderKind.OLLAMA else api_key
        if embedding_provider is ProviderKind.OPENAI_COMPATIBLE and not embedding_key:
            raise HySdkRuntimeError("hy_sdk_embedding_api_key_missing")
        if embedding_provider not in {
            ProviderKind.OPENAI_COMPATIBLE,
            ProviderKind.OLLAMA,
        }:
            raise HySdkRuntimeError("hy_sdk_embedding_provider_unsupported")
        dimensions = profile.embedding_dimensions

    root = data_dir.resolve() / "hy-memory" / "governed-v2"
    root.mkdir(parents=True, exist_ok=True)
    collection = _collection_base(
        embedding_provider, embedding_endpoint, embedding_model, dimensions
    )

    llm: dict[str, Any] = {
        "provider": "openai",
        "model": profile.model,
        "api_key": generation_key,
        "base_url": generation_endpoint,
    }
    # The SDK sends `extra_body` on every chat call, chat and coding/working
    # alike. Without it DeepSeek spent the whole agent_max_tokens on thinking
    # and answered finish_reason=length with no content.
    thinking_off = disable_thinking_params(profile)
    if thinking_off:
        llm["extra_body"] = thinking_off

    return MemoryConfig.from_dict(
        {
            "llm": llm,
            "embedder": {
                "provider": "openai",
                "model": (
                    BUILTIN_EMBEDDING_MODEL if embedding_service_is_local else embedding_model
                ),
                "api_key": embedding_key,
                "base_url": embedding_endpoint,
                "embedding_dims": dimensions,
            },
            "vector_store": {
                "provider": "chroma",
                "collection_name": collection,
                "persist_directory": str(root / "vector"),
                "embedding_dims": dimensions,
                "host": None,
                "port": None,
                "api_key": None,
            },
            "graph_store": {
                "provider": "kuzu",
                "db_path": str(root / "graph"),
                "url": None,
                "username": None,
                "password": None,
                "database": None,
            },
            "cache": {
                "backend": "sqlite",
                "db_path": str(root / "cache.sqlite3"),
            },
            "history": {"db_path": str(root / "history.sqlite3")},
            "coding": {"db_path": str(root / "coding.sqlite3")},
        }
    )


def acquire_client(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
    http_transport_factory: Callable[[], Any] | None = None,
) -> ClientLease:
    """Construct a public SDK client and return its explicit runtime lease.

    Waits while `hold_stores_offline` holds this home's stores.
    """
    key = data_dir.resolve()
    with _STORE_GATE:
        while key in _OFFLINE_STORES:
            _STORE_GATE.wait()
        _OPENING[key] = _OPENING.get(key, 0) + 1
    try:
        return _acquire_client(
            profile,
            api_key=api_key,
            data_dir=data_dir,
            http_transport_factory=http_transport_factory,
        )
    finally:
        with _STORE_GATE:
            _OPENING[key] -= 1
            if not _OPENING[key]:
                del _OPENING[key]
            _STORE_GATE.notify_all()


def hold_stores_offline(data_dir: Path) -> bool:
    """Hold this home's SDK stores for offline maintenance; False if a client is open.

    While held, `acquire_client` for the home waits. Every SDK client in Core
    is built there, so nothing reads or writes Chroma beside the maintenance.
    `release_stores` ends the hold; it is idempotent.
    """
    key = data_dir.resolve()
    with _STORE_GATE:
        with _RUNTIME_LOCK:
            open_runtime = key in _RUNTIMES
        if key in _OFFLINE_STORES or _OPENING.get(key) or open_runtime:
            return False
        _OFFLINE_STORES.add(key)
        return True


def release_stores(data_dir: Path) -> None:
    with _STORE_GATE:
        _OFFLINE_STORES.discard(data_dir.resolve())
        _STORE_GATE.notify_all()


def _acquire_client(
    profile: ModelProfileSnapshot,
    *,
    api_key: str | None,
    data_dir: Path,
    http_transport_factory: Callable[[], Any] | None,
) -> ClientLease:
    config = build_sdk_config(profile, api_key=api_key, data_dir=data_dir)
    config.http_transport_factory = http_transport_factory
    mismatch = inspect_graph_width_mismatch(config)
    if mismatch is not None:
        raise GraphWidthMismatchError(mismatch)
    key = data_dir.resolve()
    runtime = _acquire_runtime(config, key)
    embedding_service: LocalOnnxEmbeddingService | None = None
    if (profile.embedding_provider_kind or profile.provider_kind) is ProviderKind.LOCAL_ONNX:
        embedding_service = LocalOnnxEmbeddingService()
    try:
        client = HyMemoryClient(
            config=config,
            mode="ultra" if graph_store_enabled() else "pro",
            runtime=runtime,
            embedding_service=embedding_service,
        )
    except BaseException:
        _release_runtime(key)
        raise
    finally:
        # The constructor's first step configures HY's text log (once per
        # process), so by here it exists whether or not construction succeeded.
        confine_hy_sdk_logging()
    return ClientLease(client, key)


def close_all_shared_runtimes() -> None:
    """Close process runtimes only after every client lease has been released."""
    with _RUNTIME_LOCK:
        if _RUNTIMES:
            raise HySdkRuntimeError("hy_sdk_runtime_leases_active")


def _generation_access(
    profile: ModelProfileSnapshot,
    api_key: str | None,
) -> tuple[str, str]:
    if profile.provider_kind is ProviderKind.LOCAL_ONNX:
        return _LOCAL_ONNX_PLACEHOLDER_URL, "local-read-only"
    if profile.provider_kind not in {
        ProviderKind.OPENAI_COMPATIBLE,
        ProviderKind.OLLAMA,
    }:
        raise HySdkRuntimeError("hy_sdk_generation_provider_unsupported")
    endpoint = _openai_endpoint(profile.provider_kind, profile.base_url)
    if profile.provider_kind is ProviderKind.OLLAMA:
        return endpoint, "ollama"
    if not api_key:
        raise HySdkRuntimeError("hy_sdk_model_api_key_missing")
    return endpoint, api_key


def _collection_base(
    provider: ProviderKind,
    endpoint: str | None,
    model: str,
    dimensions: int | None,
) -> str:
    identity = "\0".join((provider.value, endpoint or "local", model, str(dimensions or "auto")))
    return "memhub_" + hashlib.sha256(identity.encode()).hexdigest()[:16]


def vector_collection_bases(profile: ModelProfileSnapshot) -> tuple[str, tuple[str, ...]]:
    """The SDK vector collection base for this embedding space, and the older ones.

    The SDK derives every vector collection (chat, Coding keys, Working facts,
    each variant) by suffixing this base. Builds before #145 named the same
    embedding space `one_memory_<12 hex>`, with or without a declared width;
    the offline migration renames those so their vectors stay readable.
    """
    provider = profile.embedding_provider_kind or profile.provider_kind
    local = provider is ProviderKind.LOCAL_ONNX
    model = profile.embedding_model or (BUILTIN_EMBEDDING_MODEL if local else None)
    if not model:
        raise HySdkRuntimeError("hy_sdk_embedding_model_required")
    endpoint: str | None
    dimensions: int | None
    if local:
        endpoint, dimensions = None, profile.embedding_dimensions or BUILTIN_EMBEDDING_DIMENSIONS
        legacy_endpoint = "local-onnx"
    else:
        endpoint = _openai_endpoint(provider, profile.embedding_base_url or profile.base_url)
        dimensions = profile.embedding_dimensions
        legacy_endpoint = endpoint
    legacy_model = profile.embedding_model or "bge-small-zh-v1.5"
    legacy = tuple(
        "one_memory_"
        + hashlib.sha256(
            ":".join((provider.value, legacy_endpoint, legacy_model, *declared)).encode()
        ).hexdigest()[:12]
        for declared in ((), ("auto",), (str(profile.embedding_dimensions or "auto"),))
    )
    return _collection_base(provider, endpoint, model, dimensions), tuple(dict.fromkeys(legacy))


def _openai_endpoint(provider: ProviderKind, endpoint: str | None) -> str:
    value = (endpoint or "").rstrip("/")
    if not value:
        raise HySdkRuntimeError("hy_sdk_model_endpoint_missing")
    if provider is ProviderKind.OLLAMA and not value.endswith("/v1"):
        return f"{value}/v1"
    return value


def _acquire_runtime(config: MemoryConfig, key: Path) -> SharedRuntime:
    with _RUNTIME_LOCK:
        entry = _RUNTIMES.get(key)
        if entry is None:
            runtime = _run_sync(SharedRuntime.create(config))
            _RUNTIMES[key] = _RuntimeEntry(runtime=runtime, references=1)
            return runtime
        entry.references += 1
        return entry.runtime


def _release_runtime(key: Path) -> None:
    runtime: SharedRuntime | None = None
    with _RUNTIME_LOCK:
        entry = _RUNTIMES.get(key)
        if entry is None:
            return
        entry.references -= 1
        if entry.references == 0:
            runtime = entry.runtime
            del _RUNTIMES[key]
    if runtime is not None:
        runtime.close()


def _run_sync[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run SDK async construction from either sync or async caller threads."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    result: list[T] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as error:
            failure.append(error)

    thread = threading.Thread(target=run, name="hy-sdk-runtime-create")
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0]
