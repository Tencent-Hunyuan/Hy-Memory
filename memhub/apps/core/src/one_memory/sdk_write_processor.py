"""Single-write adapter from durable Evidence payloads to the public HY SDK."""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from one_memory.background_work import in_sdk_write
from one_memory.embedding_spaces import (
    EmbeddingSpace,
    EmbeddingSpaceStore,
    embedding_identity,
    registry_for,
)
from one_memory.hy_graph_admin import effective_memory_mode
from one_memory.hy_sdk_runtime import (
    SdkClientHandle,
    builtin_retrieval_snapshot,
    language_kwargs,
    retrieval_view,
)
from one_memory.imports.models import EvidenceContentKind, EvidenceOrigin, EvidenceRecord
from one_memory.input_sanitization import sanitize_model_input
from one_memory.memory_features import FeatureOverrides
from one_memory.model_profiles import ModelProfileSnapshot, ModelProfileStore, ProviderKind
from one_memory.secrets import SecretStore, SecretStoreUnavailableError

PRODUCT_AGENT_ID = "all-apps"

#: SDK request ids whose write call is running on a thread right now. A write
#: the drain gave up on (its work ceiling, a lost lease) keeps running on its
#: thread; it must not be re-run beside itself (`recover_unknown`).
_IN_FLIGHT: set[str] = set()
_IN_FLIGHT_LOCK = threading.Lock()


def sdk_write_in_flight(request_id: str) -> bool:
    with _IN_FLIGHT_LOCK:
        return request_id in _IN_FLIGHT


class _Tracked:
    """One SDK write call, held in `_IN_FLIGHT` from submission until it ends.

    If the wait for it is cancelled before a thread picked it up, it is
    abandoned: it will not start, and it leaves `_IN_FLIGHT` at once.
    """

    def __init__(self, request_id: str, work: Callable[..., Any]) -> None:
        self.request_id = request_id
        self.work = work
        self.started = False
        self.abandoned = False
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.add(request_id)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with _IN_FLIGHT_LOCK:
            if self.abandoned:
                raise RuntimeError("hy_memory_write_abandoned")
            self.started = True
        try:
            return self.work(*args, **kwargs)
        finally:
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.discard(self.request_id)

    def abandon(self) -> None:
        with _IN_FLIGHT_LOCK:
            if not self.started:
                self.abandoned = True
                _IN_FLIGHT.discard(self.request_id)


