"""The read client, kept in the live embedding space.

Every read used to open the built-in 512-dim collection while writes followed
the active profile, so a custom embedding read an empty space (#242). Reads
now open the space the registry pins as live (`embedding_spaces`), which is
the space writes fill. The embedding that reaches it is the active profile's
when its model and width are the space's -- a new endpoint for the same model
keeps the space -- and otherwise the profile version that filled the space
(the built-in model for the built-in space). A switch that is still running
never changes this: reads keep serving the old space until its commit.

A change of space or profile builds a new client and retires the old one; a
retired client is closed once no task still uses it, as `SdkWriteCoordinator`
does for writes (#218). A use is the task that asked for the client: callers
keep their `client = await handle.client()` shape, and the use ends with that
task (one HTTP request, one retention pass).

The memory mode (lite / pro / ultra) is fixed per SDK client, so a mode change
rebuilds and retires the same way, and so do the SDK features
(`memory_features`) and the SDK prompt language (`ui_language`): handles are
keyed by (space, profile, mode, features, language). None of mode, features
or language names a collection; each is a separate client parameter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from one_memory.background_work import in_sdk_read
from one_memory.embedding_health import EmbeddingGuardedClient, EmbeddingHealth
from one_memory.embedding_spaces import (
    EmbeddingSpace,
    EmbeddingSpaceStore,
    embedding_identity,
    registry_for,
)
from one_memory.hy_graph_admin import effective_memory_mode
from one_memory.hy_sdk_runtime import (
    BUILTIN_RETRIEVAL_PROFILE_ID,
    SdkClientHandle,
    builtin_retrieval_snapshot,
    embedding_endpoint,
    language_kwargs,
    retrieval_view,
)
from one_memory.hy_sdk_types import HySdkRuntimeError
from one_memory.memory_features import FeatureOverrides
from one_memory.model_profiles import ModelProfileSnapshot, ModelProfileStore, ProviderKind
from one_memory.secrets import SecretStore, SecretStoreUnavailableError

_BUILTIN = (BUILTIN_RETRIEVAL_PROFILE_ID, 0)
#: (space id, profile id, revision, memory mode, features, SDK language)
_HandleKey = tuple[str, str, int, str, FeatureOverrides, str | None]


class ActiveProfileReadHandle(SdkClientHandle):
    """An `SdkClientHandle` that follows the live space and its embedding."""

    def __init__(
        self,
        *,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        data_dir: Path,
        memory_mode: Callable[[], str] = effective_memory_mode,
        features: Callable[[], FeatureOverrides] = lambda: (),
        spaces: EmbeddingSpaceStore | None = None,
        health: EmbeddingHealth | None = None,
        language: Callable[[], str | None] = lambda: None,
    ) -> None:
        super().__init__(builtin_retrieval_snapshot(), api_key=None, data_dir=data_dir)
        self.profiles = profiles
        self.secrets = secrets
        self.data_dir = data_dir
        self.memory_mode = memory_mode
        self.current_features = features
        self.current_language = language
        #: None only for a profile store without a database (test doubles).
        self.spaces = spaces if spaces is not None else registry_for(profiles)
        self.health = health or EmbeddingHealth()
        self._handles: dict[_HandleKey, SdkClientHandle] = {}
        #: Whether an active profile version reads a space, per (profile, space).
        self._matches: dict[tuple[str, int, str], bool] = {}
        #: Tasks in flight per handle; a retired handle closes at zero.
        self._uses: dict[SdkClientHandle, int] = {}
        self._retired: set[SdkClientHandle] = set()
        self._closing: set[asyncio.Task[None]] = set()
        self._handle_lock = asyncio.Lock()

    async def client(self) -> Any:
        handle = await self.current_handle()
        return self._guarded(handle, await handle.client())

    async def client_for_mode(self, mode: str) -> Any:
        """Build/read with the caller's one mode snapshot, without rereading it."""
        handle = await self.current_handle(mode=mode)
        return self._guarded(handle, await handle.client())

    def _guarded(self, handle: SdkClientHandle, client: Any) -> Any:
        """A remote embedding's searches report `embedding_unavailable` (no fallback)."""
        profile = getattr(handle, "profile", None)
        if not isinstance(profile, ModelProfileSnapshot):
            return client
        endpoint = embedding_endpoint(profile)
        if endpoint is None:
            return client
        space = handle.space
        name_dims = space.name_dims if space is not None else profile.embedding_dimensions
        return EmbeddingGuardedClient(
            client,
            lambda: self.health.available(
                profile, endpoint=endpoint, name_dims=name_dims, api_key=handle.api_key
            ),
        )

    async def current_handle(self, *, mode: str | None = None) -> SdkClientHandle:
        """The handle of the live space, held for the asking task."""
        space, identity = await in_sdk_read(self._resolve)
        selected_mode, features = mode or self.memory_mode(), self.current_features()
        language = self.current_language()
        key = (
            space.space_id if space is not None else "",
            *identity,
            selected_mode,
            features,
            language,
        )
        async with self._handle_lock:
            handle = self._handles.get(key)
            if handle is None:
                # Only on a change of space or profile: resolving a key can
                # reach the system keychain, so it is not done on every read.
                handle = await in_sdk_read(
                    self._build, space, identity, selected_mode, features, language
                )
                self._handles[key] = handle
            self._retired.update(value for name, value in self._handles.items() if name != key)
            self._handles = {key: handle}
            unused = {value for value in self._retired if not self._uses.get(value)}
            self._retired -= unused
            self._hold(handle)
        await asyncio.gather(*(value.close() for value in unused))
        return handle

    async def close(self) -> None:
        async with self._handle_lock:
            handles = (*self._handles.values(), *self._retired)
            self._handles, self._retired, self._uses = {}, set(), {}
        await asyncio.gather(*(handle.close() for handle in handles), *self._closing)

    async def close_idle(self) -> None:
        """Retire every client; the unused close now, the rest when their task ends."""
        async with self._handle_lock:
            self._retired.update(self._handles.values())
            self._handles = {}
            unused = {value for value in self._retired if not self._uses.get(value)}
            self._retired -= unused
        await asyncio.gather(*(value.close() for value in unused))

    def _resolve(self) -> tuple[EmbeddingSpace | None, tuple[str, int]]:
        active = self.profiles.active_identity()
        if self.spaces is None:
            return None, active or _BUILTIN
        space = self.spaces.live()
        if space is None:
            # A home this process has not bootstrapped yet: register what it
            # already uses, under the name it already has.
            space = self.spaces.bootstrap(
                self.profiles.snapshot(*active) if active is not None else None
            )
        if active is not None and self._reads(active, space):
            return space, active
        if space.profile_id is not None and space.profile_revision is not None:
            identity = (space.profile_id, space.profile_revision)
            try:
                self.profiles.snapshot(*identity)
            except KeyError:
                # Its endpoint and key went with the profile; the built-in
                # model cannot read these vectors. Say so, never read wrong.
                raise HySdkRuntimeError("embedding_space_profile_missing") from None
            return space, identity
        return space, _BUILTIN

    def _reads(self, identity: tuple[str, int], space: EmbeddingSpace) -> bool:
        key = (*identity, space.space_id)
        known = self._matches.get(key)
        if known is None:
            if space.identity is None:
                known = False
            else:
                profile = self.profiles.snapshot(*identity)
                known = embedding_identity(profile).matches(space.identity)
            self._matches[key] = known
        return known

    def _hold(self, handle: SdkClientHandle) -> None:
        """Count a use of `handle` until the asking task finishes."""
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - always called from a coroutine
            return
        self._uses[handle] = self._uses.get(handle, 0) + 1
        task.add_done_callback(lambda _task: self._release(handle))

    def _release(self, handle: SdkClientHandle) -> None:
        remaining = self._uses.get(handle, 0) - 1
        if remaining > 0:
            self._uses[handle] = remaining
            return
        self._uses.pop(handle, None)
        if handle in self._retired:
            self._retired.discard(handle)
            closing = asyncio.get_running_loop().create_task(handle.close())
            self._closing.add(closing)
            closing.add_done_callback(self._closing.discard)

    def _build(
        self,
        space: EmbeddingSpace | None,
        identity: tuple[str, int],
        mode: str,
        features: FeatureOverrides = (),
        language: str | None = None,
    ) -> SdkClientHandle:
        # Without a registry (a profile store with no database) the handle
        # opens the space its profile names, exactly as before.
        named: dict[str, Any] = {"space": space} if space is not None else {}
        named.update(language_kwargs(language))
        if identity == _BUILTIN:
            return SdkClientHandle(
                builtin_retrieval_snapshot(),
                api_key=None,
                data_dir=self.data_dir,
                mode=mode,
                features=features,
                **named,
            )
        profile: ModelProfileSnapshot = self.profiles.snapshot(*identity)
        view = retrieval_view(profile)
        secret: str | None = None
        # Only a remote OpenAI-compatible embedding sends the shared key.
        if view.embedding_provider_kind is ProviderKind.OPENAI_COMPATIBLE:
            try:
                secret = self.profiles.resolve_snapshot_secret(*identity, self.secrets)
            except SecretStoreUnavailableError:
                raise HySdkRuntimeError("secure_storage_unavailable") from None
            if secret is None:
                raise HySdkRuntimeError("model_secret_unavailable")
        return SdkClientHandle(
            view, api_key=secret, data_dir=self.data_dir, mode=mode, features=features, **named
        )
