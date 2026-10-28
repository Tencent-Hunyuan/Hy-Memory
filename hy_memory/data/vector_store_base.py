"""
Agent Memory V2 - VectorStore 抽象基类

定义向量存储层的统一接口（Provider 模式），允许切换不同的向量数据库后端。

当前支持:
- Chroma   (嵌入式, 默认, 零外部依赖)
- Qdrant   (嵌入式 / 远程)
- FAISS    (嵌入式, CPU)

存储的 payload 结构:
  - node_id: str
  - isolation_key: str (user_id::agent_id::session_id)
  - user_id / agent_id / session_id: str
  - layer: str
  - content: str                  (核心语义，embed 只用此字段)
  - status: str
  - confidence: float
  - memory_at / temporal_anchor / gmt_created / gmt_modified: str (ISO) | None
  - valid_from / valid_until: str (ISO) | None
  - supersedes: List[str] | None  (取代的旧节点 ID 列表)
  - superseded_by: List[str] | None (被哪些新节点取代)
  - is_latest: bool               (是否链条末端，默认 True，加 payload index)
  - speculate: str | None         (推断注解，不参与 embed)
  - source_raw_memory_id: str | None (L3_SUMMARY 锚点)
  - emotional_valence / emotional_arousal: float
  - meta_tags: List[str]
  - source_session_id: str
"""

from abc import ABC, abstractmethod
import asyncio
from typing import Optional, List, Dict, Any
import logging
import uuid

from ..models.memory import (
    MemoryNode, MemoryLayer, MemoryStatus,
    MetaCognitionTag,
)
from ..config import MemoryConfig


# ================================================================
# 时间过滤：事件时间（memory_at）优先，缺失时回退写入时间（gmt_created）
# ================================================================
#
# `created_after` 的语义是**记忆描述的事件发生在该时间之后**，因此必须按
# `memory_at`（事件时间，调用方可指定）过滤，而不是 `gmt_created`（落库时间）。
#
# 早期实现按 gmt_created 过滤，在批量灌入场景下等于没有过滤 —— 一次导入进来的
# 全部记忆 gmt_created 几乎同一时刻，无论 memory_at 跨越多长时间跨度都会一起命中。
#
# 回退规则：`memory_at` 为空时回退 `gmt_created`。writer 两个入口都保证
# memory_at 有值，但 L0_BASIC_INFO（`agent/tools/basic_profile.py`）等旁路写入
# 以及 owner 字段引入前的历史数据可能为空；回退保证这些节点的行为与改动前一致，
# 不会因为换了字段而被静默过滤掉。

def node_effective_time(payload: Dict[str, Any]) -> Optional[float]:
    """取 payload 的有效时间戳：memory_at 优先，回退 gmt_created；都没有返回 None。

    兼容 Chroma —— 它不支持 None，`_payload_to_chroma_metadata` 把空值写成 `""`。
    """
    for key in ("memory_at", "gmt_created"):
        v = payload.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def passes_created_after(
    payload: Dict[str, Any], created_after: Optional[float]
) -> bool:
    """post-filter 版时间过滤（Chroma / FAISS 用，它们的过滤 DSL 表达不了 null 判断）。

    两个时间字段都缺失时返回 True —— 宁可多召回，也不因为缺字段丢数据。
    """
    if not created_after:
        return True
    t = node_effective_time(payload)
    if t is None:
        return True
    return t >= float(created_after)

logger = logging.getLogger(__name__)


