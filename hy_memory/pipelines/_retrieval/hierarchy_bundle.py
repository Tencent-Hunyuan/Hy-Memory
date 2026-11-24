"""Query-shape-free selection for hierarchical L2 bundles.

This module intentionally does not inspect question words or regex patterns.
Callers provide semantic scores and write-time facets; the selector decides
which structural node is useful and which canonical L2 members add evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from ...utils.async_executor import to_thread_guarded
from .ner import (
    memory_matches_day_hints,
    memory_matches_time_hints,
    query_day_hints,
    query_time_hints,
)


@dataclass(frozen=True)
class HierarchyNodeCandidate:
    topic_id: str
    subject: str
    path: Tuple[str, ...]
    semantic_score: float
    cohesion: float = 1.0
    relation_confidence: float = 1.0
    member_count: int = 1
    child_count: int = 0
    node_type: str = "facet"
    node_types: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def depth(self) -> int:
        return len(self.path)


@dataclass(frozen=True)
class BundleMemberCandidate:
    memory_id: str
    relevance: float
    relation_confidence: float = 1.0
    token_count: int = 0
    child_topic_id: str = ""
    semantic_units: FrozenSet[str] = field(default_factory=frozenset)
    subject_consistent: bool = True
    entity_conflict: bool = False
    time_conflict: bool = False
    state_conflict: bool = False


@dataclass(frozen=True)
class HierarchySelection:
    node: Optional[HierarchyNodeCandidate]
    utility: float
    mode: str


def _unit_interval(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def estimate_tokens_no_io(text: str) -> int:
    """Conservative multilingual token estimate without loading a tokenizer."""
    value = str(text or "")
    if not value:
        return 0
    ascii_count = sum(1 for char in value if ord(char) < 128)
    non_ascii_count = len(value) - ascii_count
    return max(1, math.ceil(ascii_count / 4.0) + non_ascii_count)


def cosine(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if not left_norm or not right_norm:
        return None
    return dot / (left_norm * right_norm)


def hierarchy_node_utility(candidate: HierarchyNodeCandidate) -> float:
    """Score relevance after discounting broad, internally weak containers.

    Depth is not rewarded directly. A narrow node wins because its members are
    coherent and its semantic label fits the query, not merely because it sits
    deeper in a tree. The logarithmic breadth penalty is scale-aware and avoids
    treating a 70-member `sport` node like a 4-member `swimming` facet.
    """
    semantic = _unit_interval(candidate.semantic_score)
    cohesion = _unit_interval(candidate.cohesion)
    confidence = _unit_interval(candidate.relation_confidence)
    breadth = max(1, int(candidate.member_count))
    breadth_penalty = 1.0 / math.sqrt(1.0 + math.log2(breadth))
    return semantic * math.sqrt(cohesion * confidence) * breadth_penalty


def select_hierarchy_node(
    candidates: Sequence[HierarchyNodeCandidate],
    *,
    query_subjects: Iterable[str] = (),
) -> HierarchySelection:
    """Select one subject-consistent node using only semantic/structural data."""
    subjects = {str(value).strip().casefold() for value in query_subjects if value}
    eligible = [
        candidate for candidate in candidates
        if not subjects or candidate.subject.strip().casefold() in subjects
    ]
    if not eligible:
        return HierarchySelection(node=None, utility=0.0, mode="none")
    ranked = sorted(
        ((hierarchy_node_utility(candidate), candidate) for candidate in eligible),
        key=lambda pair: (-pair[0], -pair[1].depth, pair[1].topic_id),
    )
    utility, node = ranked[0]
    # Broad Topic nodes only route/sample, even when current data happens to
    # make them leaves. Facet is dense only at a precise leaf. Series/Episode
    # are explicit bounded structures and may gather complementary evidence.
    node_type = str(node.node_type or "facet").casefold()
    mode = (
        "representative"
        if node_type == "topic" or (node_type == "facet" and node.child_count > 0)
        else "dense"
    )
    return HierarchySelection(node=node, utility=utility, mode=mode)


def hierarchy_node_document(candidate: HierarchyNodeCandidate) -> str:
    """Render structural semantics for the same fixed query/document scorer."""
    types = list(candidate.node_types)
    if len(types) != len(candidate.path):
        types = ["topic"] + ["facet"] * max(0, len(candidate.path) - 1)
        if types:
            types[-1] = candidate.node_type or types[-1]
    typed_path = " > ".join(
        f"{kind}: {name}" for name, kind in zip(candidate.path, types)
    )
    return f"subject: {candidate.subject}; {typed_path}"


def hierarchy_node_beam(
    candidates: Sequence[HierarchyNodeCandidate],
    *,
    node_scores: Dict[str, float],
    limit: int,
) -> List[HierarchySelection]:
    """Keep a small semantic beam; type controls expansion mode, not query form."""
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -float(node_scores.get(candidate.topic_id, float("-inf"))),
            -hierarchy_node_utility(candidate),
            candidate.topic_id,
        ),
    )[:max(1, int(limit))]
    return [
        HierarchySelection(
            node=candidate,
            utility=hierarchy_node_utility(candidate),
            mode=(
                "representative"
                if candidate.node_type == "topic"
                or (candidate.node_type == "facet" and candidate.child_count > 0)
                else "dense"
            ),
        )
        for candidate in ranked
    ]


def _member_utility(candidate: BundleMemberCandidate) -> float:
    raw = float(candidate.relevance)
    if raw >= 0:
        relevance = 1.0 / (1.0 + math.exp(-min(raw, 60.0)))
    else:
        relevance = math.exp(max(raw, -60.0)) / (1.0 + math.exp(max(raw, -60.0)))
    return relevance * _unit_interval(candidate.relation_confidence)


def select_bundle_members(
    candidates: Sequence[BundleMemberCandidate],
    *,
    mode: str,
    max_members: int = 8,
    token_budget: int = 400,
) -> List[BundleMemberCandidate]:
    """Choose bounded, non-conflicting L2s by marginal evidence contribution.

    `representative` mode permits at most one member per child subtree. `dense`
    mode may take several members from one precise leaf. In either mode a member
    must add a previously unseen semantic unit, unless it is the first anchor.
    This prevents a large group of near-duplicates from consuming the bundle.
    """
    if max_members <= 0 or token_budget <= 0:
        return []
    eligible = [
        candidate for candidate in candidates
        if candidate.subject_consistent
        and not candidate.entity_conflict
        and not candidate.time_conflict
        and not candidate.state_conflict
        and _member_utility(candidate) > 0.0
    ]
    ranked = sorted(
        eligible,
        key=lambda item: (-_member_utility(item), item.memory_id),
    )
    selected: List[BundleMemberCandidate] = []
    covered: set[str] = set()
    used_children: set[str] = set()
    used_tokens = 0
    for candidate in ranked:
        cost = max(1, int(candidate.token_count or 1))
        if used_tokens + cost > token_budget:
            continue
        if mode == "representative" and candidate.child_topic_id:
            if candidate.child_topic_id in used_children:
                continue
        new_units = set(candidate.semantic_units) - covered
        if selected and not new_units:
            continue
        selected.append(candidate)
        covered.update(candidate.semantic_units)
        if candidate.child_topic_id:
            used_children.add(candidate.child_topic_id)
        used_tokens += cost
        if len(selected) >= max_members:
            break
    return selected


def _descendant_members(
    topic_id: str,
    children: Dict[str, List[str]],
    direct_members: Dict[str, set[str]],
    memo: Dict[str, set[str]],
    visiting: Optional[set[str]] = None,
) -> set[str]:
    if topic_id in memo:
        return memo[topic_id]
    visiting = visiting or set()
    if topic_id in visiting:
        return set()
    visiting.add(topic_id)
    members = set(direct_members.get(topic_id, set()))
    for child_id in children.get(topic_id, []):
        members.update(_descendant_members(
            child_id, children, direct_members, memo, visiting,
        ))
    visiting.remove(topic_id)
    memo[topic_id] = members
    return members


def _first_child_below(
    selected_id: str, leaf_id: str, parent_by_child: Dict[str, str],
) -> str:
    current = leaf_id
    previous = leaf_id
    seen = set()
    while current and current not in seen:
        if current == selected_id:
            return previous if previous != selected_id else selected_id
        seen.add(current)
        previous = current
        current = parent_by_child.get(current, "")
    return ""


def _ancestor_topic_ids(
    memory_ids: Sequence[str],
    leaf_by_member: Dict[str, List[str]],
    parent_by_child: Dict[str, str],
) -> set[str]:
    """Return every structural node on the explicit paths of normal anchors."""
    out: set[str] = set()
    for memory_id in memory_ids:
        for leaf_id in leaf_by_member.get(str(memory_id), []):
            current = leaf_id
            seen = set()
            while current and current not in seen:
                seen.add(current)
                out.add(current)
                current = parent_by_child.get(current, "")
    return out


def _round_robin_unique(
    groups: Sequence[Sequence[str]], *, limit: int,
) -> List[str]:
    """Interleave node-local pools under one hard global candidate budget."""
    if limit <= 0:
        return []
    out: List[str] = []
    seen = set()
    max_width = max((len(group) for group in groups), default=0)
    for index in range(max_width):
        for group in groups:
            if index >= len(group):
                continue
            memory_id = str(group[index])
            if not memory_id or memory_id in seen:
                continue
            seen.add(memory_id)
            out.append(memory_id)
            if len(out) >= limit:
                return out
    return out


def _semantic_units(node: Any, topic_path: Sequence[str]) -> FrozenSet[str]:
    custom = getattr(node, "custom", None) or {}
    facet = custom.get("event_facet") or {}
    units = set()
    for key in ("action",):
        value = str(facet.get(key) or "").strip().casefold()
        if value:
            units.add(f"{key}:{value}")
    for key in ("objects", "locations"):
        for value in facet.get(key) or []:
            text = str(value or "").strip().casefold()
            if text:
                units.add(f"{key}:{text}")
    when = getattr(node, "memory_at", None)
    if when is not None:
        try:
            units.add(f"time:{when.date().isoformat()}")
        except AttributeError:
            units.add(f"time:{str(when)}")
    if not units and topic_path:
        units.add(f"topic:{str(topic_path[-1]).casefold()}")
    return frozenset(units)


def member_has_subject_assignment(node: Any, subject: str) -> bool:
    """Check structural membership, including multi-participant memories."""
    from ...structure import normalize_topic_assignments

    wanted = str(subject or "").strip().casefold()
    if not wanted:
        return False
    custom = getattr(node, "custom", None) or {}
    assignments = normalize_topic_assignments(custom.get("topic_assignments"))
    if assignments:
        return any(
            str(assignment.get("subject") or "").strip().casefold() == wanted
            for assignment in assignments
        )
    # Legacy memories predate multi-subject assignments. Preserve their former
    # single-subject behavior, but never promote a bare participants list into
    # structural membership because co-occurrence alone is insufficient.
    facet = custom.get("event_facet") or {}
    return str(facet.get("subject") or "").strip().casefold() == wanted


def _bundle_from_items(
    items: Sequence[Dict[str, Any]], selection: HierarchySelection,
) -> Dict[str, Any]:
    anchor = items[0]
    return {
        **anchor,
        "source": "hierarchy_bundle",
        "_bundle_items": list(items),
        "_hierarchy_topic_id": selection.node.topic_id,
        "_hierarchy_path": list(selection.node.path),
        "_hierarchy_mode": selection.mode,
    }


def _contains_entity(query: str, value: str) -> bool:
    value = str(value or "").strip()
    if not query or not value:
        return False
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", query, re.I) is not None


def hierarchy_query_subjects(
    query: str,
    topics: Sequence[Dict[str, Any]],
    *,
    owner_name: str = "",
) -> List[str]:
    """Resolve only explicit subject identity, never interrogative syntax."""
    matched = []
    owner_mentioned = _contains_entity(query, owner_name)
    for topic in topics:
        subject = str(topic.get("subject") or "").strip()
        if not subject:
            continue
        normalized = subject.casefold()
        is_owner_alias = normalized in {"the user", "user", "用户"}
        if _contains_entity(query, subject) or (is_owner_alias and owner_mentioned):
            if normalized not in matched:
                matched.append(normalized)
    return matched


_QUERY_STATE_TERMS = {
    "planned": ("planned", "planning", "plans", "intends", "intended", "打算", "计划", "即将"),
    "ongoing": ("ongoing", "currently", "in progress", "正在", "仍在"),
    "completed": ("completed", "finished", "already done", "已完成", "完成了", "结束了"),
}


def query_state_constraints(query: str) -> FrozenSet[str]:
    """Extract explicit lifecycle states without inspecting question form."""
    text = (query or "").casefold()
    states = {
        state for state, terms in _QUERY_STATE_TERMS.items()
        if any(_contains_entity(text, term) for term in terms)
    }
    return frozenset(states)


def member_conflicts(
    query: str,
    node: Any,
    *,
    requested_locations: FrozenSet[str] = frozenset(),
    requested_objects: FrozenSet[str] = frozenset(),
    requested_states: FrozenSet[str] = frozenset(),
) -> Tuple[bool, bool, bool]:
    """Return entity/time/state conflicts only when both sides are explicit."""
    custom = getattr(node, "custom", None) or {}
    facet = custom.get("event_facet") or {}
    locations = {
        str(value).strip().casefold() for value in facet.get("locations") or []
        if str(value).strip()
    }
    objects = {
        str(value).strip().casefold() for value in facet.get("objects") or []
        if str(value).strip()
    }
    entity_conflict = bool(
        (requested_locations and locations and requested_locations.isdisjoint(locations))
        or (requested_objects and objects and requested_objects.isdisjoint(objects))
    )

    day_hints = query_day_hints(query)
    month_hints = query_time_hints(query)
    memory_at = getattr(node, "memory_at", None)
    content = str(getattr(node, "content", "") or "")
    candidate_day_hints = query_day_hints(content)
    candidate_month_hints = query_time_hints(content)
    candidate_has_time = bool(
        memory_at is not None or candidate_day_hints or candidate_month_hints
    )
    time_conflict = False
    if day_hints and candidate_has_time:
        time_conflict = not memory_matches_day_hints(memory_at, day_hints)
        if memory_at is None:
            time_conflict = set(day_hints).isdisjoint(candidate_day_hints)
    elif month_hints and candidate_has_time:
        time_conflict = not memory_matches_time_hints(
            content, month_hints, memory_at=memory_at,
        )

    state = str(facet.get("state") or "unknown").strip().casefold()
    state_conflict = bool(
        requested_states and state not in {"", "unknown"}
        and state not in requested_states
    )
    return entity_conflict, time_conflict, state_conflict


async def build_hierarchy_bundle_candidate(
    *,
    graph_store,
    vector_store,
    isolation_key: str,
    query: str,
    query_embedding: Sequence[float],
    query_subjects: Sequence[str] = (),
    anchor_ids: Sequence[str] = (),
    required_anchor_ids: Sequence[str] = (),
    owner_name: str = "",
    scorer: Callable[[str, List[str]], Optional[List[float]]],
    text_fn: Callable[[Dict[str, Any]], str],
    run_scorer=None,
    topic_limit: int = 12,
    node_beam_limit: int = 4,
    member_pool: int = 48,
    max_members: int = 8,
    token_budget: int = 400,
    allowed_node_types: Sequence[str] = (),
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Build one hierarchy bundle candidate without inspecting query syntax."""
    info: Dict[str, Any] = {"enabled": True, "applied": False}
    if not query_embedding:
        return None, {**info, "reason": "missing_embedding"}
    topics = await graph_store.get_hierarchy_topics(isolation_key)
    memberships = await graph_store.get_hierarchy_memberships(isolation_key)
    if not topics or not memberships:
        return None, {**info, "reason": "empty_hierarchy"}
    all_topics_by_id = {
        str(topic.get("topic_id") or ""): topic for topic in topics
        if topic.get("topic_id")
    }
    matched_subjects = list(query_subjects) or hierarchy_query_subjects(
        query, topics, owner_name=owner_name,
    )
    subject_source = "query"
    if not matched_subjects and anchor_ids:
        anchor_set = {str(memory_id) for memory_id in anchor_ids if memory_id}
        anchor_topic_ids_all = {
            str(relation.get("topic_id") or "")
            for relation in memberships
            if str(relation.get("node_id") or "") in anchor_set
        }
        anchor_subjects = {
            str(all_topics_by_id[topic_id].get("subject") or "").strip().casefold()
            for topic_id in anchor_topic_ids_all
            if topic_id in all_topics_by_id
            and str(all_topics_by_id[topic_id].get("subject") or "").strip()
        }
        if len(anchor_subjects) == 1:
            matched_subjects = list(anchor_subjects)
            subject_source = "anchor_consensus"
    if not matched_subjects:
        return None, {**info, "reason": "no_subject_consensus"}

    async def score_documents(documents: List[str]):
        if run_scorer is not None:
            return await run_scorer(scorer, query or "", documents)
        return await to_thread_guarded(scorer, query or "", documents)

    topics_by_id = {
        str(topic.get("topic_id") or ""): topic for topic in topics
        if topic.get("topic_id")
        and str(topic.get("subject") or "").strip().casefold() in {
            subject.casefold() for subject in matched_subjects
        }
    }
    children: Dict[str, List[str]] = {}
    parent_by_child: Dict[str, str] = {}
    for topic_id, topic in topics_by_id.items():
        parent_id = str(topic.get("parent_id") or "")
        if parent_id:
            children.setdefault(parent_id, []).append(topic_id)
            parent_by_child[topic_id] = parent_id
    direct_members: Dict[str, set[str]] = {}
    leaf_by_member: Dict[str, List[str]] = {}
    for relation in memberships:
        memory_id = str(relation.get("node_id") or "")
        topic_id = str(relation.get("topic_id") or "")
        if memory_id and topic_id in topics_by_id:
            direct_members.setdefault(topic_id, set()).add(memory_id)
            leaf_by_member.setdefault(memory_id, []).append(topic_id)

    all_member_ids = sorted(leaf_by_member)
    embeddings = await vector_store.get_embeddings(all_member_ids)
    subtree_cache: Dict[str, set[str]] = {}
    node_candidates: List[HierarchyNodeCandidate] = []
    for topic_id, topic in topics_by_id.items():
        topic_embedding = topic.get("embedding") or []
        semantic_score = cosine(query_embedding, topic_embedding)
        semantic_score = max(0.0, float(semantic_score or 0.0))
        member_ids = _descendant_members(
            topic_id, children, direct_members, subtree_cache,
        )
        member_sims = [
            cosine(topic_embedding, embeddings.get(memory_id) or [])
            for memory_id in member_ids
        ]
        member_sims = [max(0.0, value) for value in member_sims if value is not None]
        cohesion = sum(member_sims) / len(member_sims) if member_sims else 0.0
        node_candidates.append(HierarchyNodeCandidate(
            topic_id=topic_id,
            subject=str(topic.get("subject") or ""),
            path=tuple(topic.get("path") or []),
            semantic_score=semantic_score,
            cohesion=cohesion,
            relation_confidence=1.0,
            member_count=len(member_ids),
            child_count=len(children.get(topic_id, [])),
            node_type=str(topic.get("node_type") or "facet"),
            node_types=tuple(topic.get("node_types") or []),
        ))
    allowed_types = {
        str(value or "").strip().casefold()
        for value in allowed_node_types if str(value or "").strip()
    }
    semantic_candidates = sorted(
        (
            candidate for candidate in node_candidates
            if candidate.semantic_score > 0.0
        ),
        key=lambda candidate: (-hierarchy_node_utility(candidate), candidate.topic_id),
    )[:max(1, int(topic_limit))]
    anchor_topic_ids = _ancestor_topic_ids(
        anchor_ids, leaf_by_member, parent_by_child,
    )
    candidates_by_id = {candidate.topic_id: candidate for candidate in node_candidates}
    shortlist_by_id = {candidate.topic_id: candidate for candidate in semantic_candidates}
    for topic_id in anchor_topic_ids:
        candidate = candidates_by_id.get(topic_id)
        if candidate is not None:
            shortlist_by_id[topic_id] = candidate
    shortlist = list(shortlist_by_id.values())
    if not shortlist:
        return None, {**info, "reason": "no_subject_consistent_topic"}

    node_score_values = await score_documents([
        hierarchy_node_document(candidate) for candidate in shortlist
    ])
    if node_score_values is None or len(node_score_values) != len(shortlist):
        return None, {**info, "reason": "node_scorer_unavailable"}
    node_scores = {
        candidate.topic_id: float(score)
        for candidate, score in zip(shortlist, node_score_values)
    }
    selections = hierarchy_node_beam(
        shortlist, node_scores=node_scores, limit=node_beam_limit,
    )
    if not selections:
        return None, {**info, "reason": "empty_node_beam"}

    anchor_order = {
        str(memory_id): index for index, memory_id in enumerate(anchor_ids)
    }
    required_anchor_set = {
        str(memory_id) for memory_id in required_anchor_ids if memory_id
    }
    per_node_pool = max(
        max(2, int(max_members)),
        math.ceil(max(2, int(member_pool)) / len(selections)),
    )
    member_ids_by_topic: Dict[str, List[str]] = {}

    def _member_rank(memory_id: str) -> tuple:
        # cosine() 用 None 表示「缺向量或维度不符」，而 0.0 是合法的相似度
        # （正交向量）。`or -1.0` 把 0.0 也折成 -1.0，于是真正正交的成员排到
        # 负相似度成员之后，在 [:per_node_pool] 截断时被优先丢掉 —— 被留下的是
        # 相关性更差的成员。同文件 score_of() 用的是 `if value is not None`。
        similarity = cosine(query_embedding, embeddings.get(memory_id) or [])
        return (
            0 if memory_id in anchor_order else 1,
            anchor_order.get(memory_id, len(anchor_order)),
            -(similarity if similarity is not None else -1.0),
            memory_id,
        )

    for selection in selections:
        subtree_members = list(subtree_cache.get(selection.node.topic_id, set()))
        subtree_members.sort(key=_member_rank)
        member_ids_by_topic[selection.node.topic_id] = subtree_members[:per_node_pool]
    union_member_ids = _round_robin_unique(
        list(member_ids_by_topic.values()), limit=max(2, int(member_pool)),
    )
    allowed_member_ids = set(union_member_ids)
    member_ids_by_topic = {
        topic_id: [
            memory_id for memory_id in member_ids
            if memory_id in allowed_member_ids
        ]
        for topic_id, member_ids in member_ids_by_topic.items()
    }

    nodes = await vector_store.get_by_ids(union_member_ids)
    items = [{
        "node_id": node.node_id,
        "node": node,
        "content": node.content,
        "score": 0.0,
        "source": "hierarchy_member",
    } for node in nodes if getattr(node, "content", None)]
    scores = await score_documents([text_fn(item) for item in items]) if items else []
    if scores is None or len(scores) != len(items):
        return None, {**info, "reason": "member_scorer_unavailable"}

    requested_locations = frozenset(
        str(value).strip().casefold()
        for item in items
        for value in ((getattr(item["node"], "custom", None) or {}).get(
            "event_facet", {}
        ).get("locations") or [])
        if _contains_entity(query, str(value))
    )
    requested_objects = frozenset(
        str(value).strip().casefold()
        for item in items
        for value in ((getattr(item["node"], "custom", None) or {}).get(
            "event_facet", {}
        ).get("objects") or [])
        if _contains_entity(query, str(value))
    )
    requested_states = query_state_constraints(query)
    score_by_id = {
        str(item["node_id"]): float(score) for item, score in zip(items, scores)
    }
    item_by_id = {
        str(item["node_id"]): {
            **item, "_local_rerank_score": score_by_id[str(item["node_id"])],
        }
        for item in items
    }
    bundle_variants: List[Dict[str, Any]] = []
    variant_meta: List[Tuple[HierarchySelection, List[BundleMemberCandidate]]] = []
    eligible_by_topic: Dict[str, List[str]] = {}
    for selection in selections:
        wanted_subject = selection.node.subject.strip().casefold()
        member_candidates: List[BundleMemberCandidate] = []
        selected_topic_members = set(member_ids_by_topic.get(selection.node.topic_id, []))
        for item in items:
            if item["node_id"] not in selected_topic_members:
                continue
            score = score_by_id[item["node_id"]]
            node = item["node"]
            leaf_ids = leaf_by_member.get(node.node_id, [])
            child_topic_id = next((
                child for leaf in leaf_ids
                for child in [_first_child_below(
                    selection.node.topic_id, leaf, parent_by_child,
                )]
                if child
            ), "")
            entity_conflict, time_conflict, state_conflict = member_conflicts(
                query, node,
                requested_locations=requested_locations,
                requested_objects=requested_objects,
                requested_states=requested_states,
            )
            member_candidates.append(BundleMemberCandidate(
                memory_id=node.node_id,
                relevance=float(score),
                relation_confidence=selection.node.relation_confidence,
                token_count=max(1, estimate_tokens_no_io(text_fn(item))),
                child_topic_id=child_topic_id,
                semantic_units=_semantic_units(node, selection.node.path),
                subject_consistent=member_has_subject_assignment(
                    node, wanted_subject,
                ),
                entity_conflict=entity_conflict,
                time_conflict=time_conflict,
                state_conflict=state_conflict,
            ))
        selected_members = select_bundle_members(
            member_candidates,
            mode=selection.mode,
            max_members=max_members,
            token_budget=token_budget,
        )
        eligible_by_topic[selection.node.topic_id] = [
            member.memory_id for member in selected_members
        ]
        if len(selected_members) < 2:
            continue
        ordered_items = [item_by_id[member.memory_id] for member in selected_members]
        for size in range(2, len(ordered_items) + 1):
            variant_items = ordered_items[:size]
            variant_ids = {
                str(item.get("node_id") or "") for item in variant_items
                if item.get("node_id")
            }
            if required_anchor_set and (
                not (variant_ids & required_anchor_set)
                or not (variant_ids - required_anchor_set)
            ):
                continue
            bundle_variants.append(_bundle_from_items(variant_items, selection))
            variant_meta.append((selection, selected_members[:size]))

    if not bundle_variants:
        return None, {
            **info,
            "reason": (
                "no_anchored_complement"
                if required_anchor_set else "fewer_than_two_complementary_members"
            ),
            "node_beam": [selection.node.topic_id for selection in selections],
        }
    variant_scores = await score_documents([text_fn(bundle) for bundle in bundle_variants])
    if variant_scores is None or len(variant_scores) != len(bundle_variants):
        return None, {**info, "reason": "bundle_scorer_unavailable"}
    best_index = max(
        range(len(bundle_variants)),
        key=lambda index: (float(variant_scores[index]), -len(
            bundle_variants[index].get("_bundle_items") or []
        ), -index),
    )
    selection, selected_members = variant_meta[best_index]
    if (
        allowed_types
        and str(selection.node.node_type or "facet").casefold()
        not in allowed_types
    ):
        return None, {
            **info,
            "reason": "disallowed_node_type",
            "topic_id": selection.node.topic_id,
            "path": list(selection.node.path),
            "node_type": selection.node.node_type,
            "mode": selection.mode,
            "allowed_node_types": sorted(allowed_types),
            "node_beam": [candidate.node.topic_id for candidate in selections],
        }
    bundle = {
        **bundle_variants[best_index],
        "_hierarchy_bundle_score": float(variant_scores[best_index]),
    }
    bundle_items = bundle["_bundle_items"]
    return bundle, {
        **info,
        "candidate_built": True,
        "topic_id": selection.node.topic_id,
        "path": list(selection.node.path),
        "node_type": selection.node.node_type,
        "subject": selection.node.subject,
        "mode": selection.mode,
        "node_utility": selection.utility,
        "node_score": node_scores.get(selection.node.topic_id),
        "node_beam": [candidate.node.topic_id for candidate in selections],
        "anchor_topic_ids": sorted(anchor_topic_ids),
        "subject_source": subject_source,
        "member_ids": [str(item.get("node_id") or "") for item in bundle_items],
        "eligible_member_ids": [member.memory_id for member in selected_members],
        "eligible_by_topic": eligible_by_topic,
        "variant_scores": [float(score) for score in variant_scores],
        "selected_width": len(bundle_items),
        "bundle_score": float(variant_scores[best_index]),
        "allowed_node_types": sorted(allowed_types),
    }


