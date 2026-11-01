"""The read client, kept in the embedding space the active profile writes to.

Every read used to open the built-in 512-dim collection while writes followed
the active profile, so a custom embedding read an empty space (#242). This
handle opens the active profile's `retrieval_view` instead, and the built-in
space only when no profile is active.

A profile change builds a new client and retires the old one; a retired client
is closed once no task still uses it, as `SdkWriteCoordinator` does for writes
(#218). A use is the task that asked for the client: callers keep their
`client = await handle.client()` shape, and the use ends with that task (one
HTTP request, one retention pass).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from one_memory.hy_sdk_runtime import (
    BUILTIN_RETRIEVAL_PROFILE_ID,
    SdkClientHandle,
    builtin_retrieval_snapshot,
    retrieval_view,
)
from one_memory.hy_sdk_types import HySdkRuntimeError
from one_memory.model_profiles import ModelProfileStore, ProviderKind
from one_memory.secrets import SecretStore, SecretStoreUnavailableError

_BUILTIN = (BUILTIN_RETRIEVAL_PROFILE_ID, 0)


class ActiveProfileReadHandle(SdkClientHandle):
    """An `SdkClientHandle` whose profile follows the active one."""

    def __init__(
        self,
        *,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        data_dir: Path,
    ) -> None:
        super().__init__(builtin_retrieval_snapshot(), api_key=None, data_dir=data_dir)
        self.profiles = profiles
        self.secrets = secrets
        self.data_dir = data_dir
        self._handles: dict[tuple[str, int], SdkClientHandle] = {}
        #: Tasks in flight per handle; a retired handle closes at zero.
        self._uses: dict[SdkClientHandle, int] = {}
        self._retired: set[SdkClientHandle] = set()
        self._closing: set[asyncio.Task[None]] = set()
        self._handle_lock = asyncio.Lock()

    async def client(self) -> Any:
        identity = await asyncio.to_thread(self.profiles.active_identity) or _BUILTIN
        async with self._handle_lock:
            handle = self._handles.get(identity)
            if handle is None:
                # Only on a change of profile: resolving a key can reach the
                # system keychain, so it is not done on every read.
                handle = await asyncio.to_thread(self._build, identity)
                self._handles[identity] = handle
            self._retired.update(value for key, value in self._handles.items() if key != identity)
            self._handles = {identity: handle}
            unused = {value for value in self._retired if not self._uses.get(value)}
            self._retired -= unused
            self._hold(handle)
        await asyncio.gather(*(value.close() for value in unused))
        return await handle.client()

    async def close(self) -> None:
        async with self._handle_lock:
            handles = (*self._handles.values(), *self._retired)
            self._handles, self._retired, self._uses = {}, set(), {}
        await asyncio.gather(*(handle.close() for handle in handles), *self._closing)

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

    def _build(self, identity: tuple[str, int]) -> SdkClientHandle:
        if identity == _BUILTIN:
            return SdkClientHandle(
                builtin_retrieval_snapshot(), api_key=None, data_dir=self.data_dir
            )
        profile = self.profiles.snapshot(*identity)
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
        return SdkClientHandle(view, api_key=secret, data_dir=self.data_dir)
