# -*- coding: utf-8 -*-
"""
HY Memory - Intention (L7) 召回 + 惰性过期转换 + 复本判定

意图（L7_INTENTION）由 extractor（System 1）直接写入 VDB，带可选 `valid_until`
截止日。reader 在 proactive 路调用本模块召回意图：

  - 召回 status=ACTIVE 的 L7 节点（**超采样**，见下）；
  - 对每条命中检查 `valid_until`：若已过期（now > valid_until），则**惰性**把该
    节点的 layer 改写为 l2_fact（持久化 update_payload），并从意图结果里剔除——
    过期意图变成普通历史事实，下次走 normal 召回；
  - **判重（重点）**：对幸存意图按下述规则分组，同组只留一条本次返回：
        同 user_id + 同 agent_id + 同一天 memory_at + valid_until 相同
        （都为 None 或都相等）+ cosine 相似度 ≥ MEMORY_DEDUP_THRESHOLD (默认 0.95)
    → 视为同一个意图的复本（extractor 复读机 / 多 session 重复表达）。
    保留组里 `gmt_created` 最早的（老的先来先得，稳定 id）；
  - **异步删库**：其余复本 fire-and-forget 从 VDB 删除，不影响本次调用 RT。

Q: 为什么要看 memory_at 和 valid_until？
A: 意图是 point-in-time 信号。同一句"我周五要开会"，如果一条 memory_at 在
   本周一（valid_until=本周五）、另一条 memory_at 在下周一（valid_until=下
   周五），指向的是**两次不同的会议**，绝不能合并。所以只看 cosine 会误伤。

best-effort：过期转换 / dedup / 异步删除任何异常都不影响主 search 流程。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from ...models.memory import MemoryLayer, MemoryStatus

logger = logging.getLogger(__name__)


def _get_dedup_threshold() -> float:
    """去重 cosine 阈值：沿用 dedup 内核的 MEMORY_DEDUP_THRESHOLD，默认 0.95。"""
    try:
        return float(os.environ.get("MEMORY_DEDUP_THRESHOLD", 0.95))
    except (TypeError, ValueError):
        return 0.95


def _memory_at_day(node: Any) -> Optional[date]:
    """取 node.memory_at 的日期部分；None 返回 None。"""
    if node is None:
        return None
    ma = getattr(node, "memory_at", None)
    if ma is None:
        return None
    # datetime 或 date 都兼容
    try:
        return ma.date() if hasattr(ma, "date") else ma
    except Exception:
        return None


def _valid_until_key(node: Any) -> Any:
    """valid_until 归一化到"比较键"：datetime→date，None→None。

    只要求日粒度一致（valid_until 本身在 writer 里被 pin 到当天日末，同天视为等）。
    """
    if node is None:
        return None
    vu = getattr(node, "valid_until", None)
    if vu is None:
        return None
    try:
        return vu.date() if hasattr(vu, "date") else vu
    except Exception:
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    """朴素 cosine（同事说"从 VDB 拿现成 embed，不重算"，这里只是对拿到的向量比一次）。

    重复调用量级：每次 search 内 fetch_limit ≤ 30，两两比较 O(n²) 也就 <500 次
    浮点内积，无需 numpy。返回 [-1, 1]，向量为空返回 -inf 避免误判。
    """
    if not a or not b:
        return float("-inf")
    la = sum(x * x for x in a) ** 0.5
    lb = sum(x * x for x in b) ** 0.5
    if la == 0.0 or lb == 0.0:
        return float("-inf")
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (la * lb)


def _partition_duplicates(
    hits: List[Dict[str, Any]],
    threshold: float,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """把 hits 分成"保留列表"和"待删 node_id 列表"。

    分组键：(memory_at.day, valid_until.day)；每组内两两比 cosine，
    ≥ threshold 视为复本。复本组保留 gmt_created 最早的（gmt 缺失视为 +inf
    最晚），其余进入 delete_ids。

    Args:
        hits: recall + 未过期过滤后的 L7 命中
        threshold: cosine 复本阈值

    Returns:
        (kept_hits, delete_ids) — kept_hits 保序（保留项按原顺序）
    """
    if len(hits) < 2:
        return list(hits), []

    # 1. 按 (user_id, agent_id, memory_at day, valid_until day) 分桶。
    #    user_id / agent_id 必须进键：多用户检索时不同用户的同一天意图可能
    #    文本、日期都相同，但它们是两个独立的租户数据；只按日期分桶会把其中
    #    一个判成复本并硬删除。
    buckets: Dict[Tuple[Any, Any, Any, Any], List[int]] = {}
    for idx, h in enumerate(hits):
        node = h.get("node")
        key = (
            getattr(node, "user_id", None) if node else None,
            getattr(node, "agent_id", None) if node else None,
            _memory_at_day(node),
            _valid_until_key(node),
        )
        buckets.setdefault(key, []).append(idx)

    def _gmt(h: Dict[str, Any]) -> float:
        node = h.get("node")
        gc = getattr(node, "gmt_created", None) if node else None
        if gc is None:
            return float("inf")
        try:
            return gc.timestamp() if hasattr(gc, "timestamp") else float(gc)
        except Exception:
            return float("inf")

    dropped_idx: set = set()

    # 2. 每桶内两两比 cosine → 并查集
    for key, idx_list in buckets.items():
        if len(idx_list) < 2:
            continue

        parent = {i: i for i in idx_list}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # 提取 embedding
        embs: Dict[int, List[float]] = {}
        for i in idx_list:
            node = hits[i].get("node")
            emb = getattr(node, "embedding", None) if node else None
            if emb:
                embs[i] = emb

        # 两两比较
        candidates = [i for i in idx_list if i in embs]
        for a_pos in range(len(candidates)):
            for b_pos in range(a_pos + 1, len(candidates)):
                ia, ib = candidates[a_pos], candidates[b_pos]
                if _cosine(embs[ia], embs[ib]) >= threshold:
                    union(ia, ib)

        # 3. 每个连通分量保留 gmt 最早的
        groups: Dict[int, List[int]] = {}
        for i in idx_list:
            groups.setdefault(find(i), []).append(i)

        for members in groups.values():
            if len(members) < 2:
                continue  # 单点，非复本
            # 保留 gmt 最早
            keeper = min(members, key=lambda i: _gmt(hits[i]))
            for i in members:
                if i != keeper:
                    dropped_idx.add(i)

    kept = [h for i, h in enumerate(hits) if i not in dropped_idx]
    delete_ids = [
        hits[i].get("node_id") for i in sorted(dropped_idx)
        if hits[i].get("node_id")
    ]
    return kept, delete_ids


def _spawn_async_delete(
    vector_store: Any,
    delete_ids: List[str],
    *,
    user_id_for_log: str = "",
) -> None:
    """fire-and-forget 从 VDB 删除复本，不影响主 search RT。

    - 用 asyncio.create_task 挂到当前 loop（recall 已经在 async 上下文）；
    - 失败逐条 swallow，最后一条日志汇总；
    - 若拿不到 loop（极少见），静默跳过。
    """
    if not delete_ids or vector_store is None:
        return

    async def _run():
        ok, fail = 0, 0
        for nid in delete_ids:
            try:
                await vector_store.delete(nid)
                ok += 1
            except Exception as e:
                fail += 1
                logger.debug(f"[intention] async delete {nid} failed: {e}")
        logger.info(
            f"[intention] async dedup delete done: ok={ok} fail={fail} "
            f"total={len(delete_ids)} user_id={user_id_for_log or '-'}"
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        # 无 running loop：静默跳过（recall 本身在 async 环境，一般不会走到）
        logger.debug("[intention] no running loop for async delete; skipped")


async def recall_intentions(
    vector_store: Any,
    query_embedding: List[float],
    *,
    user_ids: Optional[List[str]] = None,
    agent_ids: Optional[List[str]] = None,
    isolation_key: str = "",
    isolation_keys: Optional[List[str]] = None,
    limit: int = 10,
    now: Optional[datetime] = None,
    stats_out: Optional[Dict[str, Any]] = None,
    include_sources: Optional[List[str]] = None,
    exclude_sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    召回未过期的 L7_INTENTION 节点（VDB），惰性过期转 L2_FACT，判重并异步删复本。

    Args:
        vector_store: VectorStoreBase 实例
        query_embedding: 查询向量
        user_ids: 用户隔离（单/多用户）
        agent_ids: 可选 agent 过滤
        isolation_key / isolation_keys: 精确的 (user, agent, session) 隔离键。
            与其它召回通道一致：调用方在按 session 过滤时只生成这两项，
            user_ids / agent_ids 为 None；不把它们透传下去就会退化成全库检索。
        limit: 召回上限（返回给上游的条数）
        now: 当前时间（默认 datetime.now()，测试可注入）
        stats_out: 可选 dict，本函数把分段耗时与数量**原地回填**进去，供调用方落
            READ_INTENTION pipeline log。传 None 则完全不统计（零开销）。

    Returns:
        [{"node_id", "score", "node", "layer": "l7_intention", "source": "vdb_intention"}, ...]

    实现细节:
      - **超采样**：VDB 侧多拿一些，扛住 L7 复读机；判重后截取 limit
      - **判重规则**：同一天 memory_at + valid_until 同日 + cosine≥0.95。
      - **异步删库**：待删复本 fire-and-forget 从 VDB 删，不影响返回 RT。

    埋点字段（回填进 stats_out）:
      耗时(ms): vdb_search_ms / expire_ms / backfill_embed_ms / dedup_ms / total_ms
      数量: fetched / expired / survivors / dedup_dropped / returned
      配置: fetch_limit / out_limit / dedup_threshold
      说明: expire_ms、backfill_embed_ms 只有真的发生了对应操作才非 0，
            借此可判断慢在 VDB 检索、逐条过期转换、补拉向量还是判重计算。
    """
    if limit <= 0:
        return []
    _now = now or datetime.now()
    _t_total = time.perf_counter()

    def _stat(**kv: Any) -> None:
        """回填埋点字段；stats_out 为 None 时是 no-op。"""
        if stats_out is not None:
            stats_out.update(kv)

    # 超采样：VDB 侧多拿一些，扛住 L7 复读机；判重后截取 limit
    fetch_limit = max(limit * 6, limit + 15)
    _stat(fetch_limit=fetch_limit, out_limit=limit)

    _t = time.perf_counter()
    try:
        hits = await vector_store.search(
            query_embedding=query_embedding,
            user_ids=user_ids or None,
            agent_ids=agent_ids,
            isolation_key=isolation_key,
            isolation_keys=isolation_keys,
            layers=[MemoryLayer.L7_INTENTION],
            limit=fetch_limit,
            score_threshold=0.0,  # intention 不设语义门槛
            status_filter=[MemoryStatus.ACTIVE],
            only_latest=True,
            include_sources=include_sources,
            exclude_sources=exclude_sources,
        ) or []
    except Exception as e:
        logger.debug(f"[intention] recall search failed: {e}")
        _stat(
            vdb_search_ms=round((time.perf_counter() - _t) * 1000, 2),
            total_ms=round((time.perf_counter() - _t_total) * 1000, 2),
            fetched=0, returned=0, search_failed=True,
        )
        return []
    _stat(vdb_search_ms=round((time.perf_counter() - _t) * 1000, 2), fetched=len(hits))

    # 1. 惰性过期转换：valid_until 已过的 → 转 L2_FACT + 剔除
    _t = time.perf_counter()
    survivors: List[Dict[str, Any]] = []
    expired_count = 0
    for h in hits:
        node = h.get("node")
        node_id = h.get("node_id", "")
        valid_until = getattr(node, "valid_until", None) if node else None

        if valid_until and _now > valid_until:
            try:
                await vector_store.update_payload(node_id, {"layer": MemoryLayer.L2_FACT.value})
                logger.debug(
                    f"[intention] expired L7 → L2_FACT: node_id={node_id} "
                    f"valid_until={valid_until}"
                )
                expired_count += 1
            except Exception as e:
                logger.debug(f"[intention] expire conversion failed for {node_id}: {e}")
            continue

        survivors.append({
            **h,
            "layer": MemoryLayer.L7_INTENTION.value,
            "retrieval_source": "vdb_intention",
        })
    # 无过期节点时这段是纯内存循环、耗时≈0；非 0 即为逐条 update_payload 的开销
    _stat(
        expire_ms=round((time.perf_counter() - _t) * 1000, 2),
        expired=expired_count,
        survivors=len(survivors),
    )

    # 1.5. 关键补丁：不同 VDB 后端的 search() 是否携带 embedding 不一致
    #    - Qdrant/Faiss 部分场景 hit 带 embedding
    #    - Chroma search() 默认 include 不含 embeddings → node.embedding=None
    # 若判重时拿不到 embedding，_partition_duplicates 会静默降级为"不去重"。
    # 这里对缺失 embedding 的节点，用 get_embeddings 批量补拉（VDB 已有向量，
    # 不重算 embed 服务）。失败 → 保持 None → dedup 自然降级，不影响主流程。
    _t = time.perf_counter()
    _backfilled = 0
    _missing_cnt = 0
    if survivors:
        missing_ids = [
            h.get("node_id") for h in survivors
            if h.get("node_id") and not getattr(h.get("node"), "embedding", None)
        ]
        _missing_cnt = len(missing_ids)
        if missing_ids and hasattr(vector_store, "get_embeddings"):
            try:
                emb_map = await vector_store.get_embeddings(missing_ids)
                if emb_map:
                    for h in survivors:
                        node = h.get("node")
                        nid = h.get("node_id")
                        if node is not None and nid in emb_map and not getattr(node, "embedding", None):
                            try:
                                node.embedding = list(emb_map[nid])
                            except Exception:
                                pass
                    _backfilled = len(emb_map)
                    logger.debug(
                        f"[intention] backfilled embeddings for {len(emb_map)}/"
                        f"{len(missing_ids)} nodes before dedup"
                    )
            except Exception as e:
                logger.debug(f"[intention] get_embeddings failed (skip backfill): {e}")
    # 只有真的补拉过才非 0；非 0 且很大 → get_embeddings 是瓶颈
    _stat(
        backfill_embed_ms=round((time.perf_counter() - _t) * 1000, 2),
        embed_missing=_missing_cnt,
        embed_backfilled=_backfilled,
    )

    # 2. 判重：同 user + 同天 memory_at + valid_until 同日 + cosine≥threshold
    _t = time.perf_counter()
    threshold = _get_dedup_threshold()
    try:
        kept, delete_ids = _partition_duplicates(survivors, threshold)
    except Exception as e:
        # 任何 dedup 异常 → 降级为不去重（返回全部 survivors 截断 limit）
        logger.warning(f"[intention] dedup partition failed, degrade to raw: {e}")
        kept, delete_ids = survivors, []
    # 纯 CPU（cosine 两两比较）；survivors 多时可能显著
    _stat(
        dedup_ms=round((time.perf_counter() - _t) * 1000, 2),
        dedup_dropped=len(delete_ids),
        dedup_threshold=threshold,
    )

    # 3. 截断到调用方要求的 limit
    result = kept[:limit]
    _stat(
        returned=len(result),
        total_ms=round((time.perf_counter() - _t_total) * 1000, 2),
    )

    # 4. 异步删库（fire-and-forget）
    if delete_ids:
        user_id_for_log = (user_ids[0] if user_ids else "") or ""
        _spawn_async_delete(vector_store, delete_ids, user_id_for_log=user_id_for_log)

    if expired_count or delete_ids or len(hits) != len(result):
        logger.info(
            f"[intention] recall: fetched={len(hits)} expired={expired_count} "
            f"survivors={len(survivors)} dedup_dropped={len(delete_ids)} "
            f"kept_after_dedup={len(kept)} returned={len(result)} "
            f"(fetch_limit={fetch_limit}, out_limit={limit}, thr={threshold})"
        )
    return result
