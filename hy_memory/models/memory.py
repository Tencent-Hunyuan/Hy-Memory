"""
Agent Memory V2 - 核心数据模型

包含：
- 枚举类型: MemoryLayer, MemoryStatus, SourceType, UpdateType,
            SchemaStatus, TriggerType, IntentionPriority, MetaCognitionTag, GapType
- 核心模型: MemoryNode (统一基类)
- 子类模型: VersionedFact, SchemaNode, IntentionNode
- 辅助模型: KnowledgeGap, TemporalEvent, UserTimeline, LifeStage
- 输出协议: MemoryContextPackage 及其子结构
- 兼容别名: MemoryEntry, MemoryMetadata, MemoryScore (保留旧接口)
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
import uuid


# ============================================================
# 枚举类型
# ============================================================

class MemoryLayer(str, Enum):
    """
    七层记忆模型 (V2 Memory Hierarchy)

    L1 RAW       — 原始对话层 (Append-Only, 向量数据库)
    L2 FACT      — 原子事实层 (版本化不可变记录)
    L3 SUMMARY   — 会话摘要层
    L4 IDENTITY  — 身份画像层 (核心画像)
    L5 KNOWLEDGE — 知识图谱层 (实体/关系/主题, Graph 层, 暂未实现)
    L6 SCHEMA    — 心智模型层 (抽象叙事模板, Graph 层)
    L7 INTENTION — 前瞻意图层 (未来待触发的意图, Graph 层)

    存储分界: L0-L4 在 VDB, L5-L7 在 Graph (ultra 模式)
    """
    L0_BASIC_INFO = "l0_basic_info"
    L1_RAW = "l1_raw"
    L2_FACT = "l2_fact"
    L3_SUMMARY = "l3_summary"
    L4_IDENTITY = "l4_identity"
    L5_KNOWLEDGE = "l5_knowledge"
    L6_SCHEMA = "l6_schema"
    L7_INTENTION = "l7_intention"

    # --- 旧编号兼容别名（已存储数据中的值） ---
    # v2 → v3 重编号兼容
    L6_IDENTITY = "l4_identity"     # 旧 l6_identity → 新 l4_identity
    L4_KNOWLEDGE = "l5_knowledge"   # 旧 l4_knowledge → 新 l5_knowledge
    L5_SCHEMA = "l6_schema"         # 旧 l5_schema → 新 l6_schema
    # v1 → v2 旧兼容（仍需保留）
    L4_5_SCHEMA = "l6_schema"       # 旧 l4_5_schema → 新 l6_schema
    L5_IDENTITY = "l4_identity"     # 旧 l5_identity → 新 l4_identity
    L6_INTENTION = "l7_intention"   # 旧 l6_intention → 新 l7_intention

    # --- V1 兼容别名 ---
    PROFILE = "l4_identity"
    DIALOGUE = "l2_fact"
    SUMMARY = "l3_summary"
    KNOWLEDGE = "l5_knowledge"
    RAW = "l1_raw"

    @classmethod
    def from_string(cls, value: str) -> "MemoryLayer":
        """从字符串创建 MemoryLayer，支持 V1/V2/V3 命名和旧编号"""
        value = value.lower().strip()
        # 旧编号 → 新编号映射（兼容已存储数据的 payload）
        legacy_mapping = {
            # v2 → v3
            "l6_identity": cls.L4_IDENTITY,
            "l4_knowledge": cls.L5_KNOWLEDGE,
            "l5_schema": cls.L6_SCHEMA,
            # v1 → v2 → v3
            "l4_5_schema": cls.L6_SCHEMA,
            "l5_identity": cls.L4_IDENTITY,
            "l6_intention": cls.L7_INTENTION,
        }
        if value in legacy_mapping:
            return legacy_mapping[value]
        # V1 兼容映射
        v1_mapping = {
            "profile": cls.L4_IDENTITY,
            "dialogue": cls.L2_FACT,
            "summary": cls.L3_SUMMARY,
            "knowledge": cls.L5_KNOWLEDGE,
            "raw": cls.L1_RAW,
        }
        if value in v1_mapping:
            return v1_mapping[value]
        for layer in cls:
            if layer.value == value:
                return layer
        raise ValueError(f"Invalid memory layer: {value}")

    @classmethod
    def all_layers(cls) -> List["MemoryLayer"]:
        """返回所有记忆层 (不含别名)"""
        return [
            cls.L0_BASIC_INFO,
            cls.L1_RAW, cls.L2_FACT, cls.L3_SUMMARY,
            cls.L4_IDENTITY, cls.L5_KNOWLEDGE,
            cls.L6_SCHEMA, cls.L7_INTENTION,
        ]



class ContentType(str, Enum):
    """记忆内容类型（保留用于旧代码兼容，MemoryNode 不再使用此字段）"""
    FACT = "fact"
    SUMMARY = "summary"
    ENTITY = "entity"
    SCHEMA = "schema"
    PROFILE = "profile"
    INTENTION = "intention"
    RAW = "raw"
    HYPOTHESIS = "hypothesis"


class MemoryStatus(str, Enum):
    """记忆状态"""
    ACTIVE = "active"               # 当前有效
    SUPERSEDED = "superseded"       # 已被新版本取代（EVOLVE 演化链上的旧节点）
    NEGATED = "negated"             # 已被否定
    CONFLICTED = "conflicted"       # 存在冲突
    SHADOW = "shadow"               # 影子状态：低置信度推断 或 reconcile DELETE 逻辑删除
                                    # 两种场景都不参与召回（is_latest 通常为 False）
    ARCHIVED = "archived"           # 已归档


class SourceType(str, Enum):
    """信息来源类型"""
    EXPLICIT = "explicit"           # 用户明确陈述
    INFERRED = "inferred"           # 系统推断
    COMPOSITE = "composite"         # 多证据综合


class UpdateType(str, Enum):
    """版本化事实的更新类型"""
    OVERRIDE = "override"           # 新值替代旧值
    SUPPLEMENT = "supplement"       # 补充信息
    TEMPORAL = "temporal"           # 时间窗口变化
    NEGATE = "negate"               # 否定旧信息
    CONFLICT = "conflict"           # 新旧冲突


class SchemaStatus(str, Enum):
    """Schema 状态"""
    FORMING = "forming"             # 形成中 (证据不足)
    STABLE = "stable"               # 稳定 (多证据支撑)
    EVOLVING = "evolving"           # 演化中 (有新证据修改)
    DEPRECATED = "deprecated"       # 已过时


class TriggerType(str, Enum):
    """意图触发类型"""
    TIME_BASED = "time_based"       # 时间触发
    EVENT_BASED = "event_based"     # 事件触发


class IntentionPriority(str, Enum):
    """意图优先级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MetaCognitionTag(str, Enum):
    """
    元认知信号标记

    附加在记忆条目上，让调用方 LLM 感知记忆的可靠程度和特殊性质。
    """
    # 置信度相关
    HIGH_CONFIDENCE = "high_confidence"
    LOW_CONFIDENCE = "low_confidence"
    INFERRED_NOT_STATED = "inferred_not_stated"
    STALE = "stale"

    # 冲突与不确定
    NEEDS_CLARIFICATION = "needs_clarification"
    CONFLICTED = "conflicted"
    CONTEXT_DEPENDENT = "context_dependent"

    # 情绪与敏感
    EMOTIONALLY_SENSITIVE = "emotionally_sensitive"
    POSITIVE_MEMORY = "positive_memory"
    NEGATIVE_MEMORY = "negative_memory"
    GOTCHA = "gotcha"

    # 时间特殊性
    RECENTLY_CHANGED = "recently_changed"
    HISTORICAL = "historical"
    TEMPORAL_CONTEXT = "temporal_context"

    # 知识缺口
    KNOWLEDGE_GAP = "knowledge_gap"
    INCOMPLETE = "incomplete"

    # 价值标记
    LONG_TAIL = "long_tail"
    SCHEMA_ACTIVATED = "schema_activated"

    # 前瞻标记
    PROACTIVE_CARE = "proactive_care"
    FOLLOW_UP = "follow_up"


