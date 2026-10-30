# -*- coding: utf-8 -*-
"""Light-search diversity helpers (no LLM, no extra embed API).

Historical helper used before top-k truncation:
  - social-boilerplate demotion (meet-up / catch-up templates — structural, not domain lexicon)
  - pool DF demotion (tokens that dominate the candidate pool)
  - near-dup demotion via content token Jaccard
  - MMR selection to reduce homogeneous clusters flooding top-k
  - RRF blend so weak BM25 still moves rank
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Discourse-level social scheduling / check-in templates (not topic lexicons).
_SOCIAL_BOILER_RE = re.compile(
    r"(?i)\b("
    r"plan(?:s|ned|ning)?\s+to\s+catch\s+up|catch\s+up\s+soon|"
    r"get\s+together|"
    r"offered\s+to\s+(?:get\s+together|show|help|meet)|"
    r"accepted\s+\w+\s+offer\s+to\s+get\s+together|"
    r"enjoying\s+(?:his|her|their)\s+new\s+hobbies|"
    r"how\s+\w+\s+is\s+enjoying\s+(?:his|her|their)\s+new\s+hobbies"
    r")\b"
)


def content_tokens(text: str) -> Set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / float(len(a | b))


def _hit_content(hit: Dict[str, Any]) -> str:
    c = hit.get("content")
    if c:
        return str(c)
    node = hit.get("node")
    if node is not None:
        return str(getattr(node, "content", "") or "")
    return ""


def is_social_boilerplate(text: str) -> bool:
    return bool(_SOCIAL_BOILER_RE.search(text or ""))


def social_boilerplate_demote(
    hits: Sequence[Dict[str, Any]],
    *,
    penalty: float = 0.22,
    score_key: str = "score",
) -> List[Dict[str, Any]]:
    """Multiply scores of meet-up / catch-up template memories.

    Structural discourse prior — not a per-topic expand lexicon.
    """
    if not hits or penalty <= 0:
        return list(hits)
    out: List[Dict[str, Any]] = []
    for h in hits:
        item = dict(h)
        if is_social_boilerplate(_hit_content(item)):
            sc = float(item.get(score_key) or 0.0)
            item[score_key] = sc * (1.0 - float(penalty))
            item["_social_boiler_demoted"] = True
        out.append(item)
    out.sort(key=lambda h: float(h.get(score_key) or 0.0), reverse=True)
    return out


def pool_df_demote(
    hits: Sequence[Dict[str, Any]],
    *,
    df_thr: float = 0.4,
    penalty: float = 0.18,
    score_key: str = "score",
    min_token_len: int = 4,
) -> List[Dict[str, Any]]:
    """Demote hits whose content is dominated by high-DF tokens in this pool.

    If many candidates share the same hub phrases (names + plan verbs), those
    hits lose score — lifts rarer, more specific facts without topic rules.
    """
    if not hits or df_thr <= 0 or penalty <= 0 or len(hits) < 4:
        return list(hits)
    n = float(len(hits))
    tok_lists: List[Set[str]] = []
    df: Counter = Counter()
    for h in hits:
        toks = {
            t for t in content_tokens(_hit_content(h))
            if len(t) >= min_token_len
        }
        tok_lists.append(toks)
        for t in toks:
            df[t] += 1
    hub = {t for t, c in df.items() if (c / n) >= df_thr}
    if not hub:
        return list(hits)
    out: List[Dict[str, Any]] = []
    for h, toks in zip(hits, tok_lists):
        item = dict(h)
        if toks and len(toks & hub) / float(len(toks)) >= 0.35:
            sc = float(item.get(score_key) or 0.0)
            item[score_key] = sc * (1.0 - float(penalty))
            item["_pool_df_demoted"] = True
        out.append(item)
    out.sort(key=lambda h: float(h.get(score_key) or 0.0), reverse=True)
    return out


def near_dup_demote(
    hits: Sequence[Dict[str, Any]],
    *,
    jaccard_thr: float = 0.55,
    penalty: float = 0.28,
    score_key: str = "score",
) -> List[Dict[str, Any]]:
    """Demote near-duplicate contents that trail a higher-scoring sibling.

    Keeps all hits (no hard drop) so later MMR / temporal can still promote;
    multiplies trailing near-dups by ``(1 - penalty)``.
    """
    if not hits or jaccard_thr <= 0 or penalty <= 0:
        return list(hits)
    ordered = sorted(
        hits, key=lambda h: float(h.get(score_key) or 0.0), reverse=True
    )
    kept_toks: List[Set[str]] = []
    out: List[Dict[str, Any]] = []
    for h in ordered:
        toks = content_tokens(_hit_content(h))
        dup = False
        for prev in kept_toks:
            if jaccard(toks, prev) >= jaccard_thr:
                dup = True
                break
        item = dict(h)
        if dup:
            sc = float(item.get(score_key) or 0.0)
            item[score_key] = sc * (1.0 - float(penalty))
            item["_near_dup_demoted"] = True
        else:
            if toks:
                kept_toks.append(toks)
        out.append(item)
    out.sort(key=lambda h: float(h.get(score_key) or 0.0), reverse=True)
    return out


# MMR 只在调用方请求的 top-k（search 的 limit）小于这个值时自动启用。
# mmr_select 的贪心是 O(k^2 * pool) 次 Jaccard，k 大时代价陡增，而大 k 本身已
# 足够容纳多样性，收益很小。权重仍由 MEMORY_HYBRID_V2_MMR_LAMBDA 控制。
MMR_MAX_TOP_K = 20


def mmr_applies(top_k: int, lambda_: float) -> bool:
    """本次检索是否走 MMR：λ 在有效区间内，且 top_k < MMR_MAX_TOP_K。"""
    return 0.0 < float(lambda_) < 0.999 and int(top_k) < MMR_MAX_TOP_K


def near_dup_applies(top_k: int) -> bool:
    """本次检索是否走近重复降权：与 MMR 同一个 top-k 门槛。

    near_dup_demote 让每个候选和此前所有"非重复"候选两两算 Jaccard，候选
    互不相似时是 O(pool^2)；池子随 limit 放大（约 max(3*limit, 30) + 关键词
    命中），limit 达到 MMR_MAX_TOP_K 时代价同样陡增。大 k 的调用方（如记忆
    面板翻页）要的是全部相关项，相似项也应照常出现，降权没有收益。
    """
    return int(top_k) < MMR_MAX_TOP_K


def mmr_select(
    hits: Sequence[Dict[str, Any]],
    limit: int,
    *,
    lambda_: float = 0.7,
    score_key: str = "score",
    sim_fn: Optional[Callable[[Dict[str, Any], Dict[str, Any]], float]] = None,
) -> List[Dict[str, Any]]:
    """Greedy MMR: ``λ·rel − (1−λ)·max_sim(selected)``.

    Default similarity is content-token Jaccard (local, ms-level).
    """
    if limit <= 0 or not hits:
        return []
    if len(hits) <= limit or lambda_ >= 0.999:
        return sorted(
            hits, key=lambda h: float(h.get(score_key) or 0.0), reverse=True
        )[:limit]

    if sim_fn is None:
        tok_cache: Dict[int, Set[str]] = {}

        def _tok(h: Dict[str, Any]) -> Set[str]:
            i = id(h)
            if i not in tok_cache:
                tok_cache[i] = content_tokens(_hit_content(h))
            return tok_cache[i]

        def sim_fn(a: Dict[str, Any], b: Dict[str, Any]) -> float:
            return jaccard(_tok(a), _tok(b))

    remaining = list(hits)
    selected: List[Dict[str, Any]] = []
    # seed with best relevance
    remaining.sort(key=lambda h: float(h.get(score_key) or 0.0), reverse=True)
    selected.append(remaining.pop(0))

    while remaining and len(selected) < limit:
        best_i = 0
        best_val = float("-inf")
        for i, cand in enumerate(remaining):
            rel = float(cand.get(score_key) or 0.0)
            max_sim = 0.0
            for s in selected:
                max_sim = max(max_sim, float(sim_fn(cand, s)))
            val = float(lambda_) * rel - (1.0 - float(lambda_)) * max_sim
            if val > best_val:
                best_val = val
                best_i = i
        selected.append(remaining.pop(best_i))
    return selected


def blend_rrf_into_scores(
    hits: Sequence[Dict[str, Any]],
    *,
    blend: float = 0.4,
    bm25_weight: float = 1.25,
    score_key: str = "score",
    sem_key: str = "_semantic",
    bm25_key: str = "_bm25",
) -> List[Dict[str, Any]]:
    """Blend additive scores with RRF(sem, bm25, additive) so weak BM25 still moves rank.

    ``blend=0`` → unchanged; ``blend=1`` → fully RRF-scaled to pool max additive.
    """
    from .rrf import rrf_fuse

    if not hits or blend <= 0:
        return list(hits)
    items = [dict(h) for h in hits]
    sem_ch = sorted(
        items, key=lambda h: float(h.get(sem_key) or 0.0), reverse=True
    )
    bm25_ch = sorted(
        [h for h in items if float(h.get(bm25_key) or 0.0) > 0],
        key=lambda h: float(h.get(bm25_key) or 0.0),
        reverse=True,
    )
    add_ch = sorted(
        items, key=lambda h: float(h.get(score_key) or 0.0), reverse=True
    )
    channels = {"sem": sem_ch, "add": add_ch}
    weights = {"sem": 1.0, "add": 0.85}
    if bm25_ch:
        channels["bm25"] = bm25_ch
        weights["bm25"] = float(bm25_weight)
    fused = rrf_fuse(channels, weights=weights)
    if not fused:
        return items
    rrf_by = {f["node_id"]: float(f.get("rrf_score") or 0.0) for f in fused}
    max_rrf = max(rrf_by.values()) or 1.0
    max_add = max(float(h.get(score_key) or 0.0) for h in items) or 1.0
    b = max(0.0, min(1.0, float(blend)))
    for h in items:
        nid = h.get("node_id")
        r_norm = (rrf_by.get(nid, 0.0) / max_rrf) if nid else 0.0
        add = float(h.get(score_key) or 0.0)
        h[score_key] = (1.0 - b) * add + b * max_add * r_norm
        h["_rrf_norm"] = r_norm
    items.sort(key=lambda h: float(h.get(score_key) or 0.0), reverse=True)
    return items
