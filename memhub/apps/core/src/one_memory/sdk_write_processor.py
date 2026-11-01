"""Single-write adapter from durable Evidence payloads to the public HY SDK."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from one_memory.hy_graph_admin import effective_memory_mode
from one_memory.hy_sdk_runtime import SdkClientHandle, builtin_retrieval_snapshot
from one_memory.imports.models import EvidenceContentKind, EvidenceOrigin, EvidenceRecord
from one_memory.input_sanitization import sanitize_model_input
from one_memory.memory_features import FeatureOverrides
from one_memory.model_profiles import ModelProfileSnapshot, ModelProfileStore
from one_memory.secrets import SecretStore, SecretStoreUnavailableError

PRODUCT_AGENT_ID = "all-apps"
_MESSAGE_ROLES = frozenset({"user", "assistant", "tool"})
_CONVERSATION_ROLES = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class SdkWriteReceipt:
    """The only SDK write identity retained by MemHub."""

    raw_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()


class SdkWriteProcessor:
    """Turn one durable job into exactly one public SDK write call."""

    def __init__(self, client_handle: SdkClientHandle, *, lite: bool = False) -> None:
        self._client_handle = client_handle
        #: lite makes no model call: `add_extracted` would still call one, so a
        #: direct memory note is stored as one raw user turn through `add`.
        self._lite = lite

    async def process_sdk_job(
        self,
        records: tuple[EvidenceRecord, ...],
        *,
        request_id: str,
        before_hy_invocation: Callable[[], Awaitable[None]],
    ) -> SdkWriteReceipt:
        if not records:
            raise RuntimeError("hy_memory_task_without_inputs")
        extracted = all(_is_extracted_content(record) for record in records)
        if not extracted and not any(
            record.content_kind is EvidenceContentKind.RAW_CONVERSATION
            and record.role in _CONVERSATION_ROLES
            for record in records
        ):
            # Nothing to remember: tool records alone. The SDK strips them on
            # the chat path and answers 400 "content or messages is
            # required", which used to settle the job as unknown. Completed
            # with no memories, and the SDK is never called.
            return SdkWriteReceipt()

        scope = _write_scope(records)
        metadata = _write_metadata(records, scope)
        try:
            client = await self._client_handle.client()
        except Exception as error:
            # Before `before_hy_invocation`: nothing was sent, so the job
            # waits and retries instead of being left `unknown`.
            raise RuntimeError("hy_memory_client_unavailable") from error

        if extracted and not self._lite:
            contents = [sanitize_model_input(record.verbatim) for record in records]
            await before_hy_invocation()
            result = await asyncio.to_thread(
                client.add_extracted,
                contents,
                user_id=scope.user_id,
                agent_id=PRODUCT_AGENT_ID,
                session_id=scope.session_id,
                source=scope.source,
                metadata=metadata,
                memory_at=scope.memory_at,
                request_id=request_id,
            )
        else:
            messages = [
                {
                    "role": "user" if extracted else record.role,
                    "content": sanitize_model_input(record.verbatim),
                }
                for record in records
                if extracted
                or (
                    record.content_kind is EvidenceContentKind.RAW_CONVERSATION
                    and record.role in _MESSAGE_ROLES
                )
            ]
            if not messages:
                raise RuntimeError("hy_memory_task_without_inputs")
            await before_hy_invocation()
            result = await asyncio.to_thread(
                client.add,
                messages,
                user_id=scope.user_id,
                agent_id=PRODUCT_AGENT_ID,
                session_id=scope.session_id,
                source=scope.source,
                metadata=metadata,
                memory_at=scope.memory_at,
                scene=None,
                workspace_id=scope.workspace,
                request_id=request_id,
            )

        return _receipt_from_result(result)

    async def inquire_write_outcome(self, request_id: str) -> SdkWriteReceipt | None:
        """Return a confirmed successful receipt without scanning SDK stores."""
        if not request_id:
            raise ValueError("request_id is required")
        client = await self._client_handle.client()
        outcome = await asyncio.to_thread(client.get_write_outcome, request_id)
        if outcome is None:
            return None
        if not isinstance(outcome, Mapping):
            raise RuntimeError("hy_sdk_write_outcome_shape_invalid")

        status = outcome.get("status")
        if status != "succeeded":
            return None
        result = outcome.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("hy_sdk_write_outcome_shape_invalid")
        return _receipt_from_result(result)

    async def close(self) -> None:
        await self._client_handle.close()


class SdkWriteCoordinator:
    """Resolve the active model profile, then execute one public SDK write.

    A model profile change retires the previous profile's handle, but only
    closes it once nothing uses it. Closing it at once tore the client out
    from under a write still running on it: the SDK cleared its stores and
    the SharedRuntime closed mid-`add`, the job failed with AttributeError
    after HY was invoked, and it was left `unknown`.

    Handles are keyed by profile, memory mode and the SDK features
    (`memory_features`), so a mode or feature change retires handles the same
    way. In lite no generation model is needed: without an
    active profile the built-in local embedder writes raw turns.

    `exclusive_for_evolve(user_id)` keeps evolve and that user's writes
    apart: it waits for the user's write in flight, and while it runs that
    user's jobs are refused before the SDK is called
    (`memory_evolve_in_progress`), so the drain puts them back.
    """

    def __init__(
        self,
        *,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        data_dir: Any,
        transport_factory: Callable[[ModelProfileSnapshot], Callable[[], Any] | None],
        sedimentation_enabled: Callable[[], bool],
        memory_mode: Callable[[], str] = effective_memory_mode,
        memory_features: Callable[[], FeatureOverrides] = lambda: (),
    ) -> None:
        self.profiles = profiles
        self.secrets = secrets
        self.data_dir = data_dir
        self.transport_factory = transport_factory
        self.sedimentation_enabled = sedimentation_enabled
        self.memory_mode = memory_mode
        self.memory_features = memory_features
        self._handles: dict[tuple[str, int, str, FeatureOverrides], SdkClientHandle] = {}
        #: Users evolve (or the graph backfill) is running for, the writes in
        #: flight per user, and one exclusive run per user at a time. Writes
        #: of one user still run side by side; only evolve waits for them.
        self._evolving: set[str] = set()
        self._writing: dict[str, int] = {}
        self._writes_idle = asyncio.Condition()
        self._exclusive: dict[str, asyncio.Lock] = {}
        #: Uses in flight per handle; a retired handle closes at zero.
        self._uses: dict[SdkClientHandle, int] = {}
        self._retired: set[SdkClientHandle] = set()
        self._lock = asyncio.Lock()

    async def process_sdk_job(
        self,
        records: tuple[EvidenceRecord, ...],
        *,
        request_id: str,
        before_hy_invocation: Callable[[], Awaitable[None]],
    ) -> SdkWriteReceipt:
        if not self.sedimentation_enabled():
            raise RuntimeError("model_sedimentation_disabled")
        user_id = records[0].user_id if records else "local"
        if user_id in self._evolving:
            # Before any SDK call: the drain returns the job to pending.
            raise RuntimeError("memory_evolve_in_progress")
        self._writing[user_id] = self._writing.get(user_id, 0) + 1
        try:
            async with self._active_handle() as handle:
                return await SdkWriteProcessor(
                    handle, lite=getattr(handle, "mode", None) == "lite"
                ).process_sdk_job(
                    records,
                    request_id=request_id,
                    before_hy_invocation=before_hy_invocation,
                )
        finally:
            remaining = self._writing.get(user_id, 1) - 1
            if remaining > 0:
                self._writing[user_id] = remaining
            else:
                self._writing.pop(user_id, None)
            async with self._writes_idle:
                self._writes_idle.notify_all()

    async def retire_stale_handles(self) -> None:
        """After a mode or feature change: retire handles built otherwise.

        The idle ones close now. A handle still writing is closed when its
        last use ends (#218), so a write that started in the old mode, or
        with the old features, finishes in it.
        """
        build = (self.memory_mode(), self.memory_features())
        async with self._lock:
            stale = {key: handle for key, handle in self._handles.items() if key[2:] != build}
            for key in stale:
                del self._handles[key]
            self._retired.update(stale.values())
            unused = {value for value in self._retired if not self._uses.get(value)}
            self._retired -= unused
        await asyncio.gather(*(value.close() for value in unused))

    #: The name #253 introduced; a mode change is one kind of rebuild.
    retire_stale_modes = retire_stale_handles

    def evolving(self, user_id: str) -> bool:
        """Whether evolve holds this user, so their queued writes should wait."""
        return user_id in self._evolving

    @asynccontextmanager
    async def exclusive_for_evolve(self, user_id: str) -> AsyncIterator[None]:
        """Hold one user's writes off while evolve runs for them.

        New writes of the user are refused from here on; the ones already in
        flight are waited for before the body runs.
        """
        async with self._exclusive.setdefault(user_id, asyncio.Lock()):
            self._evolving.add(user_id)
            try:
                async with self._writes_idle:
                    await self._writes_idle.wait_for(lambda: not self._writing.get(user_id))
                yield
            finally:
                self._evolving.discard(user_id)

    async def inquire_write_outcome(self, request_id: str) -> SdkWriteReceipt | None:
        async with self._active_handle() as handle:
            return await SdkWriteProcessor(handle).inquire_write_outcome(request_id)

    async def close(self) -> None:
        async with self._lock:
            handles = (*self._handles.values(), *self._retired)
            self._handles, self._retired, self._uses = {}, set(), {}
        await asyncio.gather(*(handle.close() for handle in handles))

    @asynccontextmanager
    async def use_client(self) -> AsyncIterator[Any]:
        """The public SDK client of the active write profile, kept open while used."""
        async with self._active_handle() as handle:
            yield await handle.client()

    async def active_profile_credentials(
        self,
    ) -> tuple[ModelProfileSnapshot, str | None]:
        """Resolve one stable profile snapshot for graph administration."""
        _identity, profile, secret = await asyncio.to_thread(self._read_active_credentials)
        return profile, secret

    @asynccontextmanager
    async def _active_handle(self) -> AsyncIterator[SdkClientHandle]:
        """Use the active profile's handle; retire the others, closing the unused."""
        mode = self.memory_mode()
        features = self.memory_features()
        profile_identity, profile, secret = await asyncio.to_thread(
            self._read_active_credentials, mode
        )
        identity = (*profile_identity, mode, features)
        async with self._lock:
            handle = self._handles.get(identity)
            if handle is None:
                handle = SdkClientHandle(
                    profile,
                    api_key=secret,
                    data_dir=self.data_dir,
                    http_transport_factory=self.transport_factory(profile),
                    mode=mode,
                    features=features,
                )
                self._handles[identity] = handle
            self._retired.update(value for key, value in self._handles.items() if key != identity)
            self._handles = {identity: handle}
            self._uses[handle] = self._uses.get(handle, 0) + 1
            unused = {value for value in self._retired if not self._uses.get(value)}
            self._retired -= unused
        await asyncio.gather(*(value.close() for value in unused))
        try:
            yield handle
        finally:
            # Bookkeeping without an await, so a cancelled caller still
            # releases its use.
            remaining = self._uses.get(handle, 0) - 1
            if remaining > 0:
                self._uses[handle] = remaining
            else:
                self._uses.pop(handle, None)
            if remaining <= 0 and handle in self._retired:
                self._retired.discard(handle)
                await handle.close()

    def _read_active_credentials(
        self,
        mode: str = "pro",
    ) -> tuple[tuple[str, int], ModelProfileSnapshot, str | None]:
        identity = self.profiles.active_identity()
        if identity is None:
            if mode == "lite":
                # lite stores raw turns with one embedding and calls no model.
                builtin = builtin_retrieval_snapshot()
                return (builtin.profile_id, builtin.revision), builtin, None
            raise RuntimeError("model_profile_not_active")
        profile = self.profiles.snapshot(*identity)
        try:
            secret = self.profiles.resolve_snapshot_secret(
                *identity,
                self.secrets,
            )
        except SecretStoreUnavailableError:
            raise RuntimeError("secure_storage_unavailable") from None
        if self.profiles.active_identity() != identity:
            raise RuntimeError("model_profile_changed")
        if profile.has_api_key and secret is None:
            raise RuntimeError("model_secret_unavailable")
        return identity, profile, secret


@dataclass(frozen=True)
class _WriteScope:
    user_id: str
    session_id: str
    source: str
    workspace: str | None
    workspaces: tuple[str, ...]
    memory_at: datetime | None


def _write_scope(records: tuple[EvidenceRecord, ...]) -> _WriteScope:
    first = records[0]
    expected = (first.user_id, first.source_id, first.conversation_id)
    if any(
        (record.user_id, record.source_id, record.conversation_id) != expected
        for record in records[1:]
    ):
        raise RuntimeError("durable_write_multiple_sources")
    workspaces = tuple(
        sorted({record.workspace for record in records if record.workspace is not None})
    )
    return _WriteScope(
        user_id=first.user_id,
        session_id=first.conversation_id,
        source=first.source_id,
        workspace=workspaces[0] if len(workspaces) == 1 else None,
        workspaces=workspaces,
        memory_at=max(
            (record.created_at for record in records if record.created_at is not None),
            default=None,
        ),
    )


def _write_metadata(
    records: tuple[EvidenceRecord, ...],
    scope: _WriteScope,
) -> dict[str, Any]:
    return {
        "source_id": scope.source,
        "session_id": scope.session_id,
        "workspace_ids": list(scope.workspaces),
        "memory_at": scope.memory_at.isoformat() if scope.memory_at is not None else None,
        "evidence_ids": [record.evidence_id for record in records],
        "input_kind": (
            "memory_content_without_raw"
            if all(_is_extracted_content(record) for record in records)
            else "raw_conversation"
        ),
    }


def _is_extracted_content(record: EvidenceRecord) -> bool:
    if record.content_kind is EvidenceContentKind.MEMORY_CONTENT:
        return True
    manual = record.origin_meta.get(EvidenceOrigin.MANUAL_IMPORT)
    return (
        record.source_id == "one-memory-manual"
        and manual is not None
        and manual.raw_meta.get("input_mode") == "direct_memory_note"
    )


def _receipt_from_result(value: object) -> SdkWriteReceipt:
    if not isinstance(value, Mapping):
        raise RuntimeError("hy_sdk_write_receipt_shape_invalid")
    if value.get("success") is not True:
        raise RuntimeError("hy_sdk_write_failed")

    # Read only documented public receipt fields. ``variant`` identifies the
    # productivity namespace; IDs from both variants are canonical memory IDs.
    variant = value.get("variant")
    if variant is not None and not isinstance(variant, str):
        raise RuntimeError("hy_sdk_write_receipt_shape_invalid")
    raw_ids = _public_ids(value, singular="raw_id", plural="raw_ids")
    memory_ids = _ordered_unique(
        (
            *_public_ids(value, singular="memory_id", plural="memory_ids"),
            *_public_ids(value, plural="experience_ids"),
            *_public_ids(value, plural="fact_ids"),
        )
    )
    return SdkWriteReceipt(raw_ids=raw_ids, memory_ids=memory_ids)


def _public_ids(
    value: Mapping[object, object],
    *,
    singular: str | None = None,
    plural: str,
) -> tuple[str, ...]:
    found: list[str] = []
    if singular is not None:
        item = value.get(singular)
        if item is not None:
            if not isinstance(item, str):
                raise RuntimeError("hy_sdk_write_receipt_shape_invalid")
            if item:
                found.append(item)
    items = value.get(plural)
    if items is not None:
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise RuntimeError("hy_sdk_write_receipt_shape_invalid")
        for item in items:
            if not isinstance(item, str):
                raise RuntimeError("hy_sdk_write_receipt_shape_invalid")
            if item:
                found.append(item)
    return _ordered_unique(found)


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
