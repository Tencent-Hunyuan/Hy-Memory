"""Deterministic candidate guards around MMR and local BGE reranking.

These helpers contain no benchmark-specific inputs and make no network or LLM
calls.  They are deliberately opt-in at the reader level.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .lemmatize import lemmatize_for_bm25


GENERIC_QUERY_TERMS = {
    "what", "which", "who", "when", "where", "why", "how", "did", "does",
    "do", "was", "were", "is", "are", "has", "have", "had", "kind", "type",
    "one", "two", "significant", "life", "event", "events", "activity",
    "activities", "thing", "things", "person", "people", "date", "time", "year",
    "month", "day", "ago", "before", "after", "during", "as", "mentioned", "tell",
}


def item_content(item: Dict[str, Any]) -> str:
    value = item.get("content")
    if value:
        return str(value)
    node = item.get("node")
    return str(getattr(node, "content", "") or "") if node is not None else ""


def nominate_multi_tag_candidates(
    tag_hits: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    *,
    excluded_tags: Sequence[str] = (),
    existing_ids: Sequence[str] = (),
    min_matches: int = 2,
    limit: int = 1,
    weight: float = 0.3,
    max_tag_df: int = 8,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Nominate bounded L2 candidates supported by multiple semantic tags.

    The tag index is only a candidate source.  A nominee retains its query-to-L2
    semantic score and receives a bounded tag score bonus used only to choose the
    nominee.  The caller may then place that single nominee in the fixed-size
    local-BGE pool.  PERSON tags and tags matching an explicit query subject are
    ignored.
    """
    tag_scores: Dict[str, float] = {}
    tag_names: Dict[str, str] = {}
    excluded = {str(value).strip().casefold() for value in excluded_tags if value}
    for hit in tag_hits or []:
        tag = str(hit.get("tag") or "").strip()
        folded = tag.casefold()
        if not tag or folded in excluded:
            continue
        score = float(hit.get("score") or 0.0)
        if score > tag_scores.get(folded, float("-inf")):
            tag_scores[folded] = score
            tag_names[folded] = tag

    seen = {str(value) for value in existing_ids if value}
    candidate_tags: Dict[str, Set[str]] = {}
    tag_df: Dict[str, int] = {}
    for item in candidates or []:
        node_id = str(item.get("node_id") or "")
        node = item.get("node")
        if not node_id or node is None:
            continue
        tags = list(getattr(node, "tags", None) or [])
        ners = list(getattr(node, "tag_ners", None) or [])
        eligible_tags = {
            str(tag or "").strip().casefold()
            for index, tag in enumerate(tags)
            if str(tag or "").strip()
            and str(tag or "").strip().casefold() not in excluded
            and str(ners[index] if index < len(ners) else "").upper() != "PERSON"
            and str(tag or "").strip().casefold() in tag_scores
        }
        candidate_tags[node_id] = eligible_tags
        for tag in eligible_tags:
            tag_df[tag] = tag_df.get(tag, 0) + 1

    ranked = []
    for item in candidates or []:
        node_id = str(item.get("node_id") or "")
        node = item.get("node")
        if not node_id or node_id in seen or node is None:
            continue
        matched = {
            tag: tag_scores[tag]
            for tag in candidate_tags.get(node_id, set())
            if tag_df.get(tag, 0) <= max(1, int(max_tag_df))
        }
        if len(matched) < max(1, int(min_matches)):
            continue
        tag_score = sum(matched.values()) / len(matched)
        semantic = float(item.get("score") or 0.0)
        nomination_score = semantic + max(0.0, float(weight)) * tag_score
        ranked.append((
            -len(matched), -tag_score, -semantic, node_id,
            {
                **item,
                "source": "tag_index_nominee",
                "_tag_index_matches": [tag_names[tag] for tag in sorted(matched)],
                "_tag_index_score": tag_score,
                "_tag_index_nomination_score": nomination_score,
                "_tag_index_original_score": semantic,
            },
        ))
    ranked.sort(key=lambda value: value[:4])
    selected = [value[4] for value in ranked[:max(0, int(limit))]]
    return selected, {
        "enabled": True,
        "matched_tags": [tag_names[tag] for tag in sorted(tag_scores)],
        "eligible": len(ranked),
        "nominated_ids": [item["node_id"] for item in selected],
        "min_matches": max(1, int(min_matches)),
        "limit": max(0, int(limit)),
        "weight": max(0.0, float(weight)),
        "max_tag_df": max(1, int(max_tag_df)),
        "tag_df": dict(sorted(tag_df.items())),
    }


