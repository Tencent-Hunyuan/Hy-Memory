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
from collections.abc import Callable, Coroutine, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, TypedDict, cast

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
from one_memory.embedding_spaces import (
    DEFAULT_GRAPH_DIR,
    EmbeddingSpace,
    EmbeddingSpaceError,
    derived_space,
    embedding_identity,
)
from one_memory.hy_graph_admin import (
    GraphWidthMismatchError,
    effective_memory_mode,
    inspect_graph_width_mismatch,
)
from one_memory.hy_sdk_types import HySdkRuntimeError
from one_memory.local_embedding import LocalEmbedder
from one_memory.log_file import confine_hy_sdk_logging
from one_memory.memory_features import (
    FEATURES,
    PARAMETERS,
    SETTINGS_BY_NAME,
    FeatureOverrides,
    SettingValue,
    feature_overrides,
)
from one_memory.model_profiles import (
    ModelProfileSnapshot,
    ProviderKind,
    disable_thinking_params,
)

_RUNTIME_LOCK = threading.RLock()
BUILTIN_RETRIEVAL_PROFILE_ID = "builtin-retrieval"
_LOCAL_ONNX_PLACEHOLDER_URL = "http://127.0.0.1/local-onnx"
DEFAULT_RELEVANCE_FLOOR = 0.35

#: The SDK's memory modes. A mode never changes a collection name: the space
#: names the collections, the mode only which stores the client opens.
MemoryMode = Literal["lite", "pro", "ultra"]


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

# One client constructs at a time per home, its first-time store setup
# included. The constructor opens Chroma's `PersistentClient`, and two first
# opens on one directory race over that same unlocked System cache. On a
# clean install the write drain's client and a UI read client started in the
# same second; neither reached `VectorStore ready`, and the first write job
# was lost as unknown. Taken inside `acquire_client`, after the offline gate
# and never while `_STORE_GATE` is held, so it cannot deadlock with
# `hold_stores_offline`, which refuses while `_OPENING` counts a client.
_CONSTRUCTING: dict[Path, threading.Lock] = {}


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
        space: EmbeddingSpace | None = None,
        mode: str | None = None,
        features: FeatureOverrides = (),
        language: str | None = None,
    ) -> None:
        self._profile = profile
        self._api_key = api_key
        self._data_dir = data_dir
        self._http_transport_factory = http_transport_factory
        self._space = space
        #: The SDK mode this handle's client is built with, fixed for its life.
        self.mode = mode or effective_memory_mode()
        #: The SDK features set on its config (`memory_features`), fixed likewise.
        self.features = features
        #: The SDK prompt language (`ui_language`); None = SDK auto-detect.
        self.language = language
        self._lease: ClientLease | None = None
        self._constructing: asyncio.Future[ClientLease] | None = None
        self._lock = asyncio.Lock()

    @property
    def profile(self) -> ModelProfileSnapshot:
        return self._profile

    @property
    def space(self) -> EmbeddingSpace | None:
        return self._space

    @property
    def api_key(self) -> str | None:
        return self._api_key

    async def client(self) -> HyMemoryClient:
        if self._lease is not None:
            return self._lease.client
        async with self._lock:
            if self._lease is None:
                constructing = asyncio.ensure_future(
                    asyncio.to_thread(
                        _acquire_for_handle,
                        self._profile,
                        self._space,
                        api_key=self._api_key,
                        data_dir=self._data_dir,
                        http_transport_factory=self._http_transport_factory,
                        mode=self.mode,
                        features=self.features,
                        **language_kwargs(self.language),
                    )
                )
                self._constructing = constructing
                try:
                    self._lease = await asyncio.shield(constructing)
                except asyncio.CancelledError:
                    # The construction thread runs on. Keep what it builds, so
                    # `close` releases it instead of leaving its runtime open.
                    constructing.add_done_callback(self._keep)
                    raise
            return self._lease.client

    def _keep(self, constructing: asyncio.Future[ClientLease]) -> None:
        if constructing.cancelled() or constructing.exception() is not None:
            return
        if self._lease is None:
            self._lease = constructing.result()

    async def close(self) -> None:
        constructing = self._constructing
        if constructing is not None and not constructing.done():
            # A construction a cancelled caller left behind: wait for its lease.
            await asyncio.gather(constructing, return_exceptions=True)
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