async def _write(tracked_id: str, work: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    call = _Tracked(tracked_id, work)
    try:
        return await in_sdk_write(call, *args, **kwargs)
    except asyncio.CancelledError:
        call.abandon()
        raise


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
        before_hy_invocation: Callable[[bool], Awaitable[None]],
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
            await before_hy_invocation(True)
            result = await _write(
                request_id,
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
            await before_hy_invocation(not self._lite)
            result = await _write(
                request_id,
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
        outcome = await in_sdk_write(client.get_write_outcome, request_id)
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

    Handles are keyed by profile, memory mode, the SDK features
    (`memory_features`) and the SDK language (`ui_language`), so a mode,
    feature or language change retires handles the same way. In lite no
    generation model is needed: without an active profile the built-in
    local embedder writes raw turns.

    `exclusive_for_evolve(user_id)` keeps evolve and that user's writes
    apart: it waits for the user's write in flight, and while it runs that
    user's jobs are refused before the SDK is called
    (`memory_evolve_in_progress`), so the drain puts them back.

    Writes go to the live embedding space only. An active profile whose
    embedding (model and width) is not the live space's is refused with
    `embedding_space_mismatch`: it used to open a new, empty collection.
    While an embedding switch runs, `writes_open` is false and no write
    starts (`embedding_switch_in_progress`); the job waits in the queue.
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
        spaces: EmbeddingSpaceStore | None = None,
        writes_open: Callable[[], bool] = lambda: True,
        sdk_language: Callable[[], str | None] = lambda: None,
    ) -> None:
        self.profiles = profiles
        self.secrets = secrets
        self.data_dir = data_dir
        self.transport_factory = transport_factory
        self.sedimentation_enabled = sedimentation_enabled
        self.memory_mode = memory_mode
        self.memory_features = memory_features
        self.sdk_language = sdk_language
        #: Keyed by (profile id, revision, space id, memory mode, features,
        #: SDK language).
        self._handles: dict[
            tuple[str, int, str, str, FeatureOverrides, str | None], SdkClientHandle
        ] = {}
        #: Users evolve (or the graph backfill) is running for, the writes in
        #: flight per user, and one exclusive run per user at a time. Writes
        #: of one user still run side by side; only evolve waits for them.
        self._evolving: set[str] = set()
        self._writing: dict[str, int] = {}
        self._writes_idle = asyncio.Condition()
        self._exclusive: dict[str, asyncio.Lock] = {}
        #: None only for a profile store without a database (test doubles):
        #: then every client opens the space its profile names, as before.
        self.spaces = spaces if spaces is not None else registry_for(profiles)
        self.writes_open = writes_open
        #: Uses in flight per handle; a retired handle closes at zero.
        self._uses: dict[SdkClientHandle, int] = {}
        self._retired: set[SdkClientHandle] = set()
        self._lock = asyncio.Lock()

    async def process_sdk_job(
        self,
        records: tuple[EvidenceRecord, ...],
        *,
        request_id: str,
        before_hy_invocation: Callable[[bool], Awaitable[None]],
    ) -> SdkWriteReceipt:
        if not self.writes_open():
            # An embedding switch paused writes: the drain puts the job back.
            raise RuntimeError("embedding_switch_in_progress")
        user_id = records[0].user_id if records else "local"
        if user_id in self._evolving:
            # Before any SDK call: the drain returns the job to pending.
            raise RuntimeError("memory_evolve_in_progress")
        self._writing[user_id] = self._writing.get(user_id, 0) + 1
        try:
            async with self._active_handle() as handle:
                lite = getattr(handle, "mode", None) == "lite"
                return await SdkWriteProcessor(handle, lite=lite).process_sdk_job(
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
        """After a mode, feature or language change: retire handles built otherwise.

        The idle ones close now. A handle still writing is closed when its
        last use ends (#218), so a write that started in the old mode, or
        with the old features, finishes in it.
        """
        build = (self.memory_mode(), self.memory_features(), self.sdk_language())
        async with self._lock:
            stale = {key: handle for key, handle in self._handles.items() if key[3:] != build}
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
        flight are waited for before the body runs. Evolve and the graph
        backfill write too, so an embedding switch refuses them as well.
        """
        if not self.writes_open():
            raise RuntimeError("embedding_switch_in_progress")
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

    def in_use(self) -> int:
        """Writes and reads holding a client right now."""
        return sum(self._uses.values())

    async def close_idle(self) -> None:
        """Retire every handle; the unused close now, the rest when released."""
        async with self._lock:
            self._retired.update(self._handles.values())
            self._handles = {}
            unused = {value for value in self._retired if not self._uses.get(value)}
            self._retired -= unused
        await asyncio.gather(*(value.close() for value in unused))

    @asynccontextmanager
    async def use_client(self) -> AsyncIterator[Any]:
        """The public SDK client of the active write profile, kept open while used."""
        async with self._active_handle() as handle:
            yield await handle.client()

    async def active_profile_credentials(
        self,
    ) -> tuple[ModelProfileSnapshot, str | None]:
        """Resolve one stable profile snapshot for graph administration."""
        _identity, profile, secret, _space = await asyncio.to_thread(self._read_active_credentials)
        return profile, secret

    async def live_space(self) -> EmbeddingSpace | None:
        if self.spaces is None:
            return None
        return await asyncio.to_thread(self._live_space)

    @asynccontextmanager
    async def _active_handle(self) -> AsyncIterator[SdkClientHandle]:
        """Use the active profile's handle; retire the others, closing the unused."""
        mode = self.memory_mode()
        features = self.memory_features()
        language = self.sdk_language()
        profile_identity, profile, secret, space = await asyncio.to_thread(
            self._read_active_credentials, mode
        )
        key = (
            *profile_identity,
            space.space_id if space is not None else "",
            mode,
            features,
            language,
        )
        async with self._lock:
            handle = self._handles.get(key)
            if handle is None:
                handle = SdkClientHandle(
                    profile,
                    api_key=secret,
                    data_dir=self.data_dir,
                    http_transport_factory=self.transport_factory(profile),
                    mode=mode,
                    features=features,
                    space=space,
                    **language_kwargs(language),
                )
                self._handles[key] = handle
            self._retired.update(value for name, value in self._handles.items() if name != key)
            self._handles = {key: handle}
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

    def _live_space(self) -> EmbeddingSpace:
        assert self.spaces is not None
        space = self.spaces.live()
        if space is None:
            active = self.profiles.active_identity()
            space = self.spaces.bootstrap(
                self.profiles.snapshot(*active) if active is not None else None
            )
        return space

    def _lite_credentials(
        self,
    ) -> tuple[tuple[str, int], ModelProfileSnapshot, str | None, EmbeddingSpace | None]:
        space = self._live_space() if self.spaces is not None else None
        if space is None or space.profile_id is None or space.profile_revision is None:
            builtin = builtin_retrieval_snapshot()
            return (builtin.profile_id, builtin.revision), builtin, None, space
        identity = (space.profile_id, space.profile_revision)
        profile = self.profiles.snapshot(*identity)
        view = retrieval_view(profile)
        secret: str | None = None
        if view.embedding_provider_kind is ProviderKind.OPENAI_COMPATIBLE:
            try:
                secret = self.profiles.resolve_snapshot_secret(*identity, self.secrets)
            except SecretStoreUnavailableError:
                raise RuntimeError("secure_storage_unavailable") from None
            if secret is None:
                raise RuntimeError("model_secret_unavailable")
        return identity, view, secret, space

    def _read_active_credentials(
        self,
        mode: str = "pro",
    ) -> tuple[tuple[str, int], ModelProfileSnapshot, str | None, EmbeddingSpace | None]:
        if mode == "lite":
            # A lite write is an embedding-only view of the live space. Never
            # resolve or inject the generation model endpoint or its secret.
            return self._lite_credentials()
        identity = self.profiles.active_identity()
        if identity is None:
            raise RuntimeError("model_profile_not_active")
        profile = self.profiles.snapshot(*identity)
        space = self._live_space() if self.spaces is not None else None
        if space is not None and (
            space.identity is None or not embedding_identity(profile).matches(space.identity)
        ):
            # Never a new, empty collection: the switch moves the space.
            raise RuntimeError("embedding_space_mismatch")
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
        return identity, profile, secret, space


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


#: The SDK's stable failure codes are upper snake case (`RECONCILE_FAILED`).
_SDK_FAILURE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,47}")


def sdk_write_failure_code(value: Mapping[object, object]) -> str:
    """The job's `error_code` for a failed SDK write: the SDK's own cause if it gave one.

    hy-memory 2.0.13 names why a write failed: `agent_error_code`
    (`LLM_ERROR`, `EMPTY_RESPONSE`, `JSON_PARSE_FAILED`, `EMBEDDING_FAILED`,
    `RECONCILE_FAILED`) and, for a failed reconcile, `reconcile_error_code`
    (`LLM_UNAVAILABLE`, `EMPTY_RESPONSE`, `JSON_PARSE_FAILED`,
    `RECONCILE_EXCEPTION`). They become `hy_sdk_reconcile_empty_response`,
    `hy_sdk_llm_error`, ... Without a recognisable code it stays
    `hy_sdk_write_failed`. Only the code is kept, never the SDK's message.
    """
    reconcile = value.get("reconcile_error_code")
    if isinstance(reconcile, str) and _SDK_FAILURE_CODE.fullmatch(reconcile):
        cause = reconcile.removeprefix("RECONCILE_") or "FAILED"
        return f"hy_sdk_reconcile_{cause.lower()}"
    agent = value.get("agent_error_code")
    if isinstance(agent, str) and _SDK_FAILURE_CODE.fullmatch(agent):
        return f"hy_sdk_{agent.lower()}"
    return "hy_sdk_write_failed"


def _receipt_from_result(value: object) -> SdkWriteReceipt:
    if not isinstance(value, Mapping):
        raise RuntimeError("hy_sdk_write_receipt_shape_invalid")
    if value.get("success") is not True:
        raise RuntimeError(sdk_write_failure_code(value))

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
