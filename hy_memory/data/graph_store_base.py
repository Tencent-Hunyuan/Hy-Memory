"""
Agent Memory V2 - GraphStore 抽象基类

定义图存储层的统一接口（Provider 模式），允许切换不同的图数据库后端。

当前支持:
- Kuzu (嵌入式)
- Neo4j (客户端-服务端)

图 Schema:
  节点类型:
    User    — 用户根节点
    Memory  — 记忆节点 (L6_SCHEMA / L7_INTENTION)
                embedding FLOAT[dims]      — 内容向量 V_con (HNSW 索引)
                beh_embedding FLOAT[dims]  — 行为向量 V_beh (HNSW 索引, 可 NULL)
    Topic   — 主题标签（LLM tags，≠ 正文 NER 实体）
    Entity  — 正文 NER 实体（GPE/ORG/PRODUCT…；不含 PERSON）
    VdbRef  — VDB 影子节点 (evidence 引用)

  边类型:
    HAS_MEMORY          : User → Memory
    TAGGED_WITH         : Memory → Topic
    MENTIONS            : Memory → Entity
    RELATED_TO          : Memory ↔ Memory (双向)
    DERIVED_FROM        : Memory → VdbRef (evidence 关系)
    CROSS_ABSTRACTS_TO  : Memory → Memory (单向, L6 basic → L6 core)
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Sequence, Tuple

from ..models.memory import MemoryNode, MemoryLayer, MemoryStatus
from ..config import MemoryConfig


async def embed_text_for_graph(embed_service, text: str) -> Optional[List[float]]:
    """给图层用的 embedding 调用，兼容 `embed_queued` / `embed` 两种方法名。

    历史上两个后端各写了一个：Kuzu 用 `embed_queued`、Neo4j 用 `embed`。真实
    `EmbedService` 两个都有，所以都能跑；但任何只实现其中一个的实现（测试 fake、
    精简版 embed service）会让另一个后端的 tag 归一化**静默退化成字符串精确匹配** ——
    而不归一化，tag 图就会碎成一堆同义孤岛。这里统一入口，消掉这个隐患。
    """
    if embed_service is None or not text:
        return None
    for name in ("embed_queued", "embed"):
        fn = getattr(embed_service, name, None)
        if fn is None:
            continue
        return await fn(text)
    raise AttributeError(
        "embed_service 需要提供 embed_queued() 或 embed() 之一"
    )


def make_entity_id(isolation_key: str, label: str, name: str) -> str:
    """确定性 Entity 主键：isolation + label + casefold(name)。"""
    import uuid as _uuid
    norm = (name or "").strip().casefold()
    lab = (label or "").strip().upper()
    return (
        f"ent_{_uuid.uuid5(_uuid.NAMESPACE_URL, f'{isolation_key}:{lab}:{norm}').hex[:12]}"
    )


def normalize_entity_name(name: str) -> str:
    return (name or "").strip().casefold()


class GraphStoreBase(ABC):
    """
    图存储抽象基类

    所有图数据库后端必须实现此接口。
    """

    def __init__(self, config: MemoryConfig):
        self.config = config

    _supports_batch_topic_tags: bool = False
    _supports_batch_memory_nodes: bool = False
    _supports_batch_entity_mentions: bool = False
    _supports_batch_related_edges: bool = False
    _supports_batch_temporal_edges: bool = False

    # ================================================================
    # 生命周期
    # ================================================================

    @abstractmethod
    async def initialize(self) -> None:
        """初始化图数据库连接和 Schema"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭连接"""
        ...

    async def checkpoint(self) -> bool:
        """把未落盘的写入合并进主存储（嵌入式后端用）。默认无事可做。"""
        return False

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """获取图统计信息"""
        ...

    # ================================================================
    # User 节点管理
    # ================================================================

    @abstractmethod
    async def ensure_user_node(
        self,
        isolation_key: str,
        user_id: str = "",
        agent_id: str = "",
    ) -> None:
        """确保 User 节点存在"""
        ...

    # ================================================================
    # Memory 节点 CRUD
    # ================================================================

    @abstractmethod
    async def upsert_memory_node(self, node: MemoryNode) -> str:
        """创建或更新 Memory 节点 + User→Memory 边，返回 node_id"""
        ...

    async def upsert_memory_nodes_batch(
        self, nodes: Sequence[MemoryNode],
    ) -> List[str]:
        """Batch-compatible Memory projection fallback.

        Backends with a native bulk primitive can override this method and set
        ``_supports_batch_memory_nodes``.  The default deliberately preserves
        the old per-node failure boundary for other and third-party backends.
        """
        out: List[str] = []
        for node in nodes:
            node_id = await self.upsert_memory_node(node)
            if node_id:
                out.append(node_id)
        return out

    @abstractmethod
    async def get_node(self, node_id: str) -> Optional[MemoryNode]:
        """获取单个 Memory 节点"""
        ...

    @abstractmethod
    async def get_nodes_by_ids(self, node_ids: List[str]) -> List[MemoryNode]:
        """批量获取 Memory 节点"""
        ...

    @abstractmethod
    async def get_all_nodes(
        self,
        isolation_key: str,
        layer: Optional[MemoryLayer] = None,
        status: Optional[MemoryStatus] = None,
        limit: int = 100,
    ) -> List[MemoryNode]:
        """按条件获取节点列表"""
        ...

    async def get_nodes_by_user(
        self,
        user_id: str,
        layer: Optional[MemoryLayer] = None,
        status: Optional[MemoryStatus] = None,
        limit: int = 100,
    ) -> List[MemoryNode]:
        """Return nodes across every historical agent scope for one user.

        User-level profile artifacts use this to discover and retire legacy
        agent-scoped catalog/supplement nodes. Third-party backends may keep the
        safe empty fallback until they implement a native user_id query.
        """
        return []

    @abstractmethod
    async def get_profile(self, isolation_key: str) -> Optional[MemoryNode]:
        """获取 L5 Identity Profile 节点"""
        ...

    @abstractmethod
    async def update_node(self, node_id: str, updates: Dict[str, Any]) -> bool:
        """更新节点属性"""
        ...

    @abstractmethod
    async def delete_node(self, node_id: str) -> bool:
        """删除 Memory 节点及其关联边"""
        ...

    @abstractmethod
    async def delete_all_nodes(self, isolation_key: str) -> int:
        """删除某隔离键下所有 Memory 节点，返回删除数量"""
        ...

    async def clear_projection(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        *,
        layer: str = "l2_fact",
    ) -> Dict[str, int]:
        """清掉某用户的 **pro 投影**（默认只删 `l2_fact`），供重建前使用。

        为什么需要它：图是可重建的派生物，但重建**不会删旧节点**。改了投影规则之后
        （例如 Topic 隔离键从 `user::agent::session` 收窄成 `user::agent`），旧的
        session 粒度 Topic 仍挂着 `TAGGED_WITH` 边留在库里，`get_all_topics` 会同时
        返回新旧两套，效果打折。

        ⚠️ 必须按 layer 收窄：一个用户的 Memory 节点里还有 System 2 / ultra 写的
        `l6_schema`，无条件删会把那部分数据一起毁掉。

        同时清掉因此变成孤儿的 Topic / Entity 节点（没有任何 Memory 挂着的）。

        默认返回 0（未实现该能力的后端不做任何事）。
        """
        return {"memories": 0, "topics": 0, "entities": 0}

    @abstractmethod
    async def delete_by_metadata(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """
        按 metadata 字段组合删除 Memory 节点。

        粒度:
        - 仅 user_id          → 删除该用户所有记忆
        - user_id + agent_id   → 删除该用户指定 agent 下所有记忆
        - user_id + agent_id + session_id → 删除精确 session 的记忆

        Returns:
            删除的节点数量
        """
        ...

    # ================================================================
    # 边操作
    # ================================================================

    @abstractmethod
    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """添加 Memory→Memory 关系边 (RELATED_TO)"""
        ...

    async def add_edges_batch(
        self,
        edges: Sequence[
            Tuple[str, str, str, Optional[Dict[str, Any]]]
        ],
    ) -> List[bool]:
        """Batch-compatible fallback for Memory→Memory edges."""
        return [
            bool(await self.add_edge(source_id, target_id, edge_type, properties))
            for source_id, target_id, edge_type, properties in edges
        ]

    @abstractmethod
    async def add_topic_tag(
        self,
        memory_node_id: str,
        topic_name: str,
        isolation_key: str,
    ) -> str:
        """记忆节点关联主题 (Memory→Topic)，返回 topic_id"""
        ...

    async def add_topic_tags_batch(
        self,
        assignments: List[Tuple[str, str, str]],
        *,
        embed_service=None,
        similarity_threshold: float = 0.85,
    ) -> List[Tuple[str, str]]:
        """Batch-compatible fallback for Memory→Topic projection.

        Backends can override this to share topic lookup/embedding and reduce
        storage round trips.  The fallback preserves the existing semantics.
        """
        out: List[Tuple[str, str]] = []
        for memory_node_id, topic_name, isolation_key in assignments:
            topic_id = await self.add_topic_tag(
                memory_node_id,
                topic_name,
                isolation_key,
                embed_service=embed_service,
                similarity_threshold=similarity_threshold,
            )
            if topic_id:
                out.append((memory_node_id, topic_id))
        return out

    async def add_topic_path(
        self,
        memory_node_id: str,
        topic_path: List[str],
        isolation_key: str,
        subject: str,
        *,
        node_types: Optional[List[str]] = None,
        role: str = "subject",
        embed_service=None,
    ) -> List[str]:
        """Attach a memory to a typed, subject-scoped structural path.

        Implementations create explicit hierarchy nodes and `SUBTOPIC_OF` edges,
        then attach the memory only to the most-specific leaf.  The default no-op
        keeps third-party GraphStore implementations backward compatible.
        """
        return []

    async def clear_hierarchy_memberships(self, memory_node_id: str) -> int:
        """Remove direct `MEMBER_OF` edges before re-projecting an updated L2."""
        return 0

    async def prune_hierarchy_topics(self, isolation_key: str) -> int:
        """Delete empty hierarchy leaves, then empty ancestors, to convergence."""
        return 0

    async def get_hierarchy_topics(
        self, isolation_key: str, *, subject: str = "",
    ) -> List[Dict[str, Any]]:
        """Return hierarchy nodes with path/depth/member-count metadata."""
        return []

    async def get_hierarchy_topic_memories(
        self, hierarchy_topic_id: str, *, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return active canonical L2 members directly assigned to one node."""
        return []

    async def get_hierarchy_memberships(
        self, isolation_key: str,
    ) -> List[Dict[str, str]]:
        """Return direct leaf memberships for one cross-session topic scope."""
        return []

    # ================================================================
    # Entity（正文 NER）+ MENTIONS
    # ================================================================

    async def add_entity_mention(
        self,
        memory_node_id: str,
        name: str,
        label: str,
        isolation_key: str,
    ) -> str:
        """Memory -[:MENTIONS]-> Entity。返回 entity_id；失败返回空串。

        Entity ≠ Topic：Topic 来自 LLM tags；Entity 来自正文 spaCy NER。
        默认 no-op（未实现的后端不影响主路径）。
        """
        return ""

    async def replace_entity_mentions_batch(
        self,
        assignments: Sequence[
            Tuple[str, Sequence[Tuple[str, str]], str]
        ],
    ) -> List[Tuple[str, str]]:
        """Replace MENTIONS edges for several memories.

        Each item is ``(memory_node_id, [(name, label), ...], isolation_key)``.
        Empty entity lists still clear stale mentions, matching update-time
        projection semantics.  The default is the original clear-then-add
        sequence so non-batch backends retain their existing behavior.
        """
        out: List[Tuple[str, str]] = []
        for memory_node_id, entities, isolation_key in assignments:
            await self.clear_entity_mentions(memory_node_id)
            for name, label in entities:
                entity_id = await self.add_entity_mention(
                    memory_node_id, name, label, isolation_key,
                )
                if entity_id:
                    out.append((memory_node_id, entity_id))
        return out

    async def clear_entity_mentions(self, memory_node_id: str) -> int:
        """删掉某 Memory 的全部 MENTIONS 边（UPDATE 投影前重建用）。返回删边数。"""
        return 0

    async def find_memories_by_entities(
        self,
        isolation_key: str,
        entities: Sequence[Tuple[str, str]],
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """按实体名（casefold）反查挂 MENTIONS 的 active Memory。

        entities: [(name, label), ...]；label 仅作同组过滤提示（可空）。
        返回 [{node_id, entity_name, entity_label, match_score}]，match_score∈(0,1]。
        """
        return []

    # ================================================================
    # Topic 检索 (Tag 收敛 + Tag 桥接召回)
    # ================================================================

    async def get_all_topics(
        self,
        isolation_key: str,
    ) -> List[Dict[str, Any]]:
        """获取某 isolation_key 下的所有 Topic 节点

        返回 [{topic_id, name, embedding, degree}]：
        - `embedding` 可能为 None（旧数据 / 写入时未传 embed_service）
        - `degree` = 挂在该 Topic 上的 active 记忆数，画像聚合用
          （Topic 度数 + 新鲜度排序 ≈ "用户是什么样的人"，零 LLM 成本）

        两个后端必须返回同样的键，否则依赖 `degree` 的能力会在某个后端上静默失效。
        """
        return []  # 默认 no-op，子类覆盖

    async def tag_bridge_search(
        self,
        topic_ids: List[str],
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Tag 桥接召回：从 Topic 节点反向查找关联的 Memory 节点

        即 MATCH (t:Topic)<-[:TAGGED_WITH]-(m:Memory) WHERE m.status = 'active'
        返回 [{node_id, content, layer, confidence, topic_name, custom_json}]
        """
        return []  # 默认 no-op，子类覆盖

    # ================================================================
    # V3: VdbRef (shadow node) + DERIVED_FROM edge
    # ================================================================

    async def ensure_vdbref(self, node_id: str, layer: str) -> None:
        """确保 VdbRef 影子节点存在（不存在则创建，已存在则跳过）"""
        ...  # 默认 no-op，子类覆盖

    async def add_derived_from(self, memory_node_id: str, vdbref_node_id: str) -> bool:
        """添加 DERIVED_FROM 边: Memory → VdbRef (evidence 关系)"""
        return False  # 默认 no-op

    async def find_referencing_memories(
        self,
        vdb_node_ids: List[str],
        limit: int = 50,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """反向查找: 给定 VDB node_id 列表，找到 Graph 中引用它们的 Memory 节点

        即 MATCH (v:VdbRef)<-[:DERIVED_FROM]-(m:Memory)
        返回 [{node_id, content, layer, confidence, evidence_vdb_id}]
        """
        return []  # 默认 no-op


    async def get_evidence_vdbrefs(self, memory_node_id: str) -> List[Dict[str, Any]]:
        """获取一个 Graph Memory 节点的所有 DERIVED_FROM evidence VdbRef

        返回 [{node_id, layer}]
        """
        return []  # 默认 no-op

    # ================================================================
    # 图检索
    # ================================================================

    @abstractmethod
    async def expand_from_anchors(
        self,
        anchor_ids: List[str],
        hop: int = 1,
        max_nodes: int = 50,
    ) -> List[Dict[str, Any]]:
        """从锚点节点出发，沿图边扩展 N 跳"""
        ...

    async def get_topic_memories(
        self, topic_id: str, *, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """列出某个 Topic 下的 active 记忆，按 memory_at 正序。

        给 Topic-scoped summary 和时序脊用。
        刻意不复用 `tag_bridge_search` —— 那条是 legacy reader 的召回通道，
        而 tag 共属已决定不作为遍历边（τ=0，见 docs/pro_graph_design.md §3.0）。

        ⚠️ **图里没有事件时间**：Kuzu 的 `Memory` 表只有 `created_at`（落库时间），
        没有 `memory_at`，而新增列对已有 DB 不生效。所以时间一律以 VDB 为准 ——
        调用方拿到 node_id 后回 VDB 取 `memory_at` 再排序
        （见 `pipelines/_graph/projector.resolve_memory_times`）。

        Returns:
            [{"node_id", "content"}, ...]，按 created_at 正序
        """
        return []  # 默认 no-op，子类覆盖

    async def add_temporal_edge(
        self, prev_id: str, next_id: str, topic_id: str = "",
    ) -> bool:
        """建一条时序脊边（TEMPORAL_NEXT，**有向**：prev → next）。

        `topic_id` 为空串表示**全局脊**（跨话题，按事件时间连相邻两条）；
        非空表示 Topic 内脊。两者共存：Topic 内脊表达"这个主题怎么演变"，
        全局脊表达"那几天前后发生了什么"。

        ⚠️ 时间关系刻意**不建成"同天两两相连"的团**：一天 20 条记忆就是 190 条边、
        度数 19，一跳把当天全拉进来、两跳把三天全拉进来 —— 和 tag hub 淹没结果同类。
        全局脊每条只连时间最近的前一条，度数恒为 2，同天记忆仍可达但候选量由跳数预算控制。

        与 `supersedes` 的区别：`supersedes` 表达矛盾取代（旧的不再成立），
        `TEMPORAL_NEXT` 表达递进（旧的仍然成立，只是更早）。
        """
        return False  # 默认 no-op，子类覆盖

    async def add_temporal_edges_batch(
        self, edges: Sequence[Tuple[str, str, str]],
    ) -> List[bool]:
        """Batch-compatible fallback for directed TEMPORAL_NEXT edges."""
        return [
            bool(await self.add_temporal_edge(prev_id, next_id, topic_id))
            for prev_id, next_id, topic_id in edges
        ]

    async def get_related_edges(
        self,
        node_ids: List[str],
        *,
        max_edges: int = 2000,
        relation_types: Optional[List[str]] = None,
        include_temporal: bool = True,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """取出所有一端落在 node_ids 里的 RELATED_TO 边（无向，去重后每对只返回一次）。

        这是读侧图重排的**唯一图查询原语**，一次查询同时服务三种用法：
          - 用法 A（池内支持度传播）：过滤出两端都在召回池里的边
          - 用法 B（阈值松弛）：取另一端不在池里的边 → 候选
          - 用法 C（关系路径）：用同一批边在 Python 侧建邻接、找 ≤2 跳路径

        返回里带上 target 的 status / layer，省掉一次回查：读侧必须能过滤
        SHADOW（判重逻辑删除）节点，否则被删掉的记忆会从图里绕回来。

        `include_temporal=True` 时把 TEMPORAL_NEXT 也一起返回，
        `relation_type="temporal_next"` + `directed=True`（方向为 source→target，
        即"更早 → 更晚"）。A/B 把它当普通边用（按 τ 先验加权），C 用它的方向。

        Returns:
            [{"source_id", "target_id", "relation_type", "weight",
              "target_status", "target_layer", "directed"}, ...]
        """
        return []  # 默认 no-op，子类覆盖

    async def expand_with_tags(
        self,
        anchor_ids: List[str],
        hop: int = 2,
        max_nodes: int = 500,
        isolation_key: str = "",
    ) -> List[Dict[str, Any]]:
        """从锚点出发，沿 RELATED_TO + TAGGED_WITH(双向) 展开 N 跳。

        路径包括:
        - Memory -[RELATED_TO]- Memory（双向）
        - Memory -[TAGGED_WITH]-> Topic <-[TAGGED_WITH]- Memory（经 Topic 桥接）

        isolation_key 非空时只展开属于同一 isolation_key 的节点。

        返回 [{node_id, content, layer, confidence, hop, source}]
        """
        return []  # 默认 no-op，子类覆盖

    # ================================================================
    # 向量检索 (Graph HNSW)
    # ================================================================

    async def vector_search(
        self,
        query_embedding: List[float],
        isolation_key: str,
        layers: Optional[List[str]] = None,
        limit: int = 10,
        score_threshold: float = 0.0,
        user_id: str = "",
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """V_con 向量检索 Graph Memory 节点

        使用 embedding 列的 HNSW 索引做 ANN 检索。
        
        当 isolation_key 为空但 user_id 非空时，执行跨 agent 搜索
        （匹配 isolation_key 以 user_id + "::" 为前缀的所有节点）。
        
        返回 [{node_id, content, layer, confidence, score}]
        """
        return []  # 默认 no-op，子类覆盖

    async def beh_vector_search(
        self,
        query_beh_embedding: List[float],
        isolation_key: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """V_beh 向量检索 L6 basic 节点 (sweeper 用)

        使用 beh_embedding 列的 HNSW 索引做 ANN 检索。
        返回 [{node_id, content, embedding, beh_distance}]
        """
        return []  # 默认 no-op，子类覆盖

    async def update_embedding(
        self,
        node_id: str,
        embedding: Optional[List[float]] = None,
        beh_embedding: Optional[List[float]] = None,
    ) -> bool:
        """更新节点的 embedding / beh_embedding 向量属性"""
        return False  # 默认 no-op

    # ================================================================
    # 跨域归纳 (Cross-Domain Schema Induction)
    # ================================================================

    async def add_cross_abstracts_to(
        self,
        basic_id: str,
        core_id: str,
    ) -> bool:
        """添加 CROSS_ABSTRACTS_TO 边: L6 basic → L6 core (单向)"""
        return False  # 默认 no-op

    async def find_cores_from_basics(
        self,
        basic_ids: List[str],
        max_hops: int = 2,
    ) -> List[Dict[str, Any]]:
        """从 L6 basic 出发，沿 RELATED_TO / CROSS_ABSTRACTS_TO 找 L6 core 节点

        返回 [{node_id, content, confidence}]，已去重
        """
        return []  # 默认 no-op

    async def get_cross_abstracts_targets(
        self,
        basic_id: str,
    ) -> List[str]:
        """查询某个 L6 basic 已有的 CROSS_ABSTRACTS_TO 边指向的 core node_id 列表"""
        return []  # 默认 no-op
