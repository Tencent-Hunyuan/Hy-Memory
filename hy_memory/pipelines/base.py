"""
HY Memory - 抽象基类 + 统一协议

定义 WritePipeline / ReadPipeline 抽象接口，以及统一的请求/响应协议。

设计原则:
1. 统一协议 — WriteRequest/Response, ReadRequest/Response 是版本无关的
2. 可扩展 — 各版本在 extra_data 中传递版本特有的数据
3. 生命周期管理 — 通过 initialize() / close() 管理资源
"""

import re
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

# 类型前向声明 (避免循环导入)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..utils.tracer import PipelineTracer


# ================================================================
# 统一请求/响应协议
# ================================================================

@dataclass
class PipelineContext:
    """
    Pipeline 执行上下文 — 贯穿整个 write/read 流程的共享状态。
    
    公共模块可以通过此上下文传递中间结果，Pipeline 内部使用。
    """
    # 请求标识
    request_id: str = ""
    
    # 用户隔离（两级: user_id + agent_id）
    user_id: str = ""
    agent_id: str = ""

    # 会话
    session_id: str = ""

    # 中间结果存储 (Pipeline 内部使用)
    intermediate: Dict[str, Any] = field(default_factory=dict)

    # 性能追踪
    start_time: Optional[datetime] = None
    timings: Dict[str, float] = field(default_factory=dict)

    def elapsed_ms(self) -> float:
        """返回从 start_time 到现在的毫秒数"""
        if self.start_time is None:
            return 0.0
        return (datetime.now() - self.start_time).total_seconds() * 1000

    @property
    def isolation_key(self) -> str:
        """构建隔离键: {user_id}:{agent_id}"""
        parts = [self.user_id or "default", self.agent_id or "default"]
        return ":".join(parts)


@dataclass
class ToolCall:
    """
    Assistant 发起的 tool 调用（OpenAI / Anthropic 通用化表示）。

    OpenAI 风格的 `tool_calls` 数组里每个 entry、Anthropic 风格的 content
    block (`type=="tool_use"`) 都规范化到这个结构。
    """
    id: str = ""                         # 调用 id（OpenAI: tc.id；Anthropic: tool_use block id）
    name: str = ""                       # tool 名
    arguments: Dict[str, Any] = field(default_factory=dict)
    # OpenAI 把 arguments 编码成 JSON 字符串；规范化层负责 json.loads 后塞进来
    # Anthropic 直接给 dict；规范化层照搬

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            arguments=data.get("arguments", {}) or {},
        )


# ── 用户名字行（多真人对话专用）──
# 有名字的对话渲染成：
#   用户名叫Tim
#   [user]: 我喜欢咖啡
#   [John]: 我也喜欢茶
# 名字单独占一行、整段只出现一次；[user] 标签**不换成名字**，视角信息留在标签里。
# extract 用这一行给 L0 basic_info 取 name（用户不必在对话里自报姓名）。
_USER_NAME_LINE_ZH = "用户名叫{name}"
_USER_NAME_LINE_EN = "The user's name is {name}"

_USER_NAME_LINE_RE = re.compile(
    r"^(?:用户名叫|The user's name is)\s*(.+?)\s*$"
)


def render_user_name_line(name: str, is_zh: bool) -> str:
    """渲染文首的用户名字行；name 为空返回空串。"""
    name = (name or "").strip()
    if not name:
        return ""
    tpl = _USER_NAME_LINE_ZH if is_zh else _USER_NAME_LINE_EN
    return tpl.format(name=name)


def parse_user_name_line(line: str) -> Optional[str]:
    """若该行是用户名字行则返回名字，否则返回 None。

    L1_RAW 存的就是 get_flat_content() 的产物，回读历史时要靠这个把名字行识别出来，
    否则它会被当成一条无前缀的裸消息（见 MemoryWriter._get_recent_history）。
    """
    m = _USER_NAME_LINE_RE.match((line or "").strip())
    return m.group(1) if m else None