class GapType(str, Enum):
    """知识缺口的四种类型"""
    PROFILE_INCOMPLETE = "profile_incomplete"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    INFERRED_UNVERIFIED = "inferred_unverified"
    STALE_UNVERIFIED = "stale_unverified"


# ============================================================
# 核心模型: MemoryNode (统一基类)
# ============================================================

@dataclass
class MemoryNode:
    """
    所有层级记忆的统一基类 (V2)

    字段分组：
    - 基础标识 (node_id, user_id, layer, content)
    - 业务隔离 (agent_id, session_id)
    - 时空维度 (memory_at, temporal_anchor, valid_from/until, gmt_created/modified)
    - 演化图谱 (supersedes, superseded_by, is_latest) — EVOLVE 语义
    - 推断注解 (speculate) — 不参与 embed
    - Summary 锚点 (source_raw_memory_id) — L3_SUMMARY/L4_IDENTITY 专用
    - 状态 (status)
    - 置信与校准 (confidence, evidence_count, source_type)
    - 情绪标记 (emotional_valence, emotional_arousal)
    - 检索元数据 (access_count, last_accessed_at, embedding)
    - 长尾标记 (specificity_score, rarity_score, longtail_flag)
    - 元认知标记 (meta_tags, meta_hints)
    - 出处追溯 (source_session_id, source_turn_index, evidence_chain)
    - 扩展 (custom, tags)
    """

    # === 基础标识 ===
    node_id: str = ""
    user_id: str = ""
    layer: MemoryLayer = MemoryLayer.L1_RAW
    content: str = ""                        # 核心语义内容，embed 只用此字段

    # === 业务隔离 ===
    agent_id: str = ""
    session_id: str = ""
    # 产生该记忆的调用来源（openclaw / cursor / codex 等）。不要与
    # source_type（显式/推断/复合）或检索通道混用。
    source: str = "default"

    # === 归属（owner）===
    # 该记忆属于谁：'user'（关于用户/用户陈述）| 'agent'（assistant 提供/用户要 agent 做的事）。
    # 仅 L2_FACT / L7_INTENTION 写入；其他层（L0/L1/L3/L5/L6）留空（None）。
    owner: Optional[str] = None

    # === 时空维度 ===
    memory_at: Optional[datetime] = None     # 记忆发生时间（用户指定，可为空）
    temporal_anchor: Optional[str] = None    # 保留字段兼容历史数据；v0.3.45_v0 post8 起不再写入
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    # === 数据管理时间 ===
    gmt_created: Optional[datetime] = None   # 数据写入时间（系统自动）
    gmt_modified: Optional[datetime] = None  # 数据最后修改时间（系统自动）

    # === 演化图谱（EVOLVE 语义）===
    supersedes: Optional[List[str]] = None       # 本节点取代的旧节点 ID 列表，全新信息为 None
    superseded_by: Optional[List[str]] = None    # 被哪些新节点取代（系统写入，不透出给调用方）
    is_latest: bool = True                       # 是否链条末端；被取代时系统自动置 False

    # === 推断注解（不参与 embed）===
    speculate: Optional[str] = None              # LLM 对复杂/模糊信号的推断注解

    # === Summary 锚点（L3_SUMMARY / L4_IDENTITY）===
    source_raw_memory_id: Optional[str] = None   # 对应的 L1_RAW 节点 ID

    # === 状态 ===
    status: MemoryStatus = MemoryStatus.ACTIVE

    # === 置信与校准 ===
    confidence: float = 1.0
    evidence_count: int = 1
    source_type: SourceType = SourceType.EXPLICIT

    # === 情绪标记 ===
    emotional_valence: float = 0.0      # [-1, 1] 情感效价
    emotional_arousal: float = 0.0      # [0, 1] 情感唤醒度

    # === 检索元数据 ===
    access_count: int = 0
    last_accessed_at: Optional[datetime] = None
    embedding: Optional[List[float]] = None

    # === 长尾标记 ===
    specificity_score: float = 0.0      # [0, 1] 具体度
    rarity_score: float = 0.0           # [0, 1] 稀有度
    longtail_flag: bool = False

    # === 元认知标记 ===
    meta_tags: List[MetaCognitionTag] = field(default_factory=list)
    meta_hints: Dict[str, str] = field(default_factory=dict)

    # === Episode（内部会话）===
    # 一件事往往跨越好几次 write（十几二十轮），从中抽出的 memory 属于同一次讨论。
    # 打同一个 episode_id，读侧就能用它**框出一片候选**做加权 —— 比建 O(n²) 时间边
    # 便宜得多，也比"同一天"语义准确。
    #
    # 注意与 session_id 的区别：session_id 是**调用方给的业务隔离键**（进 isolation_key），
    # 可能很长（整段会话）或干脆是 default；episode_id 是 SDK **内部按时间间隔切**的
    # "一次讨论"，粒度更细，且不参与隔离。
    episode_id: str = ""

    # === 出处追溯 ===
    source_session_id: str = ""
    source_turn_index: Optional[int] = None
    evidence_chain: List[str] = field(default_factory=list)

    # === 扩展字段 ===
    custom: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    # 与 tags 等长的 NER label（GPE/PERSON/...）；主题词为空串。写入时标注，读侧 boost 用。
    tag_ners: List[str] = field(default_factory=list)

    def __post_init__(self):
        from ..utils.source_filter import normalize_source
        self.source = normalize_source(self.source)
        if not self.node_id:
            self.node_id = str(uuid.uuid4())
        now = datetime.now()
        # gmt_created/gmt_modified 是系统时间，自动填写
        if self.gmt_created is None:
            self.gmt_created = now
        if self.gmt_modified is None:
            self.gmt_modified = now
        # memory_at 是记忆发生时间，由调用方指定，不自动填写
        # valid_from 用 gmt_created 作为默认（数据有效从写入时开始）
        if self.valid_from is None:
            self.valid_from = now

    @property
    def memory_id(self) -> str:
        """V1 兼容: memory_id == node_id"""
        return self.node_id

    @property
    def uid(self) -> str:
        """V1 兼容: uid == user_id"""
        return self.user_id

    def get_isolation_key(self) -> str:
        """隔离键: {user_id}::{agent_id}::{session_id}

        session_id 为空时使用 'default_session'，与 build_isolation_key 对齐。
        """
        sid = self.session_id or "default_session"
        return f"{self.user_id}::{self.agent_id}::{sid}"

    def get_topic_scope_key(self) -> str:
        """Topic 的隔离键: {user_id}::{agent_id} —— **不含 session_id**。

        为什么 Topic 要比记忆本身更宽：主题是**跨会话累积**的概念。"关于你的健康我知道
        这些"不该按会话切开。而 `get_isolation_key()` 带 session，用它当 Topic 的
        隔离键会让同名 tag 在每个 session 里各建一个互不相识的 Topic 节点。

        如果按 session 隔离，同名 Topic 会分裂成多个节点，导致时序脊建不出边、
        topic summary 反复总结同名碎片、跨会话 tag 召回不连通。
        """
        return f"{self.user_id}::{self.agent_id}"

    @staticmethod
    def build_topic_scope_key(uid: str, agent_id: str) -> str:
        """构建 Topic 隔离键: {uid}::{agent_id}（不含 session，见 get_topic_scope_key）"""
        return f"{uid}::{agent_id}"

    @staticmethod
    def topic_scope_of(isolation_key: str) -> str:
        """把带 session 的 isolation_key 削成 Topic 作用域（取前两段）。

        供只拿到 isolation_key 的读侧使用 —— 建 Topic 与查 Topic 必须用同一个键，
        否则查不到（两个后端都是 `isolation_key` 精确匹配）。
        """
        if not isolation_key:
            return isolation_key
        parts = isolation_key.split("::")
        return "::".join(parts[:2]) if len(parts) >= 2 else isolation_key

    @staticmethod
    def build_isolation_key(uid: str, agent_id: str, session_id: str = "default_session") -> str:
        """构建隔离键: {uid}::{agent_id}::{session_id}"""
        sid = session_id or "default_session"
        return f"{uid}::{agent_id}::{sid}"

    @staticmethod
    def parse_isolation_key(key: str) -> tuple:
        """
        解析隔离键，返回 (user_id, agent_id, session_id)。

        兼容多种格式:
        - 4 段: (旧格式带 appid) 跳过首段 → uid::agent_id::session_id
        - 3 段: uid::agent_id::session_id (标准格式)
        - 2 段: uid::agent_id (session_id 默认 "default_session")
        """
        parts = key.split("::")
        if len(parts) >= 4:
            # 兼容旧格式: 跳过 appid
            return parts[1], parts[2], parts[3]
        elif len(parts) == 3:
            return parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            return parts[0], parts[1], "default_session"
        return "", "", ""

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "node_id": self.node_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "source": self.source,
            "episode_id": self.episode_id,
            "owner": self.owner,
            "layer": self.layer.value,
            "content": self.content,
            "memory_at": int(self.memory_at.timestamp()) if self.memory_at else None,
            "temporal_anchor": self.temporal_anchor,
            "gmt_created": int(self.gmt_created.timestamp()) if self.gmt_created else None,
            "gmt_modified": int(self.gmt_modified.timestamp()) if self.gmt_modified else None,
            "valid_from": int(self.valid_from.timestamp()) if self.valid_from else None,
            "valid_until": int(self.valid_until.timestamp()) if self.valid_until else None,
            # 演化图谱
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "is_latest": self.is_latest,
            # 推断注解
            "speculate": self.speculate,
            # Summary 锚点
            "source_raw_memory_id": self.source_raw_memory_id,
            # 状态
            "status": self.status.value,
            # 置信与校准
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "source_type": self.source_type.value,
            # 情绪
            "emotional_valence": self.emotional_valence,
            "emotional_arousal": self.emotional_arousal,
            # 检索元数据
            "access_count": self.access_count,
            "last_accessed_at": int(self.last_accessed_at.timestamp()) if self.last_accessed_at else None,
            # 长尾标记
            "specificity_score": self.specificity_score,
            "rarity_score": self.rarity_score,
            "longtail_flag": self.longtail_flag,
            # 元认知
            "meta_tags": [t.value for t in self.meta_tags],
            "meta_hints": self.meta_hints,
            # 出处追溯
            "source_session_id": self.source_session_id,
            "source_turn_index": self.source_turn_index,
            "evidence_chain": self.evidence_chain,
            # 扩展
            "custom": self.custom,
            "tags": self.tags,
            "tag_ners": list(getattr(self, "tag_ners", None) or []),
            # 向量（可选，clone / 序列化场景需要携带）
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryNode":
        """从字典反序列化（兼容旧 payload 中的废弃字段，忽略即可）"""
        def parse_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v)
            if isinstance(v, str):
                return datetime.fromisoformat(v)
            return None

        def parse_enum(enum_cls, v, default):
            if v is None:
                return default
            if isinstance(v, enum_cls):
                return v
            try:
                return enum_cls(v)
            except (ValueError, KeyError):
                return default

        meta_tags_raw = data.get("meta_tags", [])
        meta_tags = []
        for t in meta_tags_raw:
            if isinstance(t, MetaCognitionTag):
                meta_tags.append(t)
            else:
                try:
                    meta_tags.append(MetaCognitionTag(t))
                except (ValueError, KeyError):
                    pass

        return cls(
            node_id=data.get("node_id", data.get("memory_id", "")),
            user_id=data.get("user_id", data.get("uid", "")),
            agent_id=data.get("agent_id", ""),
            session_id=data.get("session_id", ""),
            source=data.get("source", "default") or "default",
            episode_id=data.get("episode_id", "") or "",
            owner=data.get("owner") or None,
            layer=parse_enum(MemoryLayer, data.get("layer"), MemoryLayer.L1_RAW),
            content=data.get("content", ""),
            # memory_at: 优先读 memory_at，兼容旧 key created_at
            memory_at=parse_dt(data.get("memory_at") or data.get("created_at")),
            temporal_anchor=data.get("temporal_anchor"),
            gmt_created=parse_dt(data.get("gmt_created")),
            gmt_modified=parse_dt(data.get("gmt_modified")),
            valid_from=parse_dt(data.get("valid_from")),
            valid_until=parse_dt(data.get("valid_until")),
            # 演化图谱
            supersedes=data.get("supersedes"),
            superseded_by=data.get("superseded_by"),
            is_latest=data.get("is_latest", True),
            # 推断注解
            speculate=data.get("speculate"),
            # Summary 锚点
            source_raw_memory_id=data.get("source_raw_memory_id"),
            # 状态
            status=parse_enum(MemoryStatus, data.get("status"), MemoryStatus.ACTIVE),
            # 置信
            confidence=data.get("confidence", 1.0),
            evidence_count=data.get("evidence_count", 1),
            source_type=parse_enum(SourceType, data.get("source_type"), SourceType.EXPLICIT),
            # 情绪
            emotional_valence=data.get("emotional_valence", 0.0),
            emotional_arousal=data.get("emotional_arousal", 0.0),
            # 检索元数据
            access_count=data.get("access_count", 0),
            last_accessed_at=parse_dt(data.get("last_accessed_at")),
            embedding=data.get("embedding"),
            # 长尾
            specificity_score=data.get("specificity_score", 0.0),
            rarity_score=data.get("rarity_score", 0.0),
            longtail_flag=data.get("longtail_flag", False),
            # 元认知
            meta_tags=meta_tags,
            meta_hints=data.get("meta_hints", {}),
            # 出处
            source_session_id=data.get("source_session_id", ""),
            source_turn_index=data.get("source_turn_index"),
            evidence_chain=data.get("evidence_chain", []),
            # 扩展
            custom=data.get("custom", {}),
            tags=data.get("tags", []) or [],
            tag_ners=list(data.get("tag_ners") or []),
        )


