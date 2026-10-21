"""Versioned read-only context contract; kinds select paths, never grant access."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ContextKind = Literal["raw", "memory"]
ContextSearchChannel = Literal["lexical", "semantic"]
ContextSemanticStatus = Literal["not_searched", "completed", "failed", "timed_out"]
ContextLimit = Literal["count_budget", "character_budget", "timeout_budget", "metadata_budget"]
ContextReason = Literal[
    "not_requested",
    "permission_required",
    "source_revoked",
    "read_unavailable",
    "timeout_budget",
    "evidence_changed",
    "memory_changed",
    "lexical_only",
]


class ContextSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    kinds: tuple[ContextKind, ...] = Field(default=("raw", "memory"), min_length=1, max_length=2)
    raw_source_ids: (
        tuple[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")], ...] | None
    ) = Field(default=None, min_length=1, max_length=32)
    limit: int = Field(default=8, ge=1, le=20, strict=True)
    character_budget: int = Field(default=4000, ge=200, le=20000, strict=True)
    timeout_ms: int = Field(default=2000, ge=50, le=30000, strict=True)

    @model_validator(mode="after")
    def validate_selection(self) -> "ContextSearchRequest":
        if not self.query.strip():
            raise ValueError("query must not be blank")
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("kinds must be unique")
        if self.raw_source_ids is not None:
            if "raw" not in self.kinds:
                raise ValueError("raw_source_ids requires the raw path")
            if len(set(self.raw_source_ids)) != len(self.raw_source_ids):
                raise ValueError("raw_source_ids must be unique")
        return self


class ContextMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=1)
    role: str = Field(max_length=32)
    written_by: str = Field(max_length=32)
    created_at: datetime | None
    text: str = Field(max_length=20000)
    truncated: bool = False


class RawContextItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["raw"] = "raw"
    source_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=512)
    anchor_evidence_id: str = Field(min_length=1, max_length=512)
    messages: tuple[ContextMessage, ...] = Field(min_length=1, max_length=3)
    truncated: bool = False


class MemoryContextItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["memory"] = "memory"
    memory_id: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=1)
    content: str = Field(max_length=20000)
    memory_kind: str = Field(max_length=64)
    updated_at: datetime
    evidence_ids: tuple[Annotated[str, Field(max_length=512)], ...] = Field(max_length=128)
    #: Which lanes actually produced this item. Defaults preserve callers that
    #: construct a memory item from an already-decided result.
    matched_by: tuple[Literal["lexical", "governed_semantic"], ...] = ()
    truncated: bool = False


ContextItem = Annotated[RawContextItem | MemoryContextItem, Field(discriminator="kind")]


class SdkRawContextItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["raw"] = "raw"
    raw_id: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=20000)
    source: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=512)
    memory_at: datetime | None = None
    score: float = Field(ge=0, le=1)
    truncated: bool = False


class SdkMemoryContextItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["memory"] = "memory"
    memory_id: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=20000)
    layer: str = Field(max_length=64)
    status: str = Field(max_length=32)
    source: str | None = Field(default=None, max_length=128)
    updated_at: datetime | None = None
    score: float = Field(ge=0, le=1)
    truncated: bool = False


SdkContextItem = Annotated[
    SdkRawContextItem | SdkMemoryContextItem,
    Field(discriminator="kind"),
]


class SdkContextSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    request_id: str = Field(min_length=1, max_length=64)
    state: Literal["grounded", "empty", "partial", "unavailable"]
    items: tuple[SdkContextItem, ...] = Field(max_length=20)
    used_characters: int = Field(ge=0, le=20000)
    truncated: bool
    total_ms: float = Field(ge=0)


class ContextLaneResult(BaseModel):
    """Internal bounded adapter result; the aggregator applies shared budgets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok", "empty", "not_requested", "not_authorized", "unavailable", "degraded"]
    reason: ContextReason | None = None
    items: tuple[ContextItem, ...] = Field(default=(), max_length=21)
    truncated_by: tuple[ContextLimit, ...] = ()
    searched_channels: tuple[ContextSearchChannel, ...] = ()
    semantic_status: ContextSemanticStatus = "not_searched"
    index_complete: bool | None = None
    timed_out: bool = False
    # Internal diagnostics only. ContextSearchResponse exposes ContextPath,
    # so these never cross the host protocol or reveal exception messages.
    failure_site: str | None = Field(default=None, max_length=64)
    error_class: str | None = Field(default=None, max_length=64)


class ContextPath(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok", "empty", "not_requested", "not_authorized", "unavailable", "degraded"]
    reason: ContextReason | None = None
    elapsed_ms: float = Field(ge=0)
    returned_count: int = Field(ge=0, le=20)
    truncated: bool
    truncated_by: tuple[ContextLimit, ...] = ()


class ContextPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    raw: ContextPath
    memory: ContextPath


class ContextSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=64)
    state: Literal["grounded", "empty", "partial", "unavailable"]
    paths: ContextPaths
    items: tuple[ContextItem, ...] = Field(max_length=20)
    used_characters: int = Field(ge=0, le=20000)
    truncated: bool
    total_ms: float = Field(ge=0)
    adoption: Literal["unknown"] = "unknown"
