"""Privileged offline access to the HY SDK data plane during App migration.

Runtime product code must use public ``HyMemoryClient`` methods.  This module
is the deliberately isolated exception: App startup has stopped all writers,
so the one-time legacy migration may access SDK stores directly without
turning migration mechanics into public Client APIs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from hy_memory import (  # type: ignore[import-untyped]
    DataPlaneRestore,
    DataPlaneSnapshot,
    restore_local_data_plane,
    snapshot_local_data_plane,
)
from hy_memory.models.memory import (  # type: ignore[import-untyped]
    MemoryLayer,
    MemoryNode,
    MemoryStatus,
    SourceType,
)
from hy_memory.utils.source_filter import normalize_source  # type: ignore[import-untyped]

from one_memory.hy_sdk_types import (
    HySdkMemoryPage,
    HySdkMemoryRecord,
    HySdkMigrationError,
    HySdkRawPage,
    HySdkRawRecord,
)


class MigrationClient(Protocol):
    _loop_thread: Any
    _vector_store: Any
    _embed_service: Any
    _graph_store: Any

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

    def delete(self, memory_id: str) -> Mapping[str, object]: ...

    def get_write_outcome(self, request_id: str) -> Mapping[str, object] | None: ...


class HySdkMigration:
    """Typed offline adapter over one initialized SDK data plane."""

    def __init__(self, client: MigrationClient) -> None:
        self.client = client
        self._loop_thread = client._loop_thread
        self._vector_store = client._vector_store
        self._embed_service = client._embed_service
        self._graph_store = client._graph_store

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

    async def write_legacy_memory(
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
            self._loop_thread.run,
            self._write_legacy_memory(
                content=content,
                memory_id=memory_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                source=source,
                layer=layer,
                status=status,
                owner=owner,
                tags=tags,
                supersedes=supersedes,
                superseded_by=superseded_by,
                is_latest=is_latest,
                memory_at=memory_at,
                metadata=metadata,
                request_id=request_id,
            ),
        )
        return _successful_result(raw, "hy_sdk_memory_import_failed")

    async def _write_legacy_memory(
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
        if not content or not memory_id or not user_id:
            raise ValueError("content, memory_id and user_id are required")
        normalized_layer = MemoryLayer.from_string(layer)
        if normalized_layer is MemoryLayer.L1_RAW:
            raise ValueError("raw content must use the raw migration lane")
        normalized_status = MemoryStatus(status.lower())
        normalized_source = normalize_source(source)
        existing = await self._vector_store.get_by_id(memory_id)
        if existing is not None:
            identity = (
                existing.content == content,
                existing.user_id == user_id,
                existing.agent_id == agent_id,
                existing.session_id == session_id,
                existing.source == normalized_source,
                existing.layer == normalized_layer,
                existing.status == normalized_status,
                existing.owner == owner,
                list(existing.tags or []) == list(tags),
                list(existing.supersedes or []) == list(supersedes),
                list(existing.superseded_by or []) == list(superseded_by),
                existing.is_latest == is_latest,
            )
            if not all(identity):
                raise ValueError("memory_id is already bound to different memory content")
            await self._project_graph(existing)
            return {
                "success": True,
                "memory_id": memory_id,
                "memory_ids": [memory_id],
                "request_id": request_id,
                "replayed": True,
            }
        custom = dict(metadata)
        custom["migration_request_id"] = request_id
        embedding = await self._embed_service.embed(content)
        node = MemoryNode(
            node_id=memory_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source=normalized_source,
            layer=normalized_layer,
            content=content,
            source_type=SourceType.EXPLICIT,
            status=normalized_status,
            owner=owner,
            tags=list(tags),
            supersedes=list(supersedes),
            superseded_by=list(superseded_by),
            is_latest=is_latest,
            embedding=embedding,
            memory_at=memory_at,
            custom=custom,
        )
        stored_id = await self._vector_store.upsert(node)
        if stored_id != memory_id:
            raise RuntimeError("exact migration changed the requested memory identity")
        await self._project_graph(node)
        return {
            "success": True,
            "memory_id": memory_id,
            "memory_ids": [memory_id],
            "request_id": request_id,
        }

    async def _project_graph(self, node: MemoryNode) -> None:
        if self._graph_store is not None:
            await self._graph_store.upsert_memory_node(node)

    async def read_raw_inventory(
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
            self._loop_thread.run,
            self._read_raw_inventory(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                source=source,
                limit=limit,
                offset=offset,
                order=order,
            ),
        )
        return _raw_page(raw, limit=limit, offset=offset)

    async def _read_raw_inventory(
        self,
        *,
        user_id: str,
        agent_id: str | None,
        session_id: str | None,
        source: str | None,
        limit: int,
        offset: int,
        order: str,
    ) -> dict[str, object]:
        if not user_id:
            raise ValueError("user_id is required")
        nodes = await self._vector_store.list_by_user(
            user_id=user_id,
            agent_id=agent_id,
            status_filter=[MemoryStatus.ACTIVE],
            layers=[MemoryLayer.L1_RAW],
            include_sources=[source] if source else None,
            limit=100_000,
            only_latest=False,
            with_vectors=False,
            allow_raw_layer=True,
        )
        if session_id:
            nodes = [node for node in nodes if node.session_id == session_id]
        nodes.sort(
            key=_node_timestamp,
            reverse=order.lower() != "asc",
        )
        return {
            "raw": [_raw_node(node) for node in nodes[offset : offset + limit]],
            "total": len(nodes),
            "limit": limit,
            "offset": offset,
        }

    async def write_legacy_raw(
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
            self._loop_thread.run,
            self._write_legacy_raw(
                content=content,
                raw_id=raw_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                source=source,
                memory_at=memory_at,
                metadata=metadata,
                request_id=request_id,
            ),
        )
        return _successful_result(raw, "hy_sdk_raw_import_failed")

    async def _write_legacy_raw(
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
        if not content or not raw_id or not user_id:
            raise ValueError("content, raw_id and user_id are required")
        normalized_source = normalize_source(source)
        existing = await self._vector_store.get_by_id(raw_id)
        if existing is not None:
            identity = (
                existing.layer is MemoryLayer.L1_RAW,
                existing.content == content,
                existing.user_id == user_id,
                existing.agent_id == agent_id,
                existing.session_id == session_id,
                existing.source == normalized_source,
            )
            if not all(identity):
                raise ValueError("raw_id is already bound to different source content")
            return {
                "success": True,
                "raw_id": raw_id,
                "request_id": request_id,
                "replayed": True,
            }
        custom = dict(metadata)
        custom["migration_request_id"] = request_id
        embedding = await self._embed_service.embed(content)
        node = MemoryNode(
            node_id=raw_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source=normalized_source,
            layer=MemoryLayer.L1_RAW,
            content=content,
            source_type=SourceType.EXPLICIT,
            status=MemoryStatus.ACTIVE,
            is_latest=True,
            embedding=embedding,
            memory_at=memory_at,
            custom=custom,
        )
        stored_id = await self._vector_store.upsert(node)
        if stored_id != raw_id:
            raise RuntimeError("exact migration changed the requested raw identity")
        return {"success": True, "raw_id": raw_id, "request_id": request_id}

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


def _node_timestamp(node: Any) -> float:
    value = getattr(node, "memory_at", None) or getattr(node, "gmt_created", None)
    return value.timestamp() if isinstance(value, datetime) else 0.0


def _raw_node(node: Any) -> dict[str, object]:
    def timestamp(value: object) -> int | None:
        return int(value.timestamp()) if isinstance(value, datetime) else None

    return {
        "raw_id": node.node_id,
        "content": node.content,
        "user_id": node.user_id,
        "agent_id": node.agent_id,
        "session_id": node.session_id,
        "source": node.source,
        "memory_at": timestamp(node.memory_at),
        "gmt_created": timestamp(node.gmt_created),
        "custom": node.custom or {},
    }


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
