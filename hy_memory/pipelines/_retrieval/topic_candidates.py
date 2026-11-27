"""Deterministic Topic candidate generation and conservative admission.

Topic nodes are navigation-only.  Every returned candidate is an active,
canonical L2 loaded from the vector store; Topic/L3 bodies never enter the
answer context.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ...models.memory import MemoryLayer, MemoryNode, MemoryStatus
from .candidate_guards import item_content, semantic_query_terms
from .lemmatize import lemmatize_for_bm25
from .ner import extract_entities, query_time_hints


GENERIC_TOPICS = {
    "life", "personal", "experience", "experiences", "feeling", "feelings",
    "emotion", "emotions", "support", "friendship", "friends", "plan", "plans",
    "goal", "goals", "future", "conversation", "advice", "update", "reflection",
    "motivation",
}
STATE_WORDS = {
    "plan", "planned", "planning", "want", "wanted", "hope", "hoping",
    "upcoming", "future", "start", "started", "begin", "began", "ongoing",
    "currently", "still", "finish", "finished", "complete", "completed",
    "first", "earliest", "latest", "recent", "recently", "current", "before", "after",
}
RELATION_WORDS = {
    "while", "during", "because", "cause", "give", "show", "tell", "recommend",
    "inspire", "motivate", "help", "support", "teach", "learn", "make", "choose",
    "describe", "feel", "think", "say", "suggest",
}
RELATION_CANONICAL = {
    "gave": "give", "given": "give", "giving": "give",
    "showed": "show", "shown": "show", "showing": "show",
    "told": "tell", "telling": "tell",
    "recommended": "recommend", "recommending": "recommend",
    "inspired": "inspire", "inspiring": "inspire",
    "motivated": "motivate", "motivating": "motivate",
    "helped": "help", "helping": "help", "supported": "support",
    "supporting": "support", "taught": "teach", "teaching": "teach",
    "learned": "learn", "learning": "learn", "made": "make", "making": "make",
    "chose": "choose", "chosen": "choose", "choosing": "choose",
    "described": "describe", "describing": "describe", "felt": "feel",
    "feeling": "feel", "thought": "think", "thinking": "think", "said": "say",
    "saying": "say", "suggested": "suggest", "suggesting": "suggest",
    "caused": "cause", "causing": "cause",
}


def cosine(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    ln = math.sqrt(sum(float(a) * float(a) for a in left))
    rn = math.sqrt(sum(float(b) * float(b) for b in right))
    return dot / (ln * rn) if ln and rn else None


def select_topics(
    topics: Sequence[Dict[str, Any]],
    query_embedding: Sequence[float],
    *, threshold: float = 0.3,
    limit: int = 10,
) -> List[Tuple[float, Dict[str, Any]]]:
    scored = []
    for topic in topics or []:
        score = cosine(query_embedding, topic.get("embedding") or [])
        if score is not None and score >= threshold:
            scored.append((score, topic))
    return sorted(
        scored, key=lambda value: (-value[0], str(value[1].get("topic_id") or "")),
    )[:max(0, limit)]


def query_subjects(query: str, owner_name: Optional[str] = None) -> List[str]:
    """Extract explicit people locally; fall back to conservative title tokens."""
    values = [text.strip() for text, label in extract_entities(query, for_boost=False)
              if label == "PERSON" and text.strip()]
    if owner_name and re.search(
        r"(?<!\w)" + re.escape(owner_name) + r"(?!\w)", query or "", re.I,
    ):
        values.append(owner_name)
    blocked = {
        "what", "which", "who", "when", "where", "why", "how", "did", "does",
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december",
    }
    if not values:
        values.extend(
            token for token in re.findall(r"\b[A-Z][a-z]+\b", query or "")
            if token.casefold() not in blocked
        )
    unique = []
    for value in values:
        folded = value.casefold()
        if folded not in unique:
            unique.append(folded)
    return unique


def primary_subject(query: str, subjects: Sequence[str]) -> str:
    folded = (query or "").casefold()
    positions = [(folded.find(subject), subject) for subject in subjects
                 if subject and folded.find(subject) >= 0]
    return min(positions)[1] if positions else ""


def item_subject(
    item: Dict[str, Any],
    subjects: Sequence[str],
    owner_name: Optional[str] = None,
) -> str:
    text = item_content(item)
    match = re.match(r"(?i)^the user\s*\(([^)]+)\)", text)
    if match:
        return match.group(1).strip().casefold()
    if re.match(r"(?i)^the user\b", text) and owner_name:
        return owner_name.casefold()
    for subject in subjects:
        if re.match(r"(?i)^" + re.escape(subject) + r"(?:'s|\b)", text[:160]):
            return subject.casefold()
    return ""


def item_time(item: Dict[str, Any]) -> Optional[datetime]:
    node = item.get("node")
    value = getattr(node, "memory_at", None) if node is not None else item.get("memory_at")
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromtimestamp(float(value)) if value is not None else None
    except (TypeError, ValueError, OSError):
        return None


def relation_terms(text: str) -> Set[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {
        RELATION_CANONICAL.get(word, word) for word in words
        if RELATION_CANONICAL.get(word, word) in RELATION_WORDS
    }


def relation_phrase_overlap(left: str, right: str) -> int:
    def windows(text: str) -> Set[Tuple[str, ...]]:
        words = [RELATION_CANONICAL.get(word, word)
                 for word in re.findall(r"[a-z]+", (text or "").lower())]
        result: Set[Tuple[str, ...]] = set()
        for index, word in enumerate(words):
            if word not in RELATION_WORDS:
                continue
            for width in (2, 3):
                first = max(0, index - width + 1)
                last = min(index + 1, len(words) - width + 1)
                for start in range(first, last):
                    result.add(tuple(words[start:start + width]))
        return result
    return len(windows(left) & windows(right))


def is_collection_query(query: str) -> bool:
    match = re.match(
        r"(?i)^\s*(?:what|which)\s+(.{1,64}?)\s+"
        r"(?:do|does|did|has|have|had|is|are|was|were|can|could|would|will)\b",
        query or "",
    )
    if not match:
        return False
    singular_s = {"class", "glass", "grass", "progress", "success", "business"}
    return any(token.endswith("s") and token not in singular_s
               for token in re.findall(r"[a-z]+", match.group(1).lower()))


def series_bundle_member_is_query_supported(
    query: str,
    item: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """Require an explainable query-to-member match before bundling a sibling.

    ``same_series`` is a navigation edge, not proof that every event in the
    series answers the current question.  The member must independently match
    an explicit time constraint, a geographic collection request, or most of
    the query's non-generic object/relation terms.
    """
    qterms = semantic_query_terms(query)
    lemmas = set(lemmatize_for_bm25(item_content(item)).split())
    query_lower = (query or "").lower()
    content_lower = item_content(item).lower()
    overlap_terms = qterms & lemmas
    overlap = len(overlap_terms)
    coverage = overlap / max(1, len(qterms))

    hints = set(query_time_hints(query))
    when = item_time(item)
    time_match = 0
    if hints and when:
        time_match = max(
            int(year is None or year == when.year) + int(month == when.month)
            for year, month in hints
        )
    fine_time_terms = set(re.findall(
        r"\b(?:earlier|later|morning|afternoon|evening|night)\b",
        query_lower,
    ))
    explicit_time = bool(hints) and time_match >= 2 and (
        not fine_time_terms or any(term in content_lower for term in fine_time_terms)
    )

    ordinal_pattern = (
        r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"\d+(?:st|nd|rd|th))\b"
    )
    query_ordinals = set(re.findall(ordinal_pattern, query_lower))
    candidate_ordinals = set(re.findall(ordinal_pattern, content_lower))
    ordinal_conflict = bool(
        query_ordinals and candidate_ordinals
        and query_ordinals.isdisjoint(candidate_ordinals)
    )

    geo_collection = bool(re.search(
        r"(?i)\b(?:countries|states|cities|places|locations|destinations)\b",
        query or "",
    ))
    has_geo = any(
        label in {"GPE", "LOC", "FAC"}
        for _, label in extract_entities(item_content(item))
    )
    lexical_constraints = (
        (0 < len(qterms) <= 2 and overlap == len(qterms))
        or (overlap >= 2 and coverage >= (2.0 / 3.0))
    )
    accepted = (
        not ordinal_conflict
        and (explicit_time or (geo_collection and has_geo) or lexical_constraints)
    )
    return accepted, {
        "accepted": accepted,
        "time_match": time_match,
        "fine_time_terms": sorted(fine_time_terms),
        "query_ordinals": sorted(query_ordinals),
        "candidate_ordinals": sorted(candidate_ordinals),
        "ordinal_conflict": ordinal_conflict,
        "geo_collection": geo_collection,
        "has_geo": has_geo,
        "object_overlap": overlap,
        "object_coverage": coverage,
        "overlap_terms": sorted(overlap_terms),
    }


def _event_features(
    query: str,
    item: Dict[str, Any],
    topic_terms: Set[str],
) -> Dict[str, Any]:
    qterms = semantic_query_terms(query)
    qlemmas = set(lemmatize_for_bm25(query).split())
    lemmas = set(lemmatize_for_bm25(item_content(item)).split())
    hints = set(query_time_hints(query))
    when = item_time(item)
    time_match = int(bool(hints and when and any(year == when.year for year, _ in hints)))
    time_match += int(bool(hints and when and any(month == when.month for _, month in hints)))
    qrelations = relation_terms(query)
    return {
        "time_match": time_match,
        "state_match": len((qlemmas & STATE_WORDS) & lemmas),
        "relation_match": len(qrelations & relation_terms(item_content(item))),
        "relation_phrase_match": relation_phrase_overlap(query, item_content(item)),
        "object_overlap": len((qterms - topic_terms) & lemmas),
    }


def topic_overlay_candidates(
    query: str,
    hits: Sequence[Dict[str, Any]],
    selected: Sequence[Dict[str, Any]],
    *, owner_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find high-confidence members of query-matched subject/raw-tag groups."""
    selected_ids = {str(item.get("node_id") or "") for item in selected}
    subjects = query_subjects(query, owner_name)
    qterms = semantic_query_terms(query)
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in hits:
        subject = item_subject(item, subjects, owner_name)
        if subjects and subject and subject not in subjects:
            continue
        node = item.get("node")
        for tag in list(getattr(node, "tags", None) or item.get("tags") or []):
            name = str(tag or "").strip()
            topic_terms = set(lemmatize_for_bm25(name).split())
            if not topic_terms or name.casefold() in GENERIC_TOPICS:
                continue
            overlap = len(qterms & topic_terms)
            if overlap <= 0:
                continue
            key = (subject, name.casefold())
            groups.setdefault(key, {
                "subject": subject, "topic_id": "tag:" + name.casefold(),
                "topic_name": name, "topic_terms": topic_terms, "members": [],
                "topic_overlap": overlap,
            })["members"].append(item)
    result = []
    for group in groups.values():
        if not any(str(item.get("node_id") or "") in selected_ids
                   for item in group["members"]):
            continue
        for item in group["members"]:
            if str(item.get("node_id") or "") in selected_ids:
                continue
            features = _event_features(query, item, group["topic_terms"])
            high = (
                features["time_match"] >= 2
                or features["state_match"] >= 1
                or features["object_overlap"] >= 2
            )
            compound = bool(re.search(
                r"(?i)\band\s+(?:how|what|which|when|where|who|why)\b", query or "",
            ))
            object_only = (
                features["object_overlap"] >= 2
                and features["time_match"] < 2
                and features["state_match"] < 1
                and features["relation_match"] < 1
            )
            if high and not (compound and object_only):
                result.append({
                    "item": item, "subject": group["subject"],
                    "topic_id": group["topic_id"], "topic_name": group["topic_name"],
                    "topic_score": float(item.get("score") or 0.0),
                    **features,
                })
    result.sort(key=lambda value: (
        -value["time_match"], -value["state_match"], -value["relation_match"],
        -value["object_overlap"], -value["topic_score"],
        str(value["item"].get("node_id") or ""),
    ))
    return result


