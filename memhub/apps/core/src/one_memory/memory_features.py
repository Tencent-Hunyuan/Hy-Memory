"""SDK retrieval and extraction features a person may turn on or off.

MemHub hard-codes none of these. Each is one `MemoryConfig` field that the SDK
reads from its config object (never from `os.environ` at call time), so
`hy_sdk_runtime.build_sdk_config` sets it per client and a client's features
are fixed for its life, like its mode. An unset feature keeps the SDK default,
so with nothing set the SDK config is byte for byte what it was before this
setting existed.

The setting is one global document in `config.json` (`memory_features`),
beside `memory_mode`; management stays global, not per user. A change
rebuilds the SDK handles the way a mode change does (#218 use count).

What is deliberately left out, and why, is in
docs/contracts/memory-features-v1.md. In short: flags the SDK reads straight
from the process environment (the System 2 schema dedup/compaction lane, the
balanced-catalog and specific-supplement lanes, the hybrid reader's
`MEMORY_HYBRID_V2_*` knobs) cannot be scoped to one client; flags MemHub never
exercises (query rewrite needs `queries=[...]`, session narratives need
`finalize_session`); flags that are dangerous or internal (graph write, local
cross-encoder download, access write-back); and everything MemHub already
controls elsewhere (pipeline log, trace, memory operations, telemetry, mode).

Beside the on/off features there is one numeric parameter,
`intention_dedup_threshold` (`PARAMETERS`): the cosine similarity at which the
read-time intention dedup treats two intentions as copies. It is set, stored,
reset and keyed exactly like a feature.

No SDK import here: only `hy_sdk_runtime` and `hy_sdk_migration` import it.
`config_state` imports this module, so it imports nothing from there.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

MemoryFeatureName = Literal[
    "graph_rerank",
    "same_series_timeline",
    "episode_recall",
    "session_episodes",
    "episode_summary",
    "intention",
    "intention_dedup",
    "few_shot",
]
#: Numeric settings beside the on/off features.
MemoryParameterName = Literal["intention_dedup_threshold"]
#: Every key of the setting: a feature or a parameter.
MemorySettingName = MemoryFeatureName | MemoryParameterName
#: Read features change search; write features change what extraction stores.
FeatureSide = Literal["read", "write"]
#: none: no extra call. extra_query: one more store query per search.
#: llm_call: extra generation-model calls. prompt_tokens: no extra call, but
#: a longer extraction prompt.
FeatureCost = Literal["none", "extra_query", "llm_call", "prompt_tokens"]
#: Why an enabled feature does nothing right now (`active: false`).
FeatureReason = Literal[
    "disabled",
    "requires_pro",
    "requires_ultra",
    "requires_graph_rerank",
    "requires_intention_dedup",
]
#: A feature is on or off; a parameter is a number.
type SettingValue = bool | float
#: A client's features as a hashable key: sorted (name, value) pairs, set ones only.
type FeatureOverrides = tuple[tuple[str, SettingValue], ...]

#: The range a person may set `intention_dedup_threshold` to. With dedup on,
#: the SDK deletes every intention at or above it from the store at read time,
#: which cannot be undone; below 0.8 the embedding models MemHub uses score
#: different plans on one topic as that close, so a lower value would delete
#: intentions that are not copies. 1.0 keeps only exact copies' dedup.
INTENTION_DEDUP_THRESHOLD_MIN = 0.8
INTENTION_DEDUP_THRESHOLD_MAX = 1.0

_MODE_RANK: dict[str, int] = {"lite": 0, "pro": 1, "ultra": 2}


@dataclass(frozen=True)
class FeatureSpec:
    name: MemoryFeatureName
    #: `MemoryConfig` section and field the value is written to.
    section: Literal["graph_store", "recall", "llm"]
    field: str
    #: The SDK's own env var for the same field; only its default comes from it.
    env: str
    side: FeatureSide
    #: Lowest mode the feature acts in.
    requires_mode: Literal["pro", "ultra"]
    #: Another feature that must be active for this one to act.
    requires_feature: MemoryFeatureName | None
    cost: FeatureCost
    description: str

    @property
    def sdk_field(self) -> str:
        return f"{self.section}.{self.field}"


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name="graph_rerank",
        section="graph_store",
        field="graph_rerank_enabled",
        env="MEMORY_GRAPH_RERANK_ENABLED",
        side="read",
        requires_mode="ultra",
        requires_feature=None,
        cost="extra_query",
        description=(
            "Usage A: propagate support over knowledge-graph edges inside the "
            "recall pool, so memories linked to strong hits rank higher. Needs "
            "the graph store (ultra). No model call; one graph query per search."
        ),
    ),
    FeatureSpec(
        name="same_series_timeline",
        section="recall",
        field="same_series_timeline_enabled",
        env="MEMORY_SAME_SERIES_TIMELINE_ENABLED",
        side="read",
        requires_mode="ultra",
        requires_feature="graph_rerank",
        cost="none",
        description=(
            "Keep memories joined by a same-series graph edge together, in time "
            "order, in the results. Uses the edges graph rerank reads, so it "
            "needs graph_rerank (ultra). No model call."
        ),
    ),
    FeatureSpec(
        name="episode_recall",
        section="recall",
        field="episode_recall_enabled",
        env="MEMORY_EPISODE_RECALL_ENABLED",
        side="read",
        requires_mode="pro",
        requires_feature=None,
        cost="extra_query",
        description=(
            "Usage B: pull in memories from the same episode as a hit and score "
            "them on tag-keyword overlap, content overlap and time affinity. No "
            "graph needed (pro or ultra). No model call; one store query per "
            "search. Helps only memories that carry an episode_id."
        ),
    ),
    FeatureSpec(
        name="session_episodes",
        section="recall",
        field="session_episode_enabled",
        env="MEMORY_SESSION_EPISODE_ENABLED",
        side="write",
        requires_mode="pro",
        requires_feature=None,
        cost="none",
        description=(
            "Episode write side: memories extracted from one conversation share "
            "one stable episode_id, instead of the default rule (a new episode "
            "after 30 minutes idle or 50 memories). No model call."
        ),
    ),
    FeatureSpec(
        name="episode_summary",
        section="recall",
        field="episode_summary_enabled",
        env="MEMORY_EPISODE_SUMMARY_ENABLED",
        side="write",
        requires_mode="pro",
        requires_feature=None,
        cost="llm_call",
        description=(
            "When an episode closes (the idle-gap rule), summarize it once into "
            "an episode memory. One model call per closed episode of 3 or more "
            "memories."
        ),
    ),
    FeatureSpec(
        name="intention",
        section="llm",
        field="enable_intention",
        env="MEMORY_ENABLE_INTENTION",
        side="write",
        requires_mode="pro",
        requires_feature=None,
        cost="none",
        description=(
            "Extract the user's stated plans and intentions (L7) along with "
            "facts. On by default; off drops them from the extraction prompt. "
            "No extra call."
        ),
    ),
    FeatureSpec(
        name="intention_dedup",
        section="recall",
        field="intention_dedup_enabled",
        env="MEMORY_INTENTION_DEDUP_ENABLED",
        side="read",
        requires_mode="pro",
        requires_feature=None,
        cost="none",
        description=(
            "Treat same-day intentions (L7) with the same expiry and a cosine "
            "similarity at or above intention_dedup_threshold as copies: a "
            "search returns the earliest and deletes the others from the store. "
            "On by default; off hides, drops and deletes no intention, at read "
            "time or in a write batch. No extra call."
        ),
    ),
    FeatureSpec(
        name="few_shot",
        section="llm",
        field="few_shot_enabled",
        env="MEMORY_FEW_SHOT_ENABLED",
        side="write",
        requires_mode="pro",
        requires_feature=None,
        cost="prompt_tokens",
        description=(
            "Append worked examples to the extraction and reconcile prompts. "
            "Can help a weaker model; no extra call, but every extraction "
            "sends more input tokens."
        ),
    ),
)
FEATURE_NAMES: tuple[MemoryFeatureName, ...] = tuple(spec.name for spec in FEATURES)
FEATURES_BY_NAME: dict[str, FeatureSpec] = {spec.name: spec for spec in FEATURES}


@dataclass(frozen=True)
class ParameterSpec:
    """A numeric setting; it acts only while `requires_feature` acts."""

    name: MemoryParameterName
    section: Literal["recall"]
    field: str
    env: str
    minimum: float
    maximum: float
    requires_feature: MemoryFeatureName
    description: str

    @property
    def sdk_field(self) -> str:
        return f"{self.section}.{self.field}"


PARAMETERS: tuple[ParameterSpec, ...] = (
    ParameterSpec(
        name="intention_dedup_threshold",
        section="recall",
        field="intention_dedup_threshold",
        env="MEMORY_INTENTION_DEDUP_THRESHOLD",
        minimum=INTENTION_DEDUP_THRESHOLD_MIN,
        maximum=INTENTION_DEDUP_THRESHOLD_MAX,
        requires_feature="intention_dedup",
        description=(
            "Cosine similarity at or above which intention_dedup treats two "
            "intentions as copies. Higher keeps more. The SDK default is 0.95 "
            "(MEMORY_DEDUP_THRESHOLD also sets it when its own env var is unset)."
        ),
    ),
)
PARAMETER_NAMES: tuple[MemoryParameterName, ...] = tuple(spec.name for spec in PARAMETERS)
PARAMETERS_BY_NAME: dict[str, ParameterSpec] = {spec.name: spec for spec in PARAMETERS}
#: Every key of the setting, features first, each in its catalog order.
SETTING_NAMES: tuple[MemorySettingName, ...] = (*FEATURE_NAMES, *PARAMETER_NAMES)
SETTINGS_BY_NAME: dict[str, FeatureSpec | ParameterSpec] = {
    **FEATURES_BY_NAME,
    **PARAMETERS_BY_NAME,
}


class MemoryFeatures(BaseModel):
    """`config.json` `memory_features`, and the `PUT` body.

    Every key is optional; absent (or null) means the SDK default. In a `PUT`
    an absent key is left as it is and null resets it. Unknown keys,
    non-boolean feature values and a threshold that is not a number in its
    range are refused (422).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    graph_rerank: StrictBool | None = None
    same_series_timeline: StrictBool | None = None
    episode_recall: StrictBool | None = None
    session_episodes: StrictBool | None = None
    episode_summary: StrictBool | None = None
    intention: StrictBool | None = None
    intention_dedup: StrictBool | None = None
    few_shot: StrictBool | None = None
    intention_dedup_threshold: float | None = Field(
        default=None,
        ge=INTENTION_DEDUP_THRESHOLD_MIN,
        le=INTENTION_DEDUP_THRESHOLD_MAX,
        allow_inf_nan=False,
    )

    def overrides(self) -> FeatureOverrides:
        """The features a person set, as the client key."""
        return feature_overrides(
            {name: value for name, value in self.model_dump().items() if value is not None}
        )

    def merged(self, update: MemoryFeatures) -> MemoryFeatures:
        """This document with the keys `update` names: a value sets, null resets."""
        values = self.model_dump()
        for name in update.model_fields_set:
            values[name] = getattr(update, name)
        return MemoryFeatures(**{key: value for key, value in values.items() if value is not None})