# ============================================================
# 子类模型
# ============================================================

@dataclass
class VersionedFact(MemoryNode):
    """
    L2 层的原子事实 — 版本化不可变记录

    V2 §3.2: 不直接修改事实内容，而是通过 status 变迁 + SUPERSEDED_BY 边来追踪变更。
    """
    update_type: Optional[UpdateType] = None

    def __post_init__(self):
        super().__post_init__()
        if self.layer == MemoryLayer.L1_RAW:
            self.layer = MemoryLayer.L2_FACT

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["update_type"] = self.update_type.value if self.update_type else None
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VersionedFact":
        base = MemoryNode.from_dict(data)
        update_type = None
        if data.get("update_type"):
            try:
                update_type = UpdateType(data["update_type"])
            except (ValueError, KeyError):
                pass
        return cls(**{**base.__dict__, "update_type": update_type})


@dataclass
class SchemaNode(MemoryNode):
    """
    L6 层: 心智模型 — 从多个关联事实中自动归纳的抽象叙事模板 (Graph 层)

    V3 §2.2
    """
    central_proposition: str = ""
    supporting_evidence: List[str] = field(default_factory=list)
    expected_inferences: List[str] = field(default_factory=list)
    activation_threshold: float = 0.7
    activation_count: int = 0
    last_activated_at: Optional[datetime] = None
    schema_status: SchemaStatus = SchemaStatus.FORMING

    def __post_init__(self):
        super().__post_init__()
        self.layer = MemoryLayer.L6_SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "central_proposition": self.central_proposition,
            "supporting_evidence": self.supporting_evidence,
            "expected_inferences": self.expected_inferences,
            "activation_threshold": self.activation_threshold,
            "activation_count": self.activation_count,
            "last_activated_at": int(self.last_activated_at.timestamp()) if self.last_activated_at else None,
            "schema_status": self.schema_status.value,
        })
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchemaNode":
        base = MemoryNode.from_dict(data)
        def parse_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v)
            if isinstance(v, str):
                return datetime.fromisoformat(v)
            return None
        schema_status = SchemaStatus.FORMING
        if data.get("schema_status"):
            try:
                schema_status = SchemaStatus(data["schema_status"])
            except (ValueError, KeyError):
                pass
        return cls(
            **{**base.__dict__,
               "central_proposition": data.get("central_proposition", ""),
               "supporting_evidence": data.get("supporting_evidence", []),
               "expected_inferences": data.get("expected_inferences", []),
               "activation_threshold": data.get("activation_threshold", 0.7),
               "activation_count": data.get("activation_count", 0),
               "last_activated_at": parse_dt(data.get("last_activated_at")),
               "schema_status": schema_status,
               })


