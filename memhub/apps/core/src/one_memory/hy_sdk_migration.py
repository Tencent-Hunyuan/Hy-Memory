"""Migration-only access to the HY SDK's public inventory and import APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol

from hy_memory import (  # type: ignore[import-untyped]
    DataPlaneRestore,
    DataPlaneSnapshot,
    restore_local_data_plane,
    snapshot_local_data_plane,
)

from one_memory.hy_sdk_types import (
    HySdkMemoryPage,
    HySdkMemoryRecord,
    HySdkMigrationError,
    HySdkRawPage,
    HySdkRawRecord,
)


class MigrationClient(Protocol):
    def list_memories(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> Mapping[str, object]: ...

    def import_memory_exact(self, content: str, **kwargs: object) -> Mapping[str, object]: ...

    def list_raw(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> Mapping[str, object]: ...

    def import_raw(self, content: str, **kwargs: object) -> Mapping[str, object]: ...

    def delete(self, memory_id: str) -> Mapping[str, object]: ...

    def get_write_outcome(self, request_id: str) -> Mapping[str, object] | None: ...


class HySdkMigration:
    """Typed async adapter over the SDK's exact migration primitives."""

    def __init__(self, client: MigrationClient) -> None:
        self.client = client

    async def list_memories(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        statuses: tuple[str, ...] = ("active",),
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> HySdkMemoryPage:
        raw = await _sdk_call(
            "hy_sdk_memory_list_failed",
            self.client.list_memories,
            user_id=user_id,
            agent_id=agent_id,
            statuses=list(statuses),
            limit=limit,
            offset=offset,
            order=order,
        )
        return _memory_page(raw, limit=limit, offset=offset)

    async def import_memory_exact(
        self,
        *,
        content: str,
        memory_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source: str,
        layer: str,
        status: str,
        owner: str | None,
        tags: tuple[str, ...],
        supersedes: tuple[str, ...],
        superseded_by: tuple[str, ...],
        is_latest: bool,
        memory_at: datetime | None,
        metadata: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        raw = await _sdk_call(
            "hy_sdk_memory_import_failed",
            self.client.import_memory_exact,
            content,
            memory_id=memory_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source=source,
            layer=layer,
            status=status,
            owner=owner,
            tags=list(tags),
            supersedes=list(supersedes),
            superseded_by=list(superseded_by),
            is_latest=is_latest,
            memory_at=memory_at,
            metadata=dict(metadata),
            request_id=request_id,
        )
        return _successful_result(raw, "hy_sdk_memory_import_failed")

    async def list_raw(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> HySdkRawPage:
        raw = await _sdk_call(
            "hy_sdk_raw_list_failed",
            self.client.list_raw,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source=source,
            limit=limit,
            offset=offset,
            order=order,
        )
        return _raw_page(raw, limit=limit, offset=offset)

    async def import_raw(
        self,
        *,
        content: str,
        raw_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source: str,
        memory_at: datetime | None,
        metadata: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        raw = await _sdk_call(
            "hy_sdk_raw_import_failed",
            self.client.import_raw,
            content,
            raw_id=raw_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source=source,
            memory_at=memory_at,
            metadata=dict(metadata),
            request_id=request_id,
        )
        return _successful_result(raw, "hy_sdk_raw_import_failed")

    async def delete(self, memory_id: str) -> dict[str, object]:
        raw = await _sdk_call(
            "hy_sdk_memory_delete_failed",
            self.client.delete,
            memory_id,
        )
        return _successful_result(raw, "hy_sdk_memory_delete_failed")

    async def get_write_outcome(self, request_id: str) -> dict[str, object] | None:
        raw = await _sdk_call(
            "hy_sdk_write_outcome_failed",
            self.client.get_write_outcome,
            request_id,
        )
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise HySdkMigrationError("hy_sdk_write_outcome_shape_invalid")
        return dict(raw)


def snapshot_data_plane(
    *,
    source_root: Path,
    target_root: Path,
    writers_stopped: bool,
) -> DataPlaneSnapshot:
    """Snapshot an explicitly quiesced SDK-owned local data plane."""
    return snapshot_local_data_plane(
        source_root=source_root,
        target_root=target_root,
        writers_stopped=writers_stopped,
    )


def restore_data_plane(
    *,
    snapshot_root: Path,
    target_root: Path,
    writers_stopped: bool,
    protective_root: Path | None = None,
) -> DataPlaneRestore:
    """Restore an explicitly quiesced SDK-owned local data plane."""
    return restore_local_data_plane(
        snapshot_root=snapshot_root,
        target_root=target_root,
        writers_stopped=writers_stopped,
        protective_root=protective_root,
    )


async def _sdk_call[T](
    code: str,
    call: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> T:
    pending = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await _finish_cancelled_call(pending)
        raise
    except Exception as error:
        raise HySdkMigrationError(code) from error


async def _finish_cancelled_call[T](pending: asyncio.Task[T]) -> None:
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            continue
        except Exception:
            return
    if not pending.cancelled():
        pending.exception()


def _memory_page(raw: object, *, limit: int, offset: int) -> HySdkMemoryPage:
    if not isinstance(raw, Mapping):
        raise HySdkMigrationError("hy_sdk_memory_page_shape_invalid")
    rows: list[object] = []
    total = 0
    for bucket_name, row_name in (("vdb", "memories"), ("graph", "nodes")):
        bucket = raw.get(bucket_name)
        if bucket is None and bucket_name == "graph":
            continue
        if not isinstance(bucket, Mapping):
            raise HySdkMigrationError("hy_sdk_memory_page_shape_invalid")
        bucket_rows = bucket.get(row_name)
        bucket_total = bucket.get("total")
        if not isinstance(bucket_rows, list) or type(bucket_total) is not int or bucket_total < 0:
            raise HySdkMigrationError("hy_sdk_memory_page_shape_invalid")
        rows.extend(bucket_rows)
        total += bucket_total

    records: dict[str, HySdkMemoryRecord] = {}
    for row in rows:
        record = _memory_record(row)
        records.setdefault(record.memory_id, record)
    return HySdkMemoryPage(
        memories=tuple(records.values()),
        total=total,
        limit=limit,
        offset=offset,
    )


def _memory_record(raw: object) -> HySdkMemoryRecord:
    if not isinstance(raw, Mapping):
        raise HySdkMigrationError("hy_sdk_memory_record_shape_invalid")
    required = ("memory_id", "content", "layer", "status", "user_id", "agent_id")
    if any(not isinstance(raw.get(name), str) or not raw[name] for name in required):
        raise HySdkMigrationError("hy_sdk_memory_record_shape_invalid")
    custom = raw.get("custom")
    source_raw_ids: list[object] = []
    if isinstance(custom, Mapping) and isinstance(custom.get("source_raw_memory_ids"), list):
        source_raw_ids.extend(custom["source_raw_memory_ids"])
    if isinstance(raw.get("source_raw_memory_id"), str):
        source_raw_ids.append(raw["source_raw_memory_id"])
    try:
        return HySdkMemoryRecord(
            memory_id=raw["memory_id"],
            content=raw["content"],
            layer=raw["layer"],
            status=raw["status"],
            is_latest=bool(raw.get("is_latest", True)),
            supersedes=_string_tuple(raw.get("supersedes")),
            superseded_by=_string_tuple(raw.get("superseded_by")),
            tags=_string_tuple(raw.get("tags")),
            owner=raw.get("owner") if raw.get("owner") in {"user", "agent"} else None,
            source_raw_ids=_string_tuple(source_raw_ids),
            user_id=raw["user_id"],
            agent_id=raw["agent_id"],
            session_id=_optional_string(raw.get("session_id")),
            source=_optional_string(raw.get("source")),
            memory_at=_optional_int(raw.get("memory_at")),
            gmt_created=_optional_int(raw.get("gmt_created")),
        )
    except (TypeError, ValueError) as error:
        raise HySdkMigrationError("hy_sdk_memory_record_shape_invalid") from error


def _raw_page(raw: object, *, limit: int, offset: int) -> HySdkRawPage:
    if not isinstance(raw, Mapping):
        raise HySdkMigrationError("hy_sdk_raw_page_shape_invalid")
    rows = raw.get("raw")
    total = raw.get("total")
    if not isinstance(rows, list) or type(total) is not int or total < 0:
        raise HySdkMigrationError("hy_sdk_raw_page_shape_invalid")
    return HySdkRawPage(
        raw=tuple(_raw_record(row) for row in rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def _raw_record(raw: object) -> HySdkRawRecord:
    if not isinstance(raw, Mapping):
        raise HySdkMigrationError("hy_sdk_raw_record_shape_invalid")
    required = ("raw_id", "content", "user_id", "agent_id", "session_id", "source")
    if any(not isinstance(raw.get(name), str) or not raw[name] for name in required):
        raise HySdkMigrationError("hy_sdk_raw_record_shape_invalid")
    custom = raw.get("custom")
    try:
        return HySdkRawRecord(
            raw_id=raw["raw_id"],
            content=raw["content"],
            user_id=raw["user_id"],
            agent_id=raw["agent_id"],
            session_id=raw["session_id"],
            source=raw["source"],
            memory_at=_optional_int(raw.get("memory_at")),
            gmt_created=_optional_int(raw.get("gmt_created")),
            custom=dict(custom) if isinstance(custom, Mapping) else {},
        )
    except (TypeError, ValueError) as error:
        raise HySdkMigrationError("hy_sdk_raw_record_shape_invalid") from error


def _successful_result(raw: object, code: str) -> dict[str, object]:
    if not isinstance(raw, Mapping) or raw.get("success") is not True:
        raise HySdkMigrationError(code)
    return dict(raw)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if type(value) is int else None