def feature_overrides(values: Mapping[str, SettingValue]) -> FeatureOverrides:
    unknown = set(values) - set(SETTINGS_BY_NAME)
    if unknown:
        raise ValueError("unknown_memory_feature")
    return tuple(sorted((name, _setting_value(name, value)) for name, value in values.items()))


def _setting_value(name: str, value: SettingValue) -> SettingValue:
    parameter = PARAMETERS_BY_NAME.get(name)
    if parameter is None:
        return bool(value)
    if isinstance(value, bool) or not parameter.minimum <= float(value) <= parameter.maximum:
        raise ValueError("invalid_memory_feature_value")
    return float(value)


def effective_features(
    overrides: FeatureOverrides, defaults: Mapping[str, SettingValue]
) -> dict[str, SettingValue]:
    """Every setting's value: what a person set, else the SDK default."""
    values: dict[str, SettingValue] = {
        spec.name: bool(defaults.get(spec.name, False)) for spec in FEATURES
    }
    for parameter in PARAMETERS:
        if parameter.name in defaults:
            values[parameter.name] = float(defaults[parameter.name])
    values.update(dict(overrides))
    return values


def feature_activity(
    name: str, effective: Mapping[str, SettingValue], mode: str
) -> FeatureReason | None:
    """None when the feature acts in this mode; else why it does not.

    A parameter acts exactly when the feature it tunes acts.
    """
    parameter = PARAMETERS_BY_NAME.get(name)
    if parameter is not None:
        reason = feature_activity(parameter.requires_feature, effective, mode)
        return "requires_intention_dedup" if reason == "disabled" else reason
    spec = FEATURES_BY_NAME[name]
    if not effective.get(name, False):
        return "disabled"
    if _MODE_RANK.get(mode, 0) < _MODE_RANK[spec.requires_mode]:
        return "requires_ultra" if spec.requires_mode == "ultra" else "requires_pro"
    if spec.requires_feature is not None and (
        feature_activity(spec.requires_feature, effective, mode) is not None
    ):
        return "requires_graph_rerank"
    return None


def non_default_features(
    overrides: FeatureOverrides, defaults: Mapping[str, SettingValue]
) -> dict[str, SettingValue]:
    """The settings whose value differs from the SDK default, for status."""
    baseline = effective_features((), defaults)
    return {
        name: value
        for name, value in effective_features(overrides, defaults).items()
        if value != baseline.get(name)
    }