@dataclass
class IntentionNode(MemoryNode):
    """
    L7 层: 前瞻性记忆 — 记住"将来要做什么" (Graph 层)

    V3 §2.2
    """
    trigger_type: TriggerType = TriggerType.EVENT_BASED
    trigger_condition: str = ""

    # 时间触发
    trigger_time: Optional[datetime] = None

    # 事件触发
    trigger_event_pattern: Optional[str] = None
    trigger_event_embedding: Optional[List[float]] = None

    # 意图内容
    intention_content: str = ""
    priority: IntentionPriority = IntentionPriority.MEDIUM
    expiry: Optional[datetime] = None
    triggered: bool = False
    triggered_at: Optional[datetime] = None

    def __post_init__(self):
        super().__post_init__()
        self.layer = MemoryLayer.L7_INTENTION

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "trigger_type": self.trigger_type.value,
            "trigger_condition": self.trigger_condition,
            "trigger_time": int(self.trigger_time.timestamp()) if self.trigger_time else None,
            "trigger_event_pattern": self.trigger_event_pattern,
            "trigger_event_embedding": self.trigger_event_embedding,
            "intention_content": self.intention_content,
            "priority": self.priority.value,
            "expiry": int(self.expiry.timestamp()) if self.expiry else None,
            "triggered": self.triggered,
            "triggered_at": int(self.triggered_at.timestamp()) if self.triggered_at else None,
        })
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntentionNode":
        base = MemoryNode.from_dict(data)
        def parse_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v)
            if isinstance(v, str):
                return datetime.fromisoformat(v)
            return None
        trigger_type = TriggerType.EVENT_BASED
        if data.get("trigger_type"):
            try:
                trigger_type = TriggerType(data["trigger_type"])
            except (ValueError, KeyError):
                pass
        priority = IntentionPriority.MEDIUM
        if data.get("priority"):
            try:
                priority = IntentionPriority(data["priority"])
            except (ValueError, KeyError):
                pass
        return cls(
            **{**base.__dict__,
               "trigger_type": trigger_type,
               "trigger_condition": data.get("trigger_condition", ""),
               "trigger_time": parse_dt(data.get("trigger_time")),
               "trigger_event_pattern": data.get("trigger_event_pattern"),
               "trigger_event_embedding": data.get("trigger_event_embedding"),
               "intention_content": data.get("intention_content", ""),
               "priority": priority,
               "expiry": parse_dt(data.get("expiry")),
               "triggered": data.get("triggered", False),
               "triggered_at": parse_dt(data.get("triggered_at")),
               })


