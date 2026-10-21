"""Legacy Governance to SDK inventory classification for cutover planning."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from one_memory.database_access import main_database
from one_memory.hy_memory_bridge import HySdkMemoryPage
from one_memory.sdk_data_plane_migration import SdkDataPlaneMigrationStore


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LegacyMemoryInventory:
    legacy_memory_id: str
    canonical_memory_id: str
    user_id: str
    agent_id: str
    session_id: str
    content: str
    content_hash: str
    status: str
    layer: str | None
    owner: str | None
    tags: tuple[str, ...]
    supersedes: tuple[str, ...]
    superseded_by: tuple[str, ...]


@dataclass(frozen=True)
class SdkMemoryInventory:
    memory_id: str
    user_id: str
    agent_id: str
    content_hash: str
    status: str


@dataclass(frozen=True)
class MemoryMigrationDecision:
    item_key: str
    action: str
    legacy_memory_id: str | None
    canonical_memory_id: str
    reason: str


_KIND_LAYER = {
    "chat": "l2_fact",
    "schema": "l6_schema",
    "intention": "l7_intention",
}

SDK_LIFECYCLE_STATUSES = (
    "active",
    "superseded",
    "shadow",
    "negated",
    "conflicted",
    "archived",
)


class SdkMigrationBridge(Protocol):
    def list_sdk_memories(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        statuses: tuple[str, ...] = ("active",),
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> Awaitable[HySdkMemoryPage]: ...

    def import_sdk_memory_exact(self, **kwargs: object) -> Awaitable[dict[str, object]]: ...

    def delete(self, memory_id: str) -> Awaitable[dict[str, object]]: ...


def read_legacy_memory_inventory(database_path: Path) -> tuple[LegacyMemoryInventory, ...]:
    """Read legacy memory intent without constructing a Governance writer."""
    with main_database(database_path).read(row_factory=sqlite3.Row) as connection:
        mappings: dict[str, tuple[str, str | None, str | None]] = {}
        for row in connection.execute("SELECT body FROM memory_task_outputs"):
            try:
                body = json.loads(row["body"])
            except (TypeError, ValueError):
                continue
            legacy_id = body.get("memory_id")
            sdk_id = body.get("hy_memory_id")
            if isinstance(legacy_id, str) and isinstance(sdk_id, str):
                mappings[legacy_id] = (
                    sdk_id,
                    body.get("layer") if isinstance(body.get("layer"), str) else None,
                    body.get("owner") if body.get("owner") in {"user", "agent"} else None,
                )

        supersedes: dict[str, list[str]] = {}
        superseded_by: dict[str, list[str]] = {}
        for row in connection.execute(
            "SELECT memory_id,superseded_by FROM governed_memory_supersessions "
            "ORDER BY memory_id,superseded_by"
        ):
            older, newer = str(row["memory_id"]), str(row["superseded_by"])
            superseded_by.setdefault(older, []).append(newer)
            supersedes.setdefault(newer, []).append(older)

        records: list[LegacyMemoryInventory] = []
        for row in connection.execute(
            "SELECT memory_id,user_id,kind,status,content,tags_json "
            "FROM governed_memories ORDER BY memory_id"
        ):
            legacy_id = str(row["memory_id"])
            mapped_id, mapped_layer, mapped_owner = mappings.get(
                legacy_id, (legacy_id, None, None)
            )
            try:
                tags_value = json.loads(row["tags_json"] or "[]")
            except (TypeError, ValueError):
                tags_value = []
            tags = tuple(value for value in tags_value if isinstance(value, str))
            kind = str(row["kind"])
            status = str(row["status"])
            records.append(
                LegacyMemoryInventory(
                    legacy_memory_id=legacy_id,
                    canonical_memory_id=mapped_id,
                    user_id=str(row["user_id"]),
                    agent_id="one-memory",
                    session_id="default_session",
                    content=str(row["content"]),
                    content_hash=content_hash(str(row["content"])),
                    status=(
                        "active"
                        if status == "confirmed"
                        else "superseded"
                        if status == "superseded"
                        else "deleted"
                    ),
                    layer=mapped_layer or _KIND_LAYER.get(kind),
                    owner=mapped_owner,
                    tags=tags,
                    supersedes=tuple(supersedes.get(legacy_id, ())),
                    superseded_by=tuple(superseded_by.get(legacy_id, ())),
                )
            )
    return tuple(records)


def classify_memory_inventory(
    legacy: tuple[LegacyMemoryInventory, ...],
    sdk: tuple[SdkMemoryInventory, ...],
) -> tuple[MemoryMigrationDecision, ...]:
    """Classify every legacy and SDK memory; no item may silently disappear."""
    sdk_by_id = {item.memory_id: item for item in sdk}
    claimed_sdk_ids: set[str] = set()
    decisions: list[MemoryMigrationDecision] = []
    for item in legacy:
        current = sdk_by_id.get(item.canonical_memory_id)
        if current is not None:
            claimed_sdk_ids.add(current.memory_id)
        if item.status == "deleted":
            decisions.append(
                MemoryMigrationDecision(
                    item_key=f"memory:{item.legacy_memory_id}",
                    action="delete" if current is not None else "verified_deleted",
                    legacy_memory_id=item.legacy_memory_id,
                    canonical_memory_id=item.canonical_memory_id,
                    reason="legacy_user_delete",
                )
            )
            continue
        if item.layer is None:
            decisions.append(
                MemoryMigrationDecision(
                    item_key=f"memory:{item.legacy_memory_id}",
                    action="quarantine",
                    legacy_memory_id=item.legacy_memory_id,
                    canonical_memory_id=item.canonical_memory_id,
                    reason="legacy_layer_unknown",
                )
            )
            continue
        if current is None:
            decisions.append(
                MemoryMigrationDecision(
                    item_key=f"memory:{item.legacy_memory_id}",
                    action="import_exact",
                    legacy_memory_id=item.legacy_memory_id,
                    canonical_memory_id=item.canonical_memory_id,
                    reason="governance_only",
                )
            )
            continue
        if (
            current.content_hash == item.content_hash
            and current.status == item.status
            and current.user_id == item.user_id
            and current.agent_id == item.agent_id
        ):
            action = (
                "matched"
                if item.legacy_memory_id == item.canonical_memory_id
                else "record_alias"
            )
            reason = "same_identity" if action == "matched" else "mapped_identity"
        else:
            action, reason = "quarantine", "content_owner_or_lifecycle_diverged"
        decisions.append(
            MemoryMigrationDecision(
                item_key=f"memory:{item.legacy_memory_id}",
                action=action,
                legacy_memory_id=item.legacy_memory_id,
                canonical_memory_id=item.canonical_memory_id,
                reason=reason,
            )
        )

    for item in sdk:
        if item.memory_id not in claimed_sdk_ids:
            decisions.append(
                MemoryMigrationDecision(
                    item_key=f"sdk:{item.memory_id}",
                    action="adopt",
                    legacy_memory_id=None,
                    canonical_memory_id=item.memory_id,
                    reason="sdk_only",
                )
            )
    return tuple(decisions)


async def read_sdk_memory_inventory(
    bridge: SdkMigrationBridge,
    *,
    user_id: str = "local",
    page_size: int = 500,
) -> tuple[SdkMemoryInventory, ...]:
    records: dict[str, SdkMemoryInventory] = {}
    offset = 0
    while True:
        page = await bridge.list_sdk_memories(
            user_id=user_id,
            statuses=SDK_LIFECYCLE_STATUSES,
            limit=page_size,
            offset=offset,
            order="asc",
        )
        for memory in page.memories:
            records[memory.memory_id] = SdkMemoryInventory(
                memory_id=memory.memory_id,
                user_id=memory.user_id,
                agent_id=memory.agent_id,
                content_hash=content_hash(memory.content),
                status=memory.status,
            )
        consumed = len(page.memories)
        offset += consumed
        if consumed == 0 or offset >= page.total:
            break
    return tuple(records[key] for key in sorted(records))


def record_memory_migration_plan(
    store: SdkDataPlaneMigrationStore,
    *,
    migration_id: str,
    legacy: tuple[LegacyMemoryInventory, ...],
    sdk: tuple[SdkMemoryInventory, ...],
) -> tuple[MemoryMigrationDecision, ...]:
    decisions = classify_memory_inventory(legacy, sdk)
    legacy_by_id = {item.legacy_memory_id: item for item in legacy}
    sdk_by_id = {item.memory_id: item for item in sdk}
    for decision in decisions:
        source = (
            legacy_by_id.get(decision.legacy_memory_id)
            if decision.legacy_memory_id is not None
            else sdk_by_id[decision.canonical_memory_id]
        )
        source_hash = source.content_hash
        operation_key = hashlib.sha256(
            f"{migration_id}:{decision.item_key}:{decision.action}:{source_hash}".encode()
        ).hexdigest()
        store.upsert_item(
            migration_id=migration_id,
            item_key=decision.item_key,
            source_kind=f"memory:{decision.action}",
            source_id=(
                decision.legacy_memory_id
                if decision.legacy_memory_id is not None
                else decision.canonical_memory_id
            ),
            source_hash=source_hash,
            operation_key=operation_key,
        )
    return decisions


async def execute_memory_migration_plan(
    bridge: SdkMigrationBridge,
    store: SdkDataPlaneMigrationStore,
    *,
    migration_id: str,
    legacy: tuple[LegacyMemoryInventory, ...],
    decisions: tuple[MemoryMigrationDecision, ...],
) -> None:
    legacy_by_id = {item.legacy_memory_id: item for item in legacy}
    for decision in decisions:
        item = (
            legacy_by_id.get(decision.legacy_memory_id)
            if decision.legacy_memory_id is not None
            else None
        )
        if decision.action == "quarantine":
            store.settle_item(
                migration_id=migration_id,
                item_key=decision.item_key,
                state="quarantine",
                error_code=decision.reason,
            )
            continue
        if decision.action == "adopt":
            store.settle_item(
                migration_id=migration_id,
                item_key=decision.item_key,
                state="adopted",
                target_memory_id=decision.canonical_memory_id,
            )
            continue
        if decision.action in {"delete", "verified_deleted"}:
            if decision.action == "delete":
                await bridge.delete(decision.canonical_memory_id)
            store.settle_item(
                migration_id=migration_id,
                item_key=decision.item_key,
                state="deleted",
                target_memory_id=decision.canonical_memory_id,
            )
            continue
        if item is None:
            raise RuntimeError(f"missing legacy inventory for {decision.item_key}")
        if decision.action == "import_exact":
            request_id = hashlib.sha256(
                f"{migration_id}:{decision.item_key}:import_exact".encode()
            ).hexdigest()
            await bridge.import_sdk_memory_exact(
                content=item.content,
                memory_id=item.canonical_memory_id,
                user_id=item.user_id,
                agent_id=item.agent_id,
                session_id=item.session_id,
                layer=item.layer,
                status=item.status,
                owner=item.owner,
                tags=item.tags,
                supersedes=item.supersedes,
                superseded_by=item.superseded_by,
                is_latest=item.status == "active",
                request_id=request_id,
            )
        if decision.action == "record_alias":
            store.record_alias(
                migration_id=migration_id,
                legacy_memory_id=item.legacy_memory_id,
                canonical_memory_id=item.canonical_memory_id,
            )
        store.settle_item(
            migration_id=migration_id,
            item_key=decision.item_key,
            state="verified",
            target_memory_id=decision.canonical_memory_id,
            target_hash=item.content_hash,
        )
