"""Validated records and response parsers for public HY SDK operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, NamedTuple, cast

from pydantic import BaseModel, ConfigDict, Field

from one_memory.semantic_deadline import SemanticMemoryHit


class HySdkError(RuntimeError):
    """Base error carrying a stable, product-facing code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HySdkRuntimeError(HySdkError):
    """The public SDK runtime could not be configured or leased."""


class HySdkMigrationError(HySdkError):
    """The privileged offline SDK data-plane migration failed."""


class HySdkReadError(HySdkError):
    """A public SDK read operation failed or returned an invalid shape."""


MemoryScene = Literal["chat", "working", "coding"]
MemoryKindName = Literal["chat", "experience", "fact"]
MemoryVariantName = Literal["working", "coding"]


class HySdkMemoryRecord(BaseModel):
    """One memory of any scene, in the unified shape the library lists.

    Chat memories keep every field they always had. Working/coding memories
    (experiences and facts) carry ``scene``/``kind``/``variant`` so a client
    can open, edit and delete them again without guessing the namespace; an
    experience also keeps ``task`` and ``solution`` apart, and ``content`` is
    the readable ``task\nsolution`` text the SDK lists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    content: str
    layer: str
    status: str
    is_latest: bool
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    owner: str | None = None
    source_raw_ids: tuple[str, ...] = ()
    user_id: str
    agent_id: str
    session_id: str | None = None
    source: str | None = None
    memory_at: int | None = None
    gmt_created: int | None = None
    scene: MemoryScene = "chat"
    kind: MemoryKindName = "chat"
    # The productivity namespace ("working" | "coding"); None for chat. Pass it
    # back with `kind` on get/update/delete.
    variant: MemoryVariantName | None = None
    task: str | None = None
    solution: str | None = None
    # Epoch seconds. `updated_at` falls back to `created_at`.
    created_at: int | None = None
    updated_at: int | None = None

    @classmethod
    def from_sdk(cls, raw: object) -> HySdkMemoryRecord:
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_record_shape_unknown")
        if raw.get("kind") in {"experience", "fact"}:
            return cls._from_productivity(raw)
        if any(
            not isinstance(raw.get(key), str) or not str(raw[key]).strip()
            for key in ("memory_id", "content")
        ):
            raise HySdkReadError("hy_memory_record_shape_unknown")
        try:
            gmt_created = (
                raw.get("gmt_created") if isinstance(raw.get("gmt_created"), int) else None
            )
            created_at = _epoch(raw.get("created_at"))
            if created_at is None:
                created_at = gmt_created
            updated_at = _epoch(raw.get("updated_at"))
            return cls(
                memory_id=str(raw["memory_id"]),
                content=str(raw["content"]),
                layer=str(raw.get("layer") or ""),
                status=str(raw.get("status") or ""),
                is_latest=bool(raw.get("is_latest", True)),
                supersedes=_string_tuple(raw.get("supersedes")),
                superseded_by=_string_tuple(raw.get("superseded_by")),
                tags=_string_tuple(raw.get("tags")),
                owner=(str(raw["owner"]) if raw.get("owner") in {"user", "agent"} else None),
                source_raw_ids=_source_raw_ids(raw),
                user_id=str(raw.get("user_id") or ""),
                agent_id=str(raw.get("agent_id") or ""),
                session_id=(
                    str(raw["session_id"]) if isinstance(raw.get("session_id"), str) else None
                ),
                source=str(raw["source"]) if isinstance(raw.get("source"), str) else None,
                memory_at=raw.get("memory_at") if isinstance(raw.get("memory_at"), int) else None,
                gmt_created=gmt_created,
                created_at=created_at,
                updated_at=updated_at if updated_at is not None else created_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HySdkReadError("hy_memory_record_shape_unknown") from error

    @classmethod
    def _from_productivity(cls, raw: Mapping[str, object]) -> HySdkMemoryRecord:
        """An experience or fact from list/search(scene="all") or get().

        get() names the namespace in ``variant`` (its ``scene`` is the legacy
        "productivity"); list/search name it in ``scene``.
        """
        kind = str(raw["kind"])
        variant = next(
            (
                value
                for value in (raw.get("variant"), raw.get("scene"))
                if value in {"working", "coding"}
            ),
            None,
        )
        id_key = "experience_id" if kind == "experience" else "fact_id"
        memory_id = raw.get("memory_id") or raw.get(id_key)
        if variant is None or not isinstance(memory_id, str) or not memory_id.strip():
            raise HySdkReadError("hy_memory_record_shape_unknown")
        task: str | None = None
        solution: str | None = None
        if kind == "experience":
            task = str(raw.get("task") or "").strip()
            solution = str(raw.get("solution") or "").strip()
            content = "\n".join(part for part in (task, solution) if part)
        else:
            statement = raw.get("statement")
            content = str(statement if isinstance(statement, str) else raw.get("content") or "")
        if not content.strip():
            raise HySdkReadError("hy_memory_record_shape_unknown")
        status = str(raw.get("status") or "active")
        memory_at = raw.get("memory_at")
        created_at = _epoch(raw.get("created_at"))
        updated_at = _epoch(raw.get("updated_at"))
        try:
            return cls(
                memory_id=memory_id,
                content=content,
                layer=kind,
                status=status,
                is_latest=status == "active",
                tags=_string_tuple(raw.get("tags")),
                user_id=str(raw.get("user_id") or ""),
                agent_id=str(raw.get("agent_id") or ""),
                session_id=(
                    str(raw["session_id"]) if isinstance(raw.get("session_id"), str) else None
                ),
                source=str(raw["source"]) if isinstance(raw.get("source"), str) else None,
                # Conversation time; hy-memory 2.0.7 stores it for experiences
                # and facts. Older stores leave it unknown.
                memory_at=memory_at if isinstance(memory_at, int) else None,
                gmt_created=created_at,
                scene=cast(MemoryScene, variant),
                kind=cast(MemoryKindName, kind),
                variant=cast(MemoryVariantName, variant),
                task=task,
                solution=solution,
                created_at=created_at,
                updated_at=updated_at if updated_at is not None else created_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HySdkReadError("hy_memory_record_shape_unknown") from error


class HySdkMemoryPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memories: tuple[HySdkMemoryRecord, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    # Set on a search whose relevance window was full: matches were looked for
    # only among this many most relevant memories, so `total` may undercount.
    search_window: int | None = None
    # Keyword search only: the server-side result window this page was cut
    # from. Pass it back with the next offset to page the same results.
    window_id: str | None = None
    # True when the window_id the client sent had expired or did not match the
    # query: the search ran again and this is page 1 of the new window.
    window_reset: bool = False
    # Empty-query listing only: how many memories each scene holds under the
    # same filters (`total` is their sum).
    totals: dict[str, int] | None = None

    @classmethod
    def from_sdk(cls, raw: object, *, limit: int, offset: int) -> HySdkMemoryPage:
        page, _skipped = cls._parse(raw, limit=limit, offset=offset, skip_invalid=False)
        return page

    @classmethod
    def from_sdk_skipping_invalid(
        cls, raw: object, *, limit: int, offset: int
    ) -> tuple[HySdkMemoryPage, int]:
        """Parse a page, leaving out rows outside the public record contract.

        Returns the page and how many rows were left out, so the caller can
        report them. The rows themselves are never touched.
        """
        return cls._parse(raw, limit=limit, offset=offset, skip_invalid=True)

    @classmethod
    def _parse(
        cls,
        raw: object,
        *,
        limit: int,
        offset: int,
        skip_invalid: bool,
    ) -> tuple[HySdkMemoryPage, int]:
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_list_shape_unknown")
        if raw.get("scene") == "all":
            return cls._parse_all_scenes(raw, limit=limit, offset=offset, skip_invalid=skip_invalid)
        if raw.get("scene") == "productivity":
            return cls._parse_experiences(
                raw, limit=limit, offset=offset, skip_invalid=skip_invalid
            )
        rows: list[object] = []
        total = 0
        for bucket_name, rows_name in (("vdb", "memories"), ("graph", "nodes")):
            bucket = raw.get(bucket_name)
            if bucket is None and bucket_name == "graph":
                continue
            if not isinstance(bucket, Mapping) or not isinstance(bucket.get(rows_name), list):
                raise HySdkReadError("hy_memory_list_shape_unknown")
            bucket_total = bucket.get("total")
            if (
                not isinstance(bucket_total, int)
                or isinstance(bucket_total, bool)
                or bucket_total < 0
            ):
                raise HySdkReadError("hy_memory_list_shape_unknown")
            total += bucket_total
            rows.extend(bucket[rows_name])
        deduplicated: dict[str, HySdkMemoryRecord] = {}
        skipped = 0
        for row in rows:
            try:
                record = HySdkMemoryRecord.from_sdk(row)
            except HySdkReadError:
                if not skip_invalid:
                    raise
                skipped += 1
                continue
            deduplicated.setdefault(record.memory_id, record)
        page = cls(
            memories=tuple(deduplicated.values()),
            # `total` is the SDK's stable result-set count. Subtracting only
            # invalid rows observed on this page would make it change as the
            # caller paged. Until the SDK exposes a quarantined count, keep the
            # authoritative total and skip malformed rows only in this page.
            total=total,
            limit=limit,
            offset=offset,
        )
        return page, skipped

    @classmethod
    def _parse_all_scenes(
        cls,
        raw: Mapping[str, object],
        *,
        limit: int,
        offset: int,
        skip_invalid: bool,
    ) -> tuple[HySdkMemoryPage, int]:
        """``list_memories(scene="all")``: chat, working and coding, newest first."""
        rows = raw.get("memories")
        total = raw.get("total")
        if (
            not isinstance(rows, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
        ):
            raise HySdkReadError("hy_memory_list_shape_unknown")
        totals_raw = raw.get("totals")
        totals = (
            {
                str(scene): count
                for scene, count in totals_raw.items()
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0
            }
            if isinstance(totals_raw, Mapping)
            else None
        )
        memories: list[HySdkMemoryRecord] = []
        seen: set[tuple[str, str, str | None]] = set()
        skipped = 0
        for row in rows:
            try:
                record = HySdkMemoryRecord.from_sdk(row)
            except HySdkReadError:
                if not skip_invalid:
                    raise
                skipped += 1
                continue
            key = (record.memory_id, record.kind, record.variant)
            if key in seen:
                continue
            seen.add(key)
            memories.append(record)
        return (
            cls(
                memories=tuple(memories),
                total=total,
                limit=limit,
                offset=offset,
                totals=totals,
            ),
            skipped,
        )

    @classmethod
    def _parse_experiences(
        cls,
        raw: Mapping[str, object],
        *,
        limit: int,
        offset: int,
        skip_invalid: bool,
    ) -> tuple[HySdkMemoryPage, int]:
        """``list_memories(scene="working"|"coding")``: that scene's experiences.

        The SDK names the scene once, in ``variant``; each row is a bare
        experience, so the kind and variant are added before parsing.
        """
        rows = raw.get("experiences")
        total = raw.get("total")
        variant = raw.get("variant")
        if (
            not isinstance(rows, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or variant not in {"working", "coding"}
        ):
            raise HySdkReadError("hy_memory_list_shape_unknown")
        memories: list[HySdkMemoryRecord] = []
        skipped = 0
        for row in rows:
            try:
                if not isinstance(row, Mapping):
                    raise HySdkReadError("hy_memory_record_shape_unknown")
                record = HySdkMemoryRecord.from_sdk(
                    {**row, "kind": "experience", "variant": variant}
                )
            except HySdkReadError:
                if not skip_invalid:
                    raise
                skipped += 1
                continue
            memories.append(record)
        return (
            cls(memories=tuple(memories), total=total, limit=limit, offset=offset),
            skipped,
        )


class HySdkRawRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_id: str
    content: str
    user_id: str
    agent_id: str
    session_id: str
    source: str
    memory_at: int | None = None
    gmt_created: int | None = None
    custom: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_sdk(cls, raw: object) -> HySdkRawRecord:
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_raw_shape_unknown")
        if any(
            not isinstance(raw.get(key), str) or not str(raw[key]).strip()
            for key in ("raw_id", "content", "user_id", "agent_id", "session_id", "source")
        ):
            raise HySdkReadError("hy_memory_raw_shape_unknown")
        custom = raw.get("custom")
        if not isinstance(custom, Mapping):
            custom = {}
        try:
            return cls(
                raw_id=str(raw["raw_id"]),
                content=str(raw["content"]),
                user_id=str(raw["user_id"]),
                agent_id=str(raw["agent_id"]),
                session_id=str(raw["session_id"]),
                source=str(raw["source"]),
                memory_at=raw.get("memory_at") if isinstance(raw.get("memory_at"), int) else None,
                gmt_created=(
                    raw.get("gmt_created") if isinstance(raw.get("gmt_created"), int) else None
                ),
                custom=dict(custom),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HySdkReadError("hy_memory_raw_shape_unknown") from error


class HySdkRawHit(HySdkRawRecord):
    score: float = Field(ge=0, le=1)

    @classmethod
    def from_sdk_search(cls, raw: object) -> HySdkRawHit:
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_raw_search_shape_unknown")
        raw_id = raw.get("memory_id")
        score = raw.get("score")
        if (
            not isinstance(raw_id, str)
            or not isinstance(score, int | float)
            or isinstance(score, bool)
        ):
            raise HySdkReadError("hy_memory_raw_search_shape_unknown")
        base = HySdkRawRecord.from_sdk({**raw, "raw_id": raw_id})
        return cls(
            **base.model_dump(),
            score=max(0.0, min(1.0, float(score))),
        )


class HySdkRawPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw: tuple[HySdkRawRecord, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @classmethod
    def from_memory_list(
        cls,
        raw: object,
        *,
        limit: int,
        offset: int,
    ) -> HySdkRawPage:
        """Parse ``list_memories(layers=['l1_raw'])`` into the App raw DTO."""
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_raw_list_shape_unknown")
        bucket = raw.get("vdb")
        if not isinstance(bucket, Mapping):
            raise HySdkReadError("hy_memory_raw_list_shape_unknown")
        rows = bucket.get("memories")
        total = bucket.get("total")
        if (
            not isinstance(rows, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
        ):
            raise HySdkReadError("hy_memory_raw_list_shape_unknown")
        converted = []
        for row in rows:
            if not isinstance(row, Mapping) or row.get("layer") != "l1_raw":
                raise HySdkReadError("hy_memory_raw_list_shape_unknown")
            converted.append({**row, "raw_id": row.get("memory_id")})
        return cls(
            raw=tuple(HySdkRawRecord.from_sdk(row) for row in converted),
            total=total,
            limit=limit,
            offset=offset,
        )


class HySdkUserProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    profile: dict[str, object] | None
    elapsed_ms: float = Field(ge=0)

    @classmethod
    def from_sdk(cls, raw: object) -> HySdkUserProfile:
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_profile_shape_unknown")
        request_id = raw.get("request_id")
        profile = raw.get("profile")
        elapsed_ms = raw.get("elapsed_ms")
        if (
            not isinstance(request_id, str)
            or (profile is not None and not isinstance(profile, Mapping))
            or not isinstance(elapsed_ms, int | float)
            or isinstance(elapsed_ms, bool)
        ):
            raise HySdkReadError("hy_memory_profile_shape_unknown")
        return cls(
            request_id=request_id,
            profile=None if profile is None else dict(profile),
            elapsed_ms=float(elapsed_ms),
        )


def parse_sdk_search(raw: object, *, error_code: str) -> tuple[SemanticMemoryHit, ...]:
    rows = _reply_rows(raw, error_code)
    found: list[SemanticMemoryHit] = []
    seen: set[str] = set()
    for item in sorted(rows, key=_score, reverse=True):
        memory_id = item.get("memory_id") or item.get("id")
        if not isinstance(memory_id, str) or not memory_id or memory_id in seen:
            continue
        seen.add(memory_id)
        content = item.get("content")
        member_ids, formatting_complete = _formatted_member_ids(item)
        found.append(
            SemanticMemoryHit(
                memory_id=memory_id,
                score=_score(item),
                formatted_content=(
                    content.strip() if isinstance(content, str) and content.strip() else None
                ),
                member_ids=member_ids,
                sdk_formatted=True,
                formatting_complete=formatting_complete,
            )
        )
    return tuple(found)


class LibraryHit(NamedTuple):
    """One keyword match in a library search window, with its namespace."""

    memory_id: str
    kind: MemoryKindName
    variant: MemoryVariantName | None


def library_keyword_hits(raw: object, query: str) -> tuple[tuple[LibraryHit, ...], int]:
    """Memories in an SDK search window whose text contains every query term.

    The local SDK store has no keyword channel (Chroma's keyword search is
    empty), so search is pure vector similarity: a word in the library can
    score below the floor, and a word in none of it still scores above. The
    library search asks the SDK for its most relevant window and keeps only the
    rows that actually contain the words, most relevant first. Returns those
    hits and how many distinct rows the window held.

    A ``search(scene="all")`` window mixes chat memories with working/coding
    experiences and facts; their text is the unified ``content`` (an
    experience's task and solution, a fact's statement). The fused order is
    kept as the SDK ranked it.
    """
    terms = query.casefold().split()
    if isinstance(raw, Mapping) and raw.get("scene") == "all":
        rows = raw.get("memories")
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise HySdkReadError("hy_memory_search_shape_unknown")
        ordered: list[Mapping[str, object]] = list(rows)
    else:
        ordered = sorted(
            _reply_rows(raw, "hy_memory_search_shape_unknown"), key=_score, reverse=True
        )
    seen: set[LibraryHit] = set()
    matched: list[LibraryHit] = []
    for item in ordered:
        hit = _library_hit(item)
        if hit is None or hit in seen:
            continue
        seen.add(hit)
        tags = item.get("tags")
        parts = [item.get(key) for key in ("content", "task", "solution", "statement")] + list(
            tags if isinstance(tags, list) else ()
        )
        text = " ".join(part for part in parts if isinstance(part, str)).casefold()
        if all(term in text for term in terms):
            matched.append(hit)
    return tuple(matched), len(seen)


def _library_hit(item: Mapping[str, object]) -> LibraryHit | None:
    kind = item.get("kind")
    if kind in {"experience", "fact"}:
        variant = item.get("variant") if item.get("variant") in {"working", "coding"} else None
        variant = variant or (
            item.get("scene") if item.get("scene") in {"working", "coding"} else None
        )
        memory_id = item.get("memory_id") or item.get(
            "experience_id" if kind == "experience" else "fact_id"
        )
        if variant is None or not isinstance(memory_id, str) or not memory_id:
            return None
        return LibraryHit(
            memory_id,
            "experience" if kind == "experience" else "fact",
            "coding" if variant == "coding" else "working",
        )
    memory_id = item.get("memory_id") or item.get("id")
    if not isinstance(memory_id, str) or not memory_id:
        return None
    return LibraryHit(memory_id, "chat", None)


def parse_sdk_raw_search(raw: object) -> tuple[HySdkRawHit, ...]:
    return tuple(
        HySdkRawHit.from_sdk_search(row)
        for row in _reply_rows(raw, "hy_memory_raw_search_shape_unknown")
    )


def _epoch(value: object) -> int | None:
    """Epoch seconds from an int or an ISO-8601 string (naive means UTC)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp())
    return None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip())
    )