# ============================================================
# 辅助模型: KnowledgeGap
# ============================================================

@dataclass
class KnowledgeGap:
    """
    知识缺口记录 — 描述"某个维度的信息缺失或不可靠"

    V2 §5.6.2
    """
    gap_id: str = ""
    user_id: str = ""
    domain: str = ""
    gap_type: GapType = GapType.PROFILE_INCOMPLETE
    importance: str = "medium"          # high / medium / low
    description: str = ""
    related_node_ids: List[str] = field(default_factory=list)
    hint: str = ""
    created_at: Optional[datetime] = None
    last_scanned_at: Optional[datetime] = None
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    resolve_trigger: Optional[str] = None

    def __post_init__(self):
        if not self.gap_id:
            self.gap_id = f"gap_{uuid.uuid4().hex[:12]}"
        now = datetime.now()
        if self.created_at is None:
            self.created_at = now
        if self.last_scanned_at is None:
            self.last_scanned_at = now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "user_id": self.user_id,
            "domain": self.domain,
            "gap_type": self.gap_type.value,
            "importance": self.importance,
            "description": self.description,
            "related_node_ids": self.related_node_ids,
            "hint": self.hint,
            "created_at": int(self.created_at.timestamp()) if self.created_at else None,
            "last_scanned_at": int(self.last_scanned_at.timestamp()) if self.last_scanned_at else None,
            "resolved": self.resolved,
            "resolved_at": int(self.resolved_at.timestamp()) if self.resolved_at else None,
            "resolve_trigger": self.resolve_trigger,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeGap":
        def parse_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v)
            if isinstance(v, str):
                return datetime.fromisoformat(v)
            return None
        gap_type = GapType.PROFILE_INCOMPLETE
        if data.get("gap_type"):
            try:
                gap_type = GapType(data["gap_type"])
            except (ValueError, KeyError):
                pass
        return cls(
            gap_id=data.get("gap_id", ""),
            user_id=data.get("user_id", ""),
            domain=data.get("domain", ""),
            gap_type=gap_type,
            importance=data.get("importance", "medium"),
            description=data.get("description", ""),
            related_node_ids=data.get("related_node_ids", []),
            hint=data.get("hint", ""),
            created_at=parse_dt(data.get("created_at")),
            last_scanned_at=parse_dt(data.get("last_scanned_at")),
            resolved=data.get("resolved", False),
            resolved_at=parse_dt(data.get("resolved_at")),
            resolve_trigger=data.get("resolve_trigger"),
        )


