"""Validated records and response parsers for public HY SDK operations."""

from __future__ import annotations

from collections.abc import Mapping

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


class HySdkMemoryRecord(BaseModel):
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

    @classmethod
    def from_sdk(cls, raw: object) -> HySdkMemoryRecord:
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_record_shape_unknown")
        if any(
            not isinstance(raw.get(key), str) or not str(raw[key]).strip()
            for key in ("memory_id", "content")
        ):
            raise HySdkReadError("hy_memory_record_shape_unknown")
        try:
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
                gmt_created=(
                    raw.get("gmt_created") if isinstance(raw.get("gmt_created"), int) else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HySdkReadError("hy_memory_record_shape_unknown") from error


class HySdkMemoryPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memories: tuple[HySdkMemoryRecord, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @classmethod
    def from_sdk(cls, raw: object, *, limit: int, offset: int) -> HySdkMemoryPage:
        if not isinstance(raw, Mapping):
            raise HySdkReadError("hy_memory_list_shape_unknown")
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
        for row in rows:
            record = HySdkMemoryRecord.from_sdk(row)
            deduplicated.setdefault(record.memory_id, record)
        return cls(
            memories=tuple(deduplicated.values()),
            total=total,
            limit=limit,
            offset=offset,
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


def parse_sdk_raw_search(raw: object) -> tuple[HySdkRawHit, ...]:
    return tuple(
        HySdkRawHit.from_sdk_search(row)
        for row in _reply_rows(raw, "hy_memory_raw_search_shape_unknown")
    )


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