def _source_raw_ids(raw: Mapping[str, object]) -> tuple[str, ...]:
    custom = raw.get("custom")
    values: list[object] = []
    if isinstance(custom, Mapping):
        listed = custom.get("source_raw_memory_ids")
        if isinstance(listed, list):
            values.extend(listed)
    singular = raw.get("source_raw_memory_id")
    if isinstance(singular, str):
        values.append(singular)
    return tuple(
        dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip())
    )


def _reply_rows(raw: object, error_code: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, Mapping):
        raise HySdkReadError(error_code)
    keys = tuple(key for key in ("memories", "coding_memories") if key in raw)
    if "scene" in raw:
        scene = raw["scene"]
        if not isinstance(scene, str):
            raise HySdkReadError(error_code)
        if scene in ("normal", "chat"):
            selected = "memories"
        elif scene in ("productivity", "working"):
            selected = "coding_memories"
        else:
            raise HySdkReadError(error_code)
        if selected not in keys:
            raise HySdkReadError(error_code)
    elif len(keys) == 1:
        selected = keys[0]
    else:
        raise HySdkReadError(error_code)

    result: list[Mapping[str, object]] = []
    for key in keys:
        value = raw[key]
        if key == "memories" and isinstance(value, Mapping):
            if set(value) != {"schema", "proactive", "normal"}:
                raise HySdkReadError(error_code)
            channels = tuple(value.values())
        else:
            channels = (value,)
        for channel in channels:
            if not isinstance(channel, list) or any(
                not isinstance(row, Mapping) for row in channel
            ):
                raise HySdkReadError(error_code)
            if key == selected:
                result.extend(channel)
    return tuple(result)