# ============================================================
# 辅助模型: 时间系统 (Temporal)
# ============================================================

@dataclass
class LifeStage:
    """用户的一个生命阶段"""
    name: str = ""                      # "大学时期", "在上海工作"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "start_time": int(self.start_time.timestamp()) if self.start_time else None,
            "end_time": int(self.end_time.timestamp()) if self.end_time else None,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LifeStage":
        def parse_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v)
            if isinstance(v, str):
                return datetime.fromisoformat(v)
            return None
        return cls(
            name=data.get("name", ""),
            start_time=parse_dt(data.get("start_time")),
            end_time=parse_dt(data.get("end_time")),
            description=data.get("description", ""),
        )


@dataclass
class TemporalEvent:
    """
    时间线上的一个事件

    V2 §6.1
    """
    event_id: str = ""                  # 关联的 fact_id
    event_description: str = ""
    event_time: Optional[datetime] = None
    time_precision: str = "approximate"  # exact / month / quarter / year / approximate
    time_description: str = ""          # 原始时间描述
    life_stage: Optional[str] = None
    is_milestone: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_description": self.event_description,
            "event_time": int(self.event_time.timestamp()) if self.event_time else None,
            "time_precision": self.time_precision,
            "time_description": self.time_description,
            "life_stage": self.life_stage,
            "is_milestone": self.is_milestone,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TemporalEvent":
        def parse_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v)
            if isinstance(v, str):
                return datetime.fromisoformat(v)
            return None
        return cls(
            event_id=data.get("event_id", ""),
            event_description=data.get("event_description", ""),
            event_time=parse_dt(data.get("event_time")),
            time_precision=data.get("time_precision", "approximate"),
            time_description=data.get("time_description", ""),
            life_stage=data.get("life_stage"),
            is_milestone=data.get("is_milestone", False),
        )


