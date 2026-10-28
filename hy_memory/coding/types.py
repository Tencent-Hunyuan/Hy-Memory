# -*- coding: utf-8 -*-
"""
Coding Memory 数据结构

设计原则：
- 与 chat 链路完全分轨，独立 schema
- task + search_keys 多 key 召回
- boundary_envs 记录经验依赖的环境，供消费方判断是否仍适用
- 覆盖式 update（不留历史），可携带 DELETE op

对外 scene 与返回结构见 docs/scene_coding_usage.md。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any, Literal


# 事实层 status 取值
FACT_STATUSES = ("active", "superseded")
FactStatus = Literal["active", "superseded"]

# 写演变总结时最多往回追几代。不设上限的话，长命事实每次覆盖都要顺着 fact_link
# 走完整条链，既多查库又把提示词撑爆。超出的部分在提示词里标一句"更早若干代已略"。
MAX_FACT_CHAIN_ENTRIES = 8


def _epoch(value: Optional[datetime]) -> Optional[int]:
    """memory_at 对外是秒级 epoch，与 chat 一致。"""
    return int(value.timestamp()) if isinstance(value, datetime) else None


# ================================================================
# 草稿（LLM extract 阶段输出）
# ================================================================

@dataclass
class CodingMemoryDraft:
    """
    LLM extractor 产出的草稿，未经 reconcile / 持久化。

    字段用于 coding/working 链路内部抽取与 reconcile。
    """
    # 检索主键
    task: str
    search_keys: List[str] = field(default_factory=list)
    solution: str = ""
    boundary_envs: str = ""

    confidence: float = 0.7

    # 上下文（SDK 注入）
    user_id: str = ""
    agent_id: str = "default_agent"
    workspace_id: Optional[str] = None
    branch: Optional[str] = None
    session_id: Optional[str] = None
    files: List[str] = field(default_factory=list)
    source: str = "default"
    extraction_source: str = "auto_extract"
    # 对话时间（SDK 注入，同 chat 的 memory_at）
    memory_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        from ..utils.source_filter import normalize_source
        self.source = normalize_source(self.source)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "search_keys": list(self.search_keys),
            "solution": self.solution,
            "boundary_envs": self.boundary_envs,
            "confidence": self.confidence,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "workspace_id": self.workspace_id,
            "branch": self.branch,
            "session_id": self.session_id,
            "files": list(self.files),
            "source": self.source,
            "extraction_source": self.extraction_source,
            "memory_at": _epoch(self.memory_at),
        }


# ================================================================
# 已持久化记忆
# ================================================================

@dataclass
class CodingMemory:
    """已持久化的经验层记忆（SQLite + VDB 双层共同表示一条）"""
    # 主键
    experience_id: str
    user_id: str
    agent_id: str = "default_agent"

    # 检索主键
    task: str = ""
    search_keys: List[str] = field(default_factory=list)
    solution: str = ""
    boundary_envs: str = ""

    # 作用域（随结果透出，同时作为 search / list 的可选筛选条件）
    workspace_id: Optional[str] = None
    branch: Optional[str] = None

    # 上下文
    session_id: Optional[str] = None
    files: List[str] = field(default_factory=list)

    # 两层互链：本经验由哪些 fact 衍生（同批全互链回填）
    source_fact_ids: List[str] = field(default_factory=list)

    # 元数据
    confidence: float = 0.7
    source: str = "default"
    extraction_source: str = "auto_extract"

    # 时间
    # memory_at 是对话时间（同 chat），created_at / updated_at 是写库时间。
    # UPDATE 合并时取两者中较晚的那个对话时间，见 store.update。
    memory_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        from ..utils.source_filter import normalize_source
        self.source = normalize_source(self.source)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "task": self.task,
            "search_keys": list(self.search_keys),
            "solution": self.solution,
            "boundary_envs": self.boundary_envs,
            "workspace_id": self.workspace_id,
            "branch": self.branch,
            "session_id": self.session_id,
            "files": list(self.files),
            "source_fact_ids": list(self.source_fact_ids),
            "confidence": self.confidence,
            "source": self.source,
            "extraction_source": self.extraction_source,
            "memory_at": _epoch(self.memory_at),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ================================================================
# 事实层（episodic）：WorkingFact
#
# 与经验层（procedural）并排的第二层。经验层存"怎么干"（可复用方法，跨会话），
# 事实层存"这次怎么推进的"（一段 trace/session 的整体记录，为对话连贯服务）。
#
# 设计决策（见对话记录）：
#   - 无 TTL：靠 embed 召回 top-K + 独立 FactReconciler(LLM) 判覆盖来保新鲜，
#             而非过期淘汰。覆盖时旧 fact status→superseded 并回填 fact_link。
#   - session_id 只存不强隔离（同经验层），检索按 user 召回。
#   - 两层互链：fact.derived_experience_ids ↔ experience.source_fact_ids（同批全互链）。
#   - subject 硬锚点（可开关，见 fact_reconciler._SUBJECT_GATE_DEFAULT）：开启时覆盖
#     判定先过 subject 闸门，再走语义召回 + LLM。
#     没有这个锚点时（纯语义 + LLM 判覆盖），"同一 workspace 下有多个并列主体"的
#     场景容易出错：多人共用一份行程、多台机器共用一套部署脚本时，各主体的
#     statement 语义高度相似，LLM 可能把它们判成同一任务的先后版本，一次写入顶掉
#     别人的事实。subject 不同就禁止覆盖可以避免这一点：措辞相近顶多多留一条，
#     跨主体覆盖则是直接把别人的状态删掉。
#
#     为什么做成开关：这个闸门是精确字符串相等（见下面 normalize_subject），
#     它的正确性完全取决于 subject 是不是稳定实体名。subject 是自由文本或位置
#     标签时，同一实体会被写成不同 subject 而互相拦截；如果后续步骤依赖前一步
#     的结果，拦掉一次真实更新会让它读到过期值。所以默认关闭，需要时用
#     HY_FACT_SUBJECT_GATE=1 开启。

# ================================================================


def normalize_subject(subject: Optional[str]) -> str:
    """subject 的比较形式：去空白、折叠内部空格、小写。

    锚点只用来判"是不是同一个主体"，所以 `Dylan` / `dylan ` / `DYLAN` 必须同值；
    但不做同义词归并（`auth/middleware.ts` 与 `middleware` 是两个主体），那属于
    语义判断，是 LLM 那一步的事。空 subject 返回空串，调用方按"没有锚点"处理。
    """
    return " ".join((subject or "").split()).lower()


@dataclass
class WorkingFactDraft:
    """LLM extractor 产出的会话级事实草稿，未经 reconcile / 持久化。

    上下文（user_id / session_id 等）不在草稿里，由 FactReconciler 落库时注入。
    每段 trace 最多一条；statement 是可恢复状态记录，简洁覆盖用户任务、实际处理、
    证据边界与当前结束状态，并保留文件/目录路径、符号、命令、错误字符串、版本和
    配置值等检索锚点。
    """
    statement: str = ""
    files: List[str] = field(default_factory=list)
    # 这段会话的主体：被操作的那个人 / 文件 / 服务 / 组件。覆盖判定的硬锚点。
    subject: str = ""
    # 会话产出的可寻址原文：计划表、配置块、命令输出、结构化结果。statement 是
    # 摘要（检索向量），details 是被摘要掉的那些具体取值——按原样保留，供检索
    # 命中之后真正读取。摘要天然会丢"第二天午餐吃什么、多少钱"这种被寻址的格子，
    # 而那正是下游唯一需要的东西。
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statement": self.statement,
            "files": list(self.files),
            "subject": self.subject,
            "details": self.details,
        }


@dataclass
class WorkingFact:
    """一段 trace/session 的整体记录（SQLite working_fact + 独立 facts 集合）。"""
    # 主键
    fact_id: str
    user_id: str
    agent_id: str = "default_agent"

    # 上下文（存但不强隔离，同经验层）
    session_id: Optional[str] = None
    workspace_id: Optional[str] = None
    branch: Optional[str] = None
    source: str = "default"

    # 内容
    statement: str = ""                    # 会话摘要（任务/做法/结果）= 检索向量
    files: List[str] = field(default_factory=list)  # 本次 trace 实际操作或调查的文件/目录路径

    # 这段会话的主体（人 / 文件 / 服务 / 组件）。覆盖判定的硬闸门：subject 不同
    # 一律不许覆盖，见模块头注释。空串表示抽取时没能定出主体，此时退回纯语义 + LLM
    # 判定（老库里的行也是空串，行为与改动前一致）。
    subject: str = ""

    # 会话产出的可寻址原文，不进检索向量，命中后随 statement 一起交给下游读取。
    # statement 负责"能被找到"，details 负责"找到之后真的有那个值"。
    details: str = ""

    # 覆盖 / 互链
    fact_link: Optional[str] = None        # 被哪条新 fact 覆盖（覆盖后回填，追溯用）
    derived_experience_ids: List[str] = field(default_factory=list)  # 衍生了哪些经验
    status: FactStatus = "active"          # active / superseded（软删除）

    # LLM 写的进展总结："同一任务怎样跨 session 推进到当前状态"，每次 SUPERSEDE 重写。
    # 空串表示没有演变可讲（从没被覆盖过），或那次 LLM 调用失败了。
    #
    # 历史本身不存在这里——被覆盖的事实原样留在库里，fact_link 指向后继，顺着
    # 反向走就能还原历代说法。这里只放提炼过的那句话。
    #
    # 不进向量库：检索认的是 statement 的当前值，把历史说法一起 embed 会让已经
    # 作废的措辞重新具备召回力。
    fact_chain_summary: str = ""

    # 时间
    memory_at: Optional[datetime] = None   # 对话时间（同 chat），没传时是收到请求的时间
    created_at: Optional[datetime] = None  # 产生时间，新的优先

    def __post_init__(self) -> None:
        from ..utils.source_filter import normalize_source
        self.source = normalize_source(self.source)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "branch": self.branch,
            "source": self.source,
            "statement": self.statement,
            "files": list(self.files),
            "subject": self.subject,
            "details": self.details,
            "fact_link": self.fact_link,
            "derived_experience_ids": list(self.derived_experience_ids),
            "status": self.status,
            "fact_chain_summary": self.fact_chain_summary,
            "memory_at": _epoch(self.memory_at),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ================================================================
# 事实血统
# ================================================================

def short_date(iso: str) -> str:
    """ISO 时间取到日。解析不了就原样返回，宁可难看也别丢信息。"""
    s = (iso or "").strip()
    return s[:10] if len(s) >= 10 else s


# ================================================================
# Reconcile op
# ================================================================

ReconcileAction = Literal["ADD", "UPDATE", "DELETE", "SKIP"]


@dataclass
class ReconcileOp:
    """
    Reconciler 决策出的单个操作。

    - ADD     ← 由某 draft 触发新增；draft_idx 必填，target_experience_id 不填
    - UPDATE  ← 由某 draft 触发更新；draft_idx + target_experience_id 都必填
    - DELETE  ← 新内容明确否定/作废了某 candidate；draft_idx 记录"由哪条 draft 触发"
                可空（少数情况），target_experience_id 必填
    - SKIP    ← duplicate；draft_idx 必填，target_experience_id 不填

    注：一次 reconcile 调用产出的 ops 数 *可以超过* drafts 数（一个 draft 可能伴随多条 DELETE）。
    """
    action: ReconcileAction
    draft_idx: Optional[int] = None
    target_experience_id: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "draft_idx": self.draft_idx,
            "target_experience_id": self.target_experience_id,
            "reason": self.reason,
        }


# ================================================================
# 事实层 op
#
# 会话级事实不设价值门槛，所以每条 draft 必定产出一个 INSERT；若它继续推进
# 某个旧 fact 所记录的同一用户任务，还会额外产出 SUPERSEDE（软删并过继经验链）。
# ================================================================

FactReconcileAction = Literal["INSERT", "SUPERSEDE"]


@dataclass
class FactReconcileOp:
    """
    - INSERT     ← 新事实入库 active；draft_idx + fact_id 必填
    - SUPERSEDE  ← 旧 session fact 与新 fact 属于同一用户任务的连续推进；
                   fact_id 是旧记录，superseded_by 是新的进展记录
    """
    action: FactReconcileAction
    draft_idx: Optional[int] = None
    fact_id: Optional[str] = None
    superseded_by: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "draft_idx": self.draft_idx,
            "fact_id": self.fact_id,
            "superseded_by": self.superseded_by,
            "reason": self.reason,
        }
