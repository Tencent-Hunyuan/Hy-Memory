"""
Agent Memory - Merger 合并器

检测重复记忆并合并，避免信息冗余。

功能：
- 相似度检测（基于语义相似度）
- 合并决策（是否合并、合并到哪条）
- 内容合并（生成合并后的内容）
- 去重处理

示例：
    merger = Merger()
    
    # 检查是否需要合并
    result = merger.check_merge(
        new_content="用户喜欢川菜，尤其是麻婆豆腐",
        existing_memories=[
            {"id": "mem_1", "content": "用户喜欢川菜", "score": 0.95},
            {"id": "mem_2", "content": "用户住在北京", "score": 0.3},
        ]
    )
    
    if result.should_merge:
        print(f"合并到 {result.target_memory_id}")
        print(f"合并后内容: {result.merged_content}")
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import logging

from ..config import LLMConfig as GlobalLLMConfig

logger = logging.getLogger(__name__)


class MergeStrategy(Enum):
    """合并策略"""
    APPEND = "append"           # 追加内容
    REPLACE = "replace"         # 替换内容
    SMART = "smart"             # 智能合并（需要 LLM）
    KEEP_BOTH = "keep_both"     # 都保留


@dataclass
class MergeResult:
    """
    合并结果
    """
    should_merge: bool = False
    target_memory_id: Optional[str] = None
    merged_content: Optional[str] = None
    strategy: MergeStrategy = MergeStrategy.KEEP_BOTH
    reason: str = ""
    similarity_score: float = 0.0


@dataclass
class MergerConfig:
    """
    合并器配置
    """
    # 相似度阈值
    merge_threshold: float = 0.85          # 高于此值触发合并
    duplicate_threshold: float = 0.95      # 高于此值认为是重复
    
    # 合并策略
    default_strategy: MergeStrategy = MergeStrategy.SMART
    
    # 其他选项
    max_content_length: int = 2000         # 合并后最大长度
    enable_smart_merge: bool = True        # 是否启用智能合并


class Merger:
    """
    记忆合并器
    
    检测和处理重复/相似记忆。
    """
    
    def __init__(
        self,
        config: Optional[MergerConfig] = None,
        llm_config: Optional[GlobalLLMConfig] = None,
    ):
        """
        初始化合并器
        
        Args:
            config: 合并器配置
            llm_config: LLM 配置（用于读取 max_tokens 等参数）
        """
        self.config = config or MergerConfig()
        self._llm_config = llm_config or GlobalLLMConfig()
        
        # 统计
        self._check_count = 0
        self._merge_count = 0
        
        logger.info("Merger initialized")
    
    def check_merge(
        self,
        new_content: str,
        existing_memories: List[Any],
        context: Dict[str, Any] = None,
    ) -> MergeResult:
        """
        检查是否需要合并
        
        Args:
            new_content: 新记忆内容
            existing_memories: 已有记忆列表（需要有 score 属性或字段）
            context: 上下文信息
        
        Returns:
            合并结果
        """
        self._check_count += 1
        
        if not existing_memories:
            return MergeResult(should_merge=False, reason="No existing memories")
        
        # 找到最相似的记忆
        best_match = None
        best_score = 0.0
        
        for mem in existing_memories:
            # 获取相似度分数
            score = self._get_similarity_score(mem)
            
            if score > best_score:
                best_score = score
                best_match = mem
        
        # 判断是否需要合并
        if best_score >= self.config.duplicate_threshold:
            # 完全重复，跳过
            return MergeResult(
                should_merge=True,
                target_memory_id=self._get_memory_id(best_match),
                strategy=MergeStrategy.KEEP_BOTH,  # 保留旧的
                similarity_score=best_score,
                reason="Duplicate content detected",
            )
        
        if best_score >= self.config.merge_threshold:
            # 需要合并
            merged_content = self._merge_content(
                new_content,
                self._get_content(best_match),
            )
            
            self._merge_count += 1
            
            return MergeResult(
                should_merge=True,
                target_memory_id=self._get_memory_id(best_match),
                merged_content=merged_content,
                strategy=MergeStrategy.SMART,
                similarity_score=best_score,
                reason="Similar content, merged",
            )
        
        # 不需要合并
        return MergeResult(
            should_merge=False,
            similarity_score=best_score,
            reason="No similar memory found",
        )
    
    def _get_similarity_score(self, memory: Any) -> float:
        """获取相似度分数"""
        if isinstance(memory, dict):
            return memory.get("score", 0.0)
        elif hasattr(memory, "score"):
            score = memory.score
            if hasattr(score, "semantic_score"):
                return score.semantic_score
            return float(score) if score else 0.0
        return 0.0
    
    def _get_memory_id(self, memory: Any) -> str:
        """获取记忆 ID"""
        if isinstance(memory, dict):
            return memory.get("id", memory.get("memory_id", ""))
        elif hasattr(memory, "memory_id"):
            return memory.memory_id
        return ""
    
    def _get_content(self, memory: Any) -> str:
        """获取记忆内容"""
        if isinstance(memory, dict):
            return memory.get("content", "")
        elif hasattr(memory, "content"):
            return memory.content
        return ""
    
    def _merge_content(
        self,
        new_content: str,
        old_content: str,
    ) -> str:
        """
        合并内容
        
        简单实现：如果新内容更长，用新内容替换；
        否则追加新信息。
        
        Args:
            new_content: 新内容
            old_content: 旧内容
        
        Returns:
            合并后的内容
        """
        # 简单策略：使用更长的内容
        if len(new_content) > len(old_content) * 1.2:
            return new_content
        
        # 检查新内容是否包含额外信息
        # 简单方法：检查新内容是否完全包含在旧内容中
        if new_content.strip() in old_content:
            return old_content
        
        # 追加新信息
        # 实际应用中应该使用 LLM 进行智能合并
        if len(old_content) + len(new_content) < self.config.max_content_length:
            # 避免简单拼接，这里只是示例
            # 实际应该提取新信息并更新
            return new_content  # 使用新内容
        
        return new_content
    
    async def smart_merge(
        self,
        new_content: str,
        old_content: str,
        llm_provider: Any = None,
    ) -> str:
        """
        智能合并（使用 LLM）
        
        Args:
            new_content: 新内容
            old_content: 旧内容
            llm_provider: LLM 提供器
        
        Returns:
            合并后的内容
        """
        if llm_provider is None:
            return self._merge_content(new_content, old_content)
        
        prompt = f"""请将以下两条记忆合并为一条，保留所有有用信息：

