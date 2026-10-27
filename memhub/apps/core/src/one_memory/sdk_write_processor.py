"""Single-write adapter from durable Evidence payloads to the public HY SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from one_memory.hy_sdk_runtime import SdkClientHandle
from one_memory.imports.models import EvidenceContentKind, EvidenceOrigin, EvidenceRecord
from one_memory.input_sanitization import sanitize_model_input
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

    def __init__(self, client_handle: SdkClientHandle) -> None:
        self._client_handle = client_handle

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
        client = await self._client_handle.client()

        if extracted:
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
                    "role": record.role,
                    "content": sanitize_model_input(record.verbatim),
                }
                for record in records
                if record.content_kind is EvidenceContentKind.RAW_CONVERSATION
                and record.role in _MESSAGE_ROLES
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
    """Resolve the active model profile, then execute one public SDK write."""

    def __init__(
        self,
        *,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        data_dir: Any,
        transport_factory: Callable[[ModelProfileSnapshot], Callable[[], Any] | None],
        sedimentation_enabled: Callable[[], bool],
    ) -> None:
        self.profiles = profiles
        self.secrets = secrets
        self.data_dir = data_dir
        self.transport_factory = transport_factory
        self.sedimentation_enabled = sedimentation_enabled
        self._handles: dict[tuple[str, int], SdkClientHandle] = {}
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
        handle = await self._active_handle()
        return await SdkWriteProcessor(handle).process_sdk_job(
            records,
            request_id=request_id,
            before_hy_invocation=before_hy_invocation,
        )

    async def inquire_write_outcome(
        self, request_id: str
    ) -> SdkWriteReceipt | None:
        return await SdkWriteProcessor(
            await self._active_handle()
        ).inquire_write_outcome(request_id)

    async def close(self) -> None:
        async with self._lock:
            handles, self._handles = tuple(self._handles.values()), {}
        await asyncio.gather(*(handle.close() for handle in handles))

    async def client(self) -> Any:
        """Return the public SDK client selected by the active write profile."""
        return await (await self._active_handle()).client()

    async def active_profile_credentials(
        self,
    ) -> tuple[ModelProfileSnapshot, str | None]:
        """Resolve one stable profile snapshot for graph administration."""
        _identity, profile, secret = await asyncio.to_thread(self._read_active_credentials)
        return profile, secret

    async def _active_handle(self) -> SdkClientHandle:
        identity, profile, secret = await asyncio.to_thread(
            self._read_active_credentials
        )
        async with self._lock:
            handle = self._handles.get(identity)
            if handle is None:
                handle = SdkClientHandle(
                    profile,
                    api_key=secret,
                    data_dir=self.data_dir,
                    http_transport_factory=self.transport_factory(profile),
                )
                self._handles[identity] = handle
            stale = [
                value
                for key, value in self._handles.items()
                if key != identity
            ]
            self._handles = {identity: handle}
        await asyncio.gather(*(value.close() for value in stale))
        return handle

    def _read_active_credentials(
        self,
    ) -> tuple[tuple[str, int], ModelProfileSnapshot, str | None]:
        identity = self.profiles.active_identity()
        if identity is None:
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
