"""
Reciprocal Rank Fusion —— 多路召回的 rank 级融合。

公式（Cormack et al. 2009）：

    score(d) = Σ_c [ w_c / (k + rank_c(d)) ]

关键性质：
  - 只看 rank 不看绝对分数 → 规避"vec cosine [0,1] 和 BM25 [0,+∞) 不可比"
  - 每路独立排名，出现在多路中的 doc 天然累加得分
  - 意图权重 w_c 按场景放大某一路贡献
"""

from typing import Any, Dict, List, Optional

from . import config


def rrf_fuse(
    channels: Dict[str, List[Dict[str, Any]]],
    weights: Optional[Dict[str, float]] = None,
    k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    RRF 融合多路召回结果。

    Args:
        channels: {"vec": [hit, ...], "tag": [hit, ...], "bm25": [hit, ...]}
                  每路的 hits **必须按各自分数降序排好**。每条 hit 必须含
                  `node_id` 字段；`node` 字段强烈推荐（用于输出层 hydrate）。
        weights:  通道权重；缺省则每路 1.0。未命中的通道名不产生贡献。
        k:        RRF 平滑常数，默认 config.RRF_K（60）。

    Returns:
        按 RRF 分数降序的合并列表，每条记录包括：
          {
            "node_id": ...,
            "node": ...,                # 若任一路原 hit 有 node 字段则保留
            "rrf_score": float,         # 融合后分数（未归一化）
            "rrf_rank_by_channel": {channel: rank, ...},  # 观测用
            "per_channel_score": {channel: raw_score, ...},
          }
    """
    weights = weights or {}
    k = k if k is not None else config.RRF_K

    acc: Dict[str, Dict[str, Any]] = {}

    for channel_name, hits in channels.items():
        w = float(weights.get(channel_name, 1.0))
        if w == 0 or not hits:
            continue
        # RRF semantics: each channel contributes at most once per document,
        # using its best (lowest) rank. A channel that returns the same node_id
        # more than once must not inflate the score by the duplicate count or
        # overwrite the rank with a worse one.
        seen_in_channel = set()
        for rank, hit in enumerate(hits, start=1):
            nid = hit.get("node_id")
            if not nid or nid in seen_in_channel:
                continue
            seen_in_channel.add(nid)
            contrib = w / (k + rank)
            entry = acc.get(nid)
            if entry is None:
                entry = {
                    "node_id": nid,
                    "node": hit.get("node"),
                    "rrf_score": 0.0,
                    "rrf_rank_by_channel": {},
                    "per_channel_score": {},
                }
                acc[nid] = entry
            else:
                # 补上先前路没带的 node 对象
                if entry.get("node") is None and hit.get("node") is not None:
                    entry["node"] = hit.get("node")
            entry["rrf_score"] += contrib
            entry["rrf_rank_by_channel"][channel_name] = rank
            # Per-channel score is observation-only; a None value must not
            # abort the whole fusion (mirrors reconcile_retrieval.py).
            entry["per_channel_score"][channel_name] = float(hit.get("score", 0.0) or 0.0)

    fused = list(acc.values())
    fused.sort(key=lambda x: x["rrf_score"], reverse=True)
    return fused


def compute_confidence(fused: List[Dict[str, Any]], top_n: int = 3) -> float:
    """
    基于 top-N RRF 分平均值的 proxy confidence，用于弃权判定。

    返回 [0, ~1] 的分数——RRF 分数的理论上界与路数和权重有关，这里只做
    相对置信度，业务方不应把它当精确概率使用。
    """
    if not fused:
        return 0.0
    n = min(top_n, len(fused))
    avg = sum(h.get("rrf_score", 0.0) for h in fused[:n]) / max(n, 1)
    # 经验归一化：单路权重 1.0 时最高贡献 ≈ 1/(60+1) ≈ 0.0164；三路都在 top-1
    # 总和约 0.05。把 0.05 映射到 1.0 上做粗糙 scaling
    return min(1.0, avg / 0.05)
