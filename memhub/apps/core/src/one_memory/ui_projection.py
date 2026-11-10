"""Stable UI projections over the HY SDK's mode-specific storage shapes."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from one_memory.hy_sdk_types import (
    HySdkMemoryPage,
    HySdkMemoryRecord,
    HySdkRawPage,
    HySdkRawRecord,
    raw_turn_messages,
)
from one_memory.imports.sources.parsing import unwrap_host_envelope_with_positions

MemoryMode = Literal["lite", "pro", "ultra"]
TimeBasis = Literal["conversation", "import", "write", "update"]
ProjectionDisplayState = Literal["empty", "excerpt", "no_user_excerpt", "no_displayable_memory"]
RAW_STATUSES = ("active", "shadow")
HOME_PROJECTION_CANDIDATES = 10
HOME_EXCERPT_CODEPOINTS = 200

_SPACE = re.compile(r"\s+")
_CONTROL_ONLY = re.compile(
    r"^(?:"
    r"<<?\s*(?:image|attachment|system[_-]?reminder|no[_ -]?content)\s*>>?"
    r"|\[\s*(?:image|attachment|system[_ -]?reminder|no[_ -]?content)\s*\]"
    r")$",
    re.IGNORECASE,
)


class HomeProjectionCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content_plane: Literal["raw", "memory"]
    generation_required: bool
    graph_enabled: bool
    evolve_available: bool


class _HomeLatest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    time: datetime | None
    time_basis: TimeBasis | None
    excerpt: str
    truncated: bool
    source_id: str


class HomeRawLatest(_HomeLatest):
    type: Literal["raw"] = "raw"
    raw_id: str
    session_id: str
    display_state: Literal["excerpt", "no_user_excerpt"]


class HomeMemoryLatest(_HomeLatest):
    type: Literal["memory"] = "memory"
    memory_id: str
    scene: Literal["chat", "working", "coding"]
    # scene=all lists chat rows and productivity experiences; facts are not
    # part of that page or its total.
    kind: Literal["chat", "experience"]
    variant: Literal["working", "coding"] | None


class HomeProjectionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    projection_version: Literal[1] = 1
    user_id: str
    effective_mode: MemoryMode
    capabilities: HomeProjectionCapabilities
    visible_count: int = Field(ge=0)
    display_state: ProjectionDisplayState
    latest: HomeRawLatest | HomeMemoryLatest | None
    observed_at: datetime


def raw_list_filters(statuses: list[str] | tuple[str, ...] | None = None) -> dict[str, list[str]]:
    """The one kept-raw definition shared by `/raw` and the home projection."""
    return {
        "layers": ["l1_raw"],
        "statuses": list(statuses or RAW_STATUSES),
    }


def home_capabilities(mode: MemoryMode) -> HomeProjectionCapabilities:
    return HomeProjectionCapabilities(
        content_plane="raw" if mode == "lite" else "memory",
        generation_required=mode != "lite",
        graph_enabled=mode == "ultra",
        evolve_available=mode == "ultra",
    )


def raw_user_excerpt(content: str, *, limit: int = HOME_EXCERPT_CODEPOINTS) -> tuple[str, bool]:
    """Return only displayable user text from one SDK raw turn.

    Role selection happens before wrapper removal, so assistant/system content
    can never become a homepage excerpt after sanitization.
    """
    candidates: list[str] = []
    for role, text in raw_turn_messages(content):
        if role != "user":
            continue
        envelope = unwrap_host_envelope_with_positions(text)
        if envelope.status == "ambiguous":
            # An uncertain host envelope may contain system context. Showing
            # nothing is safer than guessing which bytes the person authored.
            continue
        cleaned = envelope.text or ""
        cleaned = _SPACE.sub(" ", cleaned).strip()
        if not cleaned or _CONTROL_ONLY.fullmatch(cleaned):
            continue
        candidates.append(cleaned)
    joined = " ".join(candidates)
    return joined[:limit], len(joined) > limit


def _epoch(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if value is not None else None


def _raw_time(record: HySdkRawRecord) -> tuple[datetime | None, TimeBasis | None]:
    if record.memory_at is not None:
        return _epoch(record.memory_at), "conversation"
    if record.gmt_created is not None:
        return _epoch(record.gmt_created), "import"
    return None, None


def _memory_time(record: HySdkMemoryRecord) -> tuple[datetime | None, TimeBasis | None]:
    # Exactly the scene=all merge key. `memory_at` is event time and does not
    # decide which row list_memories returns first.
    if record.sort_updated_at is not None:
        return _epoch(record.sort_updated_at), "update"
    written = record.gmt_created if record.scene == "chat" else record.created_at
    if written is not None:
        return _epoch(written), "write"
    return None, None


def _bounded(text: str) -> tuple[str, bool]:
    flattened = _SPACE.sub(" ", text).strip()
    return flattened[:HOME_EXCERPT_CODEPOINTS], len(flattened) > HOME_EXCERPT_CODEPOINTS


def project_raw_latest(records: tuple[HySdkRawRecord, ...]) -> HomeRawLatest | None:
    if not records:
        return None
    for record in records:
        excerpt, truncated = raw_user_excerpt(record.content)
        if excerpt:
            at, basis = _raw_time(record)
            return HomeRawLatest(
                raw_id=record.raw_id,
                session_id=record.session_id,
                display_state="excerpt",
                time=at,
                time_basis=basis,
                excerpt=excerpt,
                truncated=truncated,
                source_id=record.source,
            )
    newest = records[0]
    at, basis = _raw_time(newest)
    return HomeRawLatest(
        raw_id=newest.raw_id,
        session_id=newest.session_id,
        display_state="no_user_excerpt",
        time=at,
        time_basis=basis,
        excerpt="",
        truncated=False,
        source_id=newest.source,
    )


def project_memory_latest(
    records: tuple[HySdkMemoryRecord, ...],
) -> HomeMemoryLatest | None:
    record = next(
        (
            item
            for item in records
            if item.kind in {"chat", "experience"}
            and (
                (item.kind == "chat" and item.scene == "chat" and item.variant is None)
                or (
                    item.kind == "experience"
                    and item.scene in {"working", "coding"}
                    and item.variant == item.scene
                )
            )
        ),
        None,
    )
    if record is None:
        return None
    excerpt, truncated = _bounded(record.content)
    at, basis = _memory_time(record)
    return HomeMemoryLatest(
        memory_id=record.memory_id,
        scene=record.scene,
        kind=cast(Literal["chat", "experience"], record.kind),
        variant=record.variant,
        time=at,
        time_basis=basis,
        excerpt=excerpt,
        truncated=truncated,
        source_id=record.source or "",
    )


async def read_home_projection(
    client: Any,
    *,
    user_id: str,
    mode: MemoryMode,
) -> HomeProjectionV1:
    """Read one authoritative projection; any SDK/shape failure escapes."""
    client_mode = getattr(client, "mode", mode)
    if client_mode != mode:
        raise RuntimeError("home_projection_mode_mismatch")
    if mode == "lite":
        raw = await asyncio.to_thread(
            client.list_memories,
            user_id=user_id,
            **raw_list_filters(),
            limit=HOME_PROJECTION_CANDIDATES,
            offset=0,
            order="desc",
        )
        raw_page = HySdkRawPage.from_memory_list(raw, limit=HOME_PROJECTION_CANDIDATES, offset=0)
        visible_count = raw_page.total
        raw_latest = project_raw_latest(raw_page.raw)
        latest: HomeRawLatest | HomeMemoryLatest | None = raw_latest
        display_state: ProjectionDisplayState = (
            "empty"
            if visible_count == 0
            else raw_latest.display_state
            if raw_latest is not None
            else "no_user_excerpt"
        )
    else:
        raw = await asyncio.to_thread(
            client.list_memories,
            user_id=user_id,
            scene="all",
            statuses=["active"],
            limit=HOME_PROJECTION_CANDIDATES,
            offset=0,
            order="desc",
        )
        memory_page, _skipped = HySdkMemoryPage.from_sdk_skipping_invalid(
            raw, limit=HOME_PROJECTION_CANDIDATES, offset=0
        )
        visible_count = memory_page.total
        latest = project_memory_latest(memory_page.memories)
        display_state = (
            "empty"
            if visible_count == 0
            else "excerpt"
            if latest is not None
            else "no_displayable_memory"
        )
    return HomeProjectionV1(
        user_id=user_id,
        effective_mode=mode,
        capabilities=home_capabilities(mode),
        visible_count=visible_count,
        display_state=display_state,
        latest=latest,
        observed_at=datetime.now(UTC),
    )