@dataclass
class UserTimeline:
    """
    用户的完整时间线

    V2 §6.1
    """
    user_id: str = ""
    events: List[TemporalEvent] = field(default_factory=list)
    life_stages: List[LifeStage] = field(default_factory=list)
    milestone_events: Dict[str, TemporalEvent] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "events": [e.to_dict() for e in self.events],
            "life_stages": [s.to_dict() for s in self.life_stages],
            "milestone_events": {k: v.to_dict() for k, v in self.milestone_events.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserTimeline":
        return cls(
            user_id=data.get("user_id", ""),
            events=[TemporalEvent.from_dict(e) for e in data.get("events", [])],
            life_stages=[LifeStage.from_dict(s) for s in data.get("life_stages", [])],
            milestone_events={
                k: TemporalEvent.from_dict(v)
                for k, v in data.get("milestone_events", {}).items()
            },
        )


# ============================================================
# 输出协议: MemoryContextPackage
# ============================================================

@dataclass
class ProfileSummary:
    """用户画像摘要 (Layer 0, 始终返回)"""
    core: str = ""                      # "张三, 30岁, 北京, 后端工程师"
    personality: str = ""               # "务实, 技术导向"
    active_schemas: List[str] = field(default_factory=list)
    gotchas: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "core": self.core,
            "personality": self.personality,
            "active_schemas": self.active_schemas,
            "gotchas": self.gotchas,
            "note": self.note,
        }


@dataclass
class MemoryIndexEntry:
    """索引视图条目 (Layer 1, ~100 tokens)"""
    node_id: str = ""
    date: str = ""
    content_type: str = ""
    title: str = ""
    relevance: float = 0.0
    meta_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "date": self.date,
            "type": self.content_type,
            "title": self.title,
            "relevance": self.relevance,
            "meta_tags": self.meta_tags,
        }


@dataclass
class MemorySummaryEntry:
    """摘要视图条目 (Layer 2, ~500 tokens)"""
    node_id: str = ""
    summary: str = ""
    confidence: float = 0.0
    meta_tags: List[str] = field(default_factory=list)
    meta_hints: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "summary": self.summary,
            "confidence": self.confidence,
            "meta_tags": self.meta_tags,
            "meta_hints": self.meta_hints,
        }


@dataclass
class MetaCognitionReport:
    """元认知报告"""
    confidence_summary: str = ""
    knowledge_gaps: List[Dict[str, Any]] = field(default_factory=list)
    unresolved_conflicts: List[Dict[str, Any]] = field(default_factory=list)
    stale_memories: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "confidence_summary": self.confidence_summary,
            "knowledge_gaps": self.knowledge_gaps,
            "unresolved_conflicts": self.unresolved_conflicts,
            "stale_memories": self.stale_memories,
        }


@dataclass
class TriggeredIntention:
    """被触发的前瞻性意图"""
    trigger_reason: str = ""
    intention: str = ""
    priority: str = "medium"
    meta_tags: List[str] = field(default_factory=list)
    source_intention_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger_reason": self.trigger_reason,
            "intention": self.intention,
            "priority": self.priority,
            "meta_tags": self.meta_tags,
            "source_intention_id": self.source_intention_id,
        }


@dataclass
class ActivatedSchema:
    """被激活的 Schema"""
    schema: str = ""
    evidence_summary: str = ""
    expected_inferences: List[str] = field(default_factory=list)
    meta_tags: List[str] = field(default_factory=list)
    source_schema_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "evidence_summary": self.evidence_summary,
            "expected_inferences": self.expected_inferences,
            "meta_tags": self.meta_tags,
            "source_schema_id": self.source_schema_id,
        }


@dataclass
class TemporalContext:
    """时间线上下文 (query 涉及时间时返回)"""
    relevant_timeline: List[Dict[str, str]] = field(default_factory=list)
    temporal_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relevant_timeline": self.relevant_timeline,
            "temporal_note": self.temporal_note,
        }


