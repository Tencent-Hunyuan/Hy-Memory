"""
演化链回溯。

原逻辑从 `reader.py` 抽出，由 legacy / hybrid 两个 reader 共用。

职责：
  对一批 search hits，识别其中在演化链上的节点，双向追溯完整链，
  以链头（is_latest=True）为代表返回，同链多个 hit 去重合并。

返回格式：
  每个 evolved hit 附加 `evolution_chain` 字段（list，latest→oldest 排序），
  每个元素 = {node_id, content, memory_at, gmt_created, speculate, layer}。
  时间字段统一为 Unix timestamp (float)。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from ...data.vector_store_base import NativeQuery, VectorStoreBase
import asyncio
import logging

from ...models.memory import MemoryNode

logger = logging.getLogger(__name__)


def _native_evolution_edges(known: Dict[str, MemoryNode]) -> Dict[str, Set[str]]:
    """Validate partial declarations before requesting another evolution frontier."""
    from ...retrieval import NativeRetrievalError

    outgoing = {nid: set() for nid in known}
    for nid, node in known.items():
        for newer in node.superseded_by or []:
            outgoing[nid].add(newer)
            outgoing.setdefault(newer, set())
        for older in node.supersedes or []:
            outgoing.setdefault(older, set()).add(nid)

    visiting, depths = set(), {}

    def depth(nid: str) -> int:
        if nid in visiting:
            raise NativeRetrievalError("cycle", "directed evolution cycle")
        if nid in depths:
            return depths[nid]
        visiting.add(nid)
        if len(visiting) > 33:
            raise NativeRetrievalError("budget", "native evolution exceeds 32 hops")
        value = max((1 + depth(newer) for newer in outgoing[nid]), default=0)
        visiting.remove(nid)
        if value > 32:
            raise NativeRetrievalError("budget", "native evolution exceeds 32 hops")
        depths[nid] = value
        return value

    for nid in outgoing:
        depth(nid)

    return outgoing


async def resolve_native_hits(
    vector_store: "VectorStoreBase",
    hits: List[Dict[str, Any]],
    request: "NativeQuery",
    *,
    created_after: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Resolve complete directed evolution components, preserving backend order.

    Reciprocal supersedes/superseded_by declarations describe the same edge.
    User/source/layer visibility is checked on every fetched node; agent and
    session scope apply only to the original retrieval seeds.
    """
    from ...retrieval import NativeRetrievalError
    from ...models.memory import MemoryStatus
    from ...utils.source_filter import source_allowed

    def check_scope(node: MemoryNode) -> None:
        if (
            node.user_id not in request.user_ids
            or node.layer not in request.layers
            or not source_allowed(
                node.source, request.include_sources, request.exclude_sources
            )
        ):
            raise NativeRetrievalError(
                "scope", "evolution component crosses the visible scope"
            )

    known = {}
    for hit in hits:
        node = hit.get("node")
        if node is None or hit.get("node_id") != node.node_id:
            raise NativeRetrievalError("integrity", "missing or mismatched native node")
        check_scope(node)
        known[node.node_id] = node
    frontier = list(known.values())
    while frontier:
        _native_evolution_edges(known)
        missing = list(
            dict.fromkeys(
                nid for n in frontier for nid in _linked_ids(n) if nid not in known
            )
        )
        if len(known) + len(missing) > 1024:
            raise NativeRetrievalError("budget", "native evolution exceeds 1024 nodes")
        if not missing:
            break
        fetched = await vector_store.native_get_by_ids(missing)
        if len(fetched) != len(missing) or {n.node_id for n in fetched} != set(missing):
            raise NativeRetrievalError("integrity", "incomplete evolution batch")
        for node in fetched:
            check_scope(node)
            known[node.node_id] = node
        frontier = fetched

    outgoing = _native_evolution_edges(known)
    adjacency = {nid: set() for nid in known}
    for older, newer_ids in outgoing.items():
        for newer in newer_ids:
            adjacency[older].add(newer)
            adjacency[newer].add(older)

    components = {}
    for nid in known:
        if nid in components:
            continue
        stack, members = [nid], set()
        while stack:
            current = stack.pop()
            if current in members:
                continue
            members.add(current)
            stack.extend(adjacency[current] - members)
        sinks = [known[mid] for mid in members if not outgoing[mid]]
        if len(sinks) != 1 or not sinks[0].is_latest:
            raise NativeRetrievalError(
                "integrity", "evolution has no unique confirmed head"
            )
        head = sinks[0]
        for mid in members:
            components[mid] = (head, members)

    output, seen = [], set()
    for hit in hits:
        head, members = components[hit["node_id"]]
        if head.node_id in seen:
            continue
        seen.add(head.node_id)
        if head.status != MemoryStatus.ACTIVE:
            continue
        when = head.memory_at or head.gmt_created
        if (
            created_after is not None
            and when is not None
            and when.timestamp() < created_after
        ):
            continue
        visible = [
            known[mid]
            for mid in sorted(members)
            if mid != head.node_id
            and known[mid].status in (MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED)
        ]
        output.append(
            {
                **hit,
                "node": head,
                "node_id": head.node_id,
                "matched_id": hit.get("matched_id", hit["node_id"]),
                "evolution_chain": [_node_to_chain_item(n) for n in [head] + visible]
                if len(members) > 1
                else [],
                "is_evolved": len(members) > 1,
            }
        )
    return output