async def topic_graph_candidates(
    graph_store: Any,
    vector_store: Any,
    *, query: str,
    query_embedding: Sequence[float],
    user_id: str,
    agent_id: str,
    selected: Sequence[Dict[str, Any]],
    owner_name: Optional[str] = None,
    threshold: float = 0.3,
    topic_limit: int = 10,
    member_limit: int = 200,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Follow query-matched Topic embeddings to active canonical L2 members."""
    if not graph_store or not user_id or not agent_id or not query_embedding:
        return [], {"enabled": True, "reason": "missing_scope_or_backend"}
    scopes = [MemoryNode.build_topic_scope_key(user_id, agent_id)]
    # Backward compatibility for dev109 clones whose Topic scope accidentally
    # retained the default-session suffix.
    scopes.append(scopes[0] + "::default_session")
    topic_rows = await asyncio.gather(
        *(graph_store.get_all_topics(scope) for scope in scopes),
        return_exceptions=True,
    )
    topics_by_id: Dict[str, Dict[str, Any]] = {}
    for rows in topic_rows:
        if isinstance(rows, Exception):
            continue
        for topic in rows or []:
            topic_id = str(topic.get("topic_id") or "")
            if topic_id and topic_id not in topics_by_id:
                topics_by_id[topic_id] = topic
    chosen_topics = select_topics(
        list(topics_by_id.values()), query_embedding,
        threshold=threshold, limit=topic_limit,
    )
    member_rows = await asyncio.gather(
        *(graph_store.get_topic_memories(topic["topic_id"], limit=member_limit)
          for _, topic in chosen_topics),
        return_exceptions=True,
    )
    member_to_topics: Dict[str, List[Tuple[float, Dict[str, Any]]]] = {}
    topic_member_ids: Dict[str, Set[str]] = {}
    for (topic_score, topic), rows in zip(chosen_topics, member_rows):
        topic_id = str(topic.get("topic_id") or "")
        if isinstance(rows, Exception):
            continue
        for row in rows or []:
            node_id = str(row.get("node_id") or "")
            if node_id:
                member_to_topics.setdefault(node_id, []).append((topic_score, topic))
                topic_member_ids.setdefault(topic_id, set()).add(node_id)
    nodes = await vector_store.get_by_ids(sorted(member_to_topics)) if member_to_topics else []
    selected_ids = {str(item.get("node_id") or "") for item in selected}
    subjects = query_subjects(query, owner_name)
    candidates = []
    diagnostics = Counter()
    for node in nodes or []:
        diagnostics["members_seen"] += 1
        if (
            node.node_id in selected_ids
            or node.layer != MemoryLayer.L2_FACT
            or node.status != MemoryStatus.ACTIVE
            or not getattr(node, "is_latest", True)
        ):
            diagnostics["ineligible_or_selected"] += 1
            continue
        item = {
            "node_id": node.node_id, "node": node, "content": node.content,
            "score": max(score for score, _ in member_to_topics[node.node_id]),
            "_semantic": 0.0, "_bm25": 0.0, "source": "topic_graph",
        }
        subject = item_subject(item, subjects, owner_name)
        if subjects and subject and subject not in subjects:
            diagnostics["subject_rejected"] += 1
            continue
        for topic_score, topic in member_to_topics[node.node_id]:
            name = str(topic.get("name") or "")
            if name.casefold() in GENERIC_TOPICS or name.casefold() in subjects:
                continue
            features = _event_features(
                query, item, set(lemmatize_for_bm25(name).split()),
            )
            if not (
                features["time_match"] >= 2
                or features["state_match"]
                or features["relation_match"]
                or features["object_overlap"] >= 2
            ):
                diagnostics["event_rejected"] += 1
                continue
            diagnostics["eligible"] += 1
            candidates.append({
                "item": item, "subject": subject,
                "topic_id": topic.get("topic_id"), "topic_name": name,
                "topic_score": float(topic_score),
                "anchor_ids": sorted(
                    selected_ids & topic_member_ids.get(
                        str(topic.get("topic_id") or ""), set()
                    )
                ),
                **features,
            })
    candidates.sort(key=lambda value: (
        -value["time_match"], -value["state_match"],
        -value["relation_phrase_match"], -value["relation_match"],
        -value["object_overlap"], -value["topic_score"],
        str(value["item"].get("node_id") or ""),
    ))
    return candidates, {
        "enabled": True,
        "scopes": scopes,
        "topics_seen": len(topics_by_id),
        "selected_topics": [
            {"topic_id": topic.get("topic_id"), "name": topic.get("name"), "score": score}
            for score, topic in chosen_topics
        ],
        "member_filters": dict(diagnostics),
    }


def select_topic_bundle(
    query: str,
    selected: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    scores_by_id: Dict[str, float],
    *,
    max_members: int = 2,
    floor_margin: float = 0.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Attach query-supported Topic siblings to final L2 anchors in-place."""
    current = list(selected)
    if not current or max_members <= 0:
        return current, {"enabled": True, "applied": False, "reason": "empty_or_disabled"}
    by_id = {str(item.get("node_id") or ""): item for item in current}
    floor = min(
        (float(scores_by_id.get(node_id, float("-inf"))) for node_id in by_id),
        default=float("-inf"),
    )
    per_query_limit = max_members if is_collection_query(query) else 1
    aggregated: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        item = candidate.get("item") or {}
        node_id = str(item.get("node_id") or "")
        if not node_id or node_id in by_id:
            continue
        entry = aggregated.setdefault(node_id, {
            "item": item, "anchor_ids": set(), "paths": [],
        })
        entry["anchor_ids"].update(candidate.get("anchor_ids") or [])
        entry["paths"].append(candidate)
    ranked = sorted(
        aggregated.items(),
        key=lambda pair: (-float(scores_by_id.get(pair[0], float("-inf"))), pair[0]),
    )
    admitted = []
    rejected = []
    for node_id, entry in ranked:
        score = float(scores_by_id.get(node_id, float("-inf")))
        if score < floor + floor_margin:
            continue
        anchors = [
            anchor for anchor in entry["anchor_ids"]
            if anchor in by_id and not by_id[anchor].get("_graph_merged_items")
        ]
        if not anchors:
            continue
        supported, support = series_bundle_member_is_query_supported(query, entry["item"])
        best_path = max(
            entry["paths"],
            key=lambda value: (
                value.get("relation_phrase_match", 0), value.get("time_match", 0),
                value.get("state_match", 0), value.get("object_overlap", 0),
                value.get("topic_score", 0.0),
            ),
        )
        if not supported:
            rejected.append({
                "node_id": node_id, "topic_id": best_path.get("topic_id"), **support,
            })
            continue
        anchor = max(
            anchors, key=lambda value: scores_by_id.get(value, float("-inf")),
        )
        admitted.append({
            **entry["item"], "graph_via": anchor,
            "graph_relation": "shared_topic",
            "_local_rerank_score": score,
            "topic_bundle_support": support,
            "topic_id": best_path.get("topic_id"),
            "topic_name": best_path.get("topic_name"),
        })
        if len(admitted) >= per_query_limit:
            break
    if not admitted:
        return current, {
            "enabled": True, "applied": False,
            "reason": "no_query_supported_member", "floor": floor,
            "rejected": rejected,
        }
    from .._graph.rerank import merge_expansions_into_seeds
    merged = merge_expansions_into_seeds(current, admitted)
    return merged, {
        "enabled": True, "applied": True, "floor": floor,
        "member_ids": [item["node_id"] for item in admitted],
        "anchor_ids": [item["graph_via"] for item in admitted],
        "topic_ids": [item["topic_id"] for item in admitted],
        "support": [item["topic_bundle_support"] for item in admitted],
        "rejected": rejected, "max_members": per_query_limit,
    }


def select_episode_bundle(
    query: str,
    selected: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    scores_by_id: Dict[str, float],
    *,
    max_members: int = 2,
    floor_margin: float = 0.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Attach query-supported siblings from an anchored compact Episode."""
    current = list(selected)
    if not current or max_members <= 0:
        return current, {"enabled": True, "applied": False, "reason": "empty_or_disabled"}
    by_id = {str(item.get("node_id") or ""): item for item in current}
    floor = min(
        (float(scores_by_id.get(node_id, float("-inf"))) for node_id in by_id),
        default=float("-inf"),
    )
    per_query_limit = max_members if is_collection_query(query) else 1
    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(scores_by_id.get(str(item.get("node_id") or ""), float("-inf"))),
            str(item.get("node_id") or ""),
        ),
    )
    admitted = []
    rejected = []
    for item in ranked:
        node_id = str(item.get("node_id") or "")
        score = float(scores_by_id.get(node_id, float("-inf")))
        anchor = str(item.get("_episode_anchor_id") or "")
        if score < floor + floor_margin or anchor not in by_id:
            continue
        if by_id[anchor].get("_graph_merged_items"):
            continue
        supported, support = series_bundle_member_is_query_supported(query, item)
        if not supported:
            rejected.append({"node_id": node_id, **support})
            continue
        admitted.append({
            **item, "graph_via": anchor, "graph_relation": "same_episode",
            "_local_rerank_score": score, "episode_bundle_support": support,
        })
        if len(admitted) >= per_query_limit:
            break
    if not admitted:
        return current, {
            "enabled": True, "applied": False,
            "reason": "no_query_supported_member", "floor": floor,
            "rejected": rejected,
        }
    from .._graph.rerank import merge_expansions_into_seeds
    merged = merge_expansions_into_seeds(current, admitted)
    return merged, {
        "enabled": True, "applied": True, "floor": floor,
        "member_ids": [item["node_id"] for item in admitted],
        "anchor_ids": [item["graph_via"] for item in admitted],
        "episode_ids": [item.get("_episode_parent_id") for item in admitted],
        "support": [item["episode_bundle_support"] for item in admitted],
        "rejected": rejected, "max_members": per_query_limit,
    }


def apply_topic_overlay(
    selected: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    scores_by_id: Dict[str, float],
    *, limit: int,
    slots: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    current = list(selected)
    applied = []
    current_ids = {str(item.get("node_id") or "") for item in current}
    for candidate in list(candidates)[:max(0, slots)]:
        item = candidate["item"]
        node_id = str(item.get("node_id") or "")
        if not current or node_id not in scores_by_id or node_id in current_ids:
            continue
        victim_index = min(
            range(len(current)),
            key=lambda index: scores_by_id.get(
                str(current[index].get("node_id") or ""), float("-inf"),
            ),
        )
        victim = current[victim_index]
        victim_id = str(victim.get("node_id") or "")
        victim_score = scores_by_id.get(victim_id, float("-inf"))
        if scores_by_id[node_id] <= victim_score:
            continue
        current[victim_index] = {**item, "_local_rerank_score": scores_by_id[node_id]}
        current_ids.discard(victim_id)
        current_ids.add(node_id)
        applied.append({
            **{key: value for key, value in candidate.items() if key != "item"},
            "admitted": node_id, "displaced": victim_id,
            "admitted_bge": scores_by_id[node_id],
            "displaced_bge": victim_score,
        })
    current.sort(
        key=lambda item: scores_by_id.get(
            str(item.get("node_id") or ""), float("-inf"),
        ),
        reverse=True,
    )
    return current[:limit], {"enabled": True, "applied": applied, "slots": slots}


def graph_decision_is_product_safe(
    query: str,
    subjects: Sequence[str],
    decision: Dict[str, Any],
) -> Tuple[bool, str]:
    subject = str(decision.get("subject") or "").casefold()
    primary = primary_subject(query, subjects)
    if not subject or not primary or subject != primary:
        return False, "subject_mismatch"
    time_match = int(decision.get("time_match") or 0)
    state_match = int(decision.get("state_match") or 0)
    phrase = int(decision.get("relation_phrase_match") or 0)
    relation = int(decision.get("relation_match") or 0)
    objects = int(decision.get("object_overlap") or 0)
    admitted = float(decision.get("admitted_bge") or 0.0)
    displaced = float(decision.get("displaced_bge") or 0.0)
    delta = admitted - displaced
    if phrase >= 2:
        return True, "exact_relation_phrase"
    if relation >= 1 and (objects >= 1 or delta >= 1.0):
        return True, "relation_with_support"
    # Product-safe replacement for the benchmark category branch.  Exact
    # month/year alignment alone is broad; require a large local-BGE margin.
    if time_match >= 2 and query_time_hints(query) and delta >= 7.0:
        return True, "explicit_time_large_margin"
    if time_match >= 2 and (
        re.search(r"(?i)\bplans?\b", query or "") or objects >= 2 or relation >= 1
    ):
        return True, "time_with_query_constraint"
    if time_match >= 1 and state_match >= 1:
        return True, "time_and_state"
    if objects >= 2 and admitted < 0 and delta >= 1.0:
        return True, "object_tail_recovery"
    return False, "insufficient_discriminator"


async def same_series_bundle_candidates(
    graph_store: Any,
    vector_store: Any,
    selected: Sequence[Dict[str, Any]],
    *,
    max_edges: int = 500,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load active L2 neighbors linked by an explicit ``same_series`` edge.

    Each candidate records the already-selected top-K anchor it can be attached
    to. No content is merged here and failures return an empty candidate set.
    """
    selected_ids = {
        str(item.get("node_id") or "") for item in selected if item.get("node_id")
    }
    if not graph_store or not selected_ids:
        return [], {"enabled": True, "reason": "missing_graph_or_anchors"}
    try:
        edges = await graph_store.get_related_edges(
            sorted(selected_ids), max_edges=max_edges,
            relation_types=["same_series"], include_temporal=False,
        )
    except Exception as exc:
        return [], {"enabled": True, "reason": "edge_read_failed",
                    "error_type": type(exc).__name__}
    candidate_anchors: Dict[str, Set[str]] = {}
    for edge in edges or []:
        source = str(edge.get("source_id") or "")
        target = str(edge.get("target_id") or "")
        if source in selected_ids and target and target not in selected_ids:
            candidate_anchors.setdefault(target, set()).add(source)
        if target in selected_ids and source and source not in selected_ids:
            candidate_anchors.setdefault(source, set()).add(target)
    if not candidate_anchors:
        return [], {"enabled": True, "edges": len(edges or []),
                    "reason": "no_outside_neighbors"}
    try:
        nodes = await vector_store.get_by_ids(sorted(candidate_anchors))
    except Exception as exc:
        return [], {"enabled": True, "reason": "node_read_failed",
                    "error_type": type(exc).__name__}
    candidates = []
    for node in nodes or []:
        if (
            node.layer != MemoryLayer.L2_FACT
            or node.status != MemoryStatus.ACTIVE
            or not getattr(node, "is_latest", True)
            or not str(node.content or "").strip()
        ):
            continue
        candidates.append({
            "node_id": node.node_id,
            "node": node,
            "content": node.content,
            "score": 0.0,
            "source": "same_series_bundle",
            "anchor_ids": sorted(candidate_anchors[node.node_id]),
        })
    return candidates, {
        "enabled": True, "edges": len(edges or []),
        "candidate_ids": [item["node_id"] for item in candidates],
    }


def select_same_series_bundle(
    query: str,
    selected: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    scores_by_id: Dict[str, float],
    *,
    max_members: int = 2,
    floor_margin: float = 0.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Attach high-BGE series members to existing anchors without adding slots."""
    current = list(selected)
    if not is_collection_query(query) or not current or max_members <= 0:
        return current, {"enabled": True, "applied": False,
                         "reason": "not_collection_or_empty"}
    by_id = {str(item.get("node_id") or ""): item for item in current}
    floor = min(
        (float(scores_by_id.get(node_id, float("-inf"))) for node_id in by_id),
        default=float("-inf"),
    )
    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(scores_by_id.get(str(item.get("node_id") or ""), float("-inf"))),
            str(item.get("node_id") or ""),
        ),
    )
    admitted = []
    rejected = []
    for item in ranked:
        node_id = str(item.get("node_id") or "")
        score = float(scores_by_id.get(node_id, float("-inf")))
        if score < floor + floor_margin:
            continue
        supported, support = series_bundle_member_is_query_supported(query, item)
        if not supported:
            rejected.append({"node_id": node_id, **support})
            continue
        anchors = [anchor for anchor in item.get("anchor_ids") or [] if anchor in by_id]
        if not anchors:
            continue
        anchor = max(anchors, key=lambda node_id: scores_by_id.get(node_id, float("-inf")))
        admitted.append({
            **item, "graph_via": anchor, "graph_relation": "same_series",
            "_local_rerank_score": score, "series_bundle_support": support,
        })
        if len(admitted) >= max_members:
            break
    if not admitted:
        return current, {"enabled": True, "applied": False,
                         "reason": "no_query_supported_member", "floor": floor,
                         "rejected": rejected}
    from .._graph.rerank import merge_expansions_into_seeds
    merged = merge_expansions_into_seeds(current, admitted)
    return merged, {
        "enabled": True, "applied": True, "floor": floor,
        "member_ids": [item["node_id"] for item in admitted],
        "anchor_ids": [item["graph_via"] for item in admitted],
        "support": [item["series_bundle_support"] for item in admitted],
        "rejected": rejected,
        "max_members": max_members,
    }


def apply_topic_graph_candidates(
    query: str,
    subjects: Sequence[str],
    selected: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    scores_by_id: Dict[str, float],
    *, limit: int,
    slots: int,
    relation_bge_tolerance: float = 1.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Apply graph candidates, then accept/reject the whole graph context safely."""
    original = list(selected)
    current = list(selected)
    current_ids = {str(item.get("node_id") or "") for item in current}
    decisions = []
    ordered = sorted(
        candidates,
        key=lambda value: (
            -scores_by_id.get(str(value["item"].get("node_id") or ""), float("-inf")),
            str(value["item"].get("node_id") or ""),
        ),
    )
    for candidate in ordered[:max(0, slots)]:
        item = candidate["item"]
        node_id = str(item.get("node_id") or "")
        if not current or node_id not in scores_by_id or node_id in current_ids:
            continue
        victim_index = min(
            range(len(current)),
            key=lambda index: scores_by_id.get(
                str(current[index].get("node_id") or ""), float("-inf"),
            ),
        )
        victim = current[victim_index]
        victim_id = str(victim.get("node_id") or "")
        victim_score = scores_by_id.get(victim_id, float("-inf"))
        tolerance = relation_bge_tolerance if candidate["relation_phrase_match"] > 0 else 0.0
        if scores_by_id[node_id] + tolerance <= victim_score:
            continue
        current[victim_index] = {**item, "_local_rerank_score": scores_by_id[node_id]}
        current_ids.discard(victim_id)
        current_ids.add(node_id)
        decision = {
            **{key: value for key, value in candidate.items() if key != "item"},
            "admitted": node_id, "displaced": victim_id,
            "admitted_bge": scores_by_id[node_id],
            "displaced_bge": victim_score,
            "bge_tolerance": tolerance,
        }
        accepted, reason = graph_decision_is_product_safe(query, subjects, decision)
        decision["accepted"] = accepted
        decision["reason"] = reason
        decisions.append(decision)
    use_graph = bool(decisions) and any(d["accepted"] for d in decisions)
    if not use_graph:
        return original, {
            "enabled": True, "applied": False, "decisions": decisions,
            "policy": "product_safe_topic_graph_v1",
        }
    current.sort(
        key=lambda item: scores_by_id.get(
            str(item.get("node_id") or ""), float("-inf"),
        ),
        reverse=True,
    )
    return current[:limit], {
        "enabled": True, "applied": True, "decisions": decisions,
        "policy": "product_safe_topic_graph_v1",
    }
