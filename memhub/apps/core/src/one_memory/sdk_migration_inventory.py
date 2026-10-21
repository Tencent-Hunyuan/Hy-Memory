"""Legacy Governance to SDK inventory classification for cutover planning."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from one_memory.database_access import main_database


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