def _fmt_time(node: MemoryNode) -> Optional[float]:
    """返回 node.memory_at 的 Unix timestamp；缺失返回 None。"""
    t = node.memory_at
    if not t:
        return None
    try:
        return int(t.timestamp())
    except Exception:
        return None


def _time_key(node: MemoryNode):
    """演化链内部排序用 tiebreaker：memory_at 优先，缺失回落 gmt_created。"""
    return node.memory_at or node.gmt_created or datetime.min


def _node_to_chain_item(node: MemoryNode) -> Dict[str, Any]:
    """将 MemoryNode 转为链条目（序列化友好的 dict）。"""
    return {
        "node_id": node.node_id,
        "content": node.content,
        "memory_at": _fmt_time(node),
        "gmt_created": int(node.gmt_created.timestamp()) if node.gmt_created else None,
        "speculate": getattr(node, "speculate", None),
        "layer": node.layer.value if node.layer else "",
    }


def _linked_ids(node: MemoryNode) -> List[str]:
    return list(dict.fromkeys(
        list(getattr(node, "supersedes", None) or [])
        + list(getattr(node, "superseded_by", None) or [])
    ))


def _order_chain(nodes: Sequence[MemoryNode]) -> List[MemoryNode]:
    """Use the historical head/timestamp ordering for one connected chain."""
    if not nodes:
        return []
    if len(nodes) == 1:
        return [nodes[0]]
    heads = [node for node in nodes if node.is_latest]
    head = max(heads or list(nodes), key=_time_key)
    rest = sorted(
        [node for node in nodes if node.node_id != head.node_id],
        key=_time_key,
        reverse=True,
    )
    return [head] + rest


async def trace_full_chains(
    vector_store,
    start_nodes: Sequence[MemoryNode],
) -> Dict[str, List[MemoryNode]]:
    """Expand many evolution chains with one batched fetch per graph depth.

    The scalar implementation performs a separate traversal for every search
    hit.  When several hits belong to the same chain this repeats identical
    ``get_by_ids`` calls.  This routine resolves the union frontier, then maps
    every start node back to its connected component.  Ordering is identical
    to ``_trace_full_chain``.
    """
    starts = [node for node in start_nodes if node is not None]
    if not starts:
        return {}

    known: Dict[str, MemoryNode] = {node.node_id: node for node in starts}
    frontier = list(dict.fromkeys(
        linked_id
        for node in starts
        for linked_id in _linked_ids(node)
        if linked_id not in known
    ))
    while frontier:
        try:
            fetched = await vector_store.get_by_ids(frontier)
        except Exception as exc:
            logger.warning("[evolution] batch get_by_ids failed: %s", exc)
            break
        next_frontier: List[str] = []
        for node in fetched or []:
            if node is None or node.node_id in known:
                continue
            known[node.node_id] = node
            for linked_id in _linked_ids(node):
                if linked_id not in known:
                    next_frontier.append(linked_id)
        frontier = list(dict.fromkeys(next_frontier))

    adjacency: Dict[str, Set[str]] = {node_id: set() for node_id in known}
    for node_id, node in known.items():
        for linked_id in _linked_ids(node):
            if linked_id in known:
                adjacency[node_id].add(linked_id)
                adjacency[linked_id].add(node_id)

    result: Dict[str, List[MemoryNode]] = {}
    component_cache: Dict[str, List[MemoryNode]] = {}
    for start in starts:
        cached = component_cache.get(start.node_id)
        if cached is not None:
            result[start.node_id] = cached
            continue
        stack = [start.node_id]
        component_ids: Set[str] = set()
        while stack:
            node_id = stack.pop()
            if node_id in component_ids:
                continue
            component_ids.add(node_id)
            stack.extend(adjacency.get(node_id, ()))
        ordered = _order_chain([known[node_id] for node_id in component_ids])
        for node_id in component_ids:
            component_cache[node_id] = ordered
        result[start.node_id] = ordered
    return result