class VectorStoreBase(ABC):
    """
    向量存储抽象基类

    所有向量数据库后端必须实现此接口。
    """

    def __init__(self, config: MemoryConfig):
        self.config = config
        base_name = config.vector_store.collection_name or "agent_memories"
        dims = config.vector_store.embedding_dims
        # 自动拼接维度后缀，避免不同 embedding 模型写入同一 collection 导致维度冲突
        if dims:
            self._collection_name = f"{base_name}_{dims}"
        else:
            self._collection_name = base_name

    _supports_retrieval_clauses: bool = False
    _supports_narrow_episode_queries: bool = False

    # ================================================================
    # 生命周期
    # ================================================================

    @abstractmethod
    async def initialize(self) -> None:
        """初始化向量数据库连接，确保集合/索引存在"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭连接，释放资源"""
        ...

    # ================================================================
    # 写入
    # ================================================================

    @abstractmethod
    async def upsert(self, node: MemoryNode) -> str:
        """
        写入或更新一个 MemoryNode 的向量和 payload。

        Args:
            node: 需要有 node.embedding 已赋值

        Returns:
            node_id (str)
        """
        ...

    @abstractmethod
    async def upsert_batch(self, nodes: List[MemoryNode]) -> List[str]:
        """批量写入"""
        ...

    @abstractmethod
    async def update_embedding(self, node_id: str, embedding: List[float]) -> bool:
        """仅更新向量（payload 不变）"""
        ...

    @abstractmethod
    async def update_payload(self, node_id: str, updates: Dict[str, Any]) -> bool:
        """
        仅更新 payload 字段（向量不变）
        
        Args:
            node_id: 节点 ID
            updates: 要更新的字段和值，如 {"status": "shadow"}
            
        Returns:
            是否成功
        """
        ...

    # ================================================================
    # 检索
    # ================================================================

    @abstractmethod
    async def search(
        self,
        query_embedding: List[float],
        isolation_key: str = "",
        isolation_keys: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        layers: Optional[List[MemoryLayer]] = None,
        limit: int = 10,
        score_threshold: float = 0.0,
        status_filter: Optional[List[MemoryStatus]] = None,
        only_latest: bool = True,
        tags_match_any: Optional[List[str]] = None,
        created_after: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        语义检索

        支持三种过滤模式（优先级从高到低）:
        1. 精确 keys: isolation_keys 非空 → IN 过滤
        2. 单 key: isolation_key 非空 → 精确匹配（向后兼容）
        3. user 级 / agent 级: user_ids/user_id + 可选 agent_ids → 字段组合过滤

        Args:
            query_embedding: 查询向量
            isolation_key: 单个隔离键（向后兼容）
            isolation_keys: 多个隔离键（精确匹配模式）
            user_id: 单个用户 ID（向后兼容）
            user_ids: 多个用户 ID（跨用户搜索）
            agent_ids: Agent ID 列表（与 user_id/user_ids 配合使用）
            include_sources: 调用来源白名单，在底层 top-k 前过滤
            exclude_sources: 调用来源黑名单，在底层 top-k 前过滤；优先级更高
            layers: 可选，限定搜索层级
            created_after: 可选，Unix timestamp (float)，只返回 gmt_created >= 此值的记忆
            limit: 返回数量
            score_threshold: 最低分数阈值
            status_filter: 可选，状态过滤 (默认只返回 ACTIVE)
            tags_match_any: 可选，仅返回 tags 字段与该列表有交集的记忆
                            （兼容底层存储能力，reader 可不使用）

        Returns:
            [{"node_id": ..., "score": ..., "node": MemoryNode}, ...]
        """
        ...

    @abstractmethod
    async def get_by_id(self, node_id: str) -> Optional[MemoryNode]:
        """按 ID 获取"""
        ...

    async def search_batch(
        self, requests: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """Execute independent semantic searches as one logical batch.

        Backends with a native batch endpoint should override this method.  The
        fallback deliberately preserves each request's exact arguments and
        ordering, so callers can adopt batching without changing retrieval
        semantics on older or non-Qdrant stores.
        """
        if not requests:
            return []
        return list(await asyncio.gather(
            *(self.search(**dict(request)) for request in requests),
        ))

    async def get_by_ids(self, node_ids: List[str]) -> List[MemoryNode]:
        """
        批量按 ID 获取（默认实现：串行调用 get_by_id）。
        后端可覆盖此方法以使用批量 API 提升效率。
        """
        results = []
        for node_id in node_ids:
            node = await self.get_by_id(node_id)
            if node is not None:
                results.append(node)
        return results

    async def list_by_episode(
        self,
        episode_ids: List[str],
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        layers: Optional[List[MemoryLayer]] = None,
        limit: int = 200,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[MemoryNode]:
        """按 episode_id 框出同一次讨论的记忆。

        Episode 是 SDK 内部按写入时间间隔切出的"一次讨论"（见 `MemoryNode.episode_id`）。
        读侧用它把同一件事的记忆一次性框成候选，比建 O(n²) 时间边便宜得多。

        默认实现：`list_by_user` + 本地过滤。后端可 override 成下推过滤
        （Qdrant / Chroma / FAISS 的 payload 是动态的，加过滤条件零迁移）。
        """
        wanted = {e for e in (episode_ids or []) if e}
        if not wanted:
            return []
        nodes = await self.list_by_user(
            user_id=user_id, agent_id=agent_id, layers=layers,
            limit=max(limit * 10, 1000), with_vectors=False,
            include_sources=include_sources,
            exclude_sources=exclude_sources,
        )
        out = [
            n for n in (nodes or [])
            if (getattr(n, "episode_id", "") or "") in wanted
        ]
        return out[:limit]

    async def list_by_session(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        session_id: str,
        limit: int = 10000,
        status_filter: Optional[List] = None,
        layers: Optional[List[MemoryLayer]] = None,
        only_latest: bool = False,
        with_vectors: bool = False,
        allow_raw_layer: bool = False,
    ) -> List[MemoryNode]:
        """List one session; remote stores should override with pushed filters."""
        nodes = await self.list_by_user(
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            status_filter=status_filter,
            layers=layers,
            only_latest=only_latest,
            with_vectors=with_vectors,
            allow_raw_layer=allow_raw_layer,
        )
        wanted = session_id or "default_session"
        return [
            node for node in nodes
            if (getattr(node, "session_id", "") or "default_session") == wanted
        ][:limit]

    async def list_by_source_raw_ids(
        self,
        source_raw_ids: List[str],
        *,
        user_id: str,
        agent_id: Optional[str],
        limit: int = 10000,
        status_filter: Optional[List] = None,
        layers: Optional[List[MemoryLayer]] = None,
        only_latest: bool = False,
        with_vectors: bool = False,
    ) -> List[MemoryNode]:
        """List nodes whose direct or accumulated provenance references raw IDs."""
        wanted = {str(value) for value in source_raw_ids if value}
        if not wanted:
            return []
        nodes = await self.list_by_user(
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            status_filter=status_filter,
            layers=layers,
            only_latest=only_latest,
            with_vectors=with_vectors,
        )
        out = []
        for node in nodes:
            direct = getattr(node, "source_raw_memory_id", "") or ""
            accumulated = (getattr(node, "custom", None) or {}).get(
                "source_raw_memory_ids", []
            )
            if direct in wanted or wanted.intersection(str(v) for v in accumulated or []):
                out.append(node)
        return out[:limit]

    async def get_embeddings(self, node_ids: List[str]) -> Dict[str, List[float]]:
        """
        批量取节点向量（用于去重时本地算 pairwise cosine，不调 embedding API）。

        默认实现复用 get_by_ids 并读 node.embedding；后端若 get_by_id 默认不返回
        向量（如 qdrant 批量版、tencent），必须 override 以确保拿到向量。

        Returns:
            {node_id: embedding}，取不到向量的 node_id 不出现在返回里。
        """
        out: Dict[str, List[float]] = {}
        nodes = await self.get_by_ids(node_ids)
        for n in nodes:
            emb = getattr(n, "embedding", None)
            if emb:
                out[n.node_id] = list(emb)
        return out

    # ================================================================
    # 删除
    # ================================================================

    @abstractmethod
    async def delete(self, node_id: str) -> bool:
        """删除单个向量点"""
        ...

    @abstractmethod
    async def delete_by_isolation_key(self, isolation_key: str) -> int:
        """删除某隔离键下的所有向量"""
        ...

    @abstractmethod
    async def delete_by_metadata(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """
        按 metadata 字段组合删除向量（与 search 同一过滤思路）。

        粒度:
        - 仅 user_id          → 删除该用户所有记忆
        - user_id + agent_id   → 删除该用户指定 agent 下所有记忆
        - user_id + agent_id + session_id → 删除精确 session 的记忆

        Returns:
            删除的向量数量（后端不支持精确计数时返回 -1）
        """
        ...

    # ================================================================
    # 枚举
    # ================================================================

    @abstractmethod
    async def list_by_user(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 10000,
        status_filter: Optional[List] = None,
        layers: Optional[List[MemoryLayer]] = None,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        only_latest: bool = False,
        with_vectors: bool = True,
        allow_raw_layer: bool = False,
        order_by_created: bool = False,
        exclude_node_ids: Optional[List[str]] = None,
    ) -> List[MemoryNode]:
        """
        枚举用户的记忆节点。

        Args:
            user_id: 用户 ID（单值）。与 user_ids 互斥，二者必须提供其一
            agent_id: 可选，限定某个 agent（单值）
            limit: 最大返回数量
            status_filter: 可选，只返回指定状态的节点（如 [MemoryStatus.ACTIVE]）。
                不传则**不限定状态**，返回含 SUPERSEDED / SHADOW 在内的全部节点
            layers: 可选，只返回指定层级的节点（如 [MemoryLayer.L2_FACT]）
            user_ids: 可选，多用户批量枚举。提供时优先于 user_id
            agent_ids: 可选，多 agent 批量枚举。提供时优先于 agent_id
            only_latest: 是否只返回演化链末端（is_latest=True）。
                **默认 False**，即返回链上全部版本。clone 等全量复制场景依赖该默认值，
                改动前务必确认调用方不需要历史版本
            with_vectors: 是否返回 embedding。只需要 payload/tags 时应传 False，避免拉取大向量。
            order_by_created: 内部历史读取按 gmt_created 倒序取 limit，时间并列按
                确定性的 point_id 倒序；默认 False 保留后端原有枚举顺序。
            exclude_node_ids: 在 limit 之前排除的 node_id（例如正在写入的 raw）。
            allow_raw_layer: 是否允许返回 L1_RAW 原始对话层。**默认 False**：
                对外读路径一律不返回原始对话。仅内部管线（writer 拼多轮对话历史）
                需要时显式置 True。
        """
        ...

    # ================================================================
    # 统计
    # ================================================================

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """获取集合统计"""
        ...

    @abstractmethod
    async def count(self, isolation_key: str) -> int:
        """统计某隔离键下的向量数量"""
        ...

    # ================================================================
    # Tag Index (per-user tag embedding) —— 供 reader_hybrid_tag 路 B 使用
    # ================================================================
    #
    # 设计：
    #   - 独立 collection（命名由 `_retrieval.config.tag_index_collection_name`
    #     负责），与主 memories collection 隔离
    #   - point_id 由 uuid5(user_id + ":" + tag) 生成，保证幂等 upsert
    #   - payload: {user_id, tag}
    #   - 只有 Qdrant 后端首版实现；其他后端继承默认实现（抛 NotImplementedError）
    #     reader_hybrid_tag 在 init 时探测能力，不支持则自动降级到 hybrid 行为
    #
    # 非必需的后端可以：
    #   - 不实现（默认抛 NotImplementedError）
    #   - 或覆盖 `_supports_tag_index = True` 并提供全部 5 个方法

    _supports_tag_index: bool = False

    async def upsert_tag_embedding(
        self, user_id: str, tag: str, embedding: List[float]
    ) -> None:
        """写入 (user_id, tag) 的 embedding。幂等：同 (user_id, tag) 永远落同一 point。"""
        raise NotImplementedError("tag_index not supported by this backend")

    async def has_tag_embedding(self, user_id: str, tag: str) -> bool:
        """判断 (user_id, tag) 是否已在 tag_index 中。"""
        raise NotImplementedError("tag_index not supported by this backend")

    async def delete_tag_embedding(self, user_id: str, tag: str) -> None:
        """删除 (user_id, tag) 的 embedding。不存在是幂等的 no-op。"""
        raise NotImplementedError("tag_index not supported by this backend")

    async def search_tag_embeddings(
        self,
        user_id: str,
        query_embedding: List[float],
        topk: int = 5,
        min_score: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        在指定 user 的 tag_index 中做向量检索，返回 [{"tag": ..., "score": ...}, ...]。
        按 score 降序。分数低于 min_score 的被过滤。
        """
        raise NotImplementedError("tag_index not supported by this backend")

    async def replace_retrieval_clauses(
        self, parent: MemoryNode, clauses: List[str], embeddings: List[List[float]],
    ) -> int:
        """Replace hidden search aliases for one visible parent memory."""
        return 0

    async def delete_retrieval_clauses(self, parent_id: str) -> int:
        """Delete all hidden search aliases belonging to one parent memory."""
        return 0

    async def delete_retrieval_clauses_by_metadata(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """Delete hidden aliases for the same scope as delete_all()."""
        return 0

    async def search_retrieval_clauses(
        self,
        *,
        query_embedding: List[float],
        user_id: str,
        agent_ids: Optional[List[str]] = None,
        top_k: int = 20,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Search hidden aliases, returning parent_id/score pairs."""
        return []

    async def count_memories_with_tag(
        self, user_id: str, tag: str, isolation_key: str = ""
    ) -> int:
        """
        统计主 memories collection 中 tags 字段含指定 tag 且属于该 user（可选
        带 isolation_key）的记忆数量。用于删除 memory 时判断 tag 是否还可保留。
        """
        raise NotImplementedError("tag_index not supported by this backend")

    # ================================================================
    # Entity Store（对齐 mem0 的 {collection}_entities 独立 collection）
    #   - entity 节点 payload: {data, entity_type, linked_memory_ids, user_id, agent_id}
    #   - upsert: 同 user 下 entity 文本向量相似度 >= 0.95 视为同一 entity，
    #     合并 linked_memory_ids；否则新建。
    #   - 默认 NotImplementedError；目前仅 chroma 实现（实验用）。
    # ================================================================

    async def upsert_entity(
        self,
        *,
        entity_text: str,
        entity_type: str,
        embedding: List[float],
        memory_id: str,
        user_id: str,
        agent_id: str = "",
        merge_threshold: float = 0.95,
    ) -> str:
        """把一个 entity 写入 entity store 并关联到 memory_id。

        若同 user 下已存在相似 entity（cosine >= merge_threshold），把 memory_id
        并入其 linked_memory_ids；否则新建一条 entity 记录。返回 entity_id。
        """
        raise NotImplementedError("entity store not supported by this backend")

    async def search_entities(
        self,
        *,
        query_embedding: List[float],
        user_id: str,
        agent_ids: Optional[List[str]] = None,
        top_k: int = 500,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """在指定 user 的 entity store 中做向量检索。

        返回 [{"entity_id", "data", "entity_type", "linked_memory_ids", "score"}, ...]，
        按 score 降序。
        """
        raise NotImplementedError("entity store not supported by this backend")

    async def list_entities(
        self, *, user_id: str, agent_ids: Optional[List[str]] = None, top_k: int = 10000
    ) -> List[Dict[str, Any]]:
        """列出指定 user 的全部 entity 记录（用于迁移/清理/统计）。"""
        raise NotImplementedError("entity store not supported by this backend")

    async def delete_entities_for_memory(
        self, *, memory_id: str, user_id: str, agent_ids: Optional[List[str]] = None
    ) -> int:
        """从所有 entity 的 linked_memory_ids 中剔除 memory_id；空链则删除该 entity。

        返回受影响的 entity 数。memory 删除时调用。
        """
        raise NotImplementedError("entity store not supported by this backend")

    # ================================================================
    # Keyword Search (Full-text / BM25)
    # ================================================================

    # keyword_search 返回的 score 是否已是 [0,1] 归一化的相关性分。
    #   - True：reader 直接使用，不再过 normalize_bm25 sigmoid
    #     （tencent sparse IP 分 ~0.x、qdrant/未来 binary 命中分 1.0 等，
    #      本身已在 [0,1]，再套为"经典 BM25 原始分 0~20"标定的 sigmoid 会被压成 ~0）
    #   - False（默认）：返回的是经典 BM25 原始分（量级 0~20+），reader 端用
    #     normalize_bm25(midpoint, steepness) 做 sigmoid 归一化
    _keyword_score_normalized: bool = False

    @property
    def keyword_score_normalized(self) -> bool:
        return self._keyword_score_normalized

    async def keyword_search(
        self,
        query: str,
        top_k: int = 10,
        user_id: Optional[str] = None,
        agent_ids: Optional[List[str]] = None,
        layers: Optional[List[MemoryLayer]] = None,
        status_filter: Optional[List[MemoryStatus]] = None,
        only_latest: bool = True,
        isolation_key: str = "",
        isolation_keys: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Full-text keyword search via payload text index.

        Returns results in the same format as search():
            [{"node_id": ..., "score": ..., "node": MemoryNode}, ...]

        Backend implementations should use their native full-text search
        capabilities. Default implementation returns empty (no-op fallback).
        """
        return []

    async def keyword_search_batch(
        self, requests: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """Execute independent keyword searches as one logical batch.

        The default is behaviorally identical to calling ``keyword_search``
        for every request.  Native backends may collapse the transport calls.
        """
        if not requests:
            return []
        return list(await asyncio.gather(
            *(self.keyword_search(**dict(request)) for request in requests),
        ))

    # ================================================================
    # 通用工具方法（所有后端共用）
    # ================================================================

    @staticmethod
    def _recent_payload_to_node(payload: Dict[str, Any]) -> MemoryNode:
        node = VectorStoreBase._payload_to_node(payload)
        created = payload.get("gmt_created")
        if isinstance(created, bool) or not isinstance(created, (int, float)):
            node.gmt_created = None
        return node

    @staticmethod
    def _created_payload_key(payload: Dict[str, Any], point_id: str) -> tuple:
        """Stable creation ordering shared by recent-history backend queries."""
        created = payload.get("gmt_created")
        # Missing legacy timestamps sort before dated records, without inventing
        # a current timestamp while constructing their MemoryNode.
        if isinstance(created, bool) or not isinstance(created, (int, float)):
            created = float("-inf")
        return created, str(point_id).lower()

    @staticmethod
    def _node_id_to_point_id(node_id: str) -> str:
        """
        将 node_id 转换为确定性的 UUID 格式 point_id。

        如果 node_id 本身是 UUID 格式则直接使用，否则生成确定性 UUID。
        """
        try:
            uuid.UUID(node_id)
            return node_id
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, node_id))

    @staticmethod
    def _tag_point_id(user_id: str, tag: str) -> str:
        """
        tag_index 专用的确定性 point_id。
        同 (user_id, tag) 永远生成同一 UUID，upsert 天然幂等。
        """
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tag_index::{user_id}::{tag}"))

    @staticmethod
    def _build_search_text(content: Any, tags: Any = None) -> str:
        """Build the canonical lexical-index text for a memory payload.

        Keep this in one place so an in-place payload UPDATE can rebuild the
        same text that an UPSERT would have produced.  Otherwise ``content``
        and ``tags`` can change while Qdrant's text/sparse indexes continue to
        describe the old memory.
        """
        # search_text: content + tags 拼接，过 lemmatize_for_bm25 (jieba 中文 + spaCy 英文)
        # 后再写入。配合 Qdrant whitespace tokenizer，让查询/索引两侧统一用 jieba 分词，
        # 修复中文 keyword search 全零问题。
        # 注意：lemmatize 只作用于 search_text（倒排索引专用），content 字段保持原文不动，
        # 用户搜索返回的 memory.content 不受影响。
        from ..pipelines._retrieval.lemmatize import lemmatize_for_bm25
        if isinstance(tags, str):
            tag_values = [tags]
        else:
            tag_values = list(tags or [])
        tags_text = " ".join(str(tag) for tag in tag_values if tag)
        content_text = str(content or "")
        raw_search_text = (
            f"{content_text} {tags_text}".strip()
            if tags_text else content_text
        )
        return lemmatize_for_bm25(raw_search_text) if raw_search_text else ""

    @staticmethod
    def _node_to_payload(node: MemoryNode) -> Dict[str, Any]:
        """将 MemoryNode 转换为通用 payload 字典（所有后端共用相同结构）"""
        search_text = VectorStoreBase._build_search_text(node.content, node.tags)

        return {
            "node_id": node.node_id,
            "isolation_key": node.get_isolation_key(),
            "user_id": node.user_id,
            "agent_id": node.agent_id,
            "session_id": node.session_id,
            "source": node.source,
            # Episode（内部会话）：读侧用它框出同一次讨论的候选。payload 是动态的，
            # Qdrant / Chroma / FAISS 加字段零迁移。
            "episode_id": getattr(node, "episode_id", "") or "",
            "owner": node.owner,
            "layer": node.layer.value,
            "content": node.content,
            "search_text": search_text,
            "status": node.status.value,
            "confidence": node.confidence,
            "source_type": node.source_type.value,
            "emotional_valence": node.emotional_valence,
            "emotional_arousal": node.emotional_arousal,
            "specificity_score": node.specificity_score,
            "rarity_score": node.rarity_score,
            "longtail_flag": node.longtail_flag,
            "meta_tags": [t.value for t in node.meta_tags],
            "source_session_id": node.source_session_id,
            "memory_at": int(node.memory_at.timestamp()) if node.memory_at else None,
            "temporal_anchor": node.temporal_anchor,
            "gmt_created": int(node.gmt_created.timestamp()) if node.gmt_created else None,
            "gmt_modified": int(node.gmt_modified.timestamp()) if node.gmt_modified else None,
            "valid_from": int(node.valid_from.timestamp()) if node.valid_from else None,
            "valid_until": int(node.valid_until.timestamp()) if node.valid_until else None,
            "access_count": node.access_count,
            "last_accessed_at": int(node.last_accessed_at.timestamp()) if node.last_accessed_at else None,
            # 演化图谱
            "supersedes": node.supersedes,
            "superseded_by": node.superseded_by,
            "is_latest": node.is_latest,
            # 推断注解（payload-only，不参与 embed）
            "speculate": node.speculate,
            # 主题标签（payload-only，reconcile 用作软提示）
            "tags": list(node.tags or []),
            # 与 tags 等长的 NER label；主题词为空串
            "tag_ners": list(getattr(node, "tag_ners", None) or []),
            # Summary 锚点
            "source_raw_memory_id": node.source_raw_memory_id,
            # 自定义扩展字段（tool 用此字段存结构化 KV，例如 basic_info_kv）
            "custom": dict(node.custom or {}),
        }

    @staticmethod
    def _payload_to_node(payload: Dict[str, Any]) -> MemoryNode:
        """从 payload 字典重建 MemoryNode"""
        return MemoryNode.from_dict(payload)