@dataclass
class ChatMessage:
    """单条聊天消息，兼容 OpenAI / Anthropic messages 格式（含 tool 调用）"""
    role: str = "user"       # user / assistant / system / tool
    content: str = ""

    name: Optional[str] = None
    # 说话人显示名（OpenAI messages 的 `name` 字段）。多真人对话用它区分对方是谁：
    #   role=user     → 当前记忆归属者。带 name 只用于给出"用户叫什么"，标签仍渲染 [user]
    #   role=assistant + name → **对方真人**（不是 AI），渲染为 [<name>]
    #   role=assistant 无 name → AI assistant，渲染为 [assistant]
    # 视角由 role 决定，name 只是显示名——两侧都可以有 name。
    # role=tool 时该字段不走这里（工具名在 tool_name）。

    # ── tool 扩展（仅 productivity / coding 路径使用；chat 路径忽略）──
    tool_calls: List[ToolCall] = field(default_factory=list)
    # role=assistant 时：发起的 tool 调用列表
    # 其他 role 必为空

    tool_call_id: Optional[str] = None
    # role=tool 时：本消息对应的调用 id
    # 其他 role 为 None

    tool_name: Optional[str] = None
    # role=tool 时：本消息对应的工具名（OpenAI 直接给；Anthropic 需要从前序 tool_use 反查，可空）

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_name is not None:
            d["tool_name"] = self.tool_name
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", "") or "",
            name=data.get("name") or None,
            tool_calls=[ToolCall.from_dict(tc) for tc in (data.get("tool_calls") or [])],
            tool_call_id=data.get("tool_call_id"),
            tool_name=data.get("tool_name"),
        )

    def speaker_label(self) -> str:
        """渲染用的说话人标签（不含方括号）。

        role=user 恒为 "user"（即使带 name），视角信息留在标签里；
        对方真人（role=assistant + name）用名字；其余回退到 role。
        """
        if self.role == "user":
            return "user"
        if self.role == "assistant" and self.name:
            return self.name
        return self.role

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def is_tool_message(self) -> bool:
        return self.role == "tool"