@dataclass
class MemoryContextPackage:
    """
    返回给调用方 LLM 的完整记忆上下文包

    V2 §7.1: 三层渐进式披露 + 元认知信号 + 前瞻意图 + Schema + 时间线
    """
    # Layer 0: 用户画像摘要 (始终返回)
    profile_summary: ProfileSummary = field(default_factory=ProfileSummary)

    # Layer 1: 索引视图 (~100 tokens)
    index_view: List[MemoryIndexEntry] = field(default_factory=list)

    # Layer 2: 摘要视图 (~500 tokens, 按需展开)
    summary_view: List[MemorySummaryEntry] = field(default_factory=list)

    # Layer 3: 完整内容 (按需懒加载)
    full_content_ids: List[str] = field(default_factory=list)

    # 元认知信号
    meta_cognition: MetaCognitionReport = field(default_factory=MetaCognitionReport)

    # 前瞻性意图
    triggered_intentions: List[TriggeredIntention] = field(default_factory=list)

    # 激活的 Schema
    activated_schemas: List[ActivatedSchema] = field(default_factory=list)

    # 时间线上下文
    temporal_context: Optional[TemporalContext] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_summary": self.profile_summary.to_dict(),
            "index_view": [e.to_dict() for e in self.index_view],
            "summary_view": [e.to_dict() for e in self.summary_view],
            "full_content_ids": self.full_content_ids,
            "meta_cognition": self.meta_cognition.to_dict(),
            "triggered_intentions": [t.to_dict() for t in self.triggered_intentions],
            "activated_schemas": [s.to_dict() for s in self.activated_schemas],
            "temporal_context": self.temporal_context.to_dict() if self.temporal_context else None,
        }


# ============================================================
# V1 兼容类型别名
# ============================================================

# MemoryEntry 作为 MemoryNode 的别名，保持旧代码不报错
MemoryEntry = MemoryNode


@dataclass
class MemoryMetadata:
    """
    V1 兼容: 记忆元数据

    新代码应直接使用 MemoryNode 的字段。此类保留用于与旧代码兼容。
    """
    uid: str = ""
    agent_id: str = ""
    layer: MemoryLayer = MemoryLayer.L1_RAW

    event_time: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    importance: float = 1.0
    recency_weight: float = 1.0
    access_count: int = 0
    last_accessed: Optional[datetime] = None

    source: Optional[str] = None
    parent_id: Optional[str] = None
    related_ids: List[str] = field(default_factory=list)

    custom: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        now = datetime.now()
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now
        if self.event_time is None:
            self.event_time = now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "agent_id": self.agent_id,
            "layer": self.layer.value,
            "event_time": int(self.event_time.timestamp()) if self.event_time else None,
            "created_at": int(self.created_at.timestamp()) if self.created_at else None,
            "updated_at": int(self.updated_at.timestamp()) if self.updated_at else None,
            "expires_at": int(self.expires_at.timestamp()) if self.expires_at else None,
            "importance": self.importance,
            "recency_weight": self.recency_weight,
            "access_count": self.access_count,
            "last_accessed": int(self.last_accessed.timestamp()) if self.last_accessed else None,
            "source": self.source,
            "parent_id": self.parent_id,
            "related_ids": self.related_ids,
            "custom": self.custom,
            "tags": self.tags,
            "tag_ners": list(self.tag_ners or []),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryMetadata":
        def parse_datetime(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value)
            if isinstance(value, str):
                return datetime.fromisoformat(value)
            return None

        layer = data.get("layer", MemoryLayer.L1_RAW)
        if isinstance(layer, str):
            layer = MemoryLayer.from_string(layer)

        return cls(
            uid=data.get("uid", ""),
            agent_id=data.get("agent_id", ""),
            layer=layer,
            event_time=parse_datetime(data.get("event_time")),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            expires_at=parse_datetime(data.get("expires_at")),
            importance=data.get("importance", 1.0),
            recency_weight=data.get("recency_weight", 1.0),
            access_count=data.get("access_count", 0),
            last_accessed=parse_datetime(data.get("last_accessed")),
            source=data.get("source"),
            parent_id=data.get("parent_id"),
            related_ids=data.get("related_ids", []),
            custom=data.get("custom", {}),
            tags=data.get("tags", []),
        )


@dataclass
class MemoryScore:
    """
    V1 兼容: 记忆评分

    V2 使用更复杂的评分公式 (参见 core/scorer.py)，此类保留兼容。
    """
    semantic_score: float = 0.0
    recency_score: float = 0.0
    importance_score: float = 0.0
    access_score: float = 0.0

    semantic_weight: float = 0.5
    recency_weight: float = 0.3
    importance_weight: float = 0.15
    access_weight: float = 0.05

    @property
    def final_score(self) -> float:
        return (
            self.semantic_score * self.semantic_weight
            + self.recency_score * self.recency_weight
            + self.importance_score * self.importance_weight
            + self.access_score * self.access_weight
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantic_score": self.semantic_score,
            "recency_score": self.recency_score,
            "importance_score": self.importance_score,
            "access_score": self.access_score,
            "final_score": self.final_score,
            "weights": {
                "semantic": self.semantic_weight,
                "recency": self.recency_weight,
                "importance": self.importance_weight,
                "access": self.access_weight,
            },
        }
