"""Legacy Governance to SDK inventory classification for cutover planning."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from one_memory.database_access import main_database
from one_memory.hy_sdk_types import HySdkMemoryPage, HySdkMemoryRecord, HySdkRawPage
from one_memory.input_sanitization import sanitize_model_input
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
    productivity_kind: str | None = None
    productivity_variant: str | None = None


@dataclass(frozen=True)
class SdkMemoryInventory:
    memory_id: str
    user_id: str
    agent_id: str
    content_hash: str
    status: str
    productivity_kind: str | None = None
    productivity_variant: str | None = None


@dataclass(frozen=True)
class MemoryMigrationDecision:
    item_key: str
    action: str
    legacy_memory_id: str | None
    canonical_memory_id: str
    reason: str


@dataclass(frozen=True)
class LegacyRawInventory:
    evidence_id: str
    user_id: str
    agent_id: str
    session_id: str
    source: str
    content: str
    content_hash: str
    memory_at: datetime | None
    metadata: dict[str, object]


@dataclass(frozen=True)
class SdkRawInventory:
    raw_id: str
    user_id: str
    agent_id: str
    session_id: str
    source: str
    content_hash: str


@dataclass(frozen=True)
class RawMigrationDecision:
    item_key: str
    action: str
    evidence_id: str | None
    raw_id: str
    reason: str


_KIND_LAYER = {
    "chat": "l2_fact",
    "schema": "l6_schema",
    "intention": "l7_intention",
}
_PRODUCTIVITY_LAYER = {
    "coding_experience": ("experience", "coding"),
    "working_experience": ("experience", "working"),
    "coding_fact": ("fact", "coding"),
    # Historical receipts called every productivity fact "working_fact",
    # including facts stored beside a coding-variant experience. Resolve the
    # actual variant from the SDK exact lookup instead of trusting this label.
    "working_fact": ("fact", None),
}
_RAW_TURN_MAX_CHARACTERS = 60_000
_RAW_USER_MAX_CHARACTERS = 30_000

SDK_LIFECYCLE_STATUSES = (
    "active",
    "superseded",
    "shadow",
    "negated",
    "conflicted",
    "archived",
)


class SdkMigrationClient(Protocol):
    def list_memories(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        statuses: tuple[str, ...] = ("active",),
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> Awaitable[HySdkMemoryPage]: ...

    def write_legacy_memory(
        self,
        *,
        content: str,
        memory_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        layer: str,
        status: str,
        owner: str | None,
        tags: tuple[str, ...],
        supersedes: tuple[str, ...],
        superseded_by: tuple[str, ...],
        is_latest: bool,
        request_id: str,
    ) -> Awaitable[dict[str, object]]: ...

    def read_raw_inventory(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> Awaitable[HySdkRawPage]: ...

    def write_legacy_raw(
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
    ) -> Awaitable[dict[str, object]]: ...

    def delete(self, memory_id: str) -> Awaitable[dict[str, object]]: ...

    def read_productivity_memory(
        self,
        memory_id: str,
        *,
        kind: str,
        variant: str | None,
    ) -> Awaitable[tuple[HySdkMemoryRecord, str] | None]: ...

    def list_productivity_memories(
        self,
    ) -> Awaitable[tuple[dict[str, object], ...]]: ...

    def delete_productivity_memory(
        self,
        memory_id: str,
        *,
        kind: str,
        variant: str | None,
        user_id: str,
    ) -> Awaitable[dict[str, object]]: ...

    def delete_raw(self, raw_id: str) -> Awaitable[dict[str, object]]: ...


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
            mapped_id, mapped_layer, mapped_owner = mappings.get(legacy_id, (legacy_id, None, None))
            try:
                tags_value = json.loads(row["tags_json"] or "[]")
            except (TypeError, ValueError):
                tags_value = []
            tags = tuple(value for value in tags_value if isinstance(value, str))
            kind = str(row["kind"])
            status = str(row["status"])
            productivity = _PRODUCTIVITY_LAYER.get(mapped_layer or "")
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
                    productivity_kind=productivity[0] if productivity else None,
                    productivity_variant=productivity[1] if productivity else None,
                )
            )
    return tuple(records)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_legacy_raw_inventory(
    database_path: Path,
    *,
    cutoff: datetime,
) -> tuple[LegacyRawInventory, ...]:
    """Rebuild recent legacy messages into one user/assistant raw per turn."""
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    with main_database(database_path).read(row_factory=sqlite3.Row) as connection:
        rows = connection.execute(
            """
            SELECT evidence_id,user_id,source_id,workspace,project,
                   conversation_id,message_id,source_ordinal,content_kind,
                   role,written_by,created_at,imported_at,verbatim,content_hash,revision
            FROM evidence
            WHERE content_kind='raw_conversation'
              AND (
                    (role='user' AND written_by='person')
                    OR role='assistant'
                  )
            ORDER BY source_id,conversation_id,workspace,
                     COALESCE(created_at,imported_at),
                     COALESCE(ingest_seq,rowid),evidence_id
            """
        ).fetchall()
    turns: list[LegacyRawInventory] = []
    current_key: tuple[str, str, str, str] | None = None
    current: list[sqlite3.Row] = []

    def finish() -> None:
        if not current or str(current[0]["role"]) != "user":
            return
        assistants = [row for row in current[1:] if str(row["role"]) == "assistant"]
        if not assistants:
            return
        rows_for_turn = [current[0], *assistants]
        completed_at = _parse_datetime(
            assistants[-1]["created_at"] or assistants[-1]["imported_at"]
        )
        if completed_at is not None and completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=UTC)
        if completed_at is None or completed_at.astimezone(UTC) < cutoff.astimezone(UTC):
            return
        evidence_ids = [str(row["evidence_id"]) for row in rows_for_turn]
        source = str(current[0]["source_id"])
        session_id = str(current[0]["conversation_id"])
        digest = hashlib.sha256(
            "\0".join((source, session_id, *evidence_ids)).encode()
        ).hexdigest()
        raw_user_text = str(rows_for_turn[0]["verbatim"] or "")
        user_text = sanitize_model_input(raw_user_text)[:_RAW_USER_MAX_CHARACTERS]
        content_parts = [f"[user]: {user_text}"]
        truncated = len(raw_user_text) > len(user_text)
        for row in assistants:
            sanitized = sanitize_model_input(str(row["verbatim"] or ""))
            text = sanitized
            prefix = "[assistant]: "
            remaining = _RAW_TURN_MAX_CHARACTERS - len("\n".join(content_parts)) - 1
            if remaining <= len(prefix):
                truncated = True
                break
            available = remaining - len(prefix)
            if len(sanitized) > available:
                text = text[:available]
                truncated = True
            content_parts.append(f"{prefix}{text}")
            if len(text) < len(sanitized):
                break
        content = "\n".join(content_parts)
        if not content:
            return
        turns.append(
            LegacyRawInventory(
                evidence_id=f"turn:{digest}",
                user_id=str(current[0]["user_id"]),
                agent_id="all-apps",
                session_id=session_id,
                source=source,
                content=content,
                content_hash=content_hash(content),
                memory_at=_parse_datetime(
                    current[0]["created_at"] or current[0]["imported_at"]
                ),
                metadata={
                    "evidence_ids": evidence_ids,
                    "workspace": current[0]["workspace"],
                    "project": current[0]["project"],
                    "input_kind": "legacy_turn",
                    "truncated": truncated,
                },
            )
        )

    for row in rows:
        key = (
            str(row["source_id"]),
            str(row["conversation_id"]),
            str(row["user_id"]),
            str(row["workspace"] or ""),
        )
        if key != current_key:
            finish()
            current_key, current = key, []
        if str(row["role"]) == "user":
            finish()
            current = [row]
        elif current:
            current.append(row)
    finish()
    return tuple(turns)


def classify_memory_inventory(
    legacy: tuple[LegacyMemoryInventory, ...],
    sdk: tuple[SdkMemoryInventory, ...],
) -> tuple[MemoryMigrationDecision, ...]:
    """Classify every legacy and SDK memory; no item may silently disappear."""
    sdk_by_id: dict[str, list[SdkMemoryInventory]] = {}
    for sdk_item in sdk:
        sdk_by_id.setdefault(sdk_item.memory_id, []).append(sdk_item)
    claimed_sdk_items: set[tuple[str, str | None, str | None]] = set()
    decisions: list[MemoryMigrationDecision] = []
    for item in legacy:
        candidates = [
            candidate
            for candidate in sdk_by_id.get(item.canonical_memory_id, ())
            if (
                candidate.productivity_kind == item.productivity_kind
                and (
                    item.productivity_kind is None
                    or item.productivity_variant is None
                    or candidate.productivity_variant == item.productivity_variant
                )
            )
        ]
        if len(candidates) > 1:
            claimed_sdk_items.update(_sdk_inventory_key(candidate) for candidate in candidates)
            decisions.append(
                MemoryMigrationDecision(
                    item_key=f"memory:{item.legacy_memory_id}",
                    action="quarantine",
                    legacy_memory_id=item.legacy_memory_id,
                    canonical_memory_id=item.canonical_memory_id,
                    reason="sdk_memory_id_ambiguous",
                )
            )
            continue
        current = candidates[0] if candidates else None
        if current is not None:
            claimed_sdk_items.add(_sdk_inventory_key(current))
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
            if item.productivity_kind is not None:
                decisions.append(
                    MemoryMigrationDecision(
                        item_key=f"memory:{item.legacy_memory_id}",
                        action="quarantine",
                        legacy_memory_id=item.legacy_memory_id,
                        canonical_memory_id=item.canonical_memory_id,
                        reason="mapped_productivity_memory_missing",
                    )
                )
                continue
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
        if item.productivity_kind is not None and (
            current.user_id == item.user_id
            and current.productivity_kind == item.productivity_kind
            and (
                item.productivity_variant is None
                or current.productivity_variant == item.productivity_variant
            )
        ):
            action = (
                "matched" if item.legacy_memory_id == item.canonical_memory_id else "record_alias"
            )
            reason = "existing_productivity_identity"
        elif (
            current.content_hash == item.content_hash
            and current.status == item.status
            and current.user_id == item.user_id
            and current.agent_id == item.agent_id
        ):
            action = (
                "matched" if item.legacy_memory_id == item.canonical_memory_id else "record_alias"
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

    for sdk_item in sdk:
        if _sdk_inventory_key(sdk_item) not in claimed_sdk_items:
            decisions.append(
                MemoryMigrationDecision(
                    item_key=_sdk_item_key(sdk_item),
                    action="adopt",
                    legacy_memory_id=None,
                    canonical_memory_id=sdk_item.memory_id,
                    reason="sdk_only",
                )
            )
    return tuple(decisions)


def _sdk_inventory_key(
    item: SdkMemoryInventory,
) -> tuple[str, str | None, str | None]:
    return (
        item.memory_id,
        item.productivity_kind,
        item.productivity_variant,
    )


def _sdk_item_key(item: SdkMemoryInventory) -> str:
    namespace = (
        f"{item.productivity_variant}:{item.productivity_kind}"
        if item.productivity_kind is not None
        else "chat"
    )
    return f"sdk:{namespace}:{item.memory_id}"


async def read_sdk_memory_inventory(
    client: SdkMigrationClient,
    *,
    legacy: tuple[LegacyMemoryInventory, ...] = (),
    user_id: str = "local",
    page_size: int = 500,
) -> tuple[SdkMemoryInventory, ...]:
    records: list[SdkMemoryInventory] = []
    offset = 0
    while True:
        page = await client.list_memories(
            user_id=user_id,
            statuses=SDK_LIFECYCLE_STATUSES,
            limit=page_size,
            offset=offset,
            order="asc",
        )
        for memory in page.memories:
            records.append(
                SdkMemoryInventory(
                    memory_id=memory.memory_id,
                    user_id=memory.user_id,
                    agent_id=memory.agent_id,
                    content_hash=content_hash(memory.content),
                    status=memory.status,
                )
            )
        consumed = len(page.memories)
        offset += consumed
        if consumed == 0 or offset >= page.total:
            break
    del legacy
    for memory in await client.list_productivity_memories():
        try:
            records.append(
                SdkMemoryInventory(
                    memory_id=str(memory["memory_id"]),
                    user_id=str(memory["user_id"]),
                    agent_id=str(memory["agent_id"]),
                    content_hash=content_hash(str(memory["content"])),
                    status=str(memory["status"]),
                    productivity_kind=str(memory["kind"]),
                    productivity_variant=str(memory["variant"]),
                )
            )
        except KeyError as error:
            raise RuntimeError("invalid productivity inventory row") from error
    return tuple(
        sorted(
            records,
            key=lambda item: (
                item.memory_id,
                item.productivity_kind or "",
                item.productivity_variant or "",
            ),
        )
    )


def record_memory_migration_plan(
    store: SdkDataPlaneMigrationStore,
    *,
    migration_id: str,
    legacy: tuple[LegacyMemoryInventory, ...],
    sdk: tuple[SdkMemoryInventory, ...],
) -> tuple[MemoryMigrationDecision, ...]:
    decisions = classify_memory_inventory(legacy, sdk)
    legacy_by_id = {item.legacy_memory_id: item for item in legacy}
    sdk_by_item_key = {_sdk_item_key(item): item for item in sdk}
    for decision in decisions:
        source_hash = (
            legacy_by_id[decision.legacy_memory_id].content_hash
            if decision.legacy_memory_id is not None
            else sdk_by_item_key[decision.item_key].content_hash
        )
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
    client: SdkMigrationClient,
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
                if item is not None and item.productivity_kind is not None:
                    await client.delete_productivity_memory(
                        decision.canonical_memory_id,
                        kind=item.productivity_kind,
                        variant=item.productivity_variant,
                        user_id=item.user_id,
                    )
                else:
                    await client.delete(decision.canonical_memory_id)
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
            assert item.layer is not None
            request_id = hashlib.sha256(
                f"{migration_id}:{decision.item_key}:import_exact".encode()
            ).hexdigest()
            await client.write_legacy_memory(
                content=item.content,
                memory_id=item.canonical_memory_id,
                user_id=item.user_id,
                agent_id=item.agent_id,
                session_id=item.session_id,
                source="legacy-governance",
                layer=item.layer,
                status=item.status,
                owner=item.owner,
                tags=item.tags,
                supersedes=item.supersedes,
                superseded_by=item.superseded_by,
                is_latest=item.status == "active",
                memory_at=None,
                metadata={},
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


async def read_sdk_raw_inventory(
    client: SdkMigrationClient,
    *,
    user_id: str = "local",
    page_size: int = 500,
) -> tuple[SdkRawInventory, ...]:
    records: dict[str, SdkRawInventory] = {}
    offset = 0
    while True:
        page = await client.read_raw_inventory(
            user_id=user_id,
            limit=page_size,
            offset=offset,
            order="asc",
        )
        for raw in page.raw:
            raw_id = str(raw.raw_id)
            records[raw_id] = SdkRawInventory(
                raw_id=raw_id,
                user_id=str(raw.user_id),
                agent_id=str(raw.agent_id),
                session_id=str(raw.session_id),
                source=str(raw.source),
                content_hash=content_hash(str(raw.content)),
            )
        consumed = len(page.raw)
        offset += consumed
        if consumed == 0 or offset >= page.total:
            break
    return tuple(records[key] for key in sorted(records))


def classify_raw_inventory(
    legacy: tuple[LegacyRawInventory, ...],
    sdk: tuple[SdkRawInventory, ...],
) -> tuple[RawMigrationDecision, ...]:
    sdk_by_identity: dict[tuple[str, str, str, str, str], list[SdkRawInventory]] = {}
    for raw in sdk:
        identity = (
            raw.user_id,
            raw.agent_id,
            raw.session_id,
            raw.source,
            raw.content_hash,
        )
        sdk_by_identity.setdefault(identity, []).append(raw)
    claimed: set[str] = set()
    decisions: list[RawMigrationDecision] = []
    for item in legacy:
        identity = (
            item.user_id,
            item.agent_id,
            item.session_id,
            item.source,
            item.content_hash,
        )
        candidates = sorted(
            sdk_by_identity.get(identity, ()),
            key=lambda candidate: candidate.raw_id,
        )
        existing = next(
            (candidate for candidate in candidates if candidate.raw_id not in claimed),
            None,
        )
        if existing is not None:
            claimed.add(existing.raw_id)
            decisions.append(
                RawMigrationDecision(
                    item_key=f"raw:{item.evidence_id}",
                    action="matched",
                    evidence_id=item.evidence_id,
                    raw_id=existing.raw_id,
                    reason="same_content_and_scope",
                )
            )
        else:
            decisions.append(
                RawMigrationDecision(
                    item_key=f"raw:{item.evidence_id}",
                    action="import_exact",
                    evidence_id=item.evidence_id,
                    raw_id=f"evidence:{item.evidence_id}",
                    reason="evidence_only",
                )
            )
    for raw in sdk:
        if raw.raw_id not in claimed:
            decisions.append(
                RawMigrationDecision(
                    item_key=f"sdk-raw:{raw.raw_id}",
                    action="delete",
                    evidence_id=None,
                    raw_id=raw.raw_id,
                    reason="outside_canonical_three_day_turns",
                )
            )
    return tuple(decisions)


def record_raw_migration_plan(
    store: SdkDataPlaneMigrationStore,
    *,
    migration_id: str,
    legacy: tuple[LegacyRawInventory, ...],
    sdk: tuple[SdkRawInventory, ...],
) -> tuple[RawMigrationDecision, ...]:
    decisions = classify_raw_inventory(legacy, sdk)
    legacy_by_id = {item.evidence_id: item for item in legacy}
    sdk_by_id = {item.raw_id: item for item in sdk}
    for decision in decisions:
        source_hash = (
            legacy_by_id[decision.evidence_id].content_hash
            if decision.evidence_id is not None
            else sdk_by_id[decision.raw_id].content_hash
        )
        operation_key = hashlib.sha256(
            f"{migration_id}:{decision.item_key}:{decision.action}:{source_hash}".encode()
        ).hexdigest()
        store.upsert_item(
            migration_id=migration_id,
            item_key=decision.item_key,
            source_kind=f"raw:{decision.action}",
            source_id=decision.evidence_id or decision.raw_id,
            source_hash=source_hash,
            operation_key=operation_key,
        )
    return decisions


async def execute_raw_migration_plan(
    client: SdkMigrationClient,
    store: SdkDataPlaneMigrationStore,
    *,
    migration_id: str,
    legacy: tuple[LegacyRawInventory, ...],
    decisions: tuple[RawMigrationDecision, ...],
) -> None:
    legacy_by_id = {item.evidence_id: item for item in legacy}
    for decision in decisions:
        if decision.action == "delete":
            await client.delete_raw(decision.raw_id)
            store.settle_item(
                migration_id=migration_id,
                item_key=decision.item_key,
                state="deleted",
                target_memory_id=decision.raw_id,
            )
            continue
        item = legacy_by_id.get(decision.evidence_id or "")
        if item is None:
            raise RuntimeError(f"missing legacy raw inventory for {decision.item_key}")
        if decision.action == "import_exact":
            request_id = hashlib.sha256(
                f"{migration_id}:{decision.item_key}:write_legacy_raw".encode()
            ).hexdigest()
            await client.write_legacy_raw(
                content=item.content,
                raw_id=decision.raw_id,
                user_id=item.user_id,
                agent_id=item.agent_id,
                session_id=item.session_id,
                source=item.source,
                memory_at=item.memory_at,
                metadata=item.metadata,
                request_id=request_id,
            )
        store.settle_item(
            migration_id=migration_id,
            item_key=decision.item_key,
            state="verified",
            target_memory_id=decision.raw_id,
            target_hash=item.content_hash,
        )
        evidence_ids = item.metadata.get("evidence_ids")
        for evidence_id in (
            evidence_ids
            if isinstance(evidence_ids, list)
            else [item.evidence_id]
        ):
            if isinstance(evidence_id, str) and evidence_id:
                store.record_raw_alias(
                    migration_id=migration_id,
                    legacy_evidence_id=evidence_id,
                    canonical_raw_id=decision.raw_id,
                )