旧记忆：
{old_content}

新记忆：
{new_content}

要求：
1. 保留所有重要信息
2. 如果有冲突，以新记忆为准
3. 语言简洁清晰
4. 不要添加原文没有的信息

合并后的记忆："""

        response = await llm_provider.complete(
            prompt=prompt,
            max_tokens=self._llm_config.agent_max_tokens,
        )
        
        return response.content.strip()
    
    def deduplicate(
        self,
        memories: List[Any],
        threshold: float = None,
    ) -> List[Any]:
        """
        批量去重
        
        Args:
            memories: 记忆列表
            threshold: 相似度阈值
        
        Returns:
            去重后的列表
        """
        if threshold is None:
            threshold = self.config.duplicate_threshold
        
        # 简单实现：基于内容哈希去重
        seen = set()
        result = []
        
        for mem in memories:
            content = self._get_content(mem)
            # A nullable "content" column can yield None, and callers may pass
            # non-string scalars; .strip() on those raised AttributeError.
            # Coerce to text so dedup treats them like the empty content.
            content_hash = hash(str(content or "").strip().lower())
            
            if content_hash not in seen:
                seen.add(content_hash)
                result.append(mem)
        
        return result
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "check_count": self._check_count,
            "merge_count": self._merge_count,
            "merge_rate": (
                self._merge_count / self._check_count
                if self._check_count > 0 else 0
            ),
        }
