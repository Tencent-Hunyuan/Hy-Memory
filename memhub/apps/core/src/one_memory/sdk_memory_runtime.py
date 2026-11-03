"""Narrow SDK-native runtime for graph, retrieval, and queue status operations."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from one_memory.database_access import main_database
from one_memory.extraction_settings import ExtractionSettings, ExtractionSettingsStore
from one_memory.hy_graph_admin import (
    GraphWidthDecision,
    GraphWidthMismatch,
    GraphWidthOutcome,
    decide_profile_graph_width,
    inspect_profile_graph_width,
)
from one_memory.hy_graph_projection import HyGraphSnapshot, read_graph_snapshot
from one_memory.hy_runtime_config import HY_MEMORY_READER
from one_memory.hy_sdk_runtime import SdkClientHandle, sdk_relevance_floor
from one_memory.hy_sdk_types import HySdkError, parse_sdk_search
from one_memory.memory_queue import ExtractionWindowPreset, QueueDepths
from one_memory.memory_server.durable_queue import WAITING_FOR_MODEL, DurableWriteQueue
from one_memory.sdk_write_processor import SdkWriteCoordinator
from one_memory.semantic_deadline import SemanticMemoryHit

# Only a code-shaped message is a code; anything else may carry provider text.
_STABLE_CODE = re.compile(r"[a-z][a-z0-9_]{0,95}")

logger = logging.getLogger(__name__)

#: Jobs not yet run that a suspended source holds back.
_WAITING_STATES = frozenset({"pending", "waiting_for_model", "waiting_for_permission"})
_WAITING_SQL = ",".join(f"'{state}'" for state in sorted(_WAITING_STATES))


class SdkMemoryRuntime:
    """Product services that remain after legacy task/governance writes retire."""

    def __init__(
        self,
        *,
        coordinator: SdkWriteCoordinator,
        read_handle: SdkClientHandle,
        write_queue: DurableWriteQueue,
        extraction_settings: ExtractionSettingsStore,
        data_dir: Path,
        memory_mode: Callable[[], str] = lambda: "pro",
        writes_open: Callable[[], bool] = lambda: True,
        source_active: Callable[[str], bool] = lambda _source_key: True,
    ) -> None:
        self.coordinator = coordinator
        #: The SDK mode, for the graph store's path (`hy_graph_admin`).
        self.memory_mode = memory_mode
        #: False while an embedding switch pauses model writes.
        self.writes_open = writes_open
        #: Whether a queue lane's source may still become memories: the same
        #: question the drain's claim asks of it. A lane that is off (the
        #: user turned the source off, or its grant was revoked) has its
        #: waiting jobs suspended: kept, never claimed, and not counted as
        #: pending until the source is on again.
        self.source_active = source_active
        self.read_handle = read_handle
        self.write_queue = write_queue
        self.extraction_settings = extraction_settings
        self.data_dir = data_dir
        # MemHub keeps every raw the SDK stores, in every mode: there is no
        # retention window (owner decision; the SDK itself never deletes raw).

    async def semantic_recall(
        self,
        query: str,
        *,
        limit: int = 12,
    ) -> tuple[SemanticMemoryHit, ...]:
        client = await self.read_handle.client()
        raw = await asyncio.to_thread(
            client.search,
            query,
            user_ids=["local"],
            agent_ids=[],
            limit=limit,
            min_score=sdk_relevance_floor(),
            schema_min_score=sdk_relevance_floor(),
            schema_limit=limit,
            intention_limit=limit,
            scene="normal",
            reader=HY_MEMORY_READER,
        )
        return parse_sdk_search(raw, error_code="hy_memory_search_shape_unknown")

    async def semantic_search(
        self,
        query: str,
        *,
        limit: int = 12,
    ) -> tuple[tuple[str, float], ...]:
        return tuple(
            (hit.memory_id, hit.score) for hit in await self.semantic_recall(query, limit=limit)
        )

    async def graph_snapshot(self, *, limit: int = 300, user_id: str = "local") -> HyGraphSnapshot:
        try:
            await self.coordinator.active_profile_credentials()
        except RuntimeError as error:
            if str(error) == "model_profile_not_active":
                return HyGraphSnapshot(consulted=False)
            raise
        async with AsyncExitStack() as stack:
            try:
                client = await stack.enter_async_context(self.coordinator.use_client())
            except Exception as error:
                raise HySdkError("hy_memory_graph_read_failed") from error
            return await asyncio.to_thread(
                read_graph_snapshot,
                client,
                user_id,
                "*",
                limit,
            )

    async def digest(self) -> dict[str, object]:
        if not self.writes_open():
            raise HySdkError("embedding_switch_in_progress")
        async with self.coordinator.use_client() as client:
            try:
                raw = await asyncio.to_thread(
                    client.list_memories,
                    user_id="local",
                    limit=1_000,
                    offset=0,
                )
                agent_ids = _agent_ids(raw)
                digests = [
                    {
                        "agent_id": agent_id,
                        "schemas_recorded": 0,
                        **dict(
                            await asyncio.to_thread(
                                client.evolve,
                                user_id="local",
                                agent_id=agent_id,
                            )
                        ),
                    }
                    for agent_id in agent_ids
                ]
            except Exception as error:
                raise HySdkError("hy_memory_digest_failed") from error
        return {
            "success": all(result.get("success") is not False for result in digests),
            "scope_count": len(digests),
            "skipped_conversation_scopes": 0,
            "schemas_recorded": 0,
            "digests": digests,
        }

    async def graph_width_mismatch(self) -> GraphWidthMismatch | None:
        try:
            profile, secret = await self.coordinator.active_profile_credentials()
        except RuntimeError as error:
            if str(error) == "model_profile_not_active":
                return None
            raise
        space = await self.coordinator.live_space()
        return await asyncio.to_thread(
            inspect_profile_graph_width,
            profile,
            api_key=secret,
            data_dir=self.data_dir,
            mode=self.memory_mode(),
            graph_dir=space.graph_dir if space is not None and space.graph_dir else "graph",
        )

    async def decide_graph_width(
        self,
        decision: GraphWidthDecision,
    ) -> GraphWidthOutcome:
        try:
            profile, secret = await self.coordinator.active_profile_credentials()
        except RuntimeError as error:
            raise HySdkError(str(error)) from error
        space = await self.coordinator.live_space()
        return await asyncio.to_thread(
            decide_profile_graph_width,
            profile,
            api_key=secret,
            data_dir=self.data_dir,
            decision=decision,
            mode=self.memory_mode(),
            graph_dir=space.graph_dir if space is not None and space.graph_dir else "graph",
        )

    @asynccontextmanager
    async def _mutation_client(self) -> AsyncIterator[Any]:
        """The write client, with no-model and keychain refusals as SDK codes.

        Callers record these against the edit or deletion instead of failing
        the request that already committed it.
        """
        if not self.writes_open():
            raise HySdkError("embedding_switch_in_progress")
        async with AsyncExitStack() as stack:
            try:
                client = await stack.enter_async_context(self.coordinator.use_client())
            except RuntimeError as error:
                code = str(error)
                if not _STABLE_CODE.fullmatch(code):
                    code = "hy_memory_client_unavailable"
                raise HySdkError(code) from error
            yield client

    async def update_hy_memory(self, memory_id: str, content: str) -> dict[str, object]:
        async with self._mutation_client() as client:
            try:
                result = await asyncio.to_thread(client.update, memory_id, content)
            except Exception as error:
                raise HySdkError("hy_memory_update_failed") from error
        if not isinstance(result, dict) or result.get("success") is not True:
            raise HySdkError("hy_memory_update_rejected")
        return result

    async def finish_deletion(self, memory_id: str) -> None:
        async with self._mutation_client() as client:
            try:
                result = await asyncio.to_thread(client.delete, memory_id)
            except Exception as error:
                raise HySdkError("hy_memory_delete_failed") from error
        if not isinstance(result, dict):
            raise HySdkError("hy_memory_delete_rejected")
        error_code = result.get("error_code")
        if error_code or result.get("partial") is True:
            raise HySdkError(str(error_code or "hy_memory_delete_incomplete"))
        if result.get("success") is not True and result.get("deleted_count") != 0:
            raise HySdkError("hy_memory_delete_rejected")

    def extraction_window_settings(self) -> ExtractionSettings:
        return self.extraction_settings.get()

    def set_extraction_window(
        self,
        preset: ExtractionWindowPreset,
    ) -> ExtractionSettings:
        return self.extraction_settings.set_window_preset(preset)

    def queue_depths(self) -> QueueDepths:
        now = datetime.now(UTC)
        fresh_since = (now - timedelta(days=7)).isoformat()
        with main_database(self.write_queue.database_path).read() as database:
            rows = database.execute(
                "SELECT source_key,event_at>=? AS fresh,COUNT(*) FROM memory_write_jobs "
                f"WHERE state IN ({_WAITING_SQL}) GROUP BY source_key,fresh",
                (fresh_since,),
            ).fetchall()
            recent = tuple(
                datetime.fromisoformat(str(row[0]))
                for row in database.execute(
                    "SELECT updated_at FROM memory_write_jobs WHERE state='completed' "
                    "ORDER BY updated_at DESC LIMIT 8"
                )
            )
        active = self._active_lanes({str(row[0]) for row in rows})
        fresh = sum(int(row[2]) for row in rows if str(row[0]) in active and row[1])
        backlog = sum(int(row[2]) for row in rows if str(row[0]) in active and not row[1])
        return QueueDepths(fresh=fresh, backlog=backlog, recent_completions=recent)

    def active_depths(self) -> tuple[int, int]:
        with main_database(self.write_queue.database_path).read() as database:
            running = int(
                database.execute(
                    "SELECT COUNT(*) FROM memory_write_jobs WHERE state='running'"
                ).fetchone()[0]
            )
        return running, running

    def status_counts(self) -> dict[str, int]:
        """Jobs per state, with a suspended source's waiting jobs as `suspended`.

        Those jobs keep their state in the table: suspension is the source's
        switch, not the row's, so turning the source back on is all it takes
        for the drain to claim them again, and nothing is moved or deleted.
        """
        with main_database(self.write_queue.database_path).read() as database:
            rows = database.execute(
                "SELECT source_key,state,COUNT(*) FROM memory_write_jobs GROUP BY source_key,state"
            ).fetchall()
        active = self._active_lanes({str(row[0]) for row in rows if str(row[1]) in _WAITING_STATES})
        counts: dict[str, int] = {}
        for source_key, state, count in rows:
            name = str(state)
            if name in _WAITING_STATES and str(source_key) not in active:
                name = "suspended"
            counts[name] = counts.get(name, 0) + int(count)
        return counts

    def _active_lanes(self, lanes: set[str]) -> set[str]:
        return {lane for lane in lanes if self.source_active(lane)}

    def manual_batch_progress(self) -> tuple[int, int]:
        return 0, 0

    def promote_next_manual_batch(self, *, task_ids: tuple[str, ...] | None = None) -> list[Any]:
        del task_ids
        return []

    async def resume_allowed(self) -> None:
        await asyncio.to_thread(
            self.write_queue.readmit_parked,
            states=(WAITING_FOR_MODEL,),
        )

    def accepted_backfill_status(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def _agent_ids(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        raise HySdkError("hy_memory_list_shape_unknown")
    found: set[str] = set()
    for bucket_name, rows_name in (("vdb", "memories"), ("graph", "nodes")):
        bucket = raw.get(bucket_name)
        if bucket is None:
            continue
        if not isinstance(bucket, dict) or not isinstance(bucket.get(rows_name), list):
            raise HySdkError("hy_memory_list_shape_unknown")
        for row in bucket[rows_name]:
            if isinstance(row, dict):
                agent_id = row.get("agent_id")
                if isinstance(agent_id, str) and agent_id:
                    found.add(agent_id)
    return tuple(sorted(found))
