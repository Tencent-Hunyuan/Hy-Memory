"""
Agent Memory - 记忆评分

计算记忆的综合评分，考虑语义相似度、时间衰减、重要性等因素
"""

import math
from typing import List, Optional
from datetime import datetime, timezone

from ..models import MemoryEntry, MemoryScore
from ..config import RecallConfig


class MemoryScorer:
    """
    记忆评分器
    
    计算记忆的综合评分，支持：
    - 语义相似度评分
    - 时间衰减评分
    - 重要性评分
    - 访问频率评分
    """
    
    def __init__(self, config: Optional[RecallConfig] = None):
        """
        初始化评分器
        
        Args:
            config: 召回配置
        """
        self.config = config or RecallConfig()
    
    def score(
        self,
        entry: MemoryEntry,
        semantic_score: float = 0.0,
        reference_time: Optional[datetime] = None
    ) -> MemoryScore:
        """
        计算单条记忆的综合评分
        
        Args:
            entry: 记忆条目
            semantic_score: 语义相似度分数（0-1）
            reference_time: 参考时间（用于计算时间衰减）
        
        Returns:
            评分结果
        """
        if reference_time is None:
            reference_time = datetime.now()
        
        # 1. 语义相似度分数（已由向量检索提供）
        # NaN compares False against every bound, so max/min would silently
        # clamp it to 1.0 (a perfect score). Treat it as no similarity.
        semantic = (
            0.0
            if math.isnan(semantic_score)
            else max(0.0, min(1.0, semantic_score))
        )
        
        # 2. 时间衰减分数
        recency = self._calc_recency_score(entry, reference_time)
        
        # 3. 重要性分数
        importance = self._calc_importance_score(entry)
        
        # 4. 访问频率分数
        access = self._calc_access_score(entry)
        
        return MemoryScore(
            semantic_score=semantic,
            recency_score=recency,
            importance_score=importance,
            access_score=access,
            semantic_weight=self.config.semantic_weight,
            recency_weight=self.config.recency_weight,
            importance_weight=self.config.importance_weight,
            access_weight=self.config.access_weight,
        )
    
    def _get_event_time(self, entry: MemoryEntry) -> Optional[datetime]:
        """获取 entry 的事件时间，兼容 V2 MemoryNode 和 V1 metadata"""
        # V2: MemoryNode 直接有 created_at / valid_from
        if hasattr(entry, 'created_at') and entry.created_at is not None:
            return entry.created_at
        # V1: entry.metadata.event_time
        if hasattr(entry, 'metadata') and entry.metadata and hasattr(entry.metadata, 'event_time'):
            return entry.metadata.event_time
        return None

    def _get_importance(self, entry: MemoryEntry) -> float:
        """获取 entry 的重要性，兼容 V2 MemoryNode 和 V1 metadata"""
        # V2: MemoryNode 直接有 confidence 字段作为重要性指标
        if hasattr(entry, 'confidence') and entry.confidence is not None:
            return entry.confidence
        # V1: entry.metadata.importance
        if hasattr(entry, 'metadata') and entry.metadata and hasattr(entry.metadata, 'importance'):
            return entry.metadata.importance
        return 0.5

    def _get_access_count(self, entry: MemoryEntry) -> int:
        """获取 entry 的访问次数，兼容 V2 MemoryNode 和 V1 metadata"""
        # V2: MemoryNode 直接有 access_count
        if hasattr(entry, 'access_count') and entry.access_count is not None:
            return entry.access_count
        # V1: entry.metadata.access_count
        if hasattr(entry, 'metadata') and entry.metadata and hasattr(entry.metadata, 'access_count'):
            return entry.metadata.access_count
        return 0

    def _get_semantic_score(self, entry: MemoryEntry) -> float:
        """获取 entry 的语义分数，兼容 score 对象"""
        if hasattr(entry, 'score') and entry.score and hasattr(entry.score, 'semantic_score'):
            return entry.score.semantic_score
        return 0.0

    def _get_final_score(self, entry: MemoryEntry) -> float:
        """获取 entry 的最终评分，兼容 score 对象"""
        if hasattr(entry, 'score') and entry.score and hasattr(entry.score, 'final_score'):
            return entry.score.final_score
        return 0.0

    def _calc_recency_score(
        self,
        entry: MemoryEntry,
        reference_time: datetime
    ) -> float:
        """
        计算时间衰减分数
        
        使用指数衰减：score = decay_factor ^ (days / decay_days)
        """
        event_time = self._get_event_time(entry)
        if event_time is None:
            return 0.5  # 默认中等分数

        # score() defaults reference_time to a naive datetime.now(), but event
        # times coming from history_store / embed_service may be timezone-aware.
        # Subtracting naive from aware (or vice versa) raises TypeError and
        # aborts scoring for that memory. Normalize: treat a naive value as UTC
        # so both sides share a tz basis, without changing the decay formula.
        if (reference_time.tzinfo is None) != (event_time.tzinfo is None):
            if reference_time.tzinfo is None:
                reference_time = reference_time.replace(tzinfo=timezone.utc)
            else:
                event_time = event_time.replace(tzinfo=timezone.utc)
        days_passed = (reference_time - event_time).days

        if days_passed <= 0:
            return 1.0
        
        # 指数衰减
        decay_days = self.config.recency_decay_days
        decay_factor = self.config.recency_decay_factor
        
        score = math.pow(decay_factor, days_passed / decay_days)
        return max(0.0, min(1.0, score))
    
    def _calc_importance_score(self, entry: MemoryEntry) -> float:
        """计算重要性分数"""
        return max(0.0, min(1.0, self._get_importance(entry)))
    
    def _calc_access_score(self, entry: MemoryEntry) -> float:
        """
        计算访问频率分数
        
        使用对数函数：score = log(access_count + 1) / log(max_count + 1)
        """
        access_count = self._get_access_count(entry)
        
        # 假设最大访问次数为 100
        max_count = 100
        
        # A NaN access count satisfies neither ``<= 0`` nor ``> 0``; without
        # this guard it reaches log() and then the NaN clamp below, yielding
        # a spurious full access score.
        if not isinstance(access_count, (int, float)) or math.isnan(
            access_count
        ) or access_count <= 0:
            return 0.0

        score = math.log(access_count + 1) / math.log(max_count + 1)
        return max(0.0, min(1.0, score))
    
    def score_batch(
        self,
        entries: List[MemoryEntry],
        reference_time: Optional[datetime] = None
    ) -> List[MemoryEntry]:
        """
        批量计算评分
        
        Args:
            entries: 记忆列表
            reference_time: 参考时间
        
        Returns:
            带评分的记忆列表
        """
        if reference_time is None:
            reference_time = datetime.now()
        
        for entry in entries:
            # 使用已有的语义分数
            semantic_score = self._get_semantic_score(entry)
            entry.score = self.score(entry, semantic_score, reference_time)
        
        return entries
    
    def rank(
        self,
        entries: List[MemoryEntry],
        reference_time: Optional[datetime] = None
    ) -> List[MemoryEntry]:
        """
        评分并排序
        
        Args:
            entries: 记忆列表
            reference_time: 参考时间
        
        Returns:
            按综合评分排序的记忆列表
        """
        scored = self.score_batch(entries, reference_time)
        return sorted(scored, key=lambda e: self._get_final_score(e), reverse=True)
    
    def filter_by_threshold(
        self,
        entries: List[MemoryEntry],
        min_score: float = 0.0
    ) -> List[MemoryEntry]:
        """
        按分数阈值过滤
        
        Args:
            entries: 记忆列表
            min_score: 最小分数阈值
        
        Returns:
            过滤后的记忆列表
        """
        return [e for e in entries if self._get_final_score(e) >= min_score]
    
    def adjust_weights(
        self,
        semantic_weight: Optional[float] = None,
        recency_weight: Optional[float] = None,
        importance_weight: Optional[float] = None,
        access_weight: Optional[float] = None
    ) -> None:
        """
        调整评分权重
        
        权重会自动归一化
        """
        # ``or`` would treat an explicitly supplied ``0.0`` as missing and
        # silently keep the old weight, so callers could not disable a single
        # scoring dimension. Only ``None`` means "leave this weight unchanged".
        weights = [
            self.config.semantic_weight
            if semantic_weight is None
            else semantic_weight,
            self.config.recency_weight
            if recency_weight is None
            else recency_weight,
            self.config.importance_weight
            if importance_weight is None
            else importance_weight,
            self.config.access_weight
            if access_weight is None
            else access_weight,
        ]

        # 归一化
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]
        
        self.config.semantic_weight = weights[0]
        self.config.recency_weight = weights[1]
        self.config.importance_weight = weights[2]
        self.config.access_weight = weights[3]