def retrieval_view(profile: ModelProfileSnapshot) -> ModelProfileSnapshot:
    """The read side of `profile`: its embedding space, and no generation.

    Reads must open the collection writes fill (#242), and the collection is
    named by the embedding only. Generation stays the built-in placeholder, so
    a read never needs the generation model. A remote embedding still needs
    the profile's API key, which `build_sdk_config` uses as the embedding key.
    """
    provider = profile.embedding_provider_kind or profile.provider_kind
    endpoint: str | None = None
    if provider is not ProviderKind.LOCAL_ONNX:
        endpoint = profile.embedding_base_url
        if not endpoint:
            raise HySdkRuntimeError("hy_sdk_model_endpoint_missing")
    return builtin_retrieval_snapshot().model_copy(
        update={
            "profile_id": profile.profile_id,
            "revision": profile.revision,
            "embedding_provider_kind": provider,
            "embedding_model": profile.embedding_model,
            "embedding_base_url": endpoint,
            "embedding_dimensions": profile.embedding_dimensions,
            "has_api_key": profile.has_api_key,
        }
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


def sdk_feature_defaults() -> dict[str, SettingValue]:
    """Each `memory_features` setting's SDK default, as a fresh config has it.

    That is the SDK's code default unless the process environment sets the
    SDK's own variable (`FeatureSpec.env`), exactly what an unset feature
    leaves on a client.
    """
    config = MemoryConfig()
    defaults: dict[str, SettingValue] = {
        spec.name: bool(getattr(getattr(config, spec.section), spec.field)) for spec in FEATURES
    }
    for parameter in PARAMETERS:
        defaults[parameter.name] = float(
            getattr(getattr(config, parameter.section), parameter.field)
        )
    return defaults


def apply_sdk_features(
    config: MemoryConfig,
    features: Mapping[str, SettingValue] | Iterable[tuple[str, SettingValue]],
) -> MemoryConfig:
    """Set each feature a person chose on its `MemoryConfig` field.

    Every exposed feature is a config field the SDK reads from the config at
    call time, never from `os.environ`, so this is per client and
    deterministic. Nothing here touches `vector_store`, so the collection
    identity never depends on a feature.
    """
    values = dict(features.items() if isinstance(features, Mapping) else features)
    for name, value in feature_overrides(values):
        spec = SETTINGS_BY_NAME[name]
        setattr(getattr(config, spec.section), spec.field, value)
    return config


class LanguageKwargs(TypedDict, total=False):
    language: str


def language_kwargs(language: str | None) -> LanguageKwargs:
    """`language=` for a client constructor, only when there is one.

    Without a language every call is the one it was before the setting
    existed, so stand-ins written for that call keep working.
    """
    return {"language": language} if language is not None else {}


def apply_sdk_language(config: MemoryConfig, language: str | None) -> MemoryConfig:
    """Set the SDK prompt language (`MemoryConfig.language`), when there is one.

    None (no language set) sets nothing: the SDK detects zh/en as it always
    did. Every display language, zh-Hans included, is set explicitly. Like a
    feature, the language never touches `vector_store`, so the collection
    identity never depends on it.
    """
    if language is not None:
        config.language = language
    return config


def build_sdk_config(
    profile: ModelProfileSnapshot,
    space: EmbeddingSpace | None = None,
    mode: MemoryMode | None = None,
    *,
    api_key: str | None,
    data_dir: Path,
    features: Mapping[str, SettingValue] | Iterable[tuple[str, SettingValue]] = (),
    language: str | None = None,
) -> MemoryConfig:
    """Map a model snapshot and its embedding space to an SDK configuration.

    `space` names the collections and the graph; the profile only says how to
    reach the models. A profile whose embedding identity (model and width) is
    not the space's raises `embedding_space_mismatch` instead of opening new,
    empty collections. Without `space`, the space the profile opened before
    the registry existed is used: offline tools on a home with no registry.
    `mode` never changes a name, and neither do `features`
    (`memory_features`), which are set on top; with none the config is
    exactly what it was before the setting existed. So is `language`
    (`apply_sdk_language`); None leaves the SDK's own zh/en detection.
    """
    if mode is not None and mode not in {"lite", "pro", "ultra"}:
        raise HySdkRuntimeError("hy_sdk_memory_mode_invalid")
    generation_endpoint, generation_key = _generation_access(profile, api_key)
    embedding_provider = profile.embedding_provider_kind or profile.provider_kind
    embedding_service_is_local = embedding_provider is ProviderKind.LOCAL_ONNX
    try:
        identity = embedding_identity(profile)
    except EmbeddingSpaceError as error:
        raise HySdkRuntimeError(error.code) from None
    if embedding_service_is_local:
        embedding_endpoint = None
        embedding_key = None
    else:
        # Never the generation endpoint: a remote embedding has its own.
        embedding_endpoint = _openai_endpoint(embedding_provider, profile.embedding_base_url)
        embedding_key = "ollama" if embedding_provider is ProviderKind.OLLAMA else api_key
        if embedding_provider is ProviderKind.OPENAI_COMPATIBLE and not embedding_key:
            raise HySdkRuntimeError("hy_sdk_embedding_api_key_missing")
        if embedding_provider not in {
            ProviderKind.OPENAI_COMPATIBLE,
            ProviderKind.OLLAMA,
        }:
            raise HySdkRuntimeError("hy_sdk_embedding_provider_unsupported")
    if space is None:
        space = derived_space(profile)
    elif space.identity is None or not identity.matches(space.identity):
        raise HySdkRuntimeError("embedding_space_mismatch")
    # The width the space was opened with: its names carry it, and a remote
    # request sends it as `dimensions`. An "auto" space sends none, as before.
    dimensions = space.name_dims
    embedding_model = identity.model

    root = data_dir.resolve() / "hy-memory" / "governed-v2"
    root.mkdir(parents=True, exist_ok=True)
    collection = space.collection_base

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

    config = MemoryConfig.from_dict(
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
                "db_path": str(root / (space.graph_dir or DEFAULT_GRAPH_DIR)),
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
    return apply_sdk_language(apply_sdk_features(config, features), language)


def _acquire_for_handle(
    profile: ModelProfileSnapshot,
    space: EmbeddingSpace | None,
    *,
    api_key: str | None,
    data_dir: Path,
    http_transport_factory: Callable[[], Any] | None,
    mode: str | None,
    features: FeatureOverrides = (),
    language: str | None = None,
) -> ClientLease:
    # `acquire_client` is looked up at call time, so a stand-in replaces every
    # construction. A handle without a space keeps the call it always made.
    named: dict[str, Any] = {"space": space} if space is not None else {}
    return acquire_client(
        profile,
        api_key=api_key,
        data_dir=data_dir,
        http_transport_factory=http_transport_factory,
        mode=mode,
        features=features,
        **named,
        **language_kwargs(language),
    )


def acquire_client(
    profile: ModelProfileSnapshot,
    space: EmbeddingSpace | None = None,
    *,
    api_key: str | None,
    data_dir: Path,
    http_transport_factory: Callable[[], Any] | None = None,
    mode: str | None = None,
    features: FeatureOverrides = (),
    language: str | None = None,
) -> ClientLease:
    """Construct a public SDK client and return its explicit runtime lease.

    `space` names the collections (`build_sdk_config`). `mode` is the SDK
    pipeline mode (lite / pro / ultra) and never changes a name; None keeps
    the pre-setting default (ultra when `ONE_MEMORY_GRAPH_STORE` is set, else
    pro). `features` are the `memory_features` set on its config; none keeps
    every SDK default. `language` is the SDK prompt language; None
    auto-detects. Waits while `hold_stores_offline` holds this home's stores.
    """
    key = data_dir.resolve()
    with _STORE_GATE:
        while key in _OFFLINE_STORES:
            _STORE_GATE.wait()
        _OPENING[key] = _OPENING.get(key, 0) + 1
    try:
        named: dict[str, Any] = {"space": space} if space is not None else {}
        return _acquire_client(
            profile,
            api_key=api_key,
            data_dir=data_dir,
            http_transport_factory=http_transport_factory,
            mode=mode or effective_memory_mode(),
            features=features,
            **named,
            **language_kwargs(language),
        )
    finally:
        with _STORE_GATE:
            _OPENING[key] -= 1
            if not _OPENING[key]:
                del _OPENING[key]
            _STORE_GATE.notify_all()


@contextmanager
def first_store_open(data_dir: Path) -> Iterator[None]:
    """One opening of this home's stores at a time: Chroma's first open is unlocked.

    The same lock client construction takes, for anything else that opens
    Chroma beside the Runtime (the re-embed functions).
    """
    key = data_dir.resolve()
    with _STORE_GATE:
        constructing = _CONSTRUCTING.setdefault(key, threading.Lock())
    with constructing:
        yield


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
    space: EmbeddingSpace | None = None,
    *,
    api_key: str | None,
    data_dir: Path,
    http_transport_factory: Callable[[], Any] | None,
    mode: str = "pro",
    features: FeatureOverrides = (),
    language: str | None = None,
) -> ClientLease:
    config = build_sdk_config(
        profile,
        space,
        cast(MemoryMode, mode),
        api_key=api_key,
        data_dir=data_dir,
        features=features,
        language=language,
    )
    config.http_transport_factory = http_transport_factory
    mismatch = inspect_graph_width_mismatch(config)
    if mismatch is not None:
        raise GraphWidthMismatchError(mismatch)
    key = data_dir.resolve()
    runtime = _acquire_runtime(config, key)
    embedding_service: LocalOnnxEmbeddingService | None = None
    if (profile.embedding_provider_kind or profile.provider_kind) is ProviderKind.LOCAL_ONNX:
        embedding_service = LocalOnnxEmbeddingService()
    with _STORE_GATE:
        constructing = _CONSTRUCTING.setdefault(key, threading.Lock())
    try:
        with constructing:
            client = HyMemoryClient(
                config=config,
                mode=mode,
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


def embedding_endpoint(profile: ModelProfileSnapshot) -> str | None:
    """The OpenAI-compatible base URL the SDK embeds through; None when local."""
    provider = profile.embedding_provider_kind or profile.provider_kind
    if provider is ProviderKind.LOCAL_ONNX:
        return None
    return _openai_endpoint(provider, profile.embedding_base_url)


def legacy_collection_base(profile: ModelProfileSnapshot) -> str:
    """The four-attribute name a profile's space had before the registry.

    Existing homes are registered under exactly this name, so it must never
    change: provider, embedding endpoint, model and declared width.
    """
    provider = profile.embedding_provider_kind or profile.provider_kind
    local = provider is ProviderKind.LOCAL_ONNX
    model = profile.embedding_model or (BUILTIN_EMBEDDING_MODEL if local else None)
    if not model:
        raise HySdkRuntimeError("hy_sdk_embedding_model_required")
    if local:
        return _collection_base(
            provider, None, model, profile.embedding_dimensions or BUILTIN_EMBEDDING_DIMENSIONS
        )
    endpoint = _openai_endpoint(provider, profile.embedding_base_url)
    return _collection_base(provider, endpoint, model, profile.embedding_dimensions)


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
        endpoint = _openai_endpoint(provider, profile.embedding_base_url)
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