@dataclass
class WriteRequest:
    """
    统一写入请求协议。
    
    所有版本的 WritePipeline 都接收此格式的请求。
    版本特有参数通过 extra 传递。
    
    输入方式 (二选一):
    1. content: str — 单条文本 (向后兼容)
    2. messages: List[ChatMessage] — 多轮 (user-assistant)*N 对话
       优先使用 messages，如果为空则回退到 content。
    """
    # 必填 (二选一)
    content: str = ""
    messages: List[ChatMessage] = field(default_factory=list)
    
    # 用户隔离
    user_id: str = ""
    agent_id: str = ""
    # 调用来源（openclaw / cursor / codex 等），与 agent_id 正交。
    source: str = "default"

    # 请求追溯 id。由 client 入口显式注入（contextvar-immune），作为整条写入链路
    # 落库 memory_ops / WRITE_SUMMARY / pipeline_logs 的单一真相源。空值时 writer
    # 会用 contextvar 兜底。
    request_id: str = ""

    # 可选元数据
    content_type: str = "text"
    session_id: str = ""
    turn_index: Optional[int] = None
    role: str = "user"
    assistant_content: Optional[str] = None
    
    # 版本特有参数 (V1: agent_mode 等; V2: gate_config 等)
    extra: Dict[str, Any] = field(default_factory=dict)
    
    # 已有记忆 (V1 合并检测用)
    existing_memories: Optional[List[Dict[str, Any]]] = None

    # Public client process_raw() only: reuse these exact L1 nodes as input.
    # Ordinary add() never populates this field.
    existing_raw_ids: List[str] = field(default_factory=list)

    # 记忆时间戳（不传则用当前时间 datetime.now()）
    memory_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        from ..utils.source_filter import normalize_source
        self.source = normalize_source(self.source)


    # 调用方自定义的业务 meta，随本次产出的记忆一并存储、查询时原样返回。
    # 纯附加负载：不 embed、不参与召回、不作过滤条件。
    #
    # 刻意独立于 extra：extra 混放 SDK 内部控制键（agent_mode / mode / layer），
    # 与调用方的自定义键放一起会互相覆盖。
    #
    # 命名带 client_ 前缀是为了和 writer 内部的 "meta"（抽取结果的元信息，
    # 形如 {"content","layer","tags"}）区分开，二者完全无关。
    client_meta: Optional[Dict[str, Any]] = None

    # ── Coding memory 路径专用（chat 路径忽略）──
    workspace_id: Optional[str] = None
    # repo 标识。建议用 git remote URL 规范化（如 "github.com/org/repo"）。
    # 随经验与事实落库、召回时透出，并作为 search / list 的可选筛选条件；
    # 缺失不会拒写。
    branch: Optional[str] = None
    # 分支名。同 workspace_id。

    # ---- 便捷方法 ----

    def has_messages(self) -> bool:
        """是否有多轮对话输入"""
        return bool(self.messages)

    def get_user_queries(self) -> List[str]:
        """提取所有 user 角色的消息内容 (用于 embed 查 novelty)"""
        return [m.content for m in self.messages if m.role == "user" and m.content.strip()]

    def user_name(self) -> Optional[str]:
        """当前 user（role=user 一侧）的显示名；没有则 None。"""
        for m in self.messages:
            if m.role == "user" and m.name:
                return m.name.strip() or None
        return None

    def get_flat_content(self) -> str:
        """
        获取完整的文本内容:
        - 如果有 messages → 拼接为对话格式
        - 否则 → 返回 content

        渲染格式（多真人对话）:
            用户名叫Tim
            [user]: 我喜欢咖啡
            [John]: 我也喜欢茶

        无 name 时与历史行为完全一致（`[user]: ...` / `[assistant]: ...`）。
        名字行只在 role=user 带 name 时出现，且整段只出现一次；语言按消息内容判定
        （与 extractor 选 zh/en prompt 同一套判据）。
        """
        if not self.messages:
            return self.content or ""

        lines = [f"[{m.speaker_label()}]: {m.content}" for m in self.messages]

        name = self.user_name()
        if name:
            from ..utils.lang_detect import is_chinese

            name_line = render_user_name_line(
                name, is_chinese(messages=self.messages)
            )
            if name_line:
                lines.insert(0, name_line)

        return "\n".join(lines)

    def get_dialogue_text(self) -> str:
        """
        获取适合 LLM 阅读的对话格式:
        - 如果有 messages → 完整对话格式
        - 如果有 content + assistant_content → 单轮 QA 格式
        - 否则 → 原始 content
        """
        if self.messages:
            parts = []
            for m in self.messages:
                label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(m.role, m.role)
                parts.append(f"{label}: {m.content}")
            return "\n".join(parts)
        if self.assistant_content:
            return f"用户: {self.content}\n助手: {self.assistant_content}"
        return self.content or ""


@dataclass
class WriteResponse:
    """
    统一写入响应协议。
    
    所有版本的 WritePipeline 都返回此格式的响应。
    """
    success: bool = False
    
    # 写入结果
    memory_id: str = ""
    layer: str = ""
    message: str = ""
    
    # 提取的实体 (通用)
    entities: List[Dict[str, Any]] = field(default_factory=list)
    
    # 性能
    elapsed_ms: float = 0.0
    tokens_used: int = 0
    
    # 错误信息
    error_code: int = 0
    error_message: str = ""
    
    # 版本特有数据 (V1: routing_confidence, should_merge 等; V2: gate_passed, l2_fact_ids 等)
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "memory_id": self.memory_id,
            "layer": self.layer,
            "message": self.message,
            "entities": self.entities,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "tokens_used": self.tokens_used,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "extra": self.extra,
        }