def semantic_query_terms(query: str) -> Set[str]:
    """Content-bearing query lemmas, excluding names and generic question words."""
    names = {value.lower() for value in re.findall(r"\b[A-Z][a-z]+\b", query or "")}
    return {
        value for value in lemmatize_for_bm25(query or "").split()
        if value not in GENERIC_QUERY_TERMS and value not in names
        and not value.isdigit() and not re.fullmatch(r"20\d\d", value)
    }


def nominate_lexical_after_mmr(
    all_hits: Sequence[Dict[str, Any]],
    selected: Sequence[Dict[str, Any]],
    *,
    limit: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Give the strongest positive-BM25 MMR omission one candidate slot."""
    original = list(selected)
    info: Dict[str, Any] = {"enabled": True, "applied": False}
    if limit <= 0 or not original:
        return original[:max(0, limit)], {**info, "reason": "empty_or_invalid_budget"}
    selected_ids = {str(item.get("node_id") or "") for item in original}
    fusion_order = {
        str(item.get("node_id") or ""): index + 1
        for index, item in enumerate(sorted(
            all_hits,
            key=lambda item: (-float(item.get("score") or 0.0), str(item.get("node_id") or "")),
        ))
    }
    candidates = sorted(
        (
            item for item in all_hits
            if item.get("node_id") not in selected_ids
            and float(item.get("_bm25") or 0.0) > 0.0
        ),
        key=lambda item: (
            -float(item.get("_bm25") or 0.0),
            -float(item.get("score") or 0.0),
            str(item.get("node_id") or ""),
        ),
    )
    if not candidates:
        return original, {**info, "reason": "no_positive_bm25_omission"}
    nominee = candidates[0]
    victim = original[-1] if len(original) >= limit else None
    guarded = (
        original[:limit - 1] + [nominee]
        if victim is not None else original + [nominee]
    )
    return guarded[:limit], {
        **info,
        "applied": True,
        "nominated": nominee.get("node_id"),
        "nominated_bm25": float(nominee.get("_bm25") or 0.0),
        "nominated_score": float(nominee.get("score") or 0.0),
        "nominated_fusion_rank": fusion_order.get(str(nominee.get("node_id") or "")),
        "displaced": victim.get("node_id") if victim else None,
        "slots": 1,
    }


def _rank_by_scores(
    pool: Sequence[Dict[str, Any]],
    by_id: Dict[str, float],
    limit: int,
) -> Tuple[List[Dict[str, Any]], List[float]]:
    ranked = sorted(
        enumerate(pool),
        key=lambda pair: (-by_id[str(pair[1].get("node_id") or "")], pair[0]),
    )[:max(0, limit)]
    selected = [
        {**item, "_local_rerank_score": by_id[str(item.get("node_id") or "")]}
        for _, item in ranked
    ]
    return selected, [
        round(by_id[str(item.get("node_id") or "")], 6) for _, item in ranked
    ]


def rerank_with_lexical_survival(
    query: str,
    original_pool: Sequence[Dict[str, Any]],
    guarded_pool: Sequence[Dict[str, Any]],
    nominee_id: Optional[str],
    *,
    limit: int,
    text_fn: Callable[[Dict[str, Any]], str],
    scorer: Callable[[str, List[str]], Optional[List[float]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], Dict[str, float]]:
    """Use the guarded pool only when its lexical nominee survives BGE top-K.

    Returns ``(top_k, diagnostics, selected_pool, scores_by_id)``.  If scoring
    fails, the original pool/order is retained.
    """
    original = list(original_pool)
    guarded = list(guarded_pool)
    union = list(original)
    seen = {str(item.get("node_id") or "") for item in union}
    union.extend(item for item in guarded if str(item.get("node_id") or "") not in seen)
    documents = [text_fn(item) for item in union]
    try:
        scores = scorer(query or "", documents)
    except Exception as exc:
        return original[:limit], {
            "applied": False,
            "reason": "scorer_failed",
            "error_type": type(exc).__name__,
            "pool_size": len(original),
            "returned": min(limit, len(original)),
        }, original, {}
    if scores is None or len(scores) != len(union):
        return original[:limit], {
            "applied": False,
            "reason": "scorer_unavailable_or_invalid",
            "pool_size": len(original),
            "returned": min(limit, len(original)),
        }, original, {}
    by_id = {
        str(item.get("node_id") or ""): float(score)
        for item, score in zip(union, scores)
    }
    guarded_top, guarded_scores = _rank_by_scores(guarded, by_id, limit)
    original_top, original_scores = _rank_by_scores(original, by_id, limit)
    survived = bool(nominee_id) and str(nominee_id) in {
        str(item.get("node_id") or "") for item in guarded_top
    }
    selected_pool = guarded if survived else original
    selected = guarded_top if survived else original_top
    top_scores = guarded_scores if survived else original_scores
    return selected, {
        "applied": True,
        "pool_size": len(selected_pool),
        "scored_pool_size": len(union),
        "returned": len(selected),
        "top_scores": top_scores,
        "lexical_survival": {
            "nominee": nominee_id,
            "nominee_in_guarded_top_k": survived,
            "selected_pool": "guarded" if survived else "original_mmr",
        },
    }, selected_pool, by_id


def rerank_with_guarded_nominee(
    query: str,
    baseline_top: Sequence[Dict[str, Any]],
    baseline_pool: Sequence[Dict[str, Any]],
    baseline_scores: Dict[str, float],
    guarded_pool: Sequence[Dict[str, Any]],
    nominee_id: Optional[str],
    *,
    limit: int,
    text_fn: Callable[[Dict[str, Any]], str],
    scorer: Callable[[str, List[str]], Optional[List[float]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], Dict[str, float]]:
    """Run one fixed-size guarded BGE pool and otherwise preserve baseline.

    This is intentionally separate from ``rerank_with_lexical_survival``.  The
    baseline BGE pass has already happened, so the tag nominee competes in one
    replacement pool of the exact same size.  If it misses top-K (or scoring
    fails), every baseline object, order, score map, and downstream pool is
    returned unchanged.
    """
    baseline_selected = list(baseline_top)
    original_pool = list(baseline_pool)
    guarded = list(guarded_pool)
    fallback_scores = dict(baseline_scores)
    info: Dict[str, Any] = {
        "applied": False,
        "pool_size": len(guarded),
        "baseline_pool_size": len(original_pool),
        "returned": min(max(0, limit), len(baseline_selected)),
        "lexical_survival": {
            "nominee": nominee_id,
            "nominee_in_guarded_top_k": False,
            "selected_pool": "baseline",
        },
    }
    if limit <= 0 or not guarded or not nominee_id:
        info["reason"] = "empty_or_invalid_budget"
        return baseline_selected[:max(0, limit)], info, original_pool, fallback_scores
    documents = [text_fn(item) for item in guarded]
    try:
        scores = scorer(query or "", documents)
    except Exception as exc:
        info.update({"reason": "scorer_failed", "error_type": type(exc).__name__})
        return baseline_selected[:limit], info, original_pool, fallback_scores
    if scores is None or len(scores) != len(guarded):
        info["reason"] = "scorer_unavailable_or_invalid"
        return baseline_selected[:limit], info, original_pool, fallback_scores
    guarded_scores_by_id = {
        str(item.get("node_id") or ""): float(score)
        for item, score in zip(guarded, scores)
    }
    guarded_top, guarded_scores = _rank_by_scores(
        guarded, guarded_scores_by_id, limit,
    )
    survived = str(nominee_id) in {
        str(item.get("node_id") or "") for item in guarded_top
    }
    info.update({
        "applied": True,
        "returned": len(guarded_top) if survived else min(limit, len(baseline_selected)),
        "top_scores": guarded_scores if survived else [
            round(float(item.get("_local_rerank_score") or 0.0), 6)
            for item in baseline_selected[:limit]
        ],
        "lexical_survival": {
            "nominee": nominee_id,
            "nominee_in_guarded_top_k": survived,
            "selected_pool": "guarded" if survived else "baseline",
        },
    })
    if not survived:
        return baseline_selected[:limit], info, original_pool, fallback_scores
    return guarded_top, info, guarded, guarded_scores_by_id


def apply_post_bge_fusion_reserve(
    query: str,
    selected_pool: Sequence[Dict[str, Any]],
    selected: Sequence[Dict[str, Any]],
    scores_by_id: Dict[str, float],
    *,
    limit: int,
    min_unique_tokens: int = 20,
    min_query_overlap_delta: int = 1,
    short_min_unique_tokens: int = 15,
    short_min_query_overlap_delta: int = 2,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Replace the weakest BGE item with the best eligible omitted fusion item."""
    current = list(selected)
    if not current or limit <= 0 or not scores_by_id:
        return current[:max(0, limit)], {"enabled": True, "applied": False}
    query_terms = semantic_query_terms(query)
    selected_ids = {str(item.get("node_id") or "") for item in current}
    omitted = sorted(
        (item for item in selected_pool if str(item.get("node_id") or "") not in selected_ids),
        key=lambda item: (-float(item.get("score") or 0.0), str(item.get("node_id") or "")),
    )
    victim_index = min(
        range(len(current)),
        key=lambda index: scores_by_id.get(
            str(current[index].get("node_id") or ""), float("-inf"),
        ),
    )
    victim = current[victim_index]
    victim_terms = set(lemmatize_for_bm25(item_content(victim)).split())
    victim_overlap = len(query_terms & victim_terms)
    decisions = []
    chosen = None
    for reserve in omitted:
        terms = set(lemmatize_for_bm25(item_content(reserve)).split())
        overlap = len(query_terms & terms)
        delta = overlap - victim_overlap
        eligible = bool(
            (len(terms) >= min_unique_tokens and delta >= min_query_overlap_delta)
            or (
                len(terms) >= short_min_unique_tokens
                and delta >= short_min_query_overlap_delta
            )
        )
        decisions.append({
            "admitted": reserve.get("node_id"),
            "admitted_unique_tokens": len(terms),
            "admitted_query_overlap": overlap,
            "displaced_query_overlap": victim_overlap,
            "query_overlap_delta": delta,
            "eligible": eligible,
        })
        if eligible:
            chosen = reserve
            break
    if chosen is None:
        return current, {
            "enabled": True, "applied": False, "decisions": decisions,
            "policy": "eligible_first_union_gate_v1",
        }
    chosen_id = str(chosen.get("node_id") or "")
    current[victim_index] = {
        **chosen,
        "_local_rerank_score": scores_by_id[chosen_id],
    }
    current.sort(
        key=lambda item: scores_by_id.get(
            str(item.get("node_id") or ""), float("-inf"),
        ),
        reverse=True,
    )
    return current[:limit], {
        "enabled": True,
        "applied": True,
        "policy": "eligible_first_union_gate_v1",
        "admitted": chosen_id,
        "displaced": victim.get("node_id"),
        "decisions": decisions,
    }


def ensure_unique_top_k(
    selected: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    scores_by_id: Dict[str, float],
    *,
    limit: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Deduplicate final IDs and backfill only when a duplicate is present.

    This is a strict no-op for already-unique results. Backfill uses already
    scored candidates only; it does not retrieve or score anything new.
    """
    original = list(selected)[:max(0, limit)]
    ids = [str(item.get("node_id") or "") for item in original]
    if len(ids) == len(set(ids)):
        return original, {"applied": False, "duplicates": []}
    unique = []
    seen = set()
    duplicates = []
    for item in original:
        node_id = str(item.get("node_id") or "")
        if not node_id or node_id in seen:
            duplicates.append(node_id)
            continue
        seen.add(node_id)
        unique.append(item)
    ranked_backfill = sorted(
        (item for item in candidates if str(item.get("node_id") or "") not in seen),
        key=lambda item: (
            -float(scores_by_id.get(str(item.get("node_id") or ""), float("-inf"))),
            str(item.get("node_id") or ""),
        ),
    )
    for item in ranked_backfill:
        node_id = str(item.get("node_id") or "")
        if not node_id or node_id in seen:
            continue
        unique.append({
            **item,
            "_local_rerank_score": scores_by_id.get(
                node_id, item.get("_local_rerank_score", 0.0)
            ),
        })
        seen.add(node_id)
        if len(unique) >= limit:
            break
    return unique[:limit], {
        "applied": True,
        "duplicates": duplicates,
        "returned": len(unique[:limit]),
    }