def compete_hierarchy_bundle(
    ranked_pool: Sequence[Dict[str, Any]],
    bundle: Optional[Dict[str, Any]],
    bundle_score: Optional[float],
    *,
    limit: int,
    baseline: Optional[Sequence[Dict[str, Any]]] = None,
    novel_floor_margin: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Let one bundle compete while preserving the existing top-K fallback.

    ``ranked_pool`` supplies scored backfill candidates.  ``baseline`` is the
    exact result produced by the pre-existing retrieval pipeline.  Hierarchy
    is an additive final-stage candidate: if it does not survive competition,
    the baseline must be returned byte-for-byte rather than recomputed from a
    wider rerank pool.
    """
    fallback = list(baseline if baseline is not None else ranked_pool)[:max(0, limit)]
    if any(
        item.get("_capsule_memories") is not None
        or item.get("_timeline_member_ids") is not None
        or item.get("_bundle_items") is not None
        or item.get("_graph_merged_items")
        for item in fallback
    ):
        return fallback, {"applied": False, "reason": "existing_bundle_slot"}
    if bundle is None or bundle_score is None or limit <= 0:
        return fallback, {"applied": False, "reason": "no_scored_bundle"}
    member_ids = {
        str(item.get("node_id") or "") for item in bundle.get("_bundle_items") or []
        if item.get("node_id")
    }
    baseline_ids = {
        str(item.get("node_id") or "") for item in fallback
        if item.get("node_id")
    }
    baseline_member_ids = sorted(member_ids & baseline_ids)
    if not baseline_member_ids:
        return fallback, {
            "applied": False,
            "reason": "no_baseline_anchor",
            "bundle_score": float(bundle_score),
            "member_ids": sorted(member_ids),
        }
    novel_member_ids = sorted(member_ids - baseline_ids)
    if not novel_member_ids:
        return fallback, {
            "applied": False,
            "reason": "no_novel_member",
            "bundle_score": float(bundle_score),
            "member_ids": sorted(member_ids),
            "baseline_member_ids": baseline_member_ids,
        }
    if novel_floor_margin is not None:
        baseline_scores = [
            float(item["_local_rerank_score"])
            for item in fallback
            if item.get("_local_rerank_score") is not None
        ]
        novel_scores = {
            str(item.get("node_id") or ""): float(item["_local_rerank_score"])
            for item in bundle.get("_bundle_items") or []
            if str(item.get("node_id") or "") in novel_member_ids
            and item.get("_local_rerank_score") is not None
        }
        if len(baseline_scores) != len(fallback) or set(novel_scores) != set(novel_member_ids):
            return fallback, {
                "applied": False,
                "reason": "novel_floor_unavailable",
                "bundle_score": float(bundle_score),
                "baseline_member_ids": baseline_member_ids,
                "novel_member_ids": novel_member_ids,
            }
        baseline_floor = min(baseline_scores)
        required_floor = baseline_floor + float(novel_floor_margin)
        if any(score < required_floor for score in novel_scores.values()):
            return fallback, {
                "applied": False,
                "reason": "novel_member_below_floor",
                "bundle_score": float(bundle_score),
                "baseline_floor": baseline_floor,
                "novel_floor_margin": float(novel_floor_margin),
                "required_novel_floor": required_floor,
                "novel_member_scores": novel_scores,
                "baseline_member_ids": baseline_member_ids,
                "novel_member_ids": novel_member_ids,
            }
    competing = []
    seen = set()
    for item in [*fallback, *ranked_pool]:
        node_id = str(item.get("node_id") or "")
        if not node_id or node_id in member_ids or node_id in seen:
            continue
        seen.add(node_id)
        competing.append(item)
    scored_bundle = {**bundle, "_local_rerank_score": float(bundle_score)}
    competing.append(scored_bundle)
    def score_of(item: Dict[str, Any]) -> float:
        value = item.get("_local_rerank_score")
        return float(value) if value is not None else float("-inf")

    competing.sort(key=lambda item: (
        -score_of(item), str(item.get("node_id") or ""),
    ))
    selected = competing[:limit]
    applied = any(item.get("source") == "hierarchy_bundle" for item in selected)
    if not applied:
        return fallback, {
            "applied": False, "reason": "bundle_below_top_k",
            "bundle_score": float(bundle_score),
        }
    return selected, {
        "applied": True,
        "bundle_score": float(bundle_score),
        "member_ids": sorted(member_ids),
        "baseline_member_ids": baseline_member_ids,
        "novel_member_ids": novel_member_ids,
        "novel_floor_margin": novel_floor_margin,
        "returned": len(selected),
    }
