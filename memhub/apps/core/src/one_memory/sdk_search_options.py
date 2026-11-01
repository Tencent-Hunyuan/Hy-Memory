"""SDK search parameters a host may pass through to `client.search`.

`POST /api/v2/context/search`, `POST /api/v1/recall` and the v2 ask routes
used to fix every SDK search knob, so a host that wanted a stricter relevance
floor filtered the answer itself. These fields are the public `search`
parameters of hy-memory 2.0.9 that are safe to hand a caller. Every field is
optional: absent, the route keeps exactly the call it made before.

Never exposed: `user_ids` (a route searches its one scoped `user_id`),
`embed_override` and `query_embedding` (they bypass the configured embedder),
and `request_id` (Core's own correlation id).

A caller's floor is also applied to what Core returns, not only handed to the
SDK: a hit below the floor that names its lane is dropped (`passes_floor`).
With `min_score` alone that floor covers every lane, so `min_score=0.9`
answers only results scoring at least 0.9.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

#: The SDK's `MemoryLayer` values (hy-memory 2.0.9).
MemoryLayerName = Literal[
    "l0_basic_info",
    "l1_raw",
    "l2_fact",
    "l3_summary",
    "l4_identity",
    "l5_knowledge",
    "l6_schema",
    "l7_intention",
]
MEMORY_LAYERS: tuple[str, ...] = (
    "l0_basic_info",
    "l1_raw",
    "l2_fact",
    "l3_summary",
    "l4_identity",
    "l5_knowledge",
    "l6_schema",
    "l7_intention",
)
#: The SDK's `MemoryStatus` values.
MEMORY_STATUSES: tuple[str, ...] = (
    "active",
    "superseded",
    "negated",
    "conflicted",
    "shadow",
    "archived",
)
SearchScene = Literal["chat", "working", "coding", "all"]
#: Which recall lane produced a hit, for the floor that applies to it.
SearchLane = Literal["normal", "schema", "proactive", "working", "coding", "raw"]

Score = Annotated[float, Field(ge=0.0, le=1.0)]
SourceName = Annotated[str, Field(min_length=1, max_length=128)]
ScopeId = Annotated[str, Field(min_length=1, max_length=512)]


class SdkSearchFilters(BaseModel):
    """Filters and floors every search route accepts (recall, context, ask)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Chat lane floor; with nothing more specific, every lane's.
    min_score: Score | None = None
    #: Schema (L6) lane floor; defaults to `min_score` when only that is set.
    schema_min_score: Score | None = None
    #: False skips the SDK's reranking stages for this call.
    rerank: StrictBool | None = None
    include_sources: tuple[SourceName, ...] | None = Field(default=None, max_length=64)
    exclude_sources: tuple[SourceName, ...] | None = Field(default=None, max_length=64)
    #: Unix seconds: only memories created at or after it.
    created_after: float | None = Field(default=None, ge=0)
    session_ids: tuple[ScopeId, ...] | None = Field(default=None, max_length=64)
    agent_ids: tuple[ScopeId, ...] | None = Field(default=None, max_length=64)
    #: Only these layers (pro/ultra; lite always searches raw).
    layers: tuple[MemoryLayerName, ...] | None = Field(default=None, min_length=1, max_length=8)

    def filter_kwargs(self) -> dict[str, object]:
        """The SDK keyword arguments this request sets, and only those."""
        values: dict[str, object] = {}
        for name in (
            "min_score",
            "schema_min_score",
            "rerank",
            "created_after",
        ):
            value = getattr(self, name)
            if value is not None:
                values[name] = value
        for name in ("include_sources", "exclude_sources", "session_ids", "agent_ids", "layers"):
            value = getattr(self, name)
            if value is not None:
                values[name] = list(dict.fromkeys(value))
        if self.min_score is not None and self.schema_min_score is None:
            values["schema_min_score"] = self.min_score
        return values

    def lane_floor(self, lane: SearchLane) -> float | None:
        """The caller's floor for one lane, or None when the caller set none."""
        if lane == "schema":
            return self.schema_min_score if self.schema_min_score is not None else self.min_score
        return self.min_score

    def passes_floor(self, lane: SearchLane, score: float) -> bool:
        floor = self.lane_floor(lane)
        return floor is None or score >= floor

    def search_overrides(self) -> Mapping[str, object]:
        return self.filter_kwargs()


class SdkSearchOptions(SdkSearchFilters):
    """Every SDK search knob a recall or context caller may set."""

    #: working/coding fact-layer floor (SDK default 0.30).
    working_fact_min_score: Score | None = None
    #: working/coding experience-layer floor (SDK default fact floor + 0.10).
    working_experience_min_score: Score | None = None
    reader: Literal["legacy", "hybrid"] | None = None
    #: chat (the default), working, coding, or all three fused.
    scene: SearchScene | None = None
    #: agentic spends one model call to classify and rewrite the query.
    search_mode: Literal["default", "agentic"] | None = None
    #: working/coding lanes only; `branch` needs `workspace_id`.
    workspace_id: ScopeId | None = None
    branch: ScopeId | None = None

    @model_validator(mode="after")
    def branch_needs_workspace(self) -> SdkSearchOptions:
        if self.branch is not None and self.workspace_id is None:
            raise ValueError("branch requires workspace_id")
        return self

    def search_overrides(self) -> Mapping[str, object]:
        values = self.filter_kwargs()
        for name in (
            "working_fact_min_score",
            "working_experience_min_score",
            "reader",
            "scene",
            "search_mode",
            "workspace_id",
            "branch",
        ):
            value = getattr(self, name)
            if value is not None:
                values[name] = value
        if self.min_score is not None and self.working_fact_min_score is None:
            values["working_fact_min_score"] = self.min_score
        if self.min_score is not None and self.working_experience_min_score is None:
            values["working_experience_min_score"] = self.min_score
        return values

    def lane_floor(self, lane: SearchLane) -> float | None:
        if lane in {"working", "coding"}:
            explicit = [
                value
                for value in (self.working_fact_min_score, self.working_experience_min_score)
                if value is not None
            ]
            # Facts and experiences share one score field on a fused hit, so
            # the looser of the two floors is the one Core can enforce.
            return min(explicit) if explicit else self.min_score
        return super().lane_floor(lane)


def split_csv(values: list[str] | None) -> list[str]:
    """Query values given repeated (`a&a`) or comma separated (`a,b`)."""
    found: list[str] = []
    for value in values or ():
        found.extend(part.strip() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(found))
