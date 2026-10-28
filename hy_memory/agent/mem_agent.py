"""
Agent Memory - MemAgent 智能体协调器

统一入口，协调调度其他智能体（Extractor、Reflector）。

功能：
- 接收处理请求
- 根据任务类型调度对应智能体
- 聚合处理结果
- 错误处理和降级

示例：
    agent = MemAgent()
    
    # 处理添加记忆请求
    result = await agent.process_add(
        content="用户说他喜欢川菜，尤其是麻婆豆腐",
        context={"uid": "user_456"}
    )
    
    # 结果包含提取的信息
    print(result.extracted_info)  # {"preferences": {"food": "川菜"}}
    print(result.suggested_layer)  # "profile"
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

from .extractor import Extractor
from .reflector import Reflector
from .llm_provider import LLMProvider
from ..config import MemoryConfig

logger = logging.getLogger(__name__)


class ProcessMode(Enum):
    """处理模式"""
    FULL = "full"           # 完整处理（提取+摘要+反思）
    EXTRACT_ONLY = "extract"  # 仅提取
    SKIP = "skip"           # 跳过 Agent 处理


@dataclass
class AgentResult:
    """
    智能体处理结果
    """
    success: bool
    
    # 提取结果
    extracted_info: Dict[str, Any] = field(default_factory=dict)
    suggested_layer: Optional[str] = None
    
    # 摘要结果
    summary: Optional[str] = None  # LLM 原始摘要文本（已 strip），直接用于存储
    
    # 反思结果
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    should_merge: bool = False
    merge_target_id: Optional[str] = None
    
    # 元信息
    processing_time_ms: float = 0
    tokens_used: int = 0
    error: Optional[str] = None
    error_code: Optional[str] = None        # "EMPTY_RESPONSE" / "JSON_PARSE_FAILED" / "LLM_ERROR"
    extract_raw_response: Optional[str] = None  # LLM 原始返回（失败时保留）

    # 细分统计（各步骤的 token 和耗时）
    extract_tokens_used: int = 0
    extract_prompt_tokens: int = 0
    extract_completion_tokens: int = 0
    extract_elapsed_ms: float = 0
    summary_tokens_used: int = 0
    summary_prompt_tokens: int = 0
    summary_completion_tokens: int = 0
    summary_elapsed_ms: float = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "extracted_info": self.extracted_info,
            "suggested_layer": self.suggested_layer,
            "summary": self.summary,
            "conflicts": self.conflicts,
            "should_merge": self.should_merge,
            "processing_time_ms": self.processing_time_ms,
            "tokens_used": self.tokens_used,
            "error": self.error,
            "extract_tokens_used": self.extract_tokens_used,
            "extract_prompt_tokens": self.extract_prompt_tokens,
            "extract_completion_tokens": self.extract_completion_tokens,
            "extract_elapsed_ms": self.extract_elapsed_ms,
            "summary_tokens_used": self.summary_tokens_used,
            "summary_prompt_tokens": self.summary_prompt_tokens,
            "summary_completion_tokens": self.summary_completion_tokens,
            "summary_elapsed_ms": self.summary_elapsed_ms,
        }


class MemAgent:
    """
    记忆智能体协调器
    
    负责协调 Extractor、Reflector 完成记忆处理。
    """
    
    def __init__(
        self,
        config: Optional[MemoryConfig] = None,
        llm_provider: Optional[LLMProvider] = None,
    ):
        """
        初始化 MemAgent
        
        Args:
            config: 配置对象
            llm_provider: LLM 提供器
        """
        self.config = config or MemoryConfig.from_env()
        self.llm_provider = llm_provider or LLMProvider(self.config)
        
        # 初始化子智能体（传入 llm_config 以支持各场景 max_tokens 配置）
        llm_config = self.config.llm
        self.extractor = Extractor(self.llm_provider, llm_config)
        self.reflector = Reflector(self.llm_provider, llm_config)
        
        logger.debug("MemAgent initialized")

    async def process_add(
        self,
        content: str,
        context: Dict[str, Any],
        mode: ProcessMode = ProcessMode.FULL,
        existing_memories: List[Dict] = None,
        memory_at: Optional[datetime] = None,
        existing_tags: Optional[List[str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_registry: Optional[Any] = None,
        tool_context: Optional[Dict[str, Any]] = None,
        history_context: str = "",
        basic_profile_fields: Optional[Dict[str, str]] = None,
    ) -> AgentResult:
        """
        处理添加记忆请求

        Args:
            content: 记忆内容
            context: 上下文信息（uid, agent_id 等）
            mode: 处理模式
            existing_memories: 已有相似记忆（用于冲突检测）
            memory_at: 记忆发生时间（用作 extract prompt 的 current_time 基准）
            existing_tags: 可选，该用户已有的所有 tags（供 extractor 传给 LLM 优先复用）
            tools:   **DEPRECATED** v0.1.5.13+ 不再透传到 LLM；保留参数仅向后兼容
            tool_registry: **DEPRECATED** 同上
            tool_context:  保留（未使用）
            basic_profile_fields:
                {field_name: description} 字段表，由调用方从 MemoryConfig.basic_profile.fields
                透传过来，extractor 会渲染到 prompt 中要求 LLM 在 JSON basic_info 字段返回。

        Returns:
            处理结果
        """
        start_time = datetime.now()
        total_tokens = 0

        # 确定 extract prompt 的时间基准：memory_at 优先，不传则为空字符串（prompt 中留空）
        current_time_str = memory_at.isoformat(timespec="seconds") if memory_at else ""

        try:
            result = AgentResult(success=True)

            # 提取结构化信息；旧 rolling-summary 路径已移除。
            _do_extract = mode in (ProcessMode.FULL, ProcessMode.EXTRACT_ONLY)

            async def _run_extract():
                return await self.extractor.extract(
                    content, context,
                    current_time=current_time_str,
                    existing_tags=existing_tags,
                    tools=tools,
                    tool_registry=tool_registry,
                    tool_context=tool_context,
                    history_context=history_context,
                    basic_profile_fields=basic_profile_fields,
                )

            _t_parallel = datetime.now()

            extract_result = await _run_extract() if _do_extract else None

            # 处理 extract 结果
            if _do_extract and extract_result:
                _extract_elapsed = (datetime.now() - _t_parallel).total_seconds() * 1000
                result.extracted_info = extract_result.info
                result.suggested_layer = extract_result.suggested_layer
                total_tokens += extract_result.tokens_used
                result.extract_tokens_used = extract_result.tokens_used
                result.extract_prompt_tokens = extract_result.prompt_tokens
                result.extract_completion_tokens = extract_result.completion_tokens
                result.extract_elapsed_ms = _extract_elapsed
                result.extract_raw_response = extract_result.raw_response
                result._extract_result = extract_result  # 保留完整结果供 trace 日志使用

                # Extract 失败时提前返回
                if not extract_result.success:
                    result.success = False
                    result.error = extract_result.error
                    result.error_code = extract_result.error_code
                    result.processing_time_ms = (
                        datetime.now() - start_time
                    ).total_seconds() * 1000
                    result.tokens_used = total_tokens
                    logger.warning(
                        f"MemAgent: extract failed: code={extract_result.error_code} "
                        f"error={extract_result.error}"
                    )
                    return result

                # INFO: 提取结果摘要
                info = extract_result.info or {}
                identity = info.get("identity", []) or []
                facts = info.get("facts", []) or []
                basic_info = info.get("basic_info", {}) or {}
                basic_field_count = sum(
                    1 for k, v in basic_info.items() if v and v != "null"
                )
                logger.info(
                    f"MemAgent: extraction → "
                    f"identity={len(identity)} facts={len(facts)} "
                    f"basic_info={basic_field_count}fields"
                )
                logger.debug(f"MemAgent: extracted_info={extract_result.info}")

            # 3. 冲突检测（如果有已有记忆）
            if mode == ProcessMode.FULL and existing_memories:
                reflect_result = await self.reflector.check_conflicts(
                    content,
                    existing_memories,
                    context
                )
                result.conflicts = reflect_result.conflicts
                result.should_merge = reflect_result.should_merge
                result.merge_target_id = reflect_result.merge_target_id
                total_tokens += reflect_result.tokens_used
                logger.info(
                    f"MemAgent: conflict detection → "
                    f"conflicts={len(reflect_result.conflicts)} "
                    f"should_merge={reflect_result.should_merge}"
                )
            
            # 计算处理时间
            result.processing_time_ms = (
                datetime.now() - start_time
            ).total_seconds() * 1000
            result.tokens_used = total_tokens

            logger.debug(f"MemAgent: tokens_used={total_tokens}")

            return result
            
        except Exception as e:
            logger.error(f"MemAgent process_add failed: {e}")
            return AgentResult(
                success=False,
                error=str(e),
                processing_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
            )
    
    async def process_recall(
        self,
        query: str,
        memories: List[Dict],
        context: Dict[str, Any],
    ) -> AgentResult:
        """
        处理召回请求（可选的后处理）
        
        可以对召回结果进行：
        - 重排序
        - 摘要生成
        - 答案生成
        
        Args:
            query: 查询内容
            memories: 召回的记忆列表
            context: 上下文信息
        
        Returns:
            处理结果
        """
        start_time = datetime.now()
        
        try:
            # 生成综合摘要
            combined_content = "\n".join([m.get("content", "") for m in memories])
            
            if combined_content:
                abstract_result = await self.abstractor.abstract(
                    combined_content,
                    context,
                    focus=query  # 聚焦于查询内容
                )
                
                return AgentResult(
                    success=True,
                    summary=abstract_result.summary,
                    tokens_used=abstract_result.tokens_used,
                    processing_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                )
            
            return AgentResult(
                success=True,
                processing_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
            )
            
        except Exception as e:
            logger.error(f"MemAgent process_recall failed: {e}")
            return AgentResult(
                success=False,
                error=str(e),
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取智能体统计信息"""
        return {
            "extractor": self.extractor.get_stats(),
            "reflector": self.reflector.get_stats(),
        }