async def _trace_full_chain(
    vector_store,
    start_node: MemoryNode,
) -> List[MemoryNode]:
    """
    从任意节点出发，**双向**追溯整条演化链。

    - 向前（supersedes）追溯祖先
    - 向后（superseded_by）追溯后继，找到真正的链头（is_latest=True）

    返回整条链（链头在 [0]）。如果只有自身则返回 [start_node]。
    """
    chains = await trace_full_chains(vector_store, [start_node])
    return chains.get(start_node.node_id, [start_node])


async def _expand_one_chain(
    vector_store, head_item: Dict[str, Any],
    include_sources: Optional[List[str]] = None,
    exclude_sources: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    追溯单个 hit 的演化链（双向）。若无链或回溯失败，原样返回。

    返回：
      - 无演化：原 head_item
      - 有演化：新 dict，以链头（is_latest=True）为代表节点，附加：
          evolution_chain: [{node_id, content, memory_at, gmt_created, speculate, layer}, ...]
                           排序: latest → oldest
          is_evolved: True
          chain_node_ids: set — 链上所有 node_id（用于跨 hit 去重）
      - **None**：整条链都不可见（全被 dedup SHADOW，或全是 L1_RAW）→ 调用方
        必须丢弃该 hit，不可 fallback 回原始 head_item（那会返回 SHADOW 节点）。
    """
    hit_node: MemoryNode = head_item.get("node")
    if not hit_node:
        return head_item

    # 如果节点既没有 supersedes 也没有 superseded_by，就不在链上
    if not hit_node.supersedes and not hit_node.superseded_by:
        return head_item

    chain = await _trace_full_chain(vector_store, hit_node)

    if len(chain) <= 1:
        return head_item

    # 防御：演化链中绝不暴露 L1_RAW 原始对话节点。
    # search 在任何路径都不返回 raw 层内容；若链上混入 raw（如跨层
    # supersede 的历史/异常 linkage），在此过滤掉，避免从 evolution_chain 泄漏。
    #
    # 同理过滤 SHADOW：dedup 把重复节点置 SHADOW（逻辑删除）而非硬删，但
    # _trace_full_chain 走 get_by_ids 按 node_id 直取、**不看 status**，链接关系
    # 也还在 —— 不过滤的话，刚被去重"删掉"的内容会从 evolution_chain 重新露出来。
    # reconciler 的 UPDATE 同样用 SHADOW 表示逻辑删除，一并挡掉。
    from ...models.memory import MemoryLayer, MemoryStatus
    from ...utils.source_filter import source_allowed
    chain_visible = [
        n for n in chain
        if n.layer != MemoryLayer.L1_RAW and n.status != MemoryStatus.SHADOW
        and source_allowed(n.source, include_sources, exclude_sources)
    ]

    # 过滤后整条链都不可见（典型场景：dedup 判重后把这条链整体 SHADOW 掉了，
    # 命中的是另一条链上残留的旧节点）→ 这条链不该再作为结果返回。
    # 必须在这里判：_trace_full_chain 挑 head 时只看 is_latest，整链被 SHADOW 后
    # is_latest 全为 False，它会退化成 `max(all_nodes, key=_time_key)` 而交回一个
    # SHADOW 节点当代表 —— 那就等于把逻辑删除的内容直接返回给用户。
    if not chain_visible:
        return None

    # 代表节点取「可见链」的链头，而非原始 chain[0]
    head_node = chain_visible[0]

    # 原始链数据
    evolution_chain = [_node_to_chain_item(n) for n in chain_visible]
    chain_node_ids = {n.node_id for n in chain}

    return {
        "node_id": head_node.node_id,
        "score": head_item.get("score", 0.0),
        "node": head_node,
        "evolution_chain": evolution_chain,
        "is_evolved": True,
        "chain_node_ids": chain_node_ids,
        **{k: v for k, v in head_item.items()
           if k not in ("node_id", "score", "node",
                         "is_evolved", "evolution_chain", "chain_node_ids")},
    }


async def expand_evolution_chains(
    vector_store,
    hits: List[Dict[str, Any]],
    include_sources: Optional[List[str]] = None,
    exclude_sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    并发扩展一批 hits 中有演化链的节点，并去重。

    关键行为（only_latest=False 时多个链节点可能同时被召回）：
    1. 对每个在链上的 hit，双向追溯找到完整链
    2. 同一条链上的多个 hit 合并为一条结果（以链头为代表，保留最高 score）
    3. 不在链上的 hit 保持原样

    输入 hits 的元素形如 {"node_id": ..., "score": ..., "node": MemoryNode, ...}。
    输出 list 可能比输入短（因为链去重）。
    """
    # 找出需要展开的 hit（在链上的节点）
    needs: List[Dict[str, Any]] = []
    idx_of_needs: Dict[int, int] = {}  # hits_idx → needs_idx
    for i, item in enumerate(hits):
        node = item.get("node")
        if node and (getattr(node, "supersedes", None) or getattr(node, "superseded_by", None)):
            idx_of_needs[i] = len(needs)
            needs.append(item)

    if not needs:
        return list(hits)

    expanded = await asyncio.gather(
        *[
            _expand_one_chain(
                vector_store, item,
                include_sources=include_sources,
                exclude_sources=exclude_sources,
            )
            for item in needs
        ],
        return_exceptions=True,
    )

    # 链去重：同一条链的多个 hit 合并为一条（保留最高 score）
    # chain_head_id → best expanded result
    chain_dedup: Dict[str, Dict[str, Any]] = {}
    expanded_by_idx: Dict[int, Dict[str, Any]] = {}  # hits_idx → expanded result
    dropped_idx: set = set()  # 整条链不可见、需要直接丢弃的 hits_idx

    for i, item in enumerate(hits):
        if i not in idx_of_needs:
            continue
        exp = expanded[idx_of_needs[i]]
        if isinstance(exp, Exception):
            logger.warning(f"[evolution] expand failed for {item.get('node_id')}: {exp}")
            expanded_by_idx[i] = item  # fallback 到原始 hit
            continue
        if exp is None:
            # 整条链都不可见（被 dedup 整体 SHADOW / 全是 raw）→ 丢弃该 hit。
            # 必须显式记进 dropped_idx：它在链上，若只是"不记进 expanded_by_idx"，
            # 下面组装时会落到 else 分支被当普通 hit 原样输出 —— 那就把刚过滤掉的
            # SHADOW 节点又交回给用户了。
            logger.debug(
                f"[evolution] chain fully invisible, dropping hit {item.get('node_id')}"
            )
            dropped_idx.add(i)
            continue

        chain_head_id = exp.get("node_id", "")
        if chain_head_id in chain_dedup:
            # 同一条链的另一个 hit — 取更高 score
            existing = chain_dedup[chain_head_id]
            if exp.get("score", 0.0) > existing.get("score", 0.0):
                chain_dedup[chain_head_id] = exp
        else:
            chain_dedup[chain_head_id] = exp

        expanded_by_idx[i] = exp

    # 收集所有已展开链覆盖的 node_id（用于过滤重复 hit）
    all_chain_node_ids: set = set()
    for exp in chain_dedup.values():
        chain_ids = exp.get("chain_node_ids", set())
        all_chain_node_ids.update(chain_ids)

    # 组装最终结果：去重 + 保持顺序
    result: List[Dict[str, Any]] = []
    seen_chain_heads: set = set()
    seen_node_ids: set = set()

    for i, item in enumerate(hits):
        nid = item.get("node_id", "")

        if i in dropped_idx:
            continue

        if i in expanded_by_idx:
            exp = expanded_by_idx[i]
            chain_head_id = exp.get("node_id", "")
            if chain_head_id in seen_chain_heads:
                continue  # 同链的另一个 hit，已经输出过了
            # 用 chain_dedup 中的最佳结果（最高 score）
            best = chain_dedup.get(chain_head_id, exp)
            seen_chain_heads.add(chain_head_id)
            seen_node_ids.add(chain_head_id)
            result.append(best)
        else:
            # 普通 hit（不在链上）——但如果它的 node_id 在某条链上，跳过
            if nid in all_chain_node_ids or nid in seen_node_ids:
                continue
            seen_node_ids.add(nid)
            result.append(item)

    return result