@dataclass
class ReadRequest:
    """
    统一读取请求协议。
    """
    # 必填
    query: str = ""

    # 用户隔离
    user_id: str = ""              # 单用户（向后兼容）
    user_ids: List[str] = field(default_factory=list)  # 多用户搜索
    agent_id: str = ""

    # 灵活搜索过滤（list 形式）
    agent_ids: List[str] = field(default_factory=list)
    session_ids: List[str] = field(default_factory=list)
    # 调用来源过滤。两者同时指定时语义为 IN(include) AND NOT IN(exclude)，
    # exclude 优先。底层 store 必须在 top-k/limit 前执行。
    include_sources: List[str] = field(default_factory=list)
    exclude_sources: List[str] = field(default_factory=list)

    # 检索参数
    limit: int = 10
    layers: Optional[List[str]] = None
    min_score: float = 0.4             # 通用最低分数阈值（部分 reader 用作召回门槛）

    # Schema (L6) 独立召回参数
    schema_min_score: float = 0.4      # Schema 路的最低分数阈值
    schema_limit: int = 10             # Schema 路的最大返回数

    # Proactive (Intention) 独立召回参数
    intention_limit: int = 0           # Intention 路的 topk，默认 0 关闭


    # 时间过滤
    created_after: Optional[float] = None  # Unix timestamp (float)，只返回 gmt_created >= 此值的记忆

    # 预计算的查询向量 (可选, 避免重复 embed)
    query_embedding: Optional[List[float]] = None

    # 会话
    session_id: str = ""

    # Trace / 可观测
    # 如果业务方在 async_search 层已生成 request_id（推荐），会透传进来。
    # reader 用它把 pipeline_logs 关联到本次 search，供 inspector 查询。
    request_id: str = ""

    # 版本特有参数 (V2: token_budget, use_llm_understanding 等)
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from ..utils.source_filter import normalize_source_filters
        self.include_sources, self.exclude_sources = normalize_source_filters(
            self.include_sources, self.exclude_sources,
        )


@dataclass
class ReadResponse:
    """
    统一读取响应协议。
    """
    success: bool = False
    
    # 召回结果 (通用格式)
    memories: List[Dict[str, Any]] = field(default_factory=list)
    total_found: int = 0
    
    # 性能
    elapsed_ms: float = 0.0
    
    # 错误信息
    error_code: int = 0
    error_message: str = ""
    
    # 版本特有数据 (V2: context_package, query_understanding, recalled_node_ids 等)
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "memories": self.memories,
            "total_found": self.total_found,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "extra": self.extra,
        }


# ================================================================
# Pipeline 抽象基类
# ================================================================

class WritePipeline(ABC):
    """
    写入抽象基类。

    公共模块 (embed_service, vector_store, etc.) 通过构造函数注入。

    Example:
        class MemoryWriter(WritePipeline):
            async def write(self, request, ctx):
                ...
    """
    
    # Pipeline 版本标识 (子类必须覆盖)
    VERSION: str = "base"
    
    @abstractmethod
    async def initialize(self) -> None:
        """
        初始化 Pipeline 所需的资源。
        
        例如: 初始化存储连接、加载模型等。
        在首次使用前调用一次即可。
        """
        pass
    
    @abstractmethod
    async def write(
        self,
        request: WriteRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional["PipelineTracer"] = None,
    ) -> WriteResponse:
        """
        执行写入流程。
        
        Args:
            request: 统一写入请求
            ctx: Pipeline 上下文 (可选, 用于传递中间状态)
            
        Returns:
            统一写入响应
        """
        pass
    
    async def close(self) -> None:
        """释放资源 (默认空实现)"""
        pass
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} version={self.VERSION}>"


class ReadPipeline(ABC):
    """
    读取抽象基类。
    """
    
    # Pipeline 版本标识 (子类必须覆盖)
    VERSION: str = "base"
    
    @abstractmethod
    async def initialize(self) -> None:
        """初始化 Pipeline 所需的资源。"""
        pass
    
    @abstractmethod
    async def read(
        self,
        request: ReadRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional["PipelineTracer"] = None,
    ) -> ReadResponse:
        """
        执行读取流程。
        
        Args:
            request: 统一读取请求
            ctx: Pipeline 上下文
            
        Returns:
            统一读取响应
        """
        pass
    
    async def close(self) -> None:
        """释放资源 (默认空实现)"""
        pass
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} version={self.VERSION}>"