def _score(memory: Mapping[str, object]) -> float:
    score = memory.get("score")
    return float(score) if isinstance(score, int | float) and not isinstance(score, bool) else 0.0


def _formatted_member_ids(memory: Mapping[str, object]) -> tuple[tuple[str, ...], bool]:
    represented: dict[str, None] = {}
    composite = False
    raw_members = memory.get("member_ids")
    if raw_members is not None:
        composite = True
        if not isinstance(raw_members, list | tuple) or any(
            not isinstance(member_id, str) or not member_id for member_id in raw_members
        ):
            return (), False
        for member_id in raw_members:
            represented.setdefault(member_id, None)
    chain = memory.get("evolution_chain")
    if chain is not None:
        composite = True
        if not isinstance(chain, list | tuple):
            return (), False
        for entry in chain:
            if not isinstance(entry, Mapping):
                return (), False
            member_id = entry.get("node_id") or entry.get("memory_id")
            if not isinstance(member_id, str) or not member_id:
                return (), False
            represented.setdefault(member_id, None)
    if memory.get("capsule_memories") is not None or memory.get("timeline") is not None:
        composite = True
        if raw_members is None:
            return (), False
    content = memory.get("content")
    if isinstance(content, str) and content.lstrip().startswith(
        ("Related memories:", "Matched memories:", "Memory timeline:")
    ):
        composite = True
        if not represented:
            return (), False
    return tuple(represented), not composite or bool(represented)
