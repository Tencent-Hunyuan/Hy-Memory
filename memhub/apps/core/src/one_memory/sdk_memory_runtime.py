"""Narrow SDK-native runtime for graph, retrieval, and queue status operations."""

from __future__ import annotations

import asyncio
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
from one_memory.memory_server.durable_queue import COMPLETED, DurableWriteQueue
from one_memory.sdk_write_processor import SdkWriteCoordinator
from one_memory.semantic_deadline import SemanticMemoryHit


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
    ) -> None:
        self.coordinator = coordinator
        self.read_handle = read_handle
        self.write_queue = write_queue
        self.extraction_settings = extraction_settings
        self.data_dir = data_dir

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
            (hit.memory_id, hit.score)
            for hit in await self.semantic_recall(query, limit=limit)
        )

    async def graph_snapshot(self, *, limit: int = 300) -> HyGraphSnapshot:
        try:
            await self.coordinator.active_profile_credentials()
        except RuntimeError as error:
            if str(error) == "model_profile_not_active":
                return HyGraphSnapshot(consulted=False)
            raise
        try:
            client = await self.coordinator.client()
        except Exception as error:
            raise HySdkError("hy_memory_graph_read_failed") from error
        return await asyncio.to_thread(
            read_graph_snapshot,
            client,
            "local",
            "*",
            limit,
        )

    async def digest(self) -> dict[str, object]:
        client = await self.coordinator.client()
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
        return await asyncio.to_thread(
            inspect_profile_graph_width,
            profile,
            api_key=secret,
            data_dir=self.data_dir,
        )

    async def decide_graph_width(
        self,
        decision: GraphWidthDecision,
    ) -> GraphWidthOutcome:
        try:
            profile, secret = await self.coordinator.active_profile_credentials()
        except RuntimeError as error:
            raise HySdkError(str(error)) from error
        return await asyncio.to_thread(
            decide_profile_graph_width,
            profile,
            api_key=secret,
            data_dir=self.data_dir,
            decision=decision,
        )

    async def update_hy_memory(self, memory_id: str, content: str) -> dict[str, object]:
        client = await self.coordinator.client()
        try:
            result = await asyncio.to_thread(client.update, memory_id, content)
        except Exception as error:
            raise HySdkError("hy_memory_update_failed") from error
        if not isinstance(result, dict) or result.get("success") is not True:
            raise HySdkError("hy_memory_update_rejected")
        return result

    async def finish_deletion(self, memory_id: str) -> None:
        client = await self.coordinator.client()
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
            fresh = int(
                database.execute(
                    "SELECT COUNT(*) FROM memory_write_jobs "
                    "WHERE state IN ('pending','waiting_for_model','waiting_for_permission') "
                    "AND event_at>=?",
                    (fresh_since,),
                ).fetchone()[0]
            )
            backlog = int(
                database.execute(
                    "SELECT COUNT(*) FROM memory_write_jobs "
                    "WHERE state IN ('pending','waiting_for_model','waiting_for_permission') "
                    "AND event_at<?",
                    (fresh_since,),
                ).fetchone()[0]
            )
            recent = tuple(
                datetime.fromisoformat(str(row[0]))
                for row in database.execute(
                    "SELECT updated_at FROM memory_write_jobs WHERE state='completed' "
                    "ORDER BY updated_at DESC LIMIT 8"
                )
            )
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
        with main_database(self.write_queue.database_path).read() as database:
            return {
                str(row[0]): int(row[1])
                for row in database.execute(
                    "SELECT state,COUNT(*) FROM memory_write_jobs GROUP BY state"
                )
            }

    def manual_batch_progress(self) -> tuple[int, int]:
        return 0, 0

    def promote_next_manual_batch(self, *, task_ids: tuple[str, ...] | None = None) -> list[Any]:
        del task_ids
        return []

    async def reconcile_unknown_outputs(
        self,
        *,
        limit: int = 128,
        exclude_error_codes: tuple[str, ...] = (),
    ) -> int:
        reconciled = 0
        for job in await asyncio.to_thread(
            self.write_queue.unknown_jobs,
            limit=limit,
            exclude_error_codes=exclude_error_codes,
        ):
            receipt = await self.coordinator.inquire_write_outcome(job.request_id)
            if receipt is None:
                continue
            await asyncio.to_thread(
                self.write_queue.settle,
                job.job_id,
                state=COMPLETED,
                sdk_raw_ids=receipt.raw_ids,
                sdk_memory_ids=receipt.memory_ids,
            )
            reconciled += 1
        return reconciled

    async def resume_allowed(self) -> None:
        await asyncio.to_thread(self.write_queue.readmit_parked)

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
